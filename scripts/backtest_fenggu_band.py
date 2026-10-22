#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周易峰谷 v3 + 波段轨：三谷买→三峰前卖（带 band 上下文切片）。研究用，非荐股。

计划：docs/plans/2026-10-13-yijing-fenggu-band.md
输出：output/feng3/band/
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as _fm

def _setup_cjk_font() -> None:
    for fp in (
        "/Users/meng/Library/Fonts/msyh.ttc",
        "/Users/meng/Library/Fonts/msyhl.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/STHeiti Light.ttc",
    ):
        if os.path.isfile(fp):
            try:
                _fm.fontManager.addfont(fp)
                plt.rcParams["font.family"] = _fm.FontProperties(fname=fp).get_name()
                plt.rcParams["axes.unicode_minus"] = False
                return
            except Exception:
                continue

_setup_cjk_font()
import numpy as np
import pandas as pd

from lib.data import list_cached_symbols, load_kline_csv
from lib.feng3 import (
    annotate_fenggu_with_band,
    build_zigzag,
    detect_fenggu_v3_events,
    detect_swings,
    filter_major_swings,
    _line_at,
)
from lib.metrics import apply_roundtrip_cost, summarize_returns
from lib.paths import OUTPUT

HOLDS = (5, 10, 20)
COST_BPS = 15.0
OUT_DIR = os.path.join(OUTPUT, "feng3", "band")


def _path_for_events(
    lows: pd.DataFrame,
    highs: pd.DataFrame,
    close_cache: Dict[str, np.ndarray],
) -> pd.DataFrame:
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
    s60 = (
        summarize_returns(sub["path_to_peak60"].dropna().tolist(), name="p60")
        if "path_to_peak60" in sub.columns
        else {"n": 0}
    )
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


def _fmt(win, mean) -> str:
    if win is None or mean is None or (isinstance(win, float) and win != win):
        return "—"
    return f"{win:.1f}/{mean:+.2f}"


def collect(
    pool: Sequence[str],
    swing_k: int,
    min_leg_pct: float,
    band_min_leg_pct: float,
    min_bars: int,
) -> Tuple[pd.DataFrame, Dict[str, np.ndarray], Dict[str, pd.DataFrame]]:
    all_rows: List[Dict[str, Any]] = []
    close_cache: Dict[str, np.ndarray] = {}
    df_cache: Dict[str, pd.DataFrame] = {}
    t0 = time.time()
    n_ok = 0
    for j, sym in enumerate(pool):
        df = load_kline_csv(str(sym), min_bars=min_bars)
        if df is None or len(df) < min_bars:
            continue
        close_cache[str(sym)] = df["close"].astype(float).values
        df_cache[str(sym)] = df
        n_ok += 1
        evs = detect_fenggu_v3_events(
            df,
            swing_k=swing_k,
            min_leg_pct=min_leg_pct,
            require_bian=True,
            near_pct=2.0,
            relax_equal=False,
        )
        ann = annotate_fenggu_with_band(
            df, evs, swing_k=swing_k, band_min_leg_pct=band_min_leg_pct
        )
        for e in ann:
            e = dict(e)
            e["sym"] = str(sym)
            all_rows.append(e)
        if (j + 1) % 200 == 0:
            print(
                f"  … {j+1}/{len(pool)} symbols, events={len(all_rows)}, "
                f"elapsed={time.time()-t0:.1f}s"
            )
    print(f"done symbols_ok={n_ok} events={len(all_rows)} elapsed={time.time()-t0:.1f}s")
    return pd.DataFrame(all_rows), close_cache, df_cache


