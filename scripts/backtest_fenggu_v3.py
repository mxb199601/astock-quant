#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周易峰/谷 v3 回测：三谷买 → 三峰前卖 PATH（研究用，非荐股）。

定义见 docs/plans/2026-10-13-yijing-fenggu-define.md
输出 output/feng3/yijing_peak/
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib.data import list_cached_symbols, load_kline_csv
from lib.feng3 import detect_feng3_events, detect_fenggu_v3_events
from lib.metrics import apply_roundtrip_cost, summarize_returns
from lib.paths import OUTPUT

HOLDS = (5, 10, 20)
COST_BPS = 15.0
OUT_DIR = os.path.join(OUTPUT, "feng3", "yijing_peak")


def _path_for_events(
    lows: pd.DataFrame,
    highs: pd.DataFrame,
    close_cache: Dict[str, np.ndarray],
) -> pd.DataFrame:
    """谷确认日买；下一峰确认日卖 / 到期卖。"""
    rows: List[Dict[str, Any]] = []
    for sym, g in lows.groupby("sym"):
        g = g.sort_values("bar_idx")
        hs = highs[highs["sym"] == sym].sort_values("bar_idx")
        high_bars = hs["bar_idx"].to_numpy(dtype=int) if len(hs) else np.array([], dtype=int)
        high_closes = hs["close"].to_numpy(dtype=float) if len(hs) else np.array([], dtype=float)
        high_dates = hs["date"].astype(str).to_numpy() if len(hs) else np.array([], dtype=object)

        close = close_cache.get(sym)
        if close is None:
            continue
        n = len(close)

        for _, e in g.iterrows():
            i = int(e["bar_idx"])
            if i < 0 or i >= n:
                continue
            entry = float(close[i])
            if entry <= 0:
                continue
            nxt = None
            if len(high_bars):
                pos = int(np.searchsorted(high_bars, i + 1, side="left"))
                if pos < len(high_bars):
                    nxt = pos
            row = e.to_dict()
            row["entry_close"] = entry
            row["year"] = int(str(e["date"])[:4])
            if nxt is not None:
                hb = int(high_bars[nxt])
                row["next_peak_bar"] = hb
                row["next_peak_date"] = str(high_dates[nxt])
                row["bars_to_peak"] = hb - i
                row["next_peak_close"] = float(high_closes[nxt])
            else:
                row["next_peak_bar"] = None
                row["next_peak_date"] = None
                row["bars_to_peak"] = None
                row["next_peak_close"] = None

            for h in HOLDS:
                exit_i = min(n - 1, i + h)
                exit_reason = "max_hold"
                if nxt is not None:
                    hb = int(high_bars[nxt])
                    if i < hb <= i + h:
                        exit_i = hb
                        exit_reason = "三峰"
                exit_px = float(close[exit_i])
                raw = (exit_px / entry - 1.0) * 100.0
                ret = apply_roundtrip_cost(raw, COST_BPS)
                row[f"path_{h}"] = ret
                row[f"exit_reason_{h}"] = exit_reason
                row[f"hold_bars_{h}"] = exit_i - i
                row[f"sold_on_peak_{h}"] = exit_reason == "三峰"
                end_f = min(n - 1, i + h)
                row[f"fixed_{h}"] = apply_roundtrip_cost(
                    (float(close[end_f]) / entry - 1.0) * 100.0, COST_BPS
                )

            # 至峰 ≤60
            exit_i60 = min(n - 1, i + 60)
            reason60 = "max_hold"
            if nxt is not None:
                hb = int(high_bars[nxt])
                if i < hb <= i + 60:
                    exit_i60 = hb
                    reason60 = "三峰"
            raw60 = (float(close[exit_i60]) / entry - 1.0) * 100.0
            row["path_to_peak60"] = apply_roundtrip_cost(raw60, COST_BPS)
            row["sold_on_peak_60"] = reason60 == "三峰"
            row["hold_bars_60"] = exit_i60 - i
            rows.append(row)
    return pd.DataFrame(rows)


