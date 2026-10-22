#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逢3必变 · 区间高低价预测误差 + 个股条件提升（研究用，非荐股）。

预测价 = 第3峰/谷 trigger_price；真实价 = [center, center+N] 窗内极值；对侧摆动仅作 found_opp 统计。
条件只看个股：量能、near_SR、峰间距/振幅、股价相对 MA。不含大盘红绿灯。

用法:
  /Users/meng/测试/.venv/bin/python scripts/feng3_hl_error.py --sample 800 --seed 42

输出: output/feng3/hl_error/
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
from lib.feng3 import (
    build_zigzag,
    detect_feng3_events,
    detect_swings,
    evaluate_event_forward,
    evaluate_hl_level_error,
)
from lib.metrics import summarize_returns
from lib.paths import OUTPUT

N_BARS_LIST = (10, 20, 40)
HOLD_M = 20
HIT_N = 10
MAE_TOL = 2.0
COST_BPS = 15.0
EXIT_MODE = "tight"
PRIMARY_N = 20  # 主表 / 条件提升用


def _feat_volume(vol: np.ndarray, conf_i: int) -> Dict[str, Any]:
    """量能：确认日相对近20日均量。"""
    if conf_i < 0 or conf_i >= len(vol):
        return {"vol_ratio": None, "vol_bucket": "未知"}
    v = float(vol[conf_i])
    start = max(0, conf_i - 20)
    base = vol[start:conf_i]
    if len(base) < 5 or float(np.mean(base)) <= 0:
        return {"vol_ratio": None, "vol_bucket": "未知"}
    ratio = v / float(np.mean(base))
    if ratio >= 1.5:
        bucket = "放量"
    elif ratio <= 0.7:
        bucket = "缩量"
    else:
        bucket = "平量"
    return {"vol_ratio": round(ratio, 3), "vol_bucket": bucket}


def _feat_ma(close: np.ndarray, conf_i: int) -> Dict[str, Any]:
    """个股收盘相对 MA20/MA60。"""
    if conf_i < 59 or conf_i >= len(close):
        return {
            "above_ma20": None,
            "above_ma60": None,
            "ma_pos": "未知",
            "px_ma20_pct": None,
            "px_ma60_pct": None,
        }
    c = float(close[conf_i])
    ma20 = float(np.mean(close[conf_i - 19 : conf_i + 1]))
    ma60 = float(np.mean(close[conf_i - 59 : conf_i + 1]))
    a20 = c > ma20
    a60 = c > ma60
    if a20 and a60:
        pos = "双上"
    elif (not a20) and (not a60):
        pos = "双下"
    elif a20 and (not a60):
        pos = "上20下60"
    else:
        pos = "下20上60"
    return {
        "above_ma20": bool(a20),
        "above_ma60": bool(a60),
        "ma_pos": pos,
        "px_ma20_pct": round((c / ma20 - 1.0) * 100, 3) if ma20 > 0 else None,
        "px_ma60_pct": round((c / ma60 - 1.0) * 100, 3) if ma60 > 0 else None,
    }


def _feat_peak_geometry(zz: Sequence, event: Dict[str, Any]) -> Dict[str, Any]:
    """三峰/三谷间距与振幅（用触发前含当前的最近3个同侧摆动）。"""
    pred = str(event["pred"])
    conf_i = int(event["bar_idx"])
    kind = "bot" if pred == "LOW" else "top"
    same = [s for s in zz if s.kind == kind and s.conf_idx <= conf_i]
    if len(same) < 3:
        return {
            "span_bars": None,
            "span_bucket": "未知",
            "amp_pct": None,
            "amp_bucket": "未知",
            "step_mono_pct": None,
        }
    a, b, c = same[-3], same[-2], same[-1]
    span = int(c.center_idx - a.center_idx)
    if span <= 15:
        span_b = "密"
    elif span <= 35:
        span_b = "中"
    else:
        span_b = "疏"
    # 振幅：首末同侧价差相对首价
    if a.price > 0:
        amp = abs(c.price - a.price) / a.price * 100.0
    else:
        amp = None
    if amp is None:
        amp_b = "未知"
    elif amp < 5:
        amp_b = "小幅"
    elif amp < 15:
        amp_b = "中幅"
    else:
        amp_b = "大幅"
    # 逐步延伸幅度（|L1-L3| 已在 amp；逐步均值）
    if a.price > 0 and b.price > 0:
        s1 = abs(b.price - a.price) / a.price * 100.0
        s2 = abs(c.price - b.price) / b.price * 100.0
        step = (s1 + s2) / 2.0
    else:
        step = None
    return {
        "span_bars": span,
        "span_bucket": span_b,
        "amp_pct": None if amp is None else round(amp, 3),
        "amp_bucket": amp_b,
        "step_mono_pct": None if step is None else round(step, 3),
    }