def plot_sample(
    sym: str,
    df: pd.DataFrame,
    events: pd.DataFrame,
    swing_k: int,
    band_min_leg_pct: float,
    out_path: str,
    max_bars: int = 250,
) -> None:
    """收盘 + major zigzag + 上/下轨外推 + 谷/峰标记。"""
    if df is None or len(df) < 80:
        return
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    dates = df["date"].astype(str).values
    swings = detect_swings(high, low, swing_k=swing_k)
    zz = build_zigzag(swings)
    major = filter_major_swings(zz, min_leg_pct=band_min_leg_pct)
    n = len(df)
    start = max(0, n - max_bars)
    xs = np.arange(start, n)

    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(xs, close[start:], color="#444", lw=1.0, label="close", alpha=0.85)

    # major zigzag polyline
    mj = [s for s in major if s.center_idx >= start]
    if len(mj) >= 2:
        ax.plot(
            [s.center_idx for s in mj],
            [s.price for s in mj],
            color="#1f77b4",
            lw=1.6,
            marker="o",
            ms=3.5,
            label="major zz",
            zorder=4,
        )

    # last upper/lower band segments projected across window (using last confirmed majors)
    tops = [s for s in major if s.conf_idx < n]
    bots = [s for s in major if s.conf_idx < n]
    tops_t = [s for s in tops if s.kind == "top"]
    bots_t = [s for s in bots if s.kind == "bot"]
    if len(tops_t) >= 2 and len(bots_t) >= 2:
        # draw band from max(start, second-last pivot) to n-1 using last two of each
        u_y = [_line_at(tops_t[-2], tops_t[-1], int(x)) for x in xs]
        l_y = [_line_at(bots_t[-2], bots_t[-1], int(x)) for x in xs]
        ax.plot(xs, u_y, color="#d62728", lw=1.2, ls="--", label="upper", alpha=0.9)
        ax.plot(xs, l_y, color="#2ca02c", lw=1.2, ls="--", label="lower", alpha=0.9)
        ax.fill_between(xs, l_y, u_y, color="#1f77b4", alpha=0.08, label="band")

    sub_e = events[(events["sym"] == sym) & (events["bar_idx"] >= start)]
    gu = sub_e[sub_e["pred"] == "LOW"]
    feng = sub_e[sub_e["pred"] == "HIGH"]
    if len(gu):
        ax.scatter(
            gu["bar_idx"],
            gu["close"],
            marker="^",
            s=60,
            c="#2ca02c",
            label="gu",
            zorder=5,
            edgecolors="k",
            linewidths=0.4,
        )
    if len(feng):
        ax.scatter(
            feng["bar_idx"],
            feng["close"],
            marker="v",
            s=60,
            c="#d62728",
            label="feng",
            zorder=5,
            edgecolors="k",
            linewidths=0.4,
        )

    # x ticks sparse dates
    tick_idx = list(range(start, n, max(1, (n - start) // 6)))
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([dates[i][2:10] for i in tick_idx], rotation=20, fontsize=8)
    ax.set_title(f"{sym} · 周易峰谷v3 + 波段轨 (major≥{band_min_leg_pct}%)")
    ax.set_ylabel("price")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def run(
    sample_n: int = 800,
    seed: int = 42,
    min_bars: int = 200,
    swing_k: int = 3,
    min_leg_pct: float = 3.0,
    band_min_leg_pct: float = 8.0,
) -> Dict[str, Any]:
    os.makedirs(OUT_DIR, exist_ok=True)
    chart_dir = os.path.join(OUT_DIR, "charts")
    os.makedirs(chart_dir, exist_ok=True)

    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))

    print(
        f"sample={len(pool)} seed={seed} swing_k={swing_k} "
        f"v3_min_leg={min_leg_pct} band_min_leg={band_min_leg_pct}"
    )
    edf, close_cache, df_cache = collect(
        pool, swing_k, min_leg_pct, band_min_leg_pct, min_bars
    )
    events_path = os.path.join(OUT_DIR, "band_events.csv")
    edf.to_csv(events_path, index=False, encoding="utf-8-sig")

    lows = edf[edf["pred"] == "LOW"].copy()
    highs = edf[edf["pred"] == "HIGH"].copy()
    pdf = _path_for_events(lows, highs, close_cache)
    path_path = os.path.join(OUT_DIR, "path_gu_to_feng_band.csv")
    pdf.to_csv(path_path, index=False, encoding="utf-8-sig")

    summary_rows: List[Dict[str, Any]] = []
    if len(pdf):
        summary_rows.append(_stats(pdf, "v3_谷ALL"))
        if "band_ok" in pdf.columns:
            m = pdf["band_ok"] == True  # noqa: E712
            if m.sum() >= 30:
                summary_rows.append(_stats(pdf.loc[m], "v3_谷_band可用"))
            m0 = pdf["band_ok"] == False  # noqa: E712
            if m0.sum() >= 30:
                summary_rows.append(_stats(pdf.loc[m0], "v3_谷_band不可用"))
        if "band_buy_zone" in pdf.columns:
            m = pdf["band_buy_zone"] == True  # noqa: E712
            if m.sum() >= 30:
                summary_rows.append(_stats(pdf.loc[m], "v3_谷_买区(下轨+非下倾)"))
            m2 = pdf["band_buy_zone"] == False  # noqa: E712
            if m2.sum() >= 30:
                summary_rows.append(_stats(pdf.loc[m2], "v3_谷_非买区"))
        if "band_pos_bucket" in pdf.columns:
            for b in ("破下", "下轨", "中下", "中轨", "中上", "上轨", "破上"):
                m = pdf["band_pos_bucket"] == b
                if m.sum() >= 30:
                    summary_rows.append(_stats(pdf.loc[m], f"v3_谷_pos_{b}"))
        if "band_slope" in pdf.columns:
            for s in ("上倾", "走平", "下倾"):
                m = pdf["band_slope"] == s
                if m.sum() >= 30:
                    summary_rows.append(_stats(pdf.loc[m], f"v3_谷_slope_{s}"))
        # combo: 下轨/中下 × 上倾
        if "band_pos_bucket" in pdf.columns and "band_slope" in pdf.columns:
            m = pdf["band_pos_bucket"].isin(["下轨", "中下", "破下"]) & (pdf["band_slope"] == "上倾")
            if m.sum() >= 30:
                summary_rows.append(_stats(pdf.loc[m], "v3_谷_下沿×上倾"))
            m = pdf["band_pos_bucket"].isin(["上轨", "中上", "破上"]) & (pdf["band_slope"] == "下倾")
            if m.sum() >= 30:
                summary_rows.append(_stats(pdf.loc[m], "v3_谷_上沿×下倾(逆)"))

    sdf = pd.DataFrame(summary_rows)
    sum_path = os.path.join(OUT_DIR, "summary.csv")
    sdf.to_csv(sum_path, index=False, encoding="utf-8-sig")

    # event counts / band coverage
    cov = {
        "n_events": int(len(edf)),
        "n_gu": int((edf["pred"] == "LOW").sum()) if len(edf) else 0,
        "n_feng": int((edf["pred"] == "HIGH").sum()) if len(edf) else 0,
        "band_ok_pct": round(100.0 * float(edf["band_ok"].mean()), 1)
        if len(edf) and "band_ok" in edf.columns
        else None,
        "buy_zone_gu_pct": None,
    }
    if len(lows) and "band_buy_zone" in lows.columns:
        cov["buy_zone_gu_pct"] = round(100.0 * float(lows["band_buy_zone"].mean()), 1)
    pd.DataFrame([cov]).to_csv(
        os.path.join(OUT_DIR, "band_coverage.csv"), index=False, encoding="utf-8-sig"
    )

    # path20 by year × buy_zone
    year_rows = []
    if len(pdf):
        for y, yg in pdf.groupby("year"):
            for label, mask in (
                ("ALL", pd.Series(True, index=yg.index)),
                ("买区", yg["band_buy_zone"] == True if "band_buy_zone" in yg.columns else pd.Series(False, index=yg.index)),  # noqa: E712
            ):
                sub = yg.loc[mask]
                if len(sub) < 20:
                    continue
                s = summarize_returns(sub["path_20"].dropna().tolist(), name="p20")
                year_rows.append(
                    {
                        "year": int(y),
                        "slice": label,
                        "n": int(s["n"]),
                        "win": round(float(s["winrate"]), 2) if s["n"] else None,
                        "mean": round(float(s["mean"]), 3) if s["n"] else None,
                    }
                )
    ydf = pd.DataFrame(year_rows)
    ydf.to_csv(os.path.join(OUT_DIR, "path20_by_year.csv"), index=False, encoding="utf-8-sig")

    # sample charts: pick symbols with both 谷 and band_ok
    chart_syms: List[str] = []
    if len(edf):
        gu_ok = edf[(edf["pred"] == "LOW") & (edf.get("band_ok", False) == True)]  # noqa: E712
        counts = gu_ok.groupby("sym").size().sort_values(ascending=False)
        for sym in counts.index.tolist():
            if sym in df_cache:
                chart_syms.append(str(sym))
            if len(chart_syms) >= 6:
                break
    for sym in chart_syms:
        plot_sample(
            sym,
            df_cache[sym],
            edf,
            swing_k,
            band_min_leg_pct,
            os.path.join(chart_dir, f"{sym}_band.png"),
        )
    print(f"charts: {len(chart_syms)} -> {chart_dir}")

    md = _write_report(
        sdf,
        edf,
        pdf,
        ydf,
        cov,
        chart_syms,
        sample_n,
        seed,
        swing_k,
        min_leg_pct,
        band_min_leg_pct,
    )
    md_path = os.path.join(OUT_DIR, "band_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print("wrote", md_path)
    return {"ok": True, "events": len(edf), "path_n": len(pdf), "md": md_path}


def _write_report(
    sdf: pd.DataFrame,
    edf: pd.DataFrame,
    pdf: pd.DataFrame,
    ydf: pd.DataFrame,
    cov: Dict[str, Any],
    chart_syms: Sequence[str],
    sample_n: int,
    seed: int,
    swing_k: int,
    min_leg_pct: float,
    band_min_leg_pct: float,
) -> str:
    lines: List[str] = []
    lines.append("# 周易峰谷 v3 + 波段轨 PATH")
    lines.append("")
    lines.append("> 研究工具，**不构成投资建议 / 非荐股**。无 HS300。不升格 joint/action。不改 MES。")
    lines.append("")
    lines.append(f"- 样本：主板非 ST **{sample_n}** 只（seed={seed}）")
    lines.append(f"- 峰谷：v3 严格三连 + 腿≥{min_leg_pct}% + 确认日阴阳翻转；swing_k={swing_k}")
    lines.append(f"- 波段：细 zz 筛 major（腿≥**{band_min_leg_pct}%**）→ 连顶=上轨、连底=下轨，确认日外推")
    lines.append("- PATH：谷确认日收盘买 → 下一峰确认日卖 / 到期；15bps；5/10/20 + 至峰≤60")
    lines.append("- 计划：`docs/plans/2026-10-13-yijing-fenggu-band.md`")
    lines.append("")
    lines.append("## 1. 波段定义（叠在 v3 上）")
    lines.append("")
    lines.append("| 对象 | 规则 |")
    lines.append("|------|------|")
    lines.append("| major 折点 | 细 zigzag 上对侧腿波幅≥band_min_leg；同向更极端替换 |")
    lines.append("| 上轨 | 最近两 major 顶线性外推至确认日 |")
    lines.append("| 下轨 | 最近两 major 底线性外推 |")
    lines.append("| 位置桶 | 破下 / 下轨 / 中下 / 中轨 / 中上 / 上轨 / 破上 |")
    lines.append("| 斜率 | 下轨%/bar + 上轨同号约束 → 上倾/走平/下倾 |")
    lines.append("| 买区 | 谷 ∧ 位置∈{破下,下轨,中下} ∧ 斜率∈{上倾,走平} |")
    lines.append("")
    lines.append(
        f"覆盖：事件 {cov.get('n_events')}（谷 {cov.get('n_gu')} / 峰 {cov.get('n_feng')}）；"
        f"band_ok={cov.get('band_ok_pct')}%；谷买区占比={cov.get('buy_zone_gu_pct')}%。"
    )
    lines.append("")
    lines.append("## 2. 三谷买→三峰前卖（band 切片）")
    lines.append("")
    lines.append(
        "| slice | n | p5胜/均 | p10胜/均 | p20胜/均 | 至峰60胜/均 | 峰卖%20 | 峰卖%60 | 至峰中位 | score |"
    )
    lines.append(
        "|-------|---|---------|----------|----------|-------------|--------|--------|----------|-------|"
    )
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

    def _row(name: str) -> Optional[pd.Series]:
        m = sdf[sdf["slice"] == name]
        return m.iloc[0] if len(m) else None

    base = _row("v3_谷ALL")
    buyz = _row("v3_谷_买区(下轨+非下倾)")
    low_up = _row("v3_谷_下沿×上倾")

    lines.append("## 3. 诚实结论")
    lines.append("")
    if base is not None:
        lines.append(
            f"1. **v3 基线（无 band 门）**：n={int(base['n'])}；"
            f"path20 {_fmt(base.get('path20_win'), base.get('path20_mean'))}；"
            f"score={base.get('score')}。"
        )
    if buyz is not None and base is not None:
        d_win = (buyz.get("path20_win") or 0) - (base.get("path20_win") or 0)
        d_mean = (buyz.get("path20_mean") or 0) - (base.get("path20_mean") or 0)
        lines.append(
            f"2. **买区（下轨附近∧非下倾）**：n={int(buyz['n'])}；"
            f"path20 {_fmt(buyz.get('path20_win'), buyz.get('path20_mean'))}；"
            f"相对 ALL Δ胜={d_win:+.1f}pt Δ均={d_mean:+.2f}pt；score={buyz.get('score')}。"
        )
    if low_up is not None:
        lines.append(
            f"3. **下沿×上倾**：n={int(low_up['n'])}；"
            f"path20 {_fmt(low_up.get('path20_win'), low_up.get('path20_mean'))}；"
            f"score={low_up.get('score')}。"
        )
    lines.append(
        "4. 波段轨是**观察层几何**（连 major 顶/底），不是独立 alpha 承诺；"
        "短窗仍大量到期卖，至峰中位偏长。"
    )
    lines.append("5. **不升格** joint/action；不荐股；不含大盘闸；不改 MES。")
    lines.append("")

    if len(ydf):
        lines.append("## 4. 分年 path20")
        lines.append("")
        lines.append("| year | slice | n | 胜率 | 均值 |")
        lines.append("|------|-------|---|------|------|")
        for _, r in ydf.iterrows():
            lines.append(
                f"| {int(r['year'])} | {r['slice']} | {int(r['n'])} | {r.get('win')} | {r.get('mean')} |"
            )
        lines.append("")

    lines.append("## 5. 样例图")
    lines.append("")
    if chart_syms:
        for s in chart_syms:
            lines.append(f"- `output/feng3/band/charts/{s}_band.png`")
    else:
        lines.append("- （无可用样例）")
    lines.append("")
    lines.append("## 6. 输出文件")
    lines.append("")
    lines.append("- `output/feng3/band/band_events.csv`")
    lines.append("- `output/feng3/band/path_gu_to_feng_band.csv`")
    lines.append("- `output/feng3/band/summary.csv`")
    lines.append("- `output/feng3/band/band_coverage.csv`")
    lines.append("- `output/feng3/band/path20_by_year.csv`")
    lines.append("- `output/feng3/band/charts/*.png`")
    lines.append("- `output/feng3/band/band_report.md`")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=800)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-bars", type=int, default=200)
    ap.add_argument("--swing-k", type=int, default=3)
    ap.add_argument("--min-leg-pct", type=float, default=3.0)
    ap.add_argument("--band-min-leg-pct", type=float, default=8.0)
    args = ap.parse_args()
    run(
        sample_n=args.sample,
        seed=args.seed,
        min_bars=args.min_bars,
        swing_k=args.swing_k,
        min_leg_pct=args.min_leg_pct,
        band_min_leg_pct=args.band_min_leg_pct,
    )


if __name__ == "__main__":
    main()