def _stats(sub: pd.DataFrame, label: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"slice": label, "n": int(len(sub))}
    wins, means = [], []
    for h in HOLDS:
        col = f"path_{h}"
        s = summarize_returns(sub[col].dropna().tolist(), name=f"path{h}")
        out[f"path{h}_n"] = int(s["n"])
        out[f"path{h}_win"] = round(float(s["winrate"]), 2) if s["n"] else None
        out[f"path{h}_mean"] = round(float(s["mean"]), 3) if s["n"] else None
        out[f"path{h}_med"] = round(float(s["median"]), 3) if s["n"] else None
        if s["n"]:
            wins.append(float(s["winrate"]))
            means.append(float(s["mean"]))
        pk = f"sold_on_peak_{h}"
        out[f"peak_exit_pct_{h}"] = (
            round(float(sub[pk].mean()) * 100, 1) if pk in sub.columns and len(sub) else None
        )
    s60 = summarize_returns(sub["path_to_peak60"].dropna().tolist(), name="p60") if "path_to_peak60" in sub.columns else {"n": 0}
    out["path60_win"] = round(float(s60["winrate"]), 2) if s60.get("n") else None
    out["path60_mean"] = round(float(s60["mean"]), 3) if s60.get("n") else None
    out["peak_exit_pct_60"] = (
        round(float(sub["sold_on_peak_60"].mean()) * 100, 1)
        if "sold_on_peak_60" in sub.columns and len(sub)
        else None
    )
    if "bars_to_peak" in sub.columns:
        b = sub["bars_to_peak"].dropna()
        out["bars_to_peak_med"] = round(float(b.median()), 1) if len(b) else None
        out["bars_to_peak_mean"] = round(float(b.mean()), 1) if len(b) else None
        out["has_next_peak_pct"] = round(100.0 * len(b) / len(sub), 1) if len(sub) else 0.0
    if wins:
        out["score"] = round(float(np.mean(wins)) + 0.5 * float(np.mean(means)), 3)
        out["avg_win"] = round(float(np.mean(wins)), 2)
        out["avg_mean"] = round(float(np.mean(means)), 3)
    else:
        out["score"] = None
        out["avg_win"] = None
        out["avg_mean"] = None
    return out


def collect_events(
    pool: Sequence[str],
    defs: Sequence[Tuple[str, Dict[str, Any]]],
    min_bars: int = 200,
) -> Tuple[pd.DataFrame, Dict[str, np.ndarray]]:
    all_rows: List[Dict[str, Any]] = []
    close_cache: Dict[str, np.ndarray] = {}
    t0 = time.time()
    n_ok = 0
    for j, sym in enumerate(pool):
        df = load_kline_csv(str(sym), min_bars=min_bars)
        if df is None or len(df) < min_bars:
            continue
        close_cache[str(sym)] = df["close"].astype(float).values
        n_ok += 1
        for def_name, kwargs in defs:
            if def_name == "v2":
                evs = detect_feng3_events(df, swing_k=kwargs.get("swing_k", 3), near_pct=kwargs.get("near_pct", 2.0))
                for e in evs:
                    e = dict(e)
                    e["sym"] = str(sym)
                    e["fenggu"] = "谷" if e["pred"] == "LOW" else "峰"
                    e["def_id"] = "v2"
                    all_rows.append(e)
            else:
                evs = detect_fenggu_v3_events(df, **kwargs)
                for e in evs:
                    e = dict(e)
                    e["sym"] = str(sym)
                    all_rows.append(e)
        if (j + 1) % 200 == 0:
            print(f"  … {j+1}/{len(pool)} symbols, events={len(all_rows)}, elapsed={time.time()-t0:.1f}s")
    print(f"done symbols_ok={n_ok} events={len(all_rows)} elapsed={time.time()-t0:.1f}s")
    return pd.DataFrame(all_rows), close_cache