def _scan_one(
    df: pd.DataFrame,
    sym: str,
    swing_k: int,
    near_pct: float,
    n_bars_list: Sequence[int],
) -> List[Dict[str, Any]]:
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    vol = df["vol"].astype(float).values if "vol" in df.columns else np.zeros(len(df))

    swings = detect_swings(high, low, swing_k=swing_k)
    zz = build_zigzag(swings)
    events = detect_feng3_events(df, swing_k=swing_k, near_pct=near_pct)
    rows: List[Dict[str, Any]] = []
    for e in events:
        base = evaluate_event_forward(
            df,
            e,
            hit_n=HIT_N,
            hold_m=HOLD_M,
            mae_tol=MAE_TOL,
            cost_bps=COST_BPS,
            exit_mode=EXIT_MODE,
        )
        base["sym"] = sym
        conf_i = int(e["bar_idx"])
        base.update(_feat_volume(vol, conf_i))
        base.update(_feat_ma(close, conf_i))
        base.update(_feat_peak_geometry(zz, e))
        base["near_sr"] = bool(e.get("near_ok"))

        for nb in n_bars_list:
            hl = evaluate_hl_level_error(df, e, zz, n_bars=nb)
            prefix = f"n{nb}_"
            for k, v in hl.items():
                if k == "n_bars":
                    continue
                base[prefix + k] = v
        rows.append(base)
    return rows


def _agg_hl(df: pd.DataFrame, mask: Optional[pd.Series], name: str, n_bars: int) -> Dict[str, Any]:
    sub = df if mask is None else df.loc[mask]
    p = f"n{n_bars}_"
    n = int(len(sub))
    empty = {
        "slice": name,
        "n_bars": n_bars,
        "n": 0,
        "found_opp_pct": None,
        "pct_err_mean": None,
        "pct_err_median": None,
        "abs_err_mean": None,
        "hit_2pct": None,
        "hit_5pct": None,
        "hit_10pct": None,
        "signed_pct_mean": None,
        "path_n": 0,
        "path_winrate": None,
        "path_mean": None,
        "fixed_up_pct": None,
        "fixed_down_pct": None,
        "fixed_mean": None,
        "hit_extreme_pct": None,
    }
    if n == 0:
        return empty
    pe = sub[p + "pct_err"].dropna()
    ae = sub[p + "abs_err"].dropna()
    se = sub[p + "signed_pct_err"].dropna()
    path = sub["path_ret"].dropna()
    fixed = sub["ret_fixed"].dropna()
    ps = summarize_returns(path.tolist(), name) if len(path) else {"n": 0, "winrate": None, "mean": None}
    wr, mn = ps.get("winrate"), ps.get("mean")
    return {
        "slice": name,
        "n_bars": n_bars,
        "n": n,
        "found_opp_pct": round(float(sub[p + "found_opp"].astype(bool).mean()) * 100, 2),
        "pct_err_mean": round(float(pe.mean()), 3) if len(pe) else None,
        "pct_err_median": round(float(pe.median()), 3) if len(pe) else None,
        "abs_err_mean": round(float(ae.mean()), 4) if len(ae) else None,
        "hit_2pct": round(float(sub[p + "hit_2pct"].astype(bool).mean()) * 100, 2),
        "hit_5pct": round(float(sub[p + "hit_5pct"].astype(bool).mean()) * 100, 2),
        "hit_10pct": round(float(sub[p + "hit_10pct"].astype(bool).mean()) * 100, 2),
        "signed_pct_mean": round(float(se.mean()), 3) if len(se) else None,
        "path_n": int(ps.get("n") or 0),
        "path_winrate": None if wr is None or wr != wr else round(float(wr), 2),
        "path_mean": None if mn is None or mn != mn else round(float(mn), 3),
        "fixed_up_pct": round(float((fixed > 0).mean()) * 100, 2) if len(fixed) else None,
        "fixed_down_pct": round(float((fixed < 0).mean()) * 100, 2) if len(fixed) else None,
        "fixed_mean": round(float(fixed.mean()), 3) if len(fixed) else None,
        "hit_extreme_pct": round(float(sub["hit_extreme"].astype(bool).mean()) * 100, 2),
    }


