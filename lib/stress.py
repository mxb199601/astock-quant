# -*- coding: utf-8 -*-
"""策略压力测试：乐观窗出场 vs 逐日路径出场。

关键发现（主板抽样）:
  原回测用「持仓窗内 max(high)/min(low) + 先判止盈」会系统性高估胜率约 25~30pt。
  逐日路径 + 同日先损更接近可执行实盘。

用法: python cli.py stress
输出: output/stress_test.csv
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .data import list_cached_symbols, load_kline_csv
from .factors import formula3_mask
from .metrics import apply_roundtrip_cost, summarize_returns
from .paths import OUTPUT


def _fresh(sig: pd.Series) -> pd.Series:
    p = sig.shift(1).fillna(False).astype(bool)
    return sig.fillna(False).astype(bool) & ~p


def _levels(em: str):
    return {"asym": (8.0, 5.0), "wr": (5.0, 3.0), "tight": (4.0, 3.0)}.get(em)


def exit_window_optimistic(entry, h, l, c, em: str, cost: float, period: int = 20) -> float:
    """原系统口径：整窗极值，先 TP 后 SL。"""
    fixed = (float(c[period]) / entry - 1) * 100
    if em == "fixed":
        return apply_roundtrip_cost(fixed, cost)
    tp, sl = _levels(em)
    hh, ll = float(np.max(h[: period + 1])), float(np.min(l[: period + 1]))
    if hh >= entry * (1 + tp / 100):
        return apply_roundtrip_cost(tp, cost)
    if ll <= entry * (1 - sl / 100):
        return apply_roundtrip_cost(-sl, cost)
    return apply_roundtrip_cost(fixed, cost)


def exit_path_daily(entry, h, l, c, em: str, cost: float, period: int = 20, same_bar: str = "sl") -> float:
    """逐日路径；同日双触 same_bar=sl|tp。"""
    fixed = (float(c[min(period, len(c) - 1)]) / entry - 1) * 100
    if em == "fixed":
        return apply_roundtrip_cost(fixed, cost)
    tp, sl = _levels(em)
    n = min(period + 1, len(h))
    for j in range(n):
        tp_hit = float(h[j]) >= entry * (1 + tp / 100)
        sl_hit = float(l[j]) <= entry * (1 - sl / 100)
        if tp_hit and sl_hit:
            return apply_roundtrip_cost(-sl if same_bar == "sl" else tp, cost)
        if sl_hit:
            return apply_roundtrip_cost(-sl, cost)
        if tp_hit:
            return apply_roundtrip_cost(tp, cost)
    return apply_roundtrip_cost(fixed, cost)


def run_stress(
    sample_n: int = 900,
    seed: int = 42,
    modes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    modes = modes or ["strict", "elite", "winrate"]
    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    sample = list(rng.choice(pool, size=min(sample_n, len(pool)), replace=False))
    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None:
        return {"ok": False, "error": "缺少沪深300"}
    idx = idx.copy()
    idx["date"] = idx["date"].astype(str)
    idates = list(idx["date"].values)
    ic = idx["close"].astype(float).values
    ipos = {d: i for i, d in enumerate(idates)}

    events: List[dict] = []
    for sym in sample:
        df = load_kline_csv(sym, min_bars=300)
        if df is None or len(df) < 320:
            continue
        dates = df["date"].astype(str).values
        c = df["close"].astype(float).values
        h = df["high"].astype(float).values
        l = df["low"].astype(float).values
        o = df["open"].astype(float).values if "open" in df.columns else c
        for mode in modes:
            f = _fresh(formula3_mask(df, idx, mode))
            for i in np.where(f.values)[0]:
                if i + 21 >= len(c):
                    continue
                d = dates[i]
                j0 = ipos.get(d)
                idx_fwd = np.nan
                if j0 is not None and j0 + 20 < len(ic):
                    idx_fwd = (ic[j0 + 20] / ic[j0] - 1) * 100
                events.append({
                    "mode": mode, "date": d, "year": d[:4],
                    "entry": float(c[i]), "entry_t1": float(o[i + 1]),
                    "h": h[i:i + 21], "l": l[i:i + 21], "c": c[i:i + 21],
                    "h1": h[i + 1:i + 22], "l1": l[i + 1:i + 22], "c1": c[i + 1:i + 22],
                    "idx_fwd": idx_fwd,
                })

    rows = []

    def add(name, mode="elite", em="wr", cost=15.0, how="opt", delay=0,
            same_bar="sl", year=None, idx_bucket=None, period=20):
        rets = []
        for e in events:
            if e["mode"] != mode:
                continue
            if year and e["year"] != year:
                continue
            if idx_bucket is not None:
                x = e["idx_fwd"]
                if x != x:
                    continue
                if idx_bucket == "bear" and not (x < -3):
                    continue
                if idx_bucket == "flat" and not (-3 <= x <= 3):
                    continue
                if idx_bucket == "bull" and not (x > 3):
                    continue
            if delay == 0:
                entry, hh, ll, cc = e["entry"], e["h"], e["l"], e["c"]
            else:
                entry, hh, ll, cc = e["entry_t1"], e["h1"], e["l1"], e["c1"]
                if len(cc) <= period:
                    continue
            if how == "opt":
                r = exit_window_optimistic(entry, hh, ll, cc, em, cost, period)
            else:
                r = exit_path_daily(entry, hh, ll, cc, em, cost, period, same_bar)
            rets.append(r)
        s = summarize_returns(rets, name)
        rows.append({
            "scenario": name,
            "n": s["n"],
            "winrate": None if not s["n"] else round(s["winrate"], 2),
            "mean": None if not s["n"] else round(s["mean"], 3),
            "median": None if not s["n"] else round(s["median"], 3),
            "pf": None if not s["n"] or s["pf"] != s["pf"] else round(s["pf"], 3),
            "sharpe": None if not s["n"] else round(s["sharpe"], 3),
            "mdd": None if not s["n"] else round(s["mdd"], 2),
        })
        return s

    # 核心情景
    for em in ["fixed", "asym", "wr", "tight"]:
        add(f"OPT elite|{em}", em=em, how="opt")
        add(f"PATH elite|{em}|sl", em=em, how="path", same_bar="sl")
    for cost in [15, 30, 50, 80]:
        add(f"OPT elite|wr|cost{cost}", em="wr", how="opt", cost=cost)
        add(f"PATH elite|wr|cost{cost}", em="wr", how="path", cost=cost)
    for em in ["wr", "tight", "asym"]:
        add(f"OPT elite|{em}|T+1", em=em, how="opt", delay=1)
        add(f"PATH elite|{em}|T+1", em=em, how="path", delay=1)
    for b in ["bull", "flat", "bear"]:
        add(f"OPT elite|wr|idx={b}", em="wr", how="opt", idx_bucket=b)
        add(f"PATH elite|wr|idx={b}", em="wr", how="path", idx_bucket=b)
    for y in ["2024", "2025", "2026"]:
        add(f"OPT elite|wr|{y}", em="wr", how="opt", year=y)
        add(f"PATH elite|wr|{y}", em="wr", how="path", year=y)
    for m in modes:
        add(f"OPT {m}|wr", mode=m, em="wr", how="opt")
        add(f"PATH {m}|wr", mode=m, em="wr", how="path")
    add("STRESS PATH wr|T+1|cost50", em="wr", how="path", delay=1, cost=50)
    add("STRESS PATH wr|bear|cost30", em="wr", how="path", cost=30, idx_bucket="bear")

    res = pd.DataFrame(rows)
    os.makedirs(OUTPUT, exist_ok=True)
    path = os.path.join(OUTPUT, "stress_test.csv")
    res.to_csv(path, index=False, encoding="utf-8-sig")

    def _get(name):
        sub = res[res["scenario"] == name]
        return sub.iloc[0].to_dict() if len(sub) else {}

    opt = _get("OPT elite|wr")
    path_s = _get("PATH elite|wr|sl")
    gap = None
    if opt.get("winrate") is not None and path_s.get("winrate") is not None:
        gap = round(opt["winrate"] - path_s["winrate"], 1)

    return {
        "ok": True,
        "n_events_elite": sum(1 for e in events if e["mode"] == "elite"),
        "n_sample": len(sample),
        "file": path,
        "rows": rows,
        "summary": {
            "opt_elite_wr": opt,
            "path_elite_wr": path_s,
            "winrate_gap_pt": gap,
            "note": (
                f"乐观窗 elite×wr 胜率 {opt.get('winrate')}% vs 路径 {path_s.get('winrate')}%"
                f"（水分约 {gap}pt）。路径口径更接近实盘；原 backtest 默认仍为乐观窗。"
            ),
        },
    }
