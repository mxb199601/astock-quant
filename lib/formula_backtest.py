# -*- coding: utf-8 -*-
"""经典公式横向回测对照：胜率 / 均值 / Sharpe（扣成本 + 可选 asym 出场）。"""
from __future__ import annotations

import os
from typing import List, Optional

import numpy as np
import pandas as pd

from .data import list_cached_symbols, load_kline_csv
from .formulas import FORMULA_REGISTRY, get_signal
from .metrics import apply_roundtrip_cost, format_stats, summarize_returns
from .paths import OUTPUT
from .themes import DEFAULT_BACKTEST


def _fresh(sig: pd.Series) -> pd.Series:
    prev = sig.shift(1)
    prev = prev.where(prev.notna(), False).astype(bool)
    return (sig.fillna(False).astype(bool) & ~prev).astype(bool)


def _sim_returns(
    df: pd.DataFrame,
    fresh: pd.Series,
    period: int,
    exit_mode: str,
    cost_bps: float,
) -> List[float]:
    c = df["close"].values
    rets = []
    idx = np.where(fresh.values)[0]
    for i in idx:
        if i + period >= len(c):
            continue
        entry = c[i]
        ret = (c[i + period] / entry - 1) * 100
        if exit_mode in ("asym", "wr", "tight", "tpsl"):
            high = df["high"].iloc[i : i + period + 1].max()
            low = df["low"].iloc[i : i + period + 1].min()
            if exit_mode == "asym":
                tp_u, sl_u = 1.08, 0.95
                tp_r, sl_r = 8.0, -5.0
            elif exit_mode == "wr":
                tp_u, sl_u = 1.05, 0.97
                tp_r, sl_r = 5.0, -3.0
            elif exit_mode == "tight":
                tp_u, sl_u = 1.04, 0.97
                tp_r, sl_r = 4.0, -3.0
            else:  # tpsl
                tp_u, sl_u = 1.06, 0.94
                tp_r, sl_r = 6.0, -6.0
            if high >= entry * tp_u:
                ret = tp_r
            elif low <= entry * sl_u:
                ret = sl_r
        rets.append(apply_roundtrip_cost(ret, cost_bps))
    return rets