def _condition_lift(edf: pd.DataFrame, n_bars: int = PRIMARY_N) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """个股条件桶 vs 同侧基线的 hit@5% / pct_err / PATH 提升。不含大盘。"""
    p = f"n{n_bars}_"
    specs: List[Tuple[str, str, pd.Series]] = []

    def add(family: str, label: str, mask: pd.Series) -> None:
        specs.append((family, label, mask))

    for side, smask in (("LOW", edf["pred"] == "LOW"), ("HIGH", edf["pred"] == "HIGH"), ("ALL", pd.Series(True, index=edf.index))):
        base = smask
        add("baseline", f"{side}_ALL", base)
        add("near_SR", f"{side}_near_SR", base & edf["near_sr"])
        add("near_SR", f"{side}_not_near", base & ~edf["near_sr"])
        for vb in ("放量", "缩量", "平量"):
            add("volume", f"{side}_vol_{vb}", base & (edf["vol_bucket"] == vb))
        for mb in ("双下", "双上", "上20下60", "下20上60"):
            add("ma_pos", f"{side}_ma_{mb}", base & (edf["ma_pos"] == mb))
        for sb in ("密", "中", "疏"):
            add("span", f"{side}_span_{sb}", base & (edf["span_bucket"] == sb))
        for ab in ("小幅", "中幅", "大幅"):
            add("amp", f"{side}_amp_{ab}", base & (edf["amp_bucket"] == ab))
        for conf in ("高", "中", "低"):
            add("conf", f"{side}_conf_{conf}", base & (edf["conf"] == conf))

    rows = []
    for family, label, mask in specs:
        sub = edf.loc[mask]
        n = int(len(sub))
        if n < 30:
            rows.append(
                {
                    "family": family,
                    "slice": label,
                    "n": n,
                    "hit_5pct": None,
                    "hit_2pct": None,
                    "hit_10pct": None,
                    "pct_err_mean": None,
                    "path_winrate": None,
                    "path_mean": None,
                    "lift_hit5_vs_side": None,
                    "lift_path_wr_vs_side": None,
                    "lift_pct_err_vs_side": None,
                }
            )
            continue
        pe = sub[p + "pct_err"].dropna()
        path = sub["path_ret"].dropna()
        ps = summarize_returns(path.tolist(), label) if len(path) else {"winrate": None, "mean": None}
        wr = ps.get("winrate")
        mn = ps.get("mean")
        rows.append(
            {
                "family": family,
                "slice": label,
                "n": n,
                "hit_5pct": round(float(sub[p + "hit_5pct"].astype(bool).mean()) * 100, 2),
                "hit_2pct": round(float(sub[p + "hit_2pct"].astype(bool).mean()) * 100, 2),
                "hit_10pct": round(float(sub[p + "hit_10pct"].astype(bool).mean()) * 100, 2),
                "pct_err_mean": round(float(pe.mean()), 3) if len(pe) else None,
                "path_winrate": None if wr is None or wr != wr else round(float(wr), 2),
                "path_mean": None if mn is None or mn != mn else round(float(mn), 3),
                "lift_hit5_vs_side": None,
                "lift_path_wr_vs_side": None,
                "lift_pct_err_vs_side": None,
            }
        )

    cdf = pd.DataFrame(rows)
    # 相对同侧 ALL 基线算 lift
    base_map = {}
    for _, r in cdf.iterrows():
        if r["family"] == "baseline" and str(r["slice"]).endswith("_ALL"):
            side = str(r["slice"]).split("_")[0]
            base_map[side] = r

    def side_of(label: str) -> str:
        return label.split("_")[0]

    lifts = []
    for i, r in cdf.iterrows():
        side = side_of(str(r["slice"]))
        b = base_map.get(side)
        if b is None or r["family"] == "baseline" or r["n"] < 30:
            lifts.append(r)
            continue
        rr = r.copy()
        if r["hit_5pct"] is not None and b["hit_5pct"] is not None:
            rr["lift_hit5_vs_side"] = round(float(r["hit_5pct"]) - float(b["hit_5pct"]), 2)
        if r["path_winrate"] is not None and b["path_winrate"] is not None:
            rr["lift_path_wr_vs_side"] = round(float(r["path_winrate"]) - float(b["path_winrate"]), 2)
        if r["pct_err_mean"] is not None and b["pct_err_mean"] is not None:
            # 误差下降为正提升
            rr["lift_pct_err_vs_side"] = round(float(b["pct_err_mean"]) - float(r["pct_err_mean"]), 3)
        lifts.append(rr)
    cdf = pd.DataFrame(lifts)

    # 排名：综合 hit5 lift + PATH 胜率 lift（LOW 侧 PATH；HIGH 侧用 hit5/误差为主）
    rank_rows = []
    for family in ("volume", "near_SR", "ma_pos", "span", "amp", "conf"):
        fam = cdf[(cdf["family"] == family) & (cdf["n"] >= 30)].copy()
        if len(fam) == 0:
            continue
        # 每 family 取 LOW/HIGH 各自最好的桶
        for side in ("LOW", "HIGH"):
            sub = fam[fam["slice"].astype(str).str.startswith(side + "_")]
            if len(sub) == 0:
                continue
            # 主排序键：hit5 lift；次：pct_err 改善；再 PATH（LOW）
            sub = sub.sort_values(
                by=["lift_hit5_vs_side", "lift_pct_err_vs_side", "lift_path_wr_vs_side"],
                ascending=False,
                na_position="last",
            )
            best = sub.iloc[0]
            score = 0.0
            if best["lift_hit5_vs_side"] == best["lift_hit5_vs_side"]:
                score += float(best["lift_hit5_vs_side"])
            if best["lift_pct_err_vs_side"] == best["lift_pct_err_vs_side"]:
                score += float(best["lift_pct_err_vs_side"])  # 已是误差下降 pt
            if side == "LOW" and best["lift_path_wr_vs_side"] == best["lift_path_wr_vs_side"]:
                score += 0.5 * float(best["lift_path_wr_vs_side"])
            rank_rows.append(
                {
                    "rank": 0,
                    "family": family,
                    "side": side,
                    "best_slice": best["slice"],
                    "n": int(best["n"]),
                    "hit_5pct": best["hit_5pct"],
                    "lift_hit5": best["lift_hit5_vs_side"],
                    "pct_err_mean": best["pct_err_mean"],
                    "lift_pct_err": best["lift_pct_err_vs_side"],
                    "path_winrate": best["path_winrate"],
                    "lift_path_wr": best["lift_path_wr_vs_side"],
                    "path_mean": best["path_mean"],
                    "score": round(score, 3),
                    "helps": bool(score > 0.5),  # 薄门槛
                }
            )
    rdf = pd.DataFrame(rank_rows)
    if len(rdf):
        rdf = rdf.sort_values("score", ascending=False).reset_index(drop=True)
        rdf["rank"] = np.arange(1, len(rdf) + 1)
    return cdf, rdf