def run(sample_n: int = 800, seed: int = 42, min_bars: int = 200) -> Dict[str, Any]:
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))

    defs = [
        ("v2", {"swing_k": 3, "near_pct": 2.0}),
        ("v3", {"swing_k": 3, "min_leg_pct": 3.0, "require_bian": True, "near_pct": 2.0, "relax_equal": False}),
        ("v3B", {"swing_k": 3, "min_leg_pct": 3.0, "require_bian": False, "near_pct": 2.0, "relax_equal": False}),
    ]
    edf, close_cache = collect_events(pool, defs, min_bars=min_bars)
    events_path = os.path.join(OUT_DIR, "fenggu_events.csv")
    edf.to_csv(events_path, index=False, encoding="utf-8-sig")

    path_rows = []
    summary_rows = []
    for def_id in ("v2", "v3", "v3B"):
        sub = edf[edf["def_id"] == def_id].copy()
        lows = sub[sub["pred"] == "LOW"]
        highs = sub[sub["pred"] == "HIGH"]
        pdf = _path_for_events(lows, highs, close_cache)
        if len(pdf):
            pdf["def_id"] = def_id
            path_rows.append(pdf)
            summary_rows.append(_stats(pdf, f"{def_id}_谷ALL"))
            # 结构同意切片
            if "struct_agree" in pdf.columns:
                m = pdf["struct_agree"] == True  # noqa: E712
                if m.sum() >= 30:
                    summary_rows.append(_stats(pdf.loc[m], f"{def_id}_谷_struct同意"))
                m2 = pdf["struct_agree"] == False  # noqa: E712
                if m2.sum() >= 30:
                    summary_rows.append(_stats(pdf.loc[m2], f"{def_id}_谷_struct不同意"))

    path_df = pd.concat(path_rows, ignore_index=True) if path_rows else pd.DataFrame()
    path_path = os.path.join(OUT_DIR, "path_gu_to_feng.csv")
    path_df.to_csv(path_path, index=False, encoding="utf-8-sig")

    sdf = pd.DataFrame(summary_rows)
    sum_path = os.path.join(OUT_DIR, "summary.csv")
    sdf.to_csv(sum_path, index=False, encoding="utf-8-sig")

    # count table
    counts = (
        edf.groupby(["def_id", "pred"]).size().unstack(fill_value=0).reset_index()
        if len(edf)
        else pd.DataFrame()
    )
    counts.to_csv(os.path.join(OUT_DIR, "event_counts.csv"), index=False, encoding="utf-8-sig")

    # buy_low dump pointer
    buy_low_src = os.path.join(OUTPUT, "feng3", "buy_low", "buy_low_report.md")
    buy_low_dump = os.path.join(OUTPUT, "feng3", "buy_low_dump", "buy_low_report.md")

    md = _write_report(sdf, edf, path_df, buy_low_src, buy_low_dump, sample_n, seed)
    md_path = os.path.join(OUT_DIR, "yijing_peak_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print("wrote", md_path)
    return {"ok": True, "events": len(edf), "path_n": len(path_df), "summary": sdf.to_dict("records"), "md": md_path}


def _write_report(
    sdf: pd.DataFrame,
    edf: pd.DataFrame,
    path_df: pd.DataFrame,
    buy_low_src: str,
    buy_low_dump: str,
    sample_n: int,
    seed: int,
) -> str:
    lines: List[str] = []
    lines.append("# 周易峰/谷 v3 定义与 PATH 回测")
    lines.append("")
    lines.append("> 研究工具，**不构成投资建议 / 非荐股**。无 HS300。不升格 joint/action。")
    lines.append("")
    lines.append(f"- 样本：主板非 ST **{sample_n}** 只（seed={seed}）")
    lines.append("- 计划：`docs/plans/2026-10-13-yijing-fenggu-define.md`")
    lines.append("- 口径：确认日收盘买谷；下一峰确认日卖 / 到期卖；成本 15bps 往返")
    lines.append("")
    lines.append("## 1. 正名（周易 → OHLC）")
    lines.append("")
    lines.append("| 易理 | 操作定义 |")
    lines.append("|------|----------|")
    lines.append("| 化 / 阴阳消长 | zigzag 折点交替（非峰谷） |")
    lines.append("| 穷 / 三变为穷 | 同侧第 3 次**严格**单调延伸 + 腿波幅≥3% |")
    lines.append("| 谷 = 阴穷 | L3<L2<L1 +（v3）确认日收盘>昨收 |")
    lines.append("| 峰 = 阳穷 | H3>H2>H1 +（v3）确认日收盘<昨收 |")
    lines.append("| 变 | 确认日阴阳翻转（阳始/阴始） |")
    lines.append("| 通 | PATH 检验，不写入定义 |")
    lines.append("")
    lines.append("## 2. 与 v2 zigzag 对照")
    lines.append("")
    lines.append("| 维度 | v2（现行 feng3） | v3 | v3B |")
    lines.append("|------|------------------|----|-----|")
    lines.append("| 称峰/谷 | LOW/HIGH 事件（口语混用摆动） | 仅穷点 | 仅穷点 |")
    lines.append("| 三连 | ≤ / ≥ 允许相等 | 严格 < / > | 严格 < / > |")
    lines.append("| 腿波幅 | 无 | ≥3% | ≥3% |")
    lines.append("| 变门 | 无 | 确认日收盘翻转 | 无 |")
    lines.append("")

    if len(edf):
        lines.append("### 事件计数")
        lines.append("")
        lines.append("| def | 谷(LOW) | 峰(HIGH) | 合计 |")
        lines.append("|-----|---------|----------|------|")
        for did in ("v2", "v3", "v3B"):
            sub = edf[edf["def_id"] == did]
            n_l = int((sub["pred"] == "LOW").sum())
            n_h = int((sub["pred"] == "HIGH").sum())
            lines.append(f"| {did} | {n_l} | {n_h} | {n_l+n_h} |")
        lines.append("")

    lines.append("## 3. 三谷买 → 三峰前卖 PATH")
    lines.append("")
    lines.append("| slice | n | p5胜/均 | p10胜/均 | p20胜/均 | 至峰60胜/均 | 峰卖%20 | 峰卖%60 | 至峰中位 | score |")
    lines.append("|-------|---|---------|----------|----------|-------------|--------|--------|----------|-------|")
    for _, r in sdf.iterrows():
        lines.append(
            "| {slice} | {n} | {p5} | {p10} | {p20} | {p60} | {pk20} | {pk60} | {med} | {score} |".format(
                slice=r["slice"],
                n=int(r["n"]),
                p5=_fmt(r.get("path5_win"), r.get("path5_mean")),
                p10=_fmt(r.get("path10_win"), r.get("path10_mean")),
                p20=_fmt(r.get("path20_win"), r.get("path20_mean")),
                p60=_fmt(r.get("path60_win"), r.get("path60_mean")),
                pk20=r.get("peak_exit_pct_20"),
                pk60=r.get("peak_exit_pct_60"),
                med=r.get("bars_to_peak_med"),
                score=r.get("score"),
            )
        )
    lines.append("")

    # honest compare v3 vs v2
    def _row(name: str) -> Optional[pd.Series]:
        m = sdf[sdf["slice"] == name]
        return m.iloc[0] if len(m) else None

    v2 = _row("v2_谷ALL")
    v3 = _row("v3_谷ALL")
    v3b = _row("v3B_谷ALL")
    lines.append("## 4. 诚实结论")
    lines.append("")
    if v2 is not None and v3 is not None:
        lines.append(
            "1. **v3 相对 v2**：n %d→%d（过滤比 %.1f%%）；path20 胜 %s→%s，均 %s→%s。"
            % (
                int(v2["n"]),
                int(v3["n"]),
                100.0 * int(v3["n"]) / max(int(v2["n"]), 1),
                v2.get("path20_win"),
                v3.get("path20_win"),
                v2.get("path20_mean"),
                v3.get("path20_mean"),
            )
        )
    if v3b is not None and v3 is not None:
        lines.append(
            "2. **变门（确认日阴阳翻转）**：v3B(无变门) n=%d score=%s；v3(有变门) n=%d score=%s。"
            % (int(v3b["n"]), v3b.get("score"), int(v3["n"]), v3.get("score"))
        )
    lines.append(
        "3. **短窗仍难卖到三峰**：至下一峰中位见上表；PATH5/10/20 大量为到期卖≈固定持有+成本，不是神话 alpha。"
    )
    lines.append(
        "4. **正名价值独立于边**：即使 PATH 无独立可交易边，v3 仍把「峰/谷」从摆动折点中剥离为阴穷/阳穷——这是 Zoey 问题的直接回答。"
    )
    lines.append("5. **不升格** joint/action；不荐股；不含大盘闸。")
    lines.append("")

    lines.append("## 5. 既有 buy_low（v2 口径）转存")
    lines.append("")
    if os.path.isfile(buy_low_src):
        lines.append(f"- 源报告：`{buy_low_src}`")
    if os.path.isfile(buy_low_dump):
        lines.append(f"- 转存目录：`output/feng3/buy_low_dump/`（含 summary/events/report）")
        # embed short summary
        sum_csv = os.path.join(os.path.dirname(buy_low_dump), "summary.csv")
        if os.path.isfile(sum_csv):
            try:
                bs = pd.read_csv(sum_csv)
                lines.append("")
                lines.append("### buy_low summary 摘录")
                lines.append("")
                lines.append("```")
                lines.append(bs.to_string(index=False))
                lines.append("```")
            except Exception:
                pass
        peak_csv = os.path.join(os.path.dirname(buy_low_dump), "peak_hold_compare.csv")
        if os.path.isfile(peak_csv):
            try:
                pc = pd.read_csv(peak_csv)
                lines.append("")
                lines.append("### peak_hold_compare 摘录")
                lines.append("")
                lines.append("```")
                lines.append(pc.to_string(index=False))
                lines.append("```")
            except Exception:
                pass
    else:
        lines.append("- buy_low 转存未见（目录空或不存在）。")
    lines.append("")
    lines.append("## 6. 输出文件")
    lines.append("")
    lines.append("- `output/feng3/yijing_peak/fenggu_events.csv`")
    lines.append("- `output/feng3/yijing_peak/path_gu_to_feng.csv`")
    lines.append("- `output/feng3/yijing_peak/summary.csv`")
    lines.append("- `output/feng3/yijing_peak/event_counts.csv`")
    lines.append("- `output/feng3/yijing_peak/yijing_peak_report.md`")
    lines.append("")
    return "\n".join(lines) + "\n"


def _fmt(win, mean) -> str:
    if win is None or mean is None or (isinstance(win, float) and win != win):
        return "—"
    return f"{win:.1f}/{mean:+.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=800)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-bars", type=int, default=200)
    args = ap.parse_args()
    run(sample_n=args.sample, seed=args.seed, min_bars=args.min_bars)


if __name__ == "__main__":
    main()
