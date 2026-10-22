#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三谷确认买入 → 三峰信号/到期卖出（研究用，非荐股）。

Zoey 口径：
  - 低谷反转不用买在低点（确认日收盘买即可）
  - 但要在三峰之前卖出 → 下一逢3 HIGH 确认日卖，或持有到期

范围：仅 LOW→买；个股条件（无 HS300）；最大化该 PATH 的 5/10/20d 胜率/均值。
条件：hl_error 的 vol / ma_pos / span / amp / near_SR / conf。

用法:
  /Users/meng/测试/.venv/bin/python scripts/feng3_buy_low.py

输出: output/feng3/buy_low/
"""
from __future__ import annotations

import itertools
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib.data import load_kline_csv
from lib.metrics import apply_roundtrip_cost, summarize_returns
from lib.paths import OUTPUT

HOLDS = (5, 10, 20)
COST_BPS = 15.0
MIN_N = 80
EVENTS_PATH = os.path.join(OUTPUT, "feng3", "feng3_events.csv")
HL_PATH = os.path.join(OUTPUT, "feng3", "hl_error", "hl_error_events.csv")
OUT_DIR = os.path.join(OUTPUT, "feng3", "buy_low")

FAMILIES: Dict[str, List[Tuple[str, Any]]] = {
    "vol": [("vol_bucket", v) for v in ("缩量", "平量", "放量")],
    "ma": [("ma_pos", v) for v in ("双下", "双上", "上20下60", "下20上60")],
    "span": [("span_bucket", v) for v in ("密", "中", "疏")],
    "amp": [("amp_bucket", v) for v in ("小幅", "中幅", "大幅")],
    "near": [("near_sr", v) for v in (True, False)],
    "conf": [("conf", v) for v in ("高", "中", "低")],
}


def _precompute_peak_exit_path() -> pd.DataFrame:
    """每个 LOW：确认日收盘买；下一 HIGH 确认日卖，或 hold_m 到期卖。"""
    ev = pd.read_csv(
        EVENTS_PATH,
        usecols=["sym", "date", "pred", "bar_idx", "center_idx", "close", "trigger_price", "conf"],
    )
    hl = pd.read_csv(
        HL_PATH,
        usecols=[
            "sym", "date", "pred", "bar_idx",
            "near_sr", "vol_bucket", "ma_pos", "span_bucket", "amp_bucket",
            "vol_ratio", "amp_pct", "span_bars", "structure", "struct_agree",
            "path_ret", "ret_fixed",  # 旧 tight PATH，对照用
        ],
    )
    # 只保留 LOW 行条件；HIGH 从 events 取卖点
    low_hl = hl[hl["pred"] == "LOW"].copy()
    lows = ev[ev["pred"] == "LOW"].merge(
        low_hl,
        on=["sym", "date", "pred", "bar_idx"],
        how="inner",
        suffixes=("", "_hl"),
    )
    highs = ev[ev["pred"] == "HIGH"][["sym", "bar_idx", "date", "close"]].rename(
        columns={"bar_idx": "high_bar", "date": "high_date", "close": "high_close"}
    )

    rows: List[Dict[str, Any]] = []
    # 按票缓存 K 线 close
    close_cache: Dict[str, np.ndarray] = {}
    date_cache: Dict[str, np.ndarray] = {}

    for sym, g in lows.groupby("sym"):
        g = g.sort_values("bar_idx")
        hs = highs[highs["sym"] == sym].sort_values("high_bar")
        high_bars = hs["high_bar"].to_numpy(dtype=int) if len(hs) else np.array([], dtype=int)
        high_closes = hs["high_close"].to_numpy(dtype=float) if len(hs) else np.array([], dtype=float)
        high_dates = hs["high_date"].astype(str).to_numpy() if len(hs) else np.array([], dtype=object)

        if sym not in close_cache:
            try:
                df = load_kline_csv(str(sym))
            except Exception:
                df = None
            if df is None or len(df) < 50:
                continue
            close_cache[sym] = df["close"].astype(float).values
            date_cache[sym] = df["date"].astype(str).values
        close = close_cache[sym]
        n = len(close)

        for _, e in g.iterrows():
            i = int(e["bar_idx"])
            if i < 0 or i >= n:
                continue
            entry = float(close[i])
            if entry <= 0:
                continue
            # 下一三峰：bar_idx > i
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
                row["next_high_bar"] = hb
                row["next_high_date"] = str(high_dates[nxt])
                row["bars_to_high"] = hb - i
                row["next_high_close"] = float(high_closes[nxt])
            else:
                row["next_high_bar"] = None
                row["next_high_date"] = None
                row["bars_to_high"] = None
                row["next_high_close"] = None

            for h in HOLDS:
                # 卖出规则：若下一 HIGH 在 (i, i+h] 内 → 在 HIGH 确认日收盘卖；否则 i+h 到期卖
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
                # 固定持有对照（忽略三峰）
                end_f = min(n - 1, i + h)
                row[f"fixed_{h}"] = apply_roundtrip_cost(
                    (float(close[end_f]) / entry - 1.0) * 100.0, COST_BPS
                )
            # 旧 tight PATH 对照
            row["tight_path20"] = e.get("path_ret")
            rows.append(row)

    out = pd.DataFrame(rows)
    if len(out) and "near_sr" in out.columns:
        out["near_sr"] = out["near_sr"].astype(bool)
    return out


def _mask_for_conds(df: pd.DataFrame, conds: Sequence[Tuple[str, Any]]) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for col, val in conds:
        m &= df[col] == val
    return m


def _label(conds: Sequence[Tuple[str, Any]]) -> str:
    if not conds:
        return "LOW_ALL"
    parts = []
    for col, val in conds:
        if col == "vol_bucket":
            parts.append(f"vol_{val}")
        elif col == "ma_pos":
            parts.append(f"ma_{val}")
        elif col == "span_bucket":
            parts.append(f"span_{val}")
        elif col == "amp_bucket":
            parts.append(f"amp_{val}")
        elif col == "near_sr":
            parts.append("near" if val else "not_near")
        elif col == "conf":
            parts.append(f"conf_{val}")
        else:
            parts.append(f"{col}={val}")
    return "LOW+" + "+".join(parts)


def _path_stats(sub: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n": int(len(sub))}
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
        # 三峰卖出占比
        pk = f"sold_on_peak_{h}"
        if pk in sub.columns and len(sub):
            out[f"peak_exit_pct_{h}"] = round(float(sub[pk].mean()) * 100, 1)
        else:
            out[f"peak_exit_pct_{h}"] = None
        fcol = f"fixed_{h}"
        if fcol in sub.columns:
            fs = summarize_returns(sub[fcol].dropna().tolist(), name=f"fix{h}")
            out[f"fixed{h}_win"] = round(float(fs["winrate"]), 2) if fs["n"] else None
            out[f"fixed{h}_mean"] = round(float(fs["mean"]), 3) if fs["n"] else None
    if wins:
        out["score"] = round(float(np.mean(wins)) + 0.5 * float(np.mean(means)), 3)
        out["avg_win"] = round(float(np.mean(wins)), 2)
        out["avg_mean"] = round(float(np.mean(means)), 3)
    else:
        out["score"] = None
        out["avg_win"] = None
        out["avg_mean"] = None
    # bars_to_high 分布
    if "bars_to_high" in sub.columns:
        b = sub["bars_to_high"].dropna()
        if len(b):
            out["bars_to_high_med"] = round(float(b.median()), 1)
            out["bars_to_high_mean"] = round(float(b.mean()), 1)
            out["has_next_high_pct"] = round(100.0 * len(b) / len(sub), 1)
        else:
            out["bars_to_high_med"] = None
            out["bars_to_high_mean"] = None
            out["has_next_high_pct"] = 0.0
    return out


def _year_stability(sub: pd.DataFrame) -> Dict[str, Any]:
    rows = []
    for y, g in sub.groupby("year"):
        s = summarize_returns(g["path_20"].dropna().tolist())
        if s["n"] >= 20:
            rows.append((int(y), float(s["winrate"]), float(s["mean"]), int(s["n"])))
    if not rows:
        return {"year_ok": False, "years_pos": 0, "n_years": 0, "year_detail": ""}
    pos = sum(1 for _, w, m, _ in rows if w >= 50 or m > 0)
    detail = "; ".join(f"{y}:{w:.0f}%/{m:+.2f}%(n={n})" for y, w, m, n in rows)
    return {"year_ok": pos >= max(2, len(rows) // 2), "years_pos": pos, "n_years": len(rows), "year_detail": detail}


def search_combos(df: pd.DataFrame) -> pd.DataFrame:
    base = _path_stats(df)
    rows: List[Dict[str, Any]] = [{
        "rank": 0, "k": 0, "slice": "LOW_ALL", "families": "",
        **base, **_year_stability(df),
        "lift_avg_win": 0.0, "lift_avg_mean": 0.0,
        "lift_path20_win": 0.0, "lift_path20_mean": 0.0,
    }]
    fam_names = list(FAMILIES.keys())
    for fam in fam_names:
        for cond in FAMILIES[fam]:
            sub = df[_mask_for_conds(df, [cond])]
            if len(sub) < MIN_N:
                continue
            st = _path_stats(sub)
            rows.append({
                "rank": 0, "k": 1, "slice": _label([cond]), "families": fam,
                **st, **_year_stability(sub),
                "lift_avg_win": round(st["avg_win"] - base["avg_win"], 2),
                "lift_avg_mean": round(st["avg_mean"] - base["avg_mean"], 3),
                "lift_path20_win": round(st["path20_win"] - base["path20_win"], 2),
                "lift_path20_mean": round(st["path20_mean"] - base["path20_mean"], 3),
            })
    for k in (2, 3):
        for fam_combo in itertools.combinations(fam_names, k):
            value_lists = [FAMILIES[f] for f in fam_combo]
            for vals in itertools.product(*value_lists):
                conds = list(vals)
                sub = df[_mask_for_conds(df, conds)]
                if len(sub) < MIN_N:
                    continue
                st = _path_stats(sub)
                rows.append({
                    "rank": 0, "k": k, "slice": _label(conds), "families": "+".join(fam_combo),
                    **st, **_year_stability(sub),
                    "lift_avg_win": round(st["avg_win"] - base["avg_win"], 2),
                    "lift_avg_mean": round(st["avg_mean"] - base["avg_mean"], 3),
                    "lift_path20_win": round(st["path20_win"] - base["path20_win"], 2),
                    "lift_path20_mean": round(st["path20_mean"] - base["path20_mean"], 3),
                })
    out = pd.DataFrame(rows)
    out = out.sort_values(["score", "n"], ascending=[False, False]).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    return out


def write_report(combo: pd.DataFrame, base: Dict[str, Any], events_n: int) -> str:
    lines: List[str] = []
    lines.append("# 三谷确认买 → 三峰/到期卖（LOW only）")
    lines.append("")
    lines.append("- **Zoey 口径**：低谷反转不用买在低点；**要在三峰之前卖出**")
    lines.append("- 入场：逢3 **LOW 确认日收盘**买（非窗内最低点）")
    lines.append("- 出场：下一逢3 **HIGH 确认日收盘**卖；若 hold 内无三峰 → **到期卖**")
    lines.append("- 持有窗 PATH：**5 / 10 / 20** 交易日；成本 15bps 往返；**无 HS300**")
    lines.append("- 条件：`hl_error` 个股桶 vol / ma_pos / span / amp / near_SR / conf")
    lines.append("- 样本：800 主板 seed=42；LOW 事件 n=%d（可算 PATH %d）" % (events_n, int(base["n"])))
    lines.append("- score = 三窗胜率均 + 0.5×三窗均值均；组合 n≥%d" % MIN_N)
    lines.append("")
    lines.append("> 研究工具，**不构成投资建议**。不升格 joint/action；非荐股。")
    lines.append("")
    lines.append("## 1. 基线 LOW_ALL（三峰/到期 PATH）")
    lines.append("")
    lines.append("| 窗 | 胜率 | 均值 | 中位 | 三峰卖出占比 | 固定持有胜/均（对照） |")
    lines.append("|----|------|------|------|--------------|----------------------|")
    for h in HOLDS:
        lines.append(
            "| %d | %.1f%% | %+.3f%% | %+.3f%% | %s%% | %.1f%% / %+.3f%% |"
            % (
                h,
                base[f"path{h}_win"], base[f"path{h}_mean"], base[f"path{h}_med"],
                base.get(f"peak_exit_pct_{h}"),
                base.get(f"fixed{h}_win") or float("nan"),
                base.get(f"fixed{h}_mean") or float("nan"),
            )
        )
    lines.append("")
    lines.append(
        "- 到下一三峰：有下一 HIGH 占比 **%s%%**；间隔中位 **%s** 根 / 均 **%s** 根"
        % (base.get("has_next_high_pct"), base.get("bars_to_high_med"), base.get("bars_to_high_mean"))
    )
    lines.append("- score=**%.3f**；三窗均胜 **%.1f%%** / 均 **%+.3f%%**" % (base["score"], base["avg_win"], base["avg_mean"]))
    if base.get("tight_note"):
        lines.append("- " + base["tight_note"])
    lines.append("")
    lines.append("## 2. 条件组合 Top20（按 score）")
    lines.append("")
    lines.append("| rank | slice | k | n | p5胜/均 | p10胜/均 | p20胜/均 | 峰卖%20 | score | Δ胜 | Δ均 | 年+ |")
    lines.append("|------|-------|---|---|---------|----------|----------|--------|-------|-----|-----|-----|")
    for _, r in combo.head(20).iterrows():
        lines.append(
            "| %d | %s | %d | %d | %.1f/%+.2f | %.1f/%+.2f | %.1f/%+.2f | %s | %.2f | %+.1f | %+.2f | %d/%d |"
            % (
                int(r["rank"]), r["slice"], int(r["k"]), int(r["n"]),
                r["path5_win"], r["path5_mean"],
                r["path10_win"], r["path10_mean"],
                r["path20_win"], r["path20_mean"],
                r.get("peak_exit_pct_20"),
                r["score"],
                r["lift_avg_win"] if pd.notna(r["lift_avg_win"]) else 0,
                r["lift_avg_mean"] if pd.notna(r["lift_avg_mean"]) else 0,
                int(r["years_pos"]), int(r["n_years"]),
            )
        )
    lines.append("")
    # 诚实短名单
    honest = combo[
        (combo["slice"] != "LOW_ALL")
        & (combo["path20_mean"] > 0)
        & (combo["avg_win"] > base["avg_win"])
        & (combo["n"] >= MIN_N)
    ].copy()
    lines.append("## 3. 诚实短名单（path20均>0 且 三窗均胜>基线）")
    lines.append("")
    if honest.empty:
        lines.append("**严格条件无命中。** 放宽为 Δ胜>0 且 Δpath20均>0：")
        lines.append("")
        soft = combo[
            (combo["slice"] != "LOW_ALL")
            & (combo["lift_avg_win"] > 0)
            & (combo["lift_path20_mean"] > 0)
            & (combo["n"] >= MIN_N)
        ].head(15)
        if soft.empty:
            lines.append("放宽后仍无。")
        else:
            lines.append("| rank | slice | n | avg胜 | avg均 | p20胜/均 | score | 年明细 |")
            lines.append("|------|-------|---|-------|-------|----------|-------|--------|")
            for _, r in soft.iterrows():
                lines.append(
                    "| %d | %s | %d | %.1f | %+.3f | %.1f/%+.3f | %.2f | %s |"
                    % (
                        int(r["rank"]), r["slice"], int(r["n"]),
                        r["avg_win"], r["avg_mean"],
                        r["path20_win"], r["path20_mean"],
                        r["score"], r["year_detail"],
                    )
                )
            honest = soft
    else:
        lines.append("| rank | slice | n | avg胜 | avg均 | p20胜/均 | score | 年明细 |")
        lines.append("|------|-------|---|-------|-------|----------|-------|--------|")
        for _, r in honest.head(20).iterrows():
            lines.append(
                "| %d | %s | %d | %.1f | %+.3f | %.1f/%+.3f | %.2f | %s |"
                % (
                    int(r["rank"]), r["slice"], int(r["n"]),
                    r["avg_win"], r["avg_mean"],
                    r["path20_win"], r["path20_mean"],
                    r["score"], r["year_detail"],
                )
            )
    lines.append("")
    lines.append("## 4. 单因子（相对 LOW_ALL）")
    lines.append("")
    single = combo[combo["k"] == 1].sort_values("score", ascending=False)
    lines.append("| slice | n | p5胜/均 | p10胜/均 | p20胜/均 | score | Δ胜 | Δ均 |")
    lines.append("|-------|---|---------|----------|----------|-------|-----|-----|")
    for _, r in single.iterrows():
        lines.append(
            "| %s | %d | %.1f/%+.2f | %.1f/%+.2f | %.1f/%+.2f | %.2f | %+.1f | %+.2f |"
            % (
                r["slice"], int(r["n"]),
                r["path5_win"], r["path5_mean"],
                r["path10_win"], r["path10_mean"],
                r["path20_win"], r["path20_mean"],
                r["score"], r["lift_avg_win"], r["lift_avg_mean"],
            )
        )
    lines.append("")
    lines.append("## 5. 与旧 tight PATH 对照（说明）")
    lines.append("")
    lines.append("旧 `path_ret`（tight TP/SL）衡量的是止盈止损路径，**不是** Zoey「三峰前卖」。")
    lines.append("本报告主结论一律用 **三峰/到期** PATH；tight 仅作水分对照，见事件 CSV 列 `tight_path20`。")
    lines.append("")
    lines.append("## 6. 诚实结论")
    lines.append("")
    best = combo.iloc[0]
    lines.append(
        "1. **出场语义已对齐 Zoey**：买在三谷确认日（不追窗内最低），卖在下一三峰确认或到期——"
        "5/10/20d 窗内三峰卖出占比见上表（短窗常大量到期，因到下一三峰中位约 %s 根）。"
        % base.get("bars_to_high_med")
    )
    lines.append(
        "2. **基线**：三窗均胜 %.1f%% / 均 %+.3f%%（score=%.2f）。"
        % (base["avg_win"], base["avg_mean"], base["score"])
    )
    lines.append(
        "3. **组合最优** `%s`（n=%d）：score=%.2f，均胜 %.1f%% / 均 %+.3f%%；Δ胜=%+.1fpt Δ均=%+.2fpt。"
        % (
            best["slice"], int(best["n"]), float(best["score"]),
            float(best["avg_win"]), float(best["avg_mean"]),
            float(best["lift_avg_win"] or 0), float(best["lift_avg_mean"] or 0),
        )
    )
    help_single = single.sort_values("lift_avg_win", ascending=False).head(3)
    if len(help_single):
        bits = ", ".join(
            "%s(Δ胜%+.1f/Δ均%+.2f)" % (r["slice"], r["lift_avg_win"], r["lift_avg_mean"])
            for _, r in help_single.iterrows()
        )
        lines.append("4. **单因子 PATH 倾向**：%s。" % bits)
    if len(honest):
        lines.append(
            "5. **短名单头名** `%s`：仍须看年稳与样本；**不**据此升格交易。"
            % honest.iloc[0]["slice"]
        )
    else:
        lines.append("5. **无**稳定短名单（严格/放宽均空或极弱）。")
    lines.append(
        "6. **总评**：本 PATH 回答的是「三谷后拿一段、三峰前下车」而非「买在最低点」。"
        "若边仍薄，保持观察层；**不升格** joint/action；不荐股；不含大盘闸。"
    )
    lines.append("")
    lines.append("## 7. 输出文件")
    lines.append("")
    for fn in (
        "buy_low_report.md",
        "buy_low_events.csv",
        "combo_rank.csv",
        "combo_top50.csv",
        "single_factor.csv",
        "honest_shortlist.csv",
        "summary.csv",
    ):
        lines.append(f"- `output/feng3/buy_low/{fn}`")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)
    print("预计算：三谷确认买 → 三峰/到期卖 PATH …")
    df = _precompute_peak_exit_path()
    print("events=%d  syms≈%d" % (len(df), df["sym"].nunique() if len(df) else 0))
    if not len(df):
        raise SystemExit("no LOW events")

    # 旧 tight 对照一句
    tight_note = ""
    if "tight_path20" in df.columns:
        ts = summarize_returns(df["tight_path20"].dropna().tolist())
        if ts["n"]:
            tight_note = "旧 tight@20 对照：胜率 %.1f%% / 均 %+.3f%%（非本主指标）" % (
                float(ts["winrate"]), float(ts["mean"])
            )

    print("搜索个股条件组合 …")
    combo = search_combos(df)
    base = combo[combo["slice"] == "LOW_ALL"].iloc[0].to_dict()
    base["tight_note"] = tight_note

    single = combo[combo["k"] == 1].copy()
    honest = combo[
        (combo["slice"] != "LOW_ALL")
        & (combo["path20_mean"] > 0)
        & (combo["avg_win"] > base["avg_win"])
        & (combo["n"] >= MIN_N)
    ].copy()
    if honest.empty:
        honest = combo[
            (combo["slice"] != "LOW_ALL")
            & (combo["lift_avg_win"] > 0)
            & (combo["lift_path20_mean"] > 0)
            & (combo["n"] >= MIN_N)
        ].copy()
        honest.insert(0, "note", "放宽:Δ胜>0且Δpath20均>0")
    else:
        honest.insert(0, "note", "严格:path20均>0且均胜>基线")

    # 落盘事件（主 PATH）
    keep_cols = [
        c for c in [
            "sym", "date", "bar_idx", "center_idx", "pred", "conf",
            "trigger_price", "close", "entry_close",
            "near_sr", "vol_bucket", "ma_pos", "span_bucket", "amp_bucket",
            "structure", "struct_agree", "year",
            "next_high_bar", "next_high_date", "bars_to_high", "next_high_close",
            "path_5", "path_10", "path_20",
            "exit_reason_5", "exit_reason_10", "exit_reason_20",
            "hold_bars_5", "hold_bars_10", "hold_bars_20",
            "sold_on_peak_5", "sold_on_peak_10", "sold_on_peak_20",
            "fixed_5", "fixed_10", "fixed_20", "tight_path20",
        ] if c in df.columns
    ]
    df[keep_cols].to_csv(os.path.join(OUT_DIR, "buy_low_events.csv"), index=False, encoding="utf-8-sig")
    combo.to_csv(os.path.join(OUT_DIR, "combo_rank.csv"), index=False, encoding="utf-8-sig")
    combo.head(50).to_csv(os.path.join(OUT_DIR, "combo_top50.csv"), index=False, encoding="utf-8-sig")
    single.to_csv(os.path.join(OUT_DIR, "single_factor.csv"), index=False, encoding="utf-8-sig")
    honest.to_csv(os.path.join(OUT_DIR, "honest_shortlist.csv"), index=False, encoding="utf-8-sig")

    summary = pd.DataFrame([
        {
            "item": "baseline_peak_exit",
            "slice": "LOW_ALL",
            "n": base["n"],
            "avg_win": base["avg_win"],
            "avg_mean": base["avg_mean"],
            "score": base["score"],
            "path20_win": base["path20_win"],
            "path20_mean": base["path20_mean"],
            "peak_exit_pct_20": base.get("peak_exit_pct_20"),
            "bars_to_high_med": base.get("bars_to_high_med"),
        },
        {
            "item": "best_combo",
            "slice": combo.iloc[0]["slice"],
            "n": int(combo.iloc[0]["n"]),
            "avg_win": combo.iloc[0]["avg_win"],
            "avg_mean": combo.iloc[0]["avg_mean"],
            "score": combo.iloc[0]["score"],
            "path20_win": combo.iloc[0]["path20_win"],
            "path20_mean": combo.iloc[0]["path20_mean"],
            "peak_exit_pct_20": combo.iloc[0].get("peak_exit_pct_20"),
            "bars_to_high_med": combo.iloc[0].get("bars_to_high_med"),
        },
    ])
    if len(honest):
        summary = pd.concat([summary, pd.DataFrame([{
            "item": "honest_top",
            "slice": honest.iloc[0]["slice"],
            "n": int(honest.iloc[0]["n"]),
            "avg_win": honest.iloc[0]["avg_win"],
            "avg_mean": honest.iloc[0]["avg_mean"],
            "score": honest.iloc[0]["score"],
            "path20_win": honest.iloc[0]["path20_win"],
            "path20_mean": honest.iloc[0]["path20_mean"],
            "peak_exit_pct_20": honest.iloc[0].get("peak_exit_pct_20"),
            "bars_to_high_med": honest.iloc[0].get("bars_to_high_med"),
        }])], ignore_index=True)
    summary.to_csv(os.path.join(OUT_DIR, "summary.csv"), index=False, encoding="utf-8-sig")

    md = write_report(combo, base, events_n=len(df))
    with open(os.path.join(OUT_DIR, "buy_low_report.md"), "w", encoding="utf-8") as f:
        f.write(md)

    print("完成 %.1fs → %s" % (time.time() - t0, OUT_DIR))
    print(summary.to_string(index=False))
    print("top5:")
    print(combo.head(5)[["rank", "slice", "n", "avg_win", "avg_mean", "path20_win", "path20_mean", "score"]].to_string(index=False))


if __name__ == "__main__":
    main()