def _fmt(v: Any, d: int = 2) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if isinstance(v, float):
        return f"{v:.{d}f}"
    return str(v)


def _write_report(
    out_dir: str,
    sample_n: int,
    seed: int,
    n_ok: int,
    summary: pd.DataFrame,
    cond: pd.DataFrame,
    rank: pd.DataFrame,
    n_events: int,
) -> str:
    lines: List[str] = []
    lines.append("# 逢3必变 · 区间高低价误差与个股条件提升")
    lines.append("")
    lines.append(f"- 样本: **{sample_n}** 主板（有事件 **{n_ok}**）seed={seed}")
    lines.append(f"- 事件数: **{n_events}**；主窗 N={PRIMARY_N}；PATH hold={HOLD_M} tight/{COST_BPS}bps")
    lines.append("- 预测价 = 第3峰/谷 `trigger_price`；真实价 = [center, center+N] 窗内极值；对侧摆动仅作 found_opp 统计")
    lines.append("- **条件不含大盘**（无 HS300 红绿灯）；只评个股量能 / near_SR / 峰间距振幅 / MA 位置 / conf")
    lines.append("")
    lines.append("> 研究工具，**不构成投资建议**。不升格 joint/action；非荐股。")
    lines.append("")
    lines.append("## 1. 指标定义")
    lines.append("")
    lines.append("| 指标 | 定义 |")
    lines.append("|------|------|")
    lines.append("| pred_level | 三峰 H3 / 三谷 L3 价格 |")
    lines.append("| true_level | LOW: 窗内 min(low)；HIGH: 窗内 max(high) |")
    lines.append("| 窗终点 | 固定 center+N（末段不足则 censored）；对侧摆动另计 found_opp |")
    lines.append("| pct_err | \\|true−pred\\| / pred × 100 |")
    lines.append("| hit_2/5/10% | pct_err ≤ 2 / 5 / 10 |")
    lines.append("| signed_pct_err | (true−pred)/pred×100（LOW 为负=未破预测低；HIGH 为正=向上突破预测高） |")
    lines.append("| PATH | 确认日收盘做多，tight 退出（HIGH 仅对照） |")
    lines.append("")
    lines.append("## 2. 主结果（按 N / 侧）")
    lines.append("")
    lines.append("| slice | N | n | 对侧% | pct_err均 | pct_err中 | hit2% | hit5% | hit10% | PATH胜率 | PATH均 | hit极值% |")
    lines.append("|------|---|---|-------|-----------|-----------|-------|-------|--------|----------|--------|----------|")
    for _, r in summary.iterrows():
        lines.append(
            f"| {r['slice']} | {int(r['n_bars'])} | {int(r['n'])} | {_fmt(r['found_opp_pct'])} | "
            f"{_fmt(r['pct_err_mean'], 3)} | {_fmt(r['pct_err_median'], 3)} | "
            f"{_fmt(r['hit_2pct'])} | {_fmt(r['hit_5pct'])} | {_fmt(r['hit_10pct'])} | "
            f"{_fmt(r['path_winrate'])} | {_fmt(r['path_mean'], 3)} | {_fmt(r['hit_extreme_pct'])} |"
        )
    lines.append("")
    lines.append("## 3. 个股条件提升排名（相对同侧 ALL，N=20）")
    lines.append("")
    lines.append("score ≈ Δhit5 + Δ(误差下降) + 0.5·ΔPATH胜率(仅 LOW)。helps 门槛 score>0.5。")
    lines.append("")
    if len(rank) == 0:
        lines.append("（无足够样本的条件桶）")
    else:
        lines.append("| rank | family | side | best_slice | n | hit5% | Δhit5 | pct_err | Δerr↓ | PATH胜率 | ΔPATH | score | helps |")
        lines.append("|------|--------|------|------------|---|-------|-------|---------|-------|----------|-------|-------|-------|")
        for _, r in rank.iterrows():
            lines.append(
                f"| {int(r['rank'])} | {r['family']} | {r['side']} | {r['best_slice']} | {int(r['n'])} | "
                f"{_fmt(r['hit_5pct'])} | {_fmt(r['lift_hit5'])} | {_fmt(r['pct_err_mean'], 3)} | "
                f"{_fmt(r['lift_pct_err'], 3)} | {_fmt(r['path_winrate'])} | {_fmt(r['lift_path_wr'])} | "
                f"{_fmt(r['score'], 3)} | {r['helps']} |"
            )
    lines.append("")
    lines.append("## 4. 条件桶明细（N=20，n≥30）")
    lines.append("")
    show = cond[cond["n"] >= 30].copy()
    lines.append("| family | slice | n | hit2% | hit5% | hit10% | pct_err | PATH胜率 | PATH均 | Δhit5 | Δerr↓ | ΔPATH |")
    lines.append("|--------|-------|---|-------|-------|--------|---------|----------|--------|-------|-------|-------|")
    for _, r in show.iterrows():
        lines.append(
            f"| {r['family']} | {r['slice']} | {int(r['n'])} | {_fmt(r['hit_2pct'])} | {_fmt(r['hit_5pct'])} | "
            f"{_fmt(r['hit_10pct'])} | {_fmt(r['pct_err_mean'], 3)} | {_fmt(r['path_winrate'])} | "
            f"{_fmt(r['path_mean'], 3)} | {_fmt(r['lift_hit5_vs_side'])} | {_fmt(r['lift_pct_err_vs_side'], 3)} | "
            f"{_fmt(r['lift_path_wr_vs_side'])} |"
        )
    lines.append("")
    lines.append("## 5. 诚实结论")
    lines.append("")
    # 自动摘要
    prim = summary[(summary["n_bars"] == PRIMARY_N) & (summary["slice"].isin(["LOW", "HIGH", "ALL"]))]
    for _, r in prim.iterrows():
        lines.append(
            f"- **{r['slice']}** N={PRIMARY_N}: n={int(r['n'])}, hit5%={_fmt(r['hit_5pct'])}, "
            f"pct_err均={_fmt(r['pct_err_mean'], 3)}, PATH={_fmt(r['path_winrate'])}%/{_fmt(r['path_mean'], 3)}%"
        )
    helps = rank[rank["helps"] == True] if len(rank) else rank  # noqa: E712
    if len(helps) == 0:
        lines.append("- 个股附加条件 **无明显稳定提升**（score≤0.5）；不宜当作「已在高低点」的强过滤器。")
    else:
        lines.append("- 相对有正向 lift 的桶（仍可能是薄边/过拟合风险）：")
        for _, r in helps.head(8).iterrows():
            lines.append(
                f"  - `{r['best_slice']}`（{r['family']}）Δhit5={_fmt(r['lift_hit5'])}pt "
                f"Δerr↓={_fmt(r['lift_pct_err'], 3)} score={_fmt(r['score'], 3)}"
            )
    # near_SR 特别点名（历史有害）
    near_rows = cond[cond["slice"].astype(str).str.contains("near_SR") & (cond["n"] >= 30)]
    if len(near_rows):
        lines.append("- **near_SR**：对照 not_near，若 Δhit5/PATH 为负则继续视为有害分层（与此前 accuracy 一致）。")
    lines.append("- PATH 主结论仍弱则 **不升格**；本模块只回答「价位估得准不准 + 哪类个股条件略好些」。")
    lines.append("- **不荐股**；不改 MENGα / joint / action；不含大盘闸。")
    lines.append("")
    lines.append("## 6. 输出文件")
    lines.append("")
    for fn in (
        "hl_error_report.md",
        "condition_lift.md",
        "hl_error_events.csv",
        "hl_error_summary.csv",
        "hl_error_by_n.csv",
        "condition_buckets.csv",
        "condition_rank.csv",
    ):
        lines.append(f"- `output/feng3/hl_error/{fn}`")
    lines.append("")
    md = "\n".join(lines)
    report_path = os.path.join(out_dir, "hl_error_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md)

    # 精简版 condition_lift.md
    cl: List[str] = []
    cl.append("# 个股条件提升（高低点判定）")
    cl.append("")
    cl.append("相对同侧逢3全样本；**不含大盘**。主窗 N=20。")
    cl.append("")
    cl.append("## 排名")
    cl.append("")
    if len(rank) == 0:
        cl.append("无。")
    else:
        cl.append("| rank | family | side | best | n | Δhit5 | Δerr↓ | ΔPATH | score | helps |")
        cl.append("|------|--------|------|------|---|-------|-------|-------|-------|-------|")
        for _, r in rank.iterrows():
            cl.append(
                f"| {int(r['rank'])} | {r['family']} | {r['side']} | {r['best_slice']} | {int(r['n'])} | "
                f"{_fmt(r['lift_hit5'])} | {_fmt(r['lift_pct_err'], 3)} | {_fmt(r['lift_path_wr'])} | "
                f"{_fmt(r['score'], 3)} | {r['helps']} |"
            )
    cl.append("")
    cl.append("## 解读")
    cl.append("")
    cl.append("1. **hit5↑ 且 pct_err↓** 才算「更好标到真实高低点」。")
    cl.append("2. PATH 胜率 lift 只作辅助（HIGH 的 PATH 是做多对照，不解读为空头边）。")
    cl.append("3. 单桶 n 过小或只在一侧有效 → 降权；整体边薄则保持观察层。")
    cl.append("")
    with open(os.path.join(out_dir, "condition_lift.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(cl))
    return report_path


def run(
    sample_n: int = 800,
    seed: int = 42,
    swing_k: int = 3,
    near_pct: float = 2.0,
    min_bars: int = 200,
) -> Dict[str, Any]:
    t0 = time.time()
    out_dir = os.path.join(OUTPUT, "feng3", "hl_error")
    os.makedirs(out_dir, exist_ok=True)

    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))

    print("=== 逢3 区间高低价误差 + 个股条件（无大盘）===")
    print(f"sample={len(pool)} seed={seed} swing_k={swing_k} N={list(N_BARS_LIST)} PATH={HOLD_M}d")

    rows: List[Dict[str, Any]] = []
    n_ok = 0
    for j, sym in enumerate(pool):
        df = load_kline_csv(sym, min_bars=min_bars)
        if df is None or len(df) < min_bars:
            continue
        try:
            evs = _scan_one(df, sym, swing_k, near_pct, N_BARS_LIST)
        except Exception:
            continue
        if evs:
            rows.extend(evs)
            n_ok += 1
        if (j + 1) % 100 == 0:
            print(f"  … {j+1}/{len(pool)} symbols, events={len(rows)}, {time.time()-t0:.1f}s")

    if not rows:
        return {"ok": False, "error": "无事件", "out_dir": out_dir}

    edf = pd.DataFrame(rows)
    events_path = os.path.join(out_dir, "hl_error_events.csv")
    edf.to_csv(events_path, index=False, encoding="utf-8-sig")

    summary_rows = []
    for nb in N_BARS_LIST:
        summary_rows.append(_agg_hl(edf, None, "ALL", nb))
        summary_rows.append(_agg_hl(edf, edf["pred"] == "LOW", "LOW", nb))
        summary_rows.append(_agg_hl(edf, edf["pred"] == "HIGH", "HIGH", nb))
        summary_rows.append(_agg_hl(edf, (edf["pred"] == "LOW") & (edf["conf"] == "高"), "LOW_conf高", nb))
        summary_rows.append(_agg_hl(edf, (edf["pred"] == "HIGH") & (edf["conf"] == "高"), "HIGH_conf高", nb))
    sdf = pd.DataFrame(summary_rows)
    sdf.to_csv(os.path.join(out_dir, "hl_error_summary.csv"), index=False, encoding="utf-8-sig")
    sdf.to_csv(os.path.join(out_dir, "hl_error_by_n.csv"), index=False, encoding="utf-8-sig")

    cond, rank = _condition_lift(edf, PRIMARY_N)
    cond.to_csv(os.path.join(out_dir, "condition_buckets.csv"), index=False, encoding="utf-8-sig")
    rank.to_csv(os.path.join(out_dir, "condition_rank.csv"), index=False, encoding="utf-8-sig")

    report_path = _write_report(out_dir, len(pool), seed, n_ok, sdf, cond, rank, len(edf))
    print(f"done events={len(edf)} symbols_with={n_ok} in {time.time()-t0:.1f}s")
    print(f"report: {report_path}")
    return {
        "ok": True,
        "n_events": len(edf),
        "n_symbols": n_ok,
        "out_dir": out_dir,
        "report": report_path,
        "elapsed": round(time.time() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="逢3区间高低价误差（研究）")
    ap.add_argument("--sample", type=int, default=800)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--swing-k", type=int, default=3)
    ap.add_argument("--near-pct", type=float, default=2.0)
    ap.add_argument("--min-bars", type=int, default=200)
    args = ap.parse_args()
    r = run(
        sample_n=args.sample,
        seed=args.seed,
        swing_k=args.swing_k,
        near_pct=args.near_pct,
        min_bars=args.min_bars,
    )
    if not r.get("ok"):
        print("FAIL", r)
        sys.exit(1)


if __name__ == "__main__":
    main()
