#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逢3必变 opt_v2：弱化 near_SR conf + 可选 HS300 红灯闸 + 任意摆动×红灯基线。

主叙事对称：
  - 三连摆动高点单调不降 → pred=HIGH → 预期下跌（看 fixed20↓ / PATH 做多对照）
  - 三连摆动低点单调不升 → pred=LOW  → 预期反弹（看 PATH / fixed20↑）

用法:
  /Users/meng/测试/.venv/bin/python scripts/backtest_feng3_opt_v2.py --sample 800 --seed 42

输出: output/feng3/opt_v2/
研究用，非荐股；不升格 joint/action。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib.data import list_cached_symbols, load_kline_csv
from lib.feng3 import (
    build_zigzag,
    detect_swings,
    evaluate_event_forward,
    scan_symbol,
)
from lib.metrics import summarize_returns
from lib.paths import OUTPUT

HIT_N = 10
HOLD_M = 20
MAE_TOL = 2.0
COST_BPS = 15.0
EXIT_MODE = "tight"


def _build_index_map(idx: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    d = idx.copy()
    d["date"] = d["date"].astype(str)
    c = d["close"].astype(float)
    d["ma20"] = c.rolling(20).mean()
    d["ma60"] = c.rolling(60).mean()
    out: Dict[str, Dict[str, Any]] = {}
    for _, row in d.iterrows():
        date = str(row["date"])
        px = float(row["close"])
        ma20, ma60 = row["ma20"], row["ma60"]
        if pd.isna(ma60) or pd.isna(ma20):
            out[date] = {"light": "未知", "bull_bear": "未知"}
            continue
        a20 = px > float(ma20)
        a60 = px > float(ma60)
        if a20 and a60:
            light = "绿灯"
        elif a20 or a60:
            light = "黄灯"
        else:
            light = "红灯"
        out[date] = {"light": light, "bull_bear": "牛" if a60 else "熊"}
    return out


def _agg(df: pd.DataFrame, mask: Optional[pd.Series], name: str) -> Dict[str, Any]:
    sub = df if mask is None else df.loc[mask]
    n = int(len(sub))
    empty = {
        "slice": name,
        "n": 0,
        "hit10_pct": None,
        "mae_mean": None,
        "path_n": 0,
        "path_winrate": None,
        "path_mean": None,
        "fixed_up_pct": None,
        "fixed_down_pct": None,
        "fixed_mean": None,
    }
    if n == 0:
        return empty
    hit = sub["hit_extreme"].astype(bool)
    path = sub["path_ret"].dropna()
    fixed = sub["ret_fixed"].dropna()
    ps = summarize_returns(path.tolist(), name) if len(path) else {"n": 0, "winrate": None, "mean": None}
    wr, mn = ps.get("winrate"), ps.get("mean")
    return {
        "slice": name,
        "n": n,
        "hit10_pct": round(float(hit.mean()) * 100, 2),
        "mae_mean": round(float(sub["mae"].dropna().mean()), 3) if sub["mae"].notna().any() else None,
        "path_n": int(ps.get("n") or 0),
        "path_winrate": None if wr is None or wr != wr else round(float(wr), 2),
        "path_mean": None if mn is None or mn != mn else round(float(mn), 3),
        "fixed_up_pct": round(float((fixed > 0).mean()) * 100, 2) if len(fixed) else None,
        "fixed_down_pct": round(float((fixed < 0).mean()) * 100, 2) if len(fixed) else None,
        "fixed_mean": round(float(fixed.mean()), 3) if len(fixed) else None,
    }


def _attach_light(edf: pd.DataFrame, imap: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    out = edf.copy()
    lights, bbs = [], []
    for d in out["date"].astype(str):
        info = imap.get(d, {"light": "未知", "bull_bear": "未知"})
        lights.append(info["light"])
        bbs.append(info["bull_bear"])
    out["light"] = lights
    out["bull_bear"] = bbs
    return out


def _scan_pool(
    pool: List[str],
    swing_k: int,
    near_pct: float,
    min_bars: int,
) -> Tuple[pd.DataFrame, int]:
    rows: List[Dict[str, Any]] = []
    n_ok = 0
    t0 = time.time()
    for j, sym in enumerate(pool):
        df = load_kline_csv(sym, min_bars=min_bars)
        if df is None or len(df) < min_bars:
            continue
        try:
            evs = scan_symbol(
                df,
                sym,
                swing_k=swing_k,
                near_pct=near_pct,
                hit_n=HIT_N,
                hold_m=HOLD_M,
                mae_tol=MAE_TOL,
                cost_bps=COST_BPS,
                exit_mode=EXIT_MODE,
            )
        except Exception:
            continue
        if evs:
            rows.extend(evs)
            n_ok += 1
        if (j + 1) % 200 == 0:
            print(f"  … feng3 {j+1}/{len(pool)} symbols, events={len(rows)}, {time.time()-t0:.1f}s")
    return pd.DataFrame(rows), n_ok


def _any_swing_baseline(
    pool: List[str],
    imap: Dict[str, Dict[str, Any]],
    swing_k: int,
    min_bars: int,
) -> pd.DataFrame:
    """任意摆动确认点作基线（非逢3）；含 light 以便 ×红灯。"""
    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    for j, sym in enumerate(pool):
        df = load_kline_csv(sym, min_bars=min_bars)
        if df is None or len(df) < min_bars:
            continue
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        dates = df["date"].astype(str).values
        swings = detect_swings(high, low, swing_k=swing_k)
        zz = build_zigzag(swings)
        for s in zz:
            pred = "LOW" if s.kind == "bot" else "HIGH"
            ev = {
                "date": str(dates[s.conf_idx]),
                "bar_idx": int(s.conf_idx),
                "center_idx": int(s.center_idx),
                "pred": pred,
                "conf": "基线",
                "trigger_price": float(s.price),
                "close": float(df["close"].iloc[s.conf_idx]),
                "near_ok": False,
                "structure": "baseline",
                "struct_agree": False,
                "swing_k": swing_k,
            }
            r = evaluate_event_forward(
                df,
                ev,
                hit_n=HIT_N,
                hold_m=HOLD_M,
                mae_tol=MAE_TOL,
                cost_bps=COST_BPS,
                exit_mode=EXIT_MODE,
            )
            r["sym"] = sym
            rows.append(r)
        if (j + 1) % 200 == 0:
            print(f"  … baseline {j+1}/{len(pool)} symbols, n={len(rows)}, {time.time()-t0:.1f}s")
    bdf = pd.DataFrame(rows)
    if len(bdf) == 0:
        return bdf
    return _attach_light(bdf, imap)


def _fmt(v: Any, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _load_v1_summary() -> Dict[str, Dict[str, Any]]:
    path = os.path.join(OUTPUT, "feng3", "feng3_summary.csv")
    if not os.path.isfile(path):
        return {}
    sdf = pd.read_csv(path)
    out = {}
    for _, r in sdf.iterrows():
        out[str(r["slice"])] = r.to_dict()
    return out


def _load_v1_baseline() -> Dict[str, Dict[str, Any]]:
    path = os.path.join(OUTPUT, "feng3", "feng3_baseline.csv")
    if not os.path.isfile(path):
        return {}
    sdf = pd.read_csv(path)
    out = {}
    for _, r in sdf.iterrows():
        out[str(r["slice"])] = r.to_dict()
    return out


def run(
    sample_n: int = 800,
    seed: int = 42,
    swing_k: int = 3,
    near_pct: float = 2.0,
    min_bars: int = 200,
) -> Dict[str, Any]:
    t0 = time.time()
    out_dir = os.path.join(OUTPUT, "feng3", "opt_v2")
    os.makedirs(out_dir, exist_ok=True)

    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))

    idx = load_kline_csv("sh000300", min_bars=100)
    idx_name = "沪深300"
    if idx is None:
        idx = load_kline_csv("sh000001", min_bars=100)
        idx_name = "上证综指"
    imap = _build_index_map(idx)

    print("=== 逢3必变 opt_v2（conf 去 near_SR 加分）===")
    print(f"sample={len(pool)} seed={seed} swing_k={swing_k} near_pct={near_pct} idx={idx_name}")
    print("主叙事对称: 三连高→HIGH/看跌；三连低→LOW/看涨。研究用，非荐股。")

    edf, n_ok = _scan_pool(pool, swing_k, near_pct, min_bars)
    if len(edf) == 0:
        return {"ok": False, "error": "无事件"}
    edf = _attach_light(edf, imap)
    events_path = os.path.join(out_dir, "feng3_events_v2.csv")
    edf.to_csv(events_path, index=False, encoding="utf-8-sig")

    print("=== 任意摆动基线（含 ×红灯）===")
    bdf = _any_swing_baseline(pool, imap, swing_k, min_bars)
    baseline_events_path = os.path.join(out_dir, "any_swing_events.csv")
    bdf.to_csv(baseline_events_path, index=False, encoding="utf-8-sig")

    # --- slices ---
    is_low = edf["pred"] == "LOW"
    is_high = edf["pred"] == "HIGH"
    red = edf["light"] == "红灯"
    green = edf["light"] == "绿灯"

    slices: List[Dict[str, Any]] = []
    # control (no gate) — v2 conf
    for name, mask in [
        ("v2_ALL", None),
        ("v2_LOW", is_low),
        ("v2_HIGH", is_high),
        ("v2_LOW_conf高", is_low & (edf["conf"] == "高")),
        ("v2_HIGH_conf高", is_high & (edf["conf"] == "高")),
        ("v2_LOW_conf中", is_low & (edf["conf"] == "中")),
        ("v2_HIGH_conf中", is_high & (edf["conf"] == "中")),
        ("v2_LOW_conf低", is_low & (edf["conf"] == "低")),
        ("v2_HIGH_conf低", is_high & (edf["conf"] == "低")),
        ("v2_LOW_near", is_low & (edf["near_ok"] == True)),
        ("v2_LOW_not_near", is_low & (edf["near_ok"] == False)),
        ("v2_HIGH_near", is_high & (edf["near_ok"] == True)),
        ("v2_HIGH_not_near", is_high & (edf["near_ok"] == False)),
        # red-light gate (treatment)
        ("v2_LOW_红灯", is_low & red),
        ("v2_HIGH_红灯", is_high & red),
        ("v2_LOW_绿灯", is_low & green),
        ("v2_HIGH_绿灯", is_high & green),
        ("v2_LOW_红灯_conf高", is_low & red & (edf["conf"] == "高")),
        ("v2_HIGH_红灯_conf高", is_high & red & (edf["conf"] == "高")),
    ]:
        slices.append(_agg(edf, mask, name))

    # baselines
    if len(bdf):
        bl = bdf["pred"] == "LOW"
        bh = bdf["pred"] == "HIGH"
        br = bdf["light"] == "红灯"
        for name, mask in [
            ("base_any_LOW", bl),
            ("base_any_HIGH", bh),
            ("base_any_LOW_红灯", bl & br),
            ("base_any_HIGH_红灯", bh & br),
        ]:
            slices.append(_agg(bdf, mask, name))

    sdf = pd.DataFrame(slices)
    summary_path = os.path.join(out_dir, "opt_v2_summary.csv")
    sdf.to_csv(summary_path, index=False, encoding="utf-8-sig")

    by_gate = sdf[sdf["slice"].str.contains(r"v2_(?:LOW|HIGH)(?:_红灯|_绿灯)?$|base_any", regex=True)].copy()
    by_gate.to_csv(os.path.join(out_dir, "opt_v2_by_gate.csv"), index=False, encoding="utf-8-sig")

    g = {r["slice"]: r for r in slices}
    v1 = _load_v1_summary()
    v1b = _load_v1_baseline()

    def row(name: str) -> Dict[str, Any]:
        return g.get(name, {"n": 0})

    # --- Chinese report (HIGH + LOW symmetric; HIGH narrative first as Zoey asked 三高峰→跌) ---
    lines = [
        "# 逢3必变 opt_v2 对照报告",
        "",
        f"- 样本: **{len(pool)}** 主板（有事件 **{n_ok}**）seed={seed}",
        f"- 指数环境: **{idx_name}** 红绿灯（价相对 MA20/MA60）",
        f"- PATH: exit={EXIT_MODE} cost={COST_BPS}bps；hit@10 mae_tol={MAE_TOL}%；hold=20",
        f"- 耗时: {time.time()-t0:.1f}s",
        "",
        "> 研究工具，**不构成投资建议**。不升格 joint/action；非荐股。",
        "",
        "## 0. 编码 A 主叙事（对称）",
        "",
        "| 侧 | 规则 | 预期 |",
        "|----|------|------|",
        "| **HIGH** | 交替 zigzag 上连续 3 个摆动**高点**单调不降（H3≥H2≥H1） | **开始下跌**（主看 fixed20↓；PATH 为做多对照） |",
        "| **LOW** | 连续 3 个摆动**低点**单调不升（L3≤L2≤L1） | **开始上涨/反弹**（主看 PATH / fixed20↑） |",
        "",
        "## 1. v2 相对 v1 改动",
        "",
        "1. **弱化 near_SR → conf高**：`conf高 = struct_agree`（不再要求 near∧struct）；"
        "`near` 单独仍可标中；两侧对称。near_ok 字段仍保留供切片。",
        "2. **可选环境闸**：LOW（及对称 HIGH）仅在 HS300 **红灯**（价≤MA20 且 ≤MA60）计数；"
        "同时保留 **无闸对照**。",
        "3. **任意摆动×红灯基线**：检验红灯边是逢3增量还是弱势共性。",
        "",
        "## 2. HIGH 侧（三连高 → 看跌）— 主表",
        "",
        "| 集合 | n | hit@10% | PATH胜率% | PATH均值% | 固定下跌% | 固定均值% |",
        "|------|---|---------|-----------|-----------|-----------|-----------|",
    ]

    def add_row(label: str, r: Dict[str, Any]):
        lines.append(
            f"| {label} | {r.get('n', 0)} | {_fmt(r.get('hit10_pct') or r.get('hit_extreme_pct'))} | "
            f"{_fmt(r.get('path_winrate'))} | {_fmt(r.get('path_mean'), 3)} | "
            f"{_fmt(r.get('fixed_down_pct'))} | {_fmt(r.get('fixed_mean'), 3)} |"
        )

    # v1 HIGH from summary (hit_extreme_pct naming)
    if "HIGH" in v1:
        add_row("v1 HIGH（无闸）", {
            "n": v1["HIGH"].get("n"),
            "hit10_pct": v1["HIGH"].get("hit_extreme_pct"),
            "path_winrate": v1["HIGH"].get("path_winrate"),
            "path_mean": v1["HIGH"].get("path_mean"),
            "fixed_down_pct": v1["HIGH"].get("fixed_down_pct"),
            "fixed_mean": v1["HIGH"].get("fixed_mean"),
        })
    add_row("v2 HIGH（无闸·对照）", row("v2_HIGH"))
    add_row("v2 HIGH×红灯", row("v2_HIGH_红灯"))
    add_row("v2 HIGH×绿灯", row("v2_HIGH_绿灯"))
    add_row("v2 HIGH conf高", row("v2_HIGH_conf高"))
    if "baseline_any_swing_HIGH" in v1b:
        add_row("v1 任意摆动高", {
            "n": v1b["baseline_any_swing_HIGH"].get("n"),
            "hit10_pct": v1b["baseline_any_swing_HIGH"].get("hit_extreme_pct"),
            "fixed_down_pct": v1b["baseline_any_swing_HIGH"].get("fixed_down_pct"),
        })
    add_row("任意摆动高（本轮）", row("base_any_HIGH"))
    add_row("任意摆动高×红灯", row("base_any_HIGH_红灯"))

    lines.extend([
        "",
        "**怎么读 HIGH**：看跌边看 **固定持有下跌%** 是否显著高于「任意摆动高」基线；"
        "PATH 均值负得更多只说明做多亏，不是可空证据。A 股难融券，HIGH 仅观察。",
        "",
        "## 3. LOW 侧（三连低 → 看涨）— 对称表",
        "",
        "| 集合 | n | hit@10% | PATH胜率% | PATH均值% | 固定上涨% | 固定均值% |",
        "|------|---|---------|-----------|-----------|-----------|-----------|",
    ])

    def add_row_low(label: str, r: Dict[str, Any]):
        lines.append(
            f"| {label} | {r.get('n', 0)} | {_fmt(r.get('hit10_pct') or r.get('hit_extreme_pct'))} | "
            f"{_fmt(r.get('path_winrate'))} | {_fmt(r.get('path_mean'), 3)} | "
            f"{_fmt(r.get('fixed_up_pct'))} | {_fmt(r.get('fixed_mean'), 3)} |"
        )

    if "LOW" in v1:
        add_row_low("v1 LOW（无闸）", {
            "n": v1["LOW"].get("n"),
            "hit10_pct": v1["LOW"].get("hit_extreme_pct"),
            "path_winrate": v1["LOW"].get("path_winrate"),
            "path_mean": v1["LOW"].get("path_mean"),
            "fixed_up_pct": v1["LOW"].get("fixed_up_pct"),
            "fixed_mean": v1["LOW"].get("fixed_mean"),
        })
    add_row_low("v2 LOW（无闸·对照）", row("v2_LOW"))
    add_row_low("v2 LOW×红灯（闸）", row("v2_LOW_红灯"))
    add_row_low("v2 LOW×绿灯", row("v2_LOW_绿灯"))
    add_row_low("v2 LOW conf高", row("v2_LOW_conf高"))
    if "baseline_any_swing_LOW" in v1b:
        add_row_low("v1 任意摆动低", {
            "n": v1b["baseline_any_swing_LOW"].get("n"),
            "hit10_pct": v1b["baseline_any_swing_LOW"].get("hit_extreme_pct"),
            "path_winrate": v1b["baseline_any_swing_LOW"].get("path_winrate"),
            "path_mean": v1b["baseline_any_swing_LOW"].get("path_mean"),
        })
    add_row_low("任意摆动低（本轮）", row("base_any_LOW"))
    add_row_low("任意摆动低×红灯", row("base_any_LOW_红灯"))

    # honest deltas
    h = row("v2_HIGH")
    h_red = row("v2_HIGH_红灯")
    h_base = row("base_any_HIGH")
    h_base_red = row("base_any_HIGH_红灯")
    lo = row("v2_LOW")
    lo_red = row("v2_LOW_红灯")
    lo_base = row("base_any_LOW")
    lo_base_red = row("base_any_LOW_红灯")
    lo_c_hi = row("v2_LOW_conf高")
    h_c_hi = row("v2_HIGH_conf高")

    def delta(a, b, key):
        if a.get(key) is None or b.get(key) is None:
            return None
        return round(float(a[key]) - float(b[key]), 3)

    lines.extend([
        "",
        "## 4. 增量是否真实（诚实）",
        "",
        "### HIGH vs 任意摆动高",
        "",
        f"- v2 HIGH fixed20↓={_fmt(h.get('fixed_down_pct'))}%（n={h.get('n')}）vs "
        f"任意高 {_fmt(h_base.get('fixed_down_pct'))}%（n={h_base.get('n')}）；"
        f"Δ={_fmt(delta(h, h_base, 'fixed_down_pct'))}pt。",
        f"- HIGH×红灯 fixed20↓={_fmt(h_red.get('fixed_down_pct'))}% vs "
        f"任意高×红灯 {_fmt(h_base_red.get('fixed_down_pct'))}%；"
        f"Δ={_fmt(delta(h_red, h_base_red, 'fixed_down_pct'))}pt。",
        f"- PATH（做多对照）HIGH 均值={_fmt(h.get('path_mean'),3)}% / 胜率={_fmt(h.get('path_winrate'))}%；"
        f"红灯下 {_fmt(h_red.get('path_mean'),3)}% / {_fmt(h_red.get('path_winrate'))}%。",
        "",
        "### LOW vs 任意摆动低",
        "",
        f"- v2 LOW PATH20 均值={_fmt(lo.get('path_mean'),3)}% 胜率={_fmt(lo.get('path_winrate'))}% "
        f"vs 任意低 {_fmt(lo_base.get('path_mean'),3)}% / {_fmt(lo_base.get('path_winrate'))}%；"
        f"Δ均值={_fmt(delta(lo, lo_base, 'path_mean'),3)}pt。",
        f"- LOW×红灯 PATH20={_fmt(lo_red.get('path_mean'),3)}% / {_fmt(lo_red.get('path_winrate'))}% "
        f"（n={lo_red.get('n')}）vs 任意低×红灯 {_fmt(lo_base_red.get('path_mean'),3)}% / "
        f"{_fmt(lo_base_red.get('path_winrate'))}%；"
        f"Δ均值={_fmt(delta(lo_red, lo_base_red, 'path_mean'),3)}pt，"
        f"Δ胜率={_fmt(delta(lo_red, lo_base_red, 'path_winrate'))}pt。",
        "",
        "### conf高（去 near 后）",
        "",
        f"- LOW conf高 n={lo_c_hi.get('n')} PATH均值={_fmt(lo_c_hi.get('path_mean'),3)}% "
        f"胜率={_fmt(lo_c_hi.get('path_winrate'))}% hit@10={_fmt(lo_c_hi.get('hit10_pct'))}%",
        f"- HIGH conf高 n={h_c_hi.get('n')} fixed↓={_fmt(h_c_hi.get('fixed_down_pct'))}% "
        f"PATH均值={_fmt(h_c_hi.get('path_mean'),3)}% hit@10={_fmt(h_c_hi.get('hit10_pct'))}%",
        "",
        "## 5. 总评（自动）",
        "",
    ])

    # auto verdict — honest
    verdict_bits = []
    d_high = delta(h, h_base, "fixed_down_pct")
    d_low_red = delta(lo_red, lo_base_red, "path_mean")
    d_low_red_wr = delta(lo_red, lo_base_red, "path_winrate")

    if d_high is not None and d_high >= 2.0 and (h.get("n") or 0) >= 100:
        verdict_bits.append(
            f"HIGH（三高峰→跌）相对任意高 fixed↓ 约 +{d_high:.1f}pt，有薄观察边；仍非可交易空头。"
        )
    elif d_high is not None and abs(d_high) < 2.0:
        verdict_bits.append(
            f"HIGH 相对任意高 fixed↓ Δ≈{d_high:.1f}pt，**无稳定可交易下跌边**。"
        )
    else:
        verdict_bits.append("HIGH 相对基线证据不足或偏弱。")

    if d_low_red is not None and (d_low_red_wr or 0) >= 2.0:
        verdict_bits.append(
            f"LOW×红灯相对「任意低×红灯」PATH 均值 Δ≈{d_low_red:.3f}pt、胜率 Δ≈{d_low_red_wr:.1f}pt → "
            f"红灯过滤有一点真实增量（不全是共性），但绝对 PATH 仍弱。"
        )
    elif d_low_red is not None:
        verdict_bits.append(
            f"LOW×红灯相对任意低×红灯 Δ均值≈{d_low_red:.3f}pt、Δ胜率≈{_fmt(d_low_red_wr)}pt → "
            f"增量不明显，红灯改善可能偏弱势共性。"
        )

    if (lo.get("path_mean") or 0) <= 0.1 and (lo.get("path_winrate") or 0) < 48:
        verdict_bits.append(
            "无闸 LOW PATH 仍近零/略负；v2 conf 去掉有害 near 分层，**不创造独立 alpha**。"
        )

    verdict_bits.append("两侧均保持研究/观察层；不升格 joint/action；不荐股。")
    lines.append("- " + "\n- ".join(verdict_bits))

    lines.extend([
        "",
        "## 6. 输出文件",
        "",
        f"- `{events_path}`",
        f"- `{summary_path}`",
        f"- `{baseline_events_path}`",
        f"- `{os.path.join(out_dir, 'opt_v2_by_gate.csv')}`",
        f"- `{os.path.join(out_dir, 'opt_v2_report.md')}`",
        "",
    ])

    md = "\n".join(lines)
    md_path = os.path.join(out_dir, "opt_v2_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    # compact compare csv for parent
    compare_rows = []
    for label, src in [
        ("v1_HIGH", v1.get("HIGH")),
        ("v2_HIGH", h),
        ("v2_HIGH_红灯", h_red),
        ("base_any_HIGH", h_base),
        ("base_any_HIGH_红灯", h_base_red),
        ("v1_LOW", v1.get("LOW")),
        ("v2_LOW", lo),
        ("v2_LOW_红灯", lo_red),
        ("base_any_LOW", lo_base),
        ("base_any_LOW_红灯", lo_base_red),
        ("v2_HIGH_conf高", h_c_hi),
        ("v2_LOW_conf高", lo_c_hi),
    ]:
        if not src:
            continue
        compare_rows.append({
            "slice": label,
            "n": src.get("n"),
            "hit10_pct": src.get("hit10_pct", src.get("hit_extreme_pct")),
            "path_winrate": src.get("path_winrate"),
            "path_mean": src.get("path_mean"),
            "fixed_up_pct": src.get("fixed_up_pct"),
            "fixed_down_pct": src.get("fixed_down_pct"),
            "fixed_mean": src.get("fixed_mean"),
        })
    cdf = pd.DataFrame(compare_rows)
    compare_path = os.path.join(out_dir, "opt_v2_compare.csv")
    cdf.to_csv(compare_path, index=False, encoding="utf-8-sig")

    print(md)
    return {
        "ok": True,
        "n_symbols": len(pool),
        "n_with_events": n_ok,
        "n_events": len(edf),
        "n_baseline": len(bdf),
        "md_path": md_path,
        "summary_path": summary_path,
        "events_path": events_path,
        "compare_path": compare_path,
        "elapsed_s": round(time.time() - t0, 1),
        "slices": {r["slice"]: r for r in slices},
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="逢3必变 opt_v2（非荐股）")
    p.add_argument("--sample", type=int, default=800)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--swing-k", type=int, default=3)
    p.add_argument("--near-pct", type=float, default=2.0)
    p.add_argument("--min-bars", type=int, default=200)
    args = p.parse_args(argv)
    r = run(
        sample_n=args.sample,
        seed=args.seed,
        swing_k=args.swing_k,
        near_pct=args.near_pct,
        min_bars=args.min_bars,
    )
    if not r.get("ok"):
        print("失败:", r.get("error"))
        return 1
    print(f"\n→ {r.get('md_path')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