def compare_formulas(
    codes: Optional[List[str]] = None,
    period: int = 20,
    exit_mode: str = "asym",
    cost_bps: float = 15.0,
    formulas: Optional[List[str]] = None,
) -> pd.DataFrame:
    codes = codes or list(dict.fromkeys(DEFAULT_BACKTEST + list_cached_symbols()))
    # 去重、去掉指数
    codes = [c for c in dict.fromkeys(codes) if c not in ("sh000300", "sh000001")]
    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None:
        print("缺少 sh000300，请先 refresh")
        return pd.DataFrame()

    keys = formulas or list(FORMULA_REGISTRY.keys())
    # 也对照我们自己的 strict
    from .factors import formula3_mask

    rows = []
    print("=== 经典量化公式横向回测 ===")
    print(f"股票数≈{len(codes)} | 持仓{period}日 | 出场={exit_mode} | 成本单边{cost_bps}bps")
    print()

    # our baselines
    baselines = {
        "f3_base": lambda d, i: formula3_mask(d, i, "base"),
        "f3_strict": lambda d, i: formula3_mask(d, i, "strict"),
        "f3_elite": lambda d, i: formula3_mask(d, i, "elite"),
        "f3_winrate": lambda d, i: formula3_mask(d, i, "winrate"),
    }

    all_keys = list(baselines.keys()) + keys
    bag = {k: [] for k in all_keys}

    for sym in codes:
        df = load_kline_csv(sym, min_bars=300)
        if df is None:
            continue
        for k, fn in baselines.items():
            sig = fn(df, idx)
            bag[k].extend(_sim_returns(df, _fresh(sig), period, exit_mode, cost_bps))
        for k in keys:
            try:
                sig = get_signal(k, df, idx)
            except Exception:
                continue
            bag[k].extend(_sim_returns(df, _fresh(sig), period, exit_mode, cost_bps))

    print(f"{'公式':16s} {'名称':18s} {'n':>5s} {'胜率':>7s} {'均值':>8s} {'中位':>8s} {'盈亏比':>6s} {'Sharpe':>7s} {'MDD':>8s}")
    print("-" * 100)
    for k in all_keys:
        rets = bag[k]
        s = summarize_returns(rets, k)
        if k in baselines:
            name = {
                "f3_base": "公式三base",
                "f3_strict": "公式三strict",
                "f3_elite": "公式三elite",
                "f3_winrate": "公式三winrate",
            }.get(k, k)
        else:
            name = FORMULA_REGISTRY[k]["name"][:18]
        rows.append({
            "key": k,
            "name": name,
            "n": s["n"],
            "winrate": s["winrate"],
            "mean": s["mean"],
            "median": s["median"],
            "pf": s["pf"],
            "sharpe": s["sharpe"],
            "mdd": s["mdd"],
        })
        if s["n"] == 0:
            print(f"{k:16s} {name:18s} {0:5d}     —        —        —      —       —        —")
            continue
        print(
            f"{k:16s} {name:18s} {s['n']:5d} {s['winrate']:6.1f}% {s['mean']:+7.2f}% "
            f"{s['median']:+7.2f}% {s['pf']:6.2f} {s['sharpe']:7.2f} {s['mdd']:7.1f}%"
        )

    res = pd.DataFrame(rows)
    # 综合分：胜率权重 0.4 + 标准化均值 0.4 + sharpe 0.2（仅 n>=15）
    valid = res[res["n"] >= 15].copy()
    if len(valid):
        valid["score"] = (
            valid["winrate"].fillna(0) / 100 * 0.4
            + (valid["mean"].fillna(0).clip(-10, 20) + 10) / 30 * 0.4
            + valid["sharpe"].fillna(0).clip(-1, 2) / 2 * 0.2
        )
        valid = valid.sort_values("score", ascending=False)
        print("\n=== 综合排名（n≥15，胜率40%+均值40%+Sharpe20%）===")
        for i, r in valid.head(8).iterrows():
            print(
                f"  {r['key']:14s} score={r['score']:.3f}  "
                f"胜率{r['winrate']:.1f}% 均值{r['mean']:+.2f}% Sharpe{r['sharpe']:.2f} n={int(r['n'])}"
            )
        best = valid.iloc[0]
        print(f"\n【本地样本最优】{best['key']}（{best['name']}）")
        print("说明: 小样本易过拟合；不构成投资建议。红灯环境仍应空仓。")

    os.makedirs(OUTPUT, exist_ok=True)
    out = os.path.join(OUTPUT, "formula_compare.csv")
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n明细: {out}")
    return res


def scan_formulas_today(theme_only: bool = True) -> pd.DataFrame:
    """今日哪些票触发了哪些经典公式。"""
    from .formulas import build_cross_sectional_rps, evaluate_today
    from .themes import THEME_POOL

    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None:
        print("缺少指数")
        return pd.DataFrame()
    rps = build_cross_sectional_rps(120)
    if theme_only:
        syms = list(THEME_POOL.keys())
    else:
        syms = list_cached_symbols()

    rows = []
    for sym in syms:
        df = load_kline_csv(sym, min_bars=260)
        if df is None:
            continue
        flags = evaluate_today(sym, df, idx, rps_map=rps)
        n_hit = sum(1 for k, v in flags.items() if v and k != "super_rs")
        if n_hit == 0 and not flags.get("super_rs"):
            continue
        name = THEME_POOL.get(sym, (sym, ""))[0]
        row = {"sym": sym, "name": name, "hits": n_hit, "rps120": round(rps.get(sym, 0), 1)}
        row.update(flags)
        rows.append(row)

    res = pd.DataFrame(rows)
    if res.empty:
        print("今日无经典公式触发（或红灯/缓存过窄）")
        return res
    res = res.sort_values(["hits", "rps120"], ascending=False)
    print("=== 今日经典公式触发 ===")
    print(f"{'代码':10s} {'名称':8s} {'命中':>4s} {'RPS':>6s}  触发公式")
    for _, r in res.iterrows():
        hits = [k for k in FORMULA_REGISTRY if r.get(k)]
        if r.get("super_rs"):
            hits.append("super_rs")
        print(f"{r['sym']:10s} {str(r['name'])[:8]:8s} {int(r['hits']):4d} {r['rps120']:6.1f}  {','.join(hits)}")
    out = os.path.join(OUTPUT, "formula_today.csv")
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n已输出 {out}")
    print("出场建议仍用: 止盈+8% / 止损-5%；不构成投资建议。")
    return res
