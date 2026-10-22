# -*- coding: utf-8 -*-
"""回测统计：胜率、盈亏比、Sharpe、最大回撤、成本扣减、超额收益。"""
from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd

# A 股个人交易粗估：佣金双边 + 印花税(卖) + 滑点，合计约单边 0.1%~0.15%
DEFAULT_COST_BPS = 15  # 单边基点；往返 = 2 * cost


def apply_roundtrip_cost(ret_pct: float, cost_bps: float = DEFAULT_COST_BPS) -> float:
    """收益(%) 扣往返成本。"""
    cost_pct = 2 * cost_bps / 100.0  # bps -> %
    return ret_pct - cost_pct


def summarize_returns(rets: Iterable[float], name: str = "") -> dict:
    a = np.asarray([float(x) for x in rets if x == x], dtype=float)
    if len(a) == 0:
        return {
            "name": name, "n": 0, "winrate": np.nan, "mean": np.nan, "median": np.nan,
            "pf": np.nan, "sharpe": np.nan, "mdd": np.nan, "std": np.nan,
        }
    w = a[a > 0]
    l = a[a <= 0]
    pf = float(w.mean() / abs(l.mean())) if len(l) and abs(l.mean()) > 1e-12 else float("nan")
    # 事件型 Sharpe：均值/标准差（非年化，便于比较持仓期）
    std = float(a.std(ddof=1)) if len(a) > 1 else float("nan")
    sharpe = float(a.mean() / std) if std and std > 1e-12 else float("nan")
    # 将事件收益串成净值，估最大回撤
    equity = np.cumprod(1 + a / 100.0)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    mdd = float(dd.min() * 100) if len(dd) else float("nan")
    return {
        "name": name,
        "n": int(len(a)),
        "winrate": float((a > 0).mean() * 100),
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "pf": pf,
        "sharpe": sharpe,
        "mdd": mdd,
        "std": std,
    }


def format_stats(s: dict) -> str:
    if s["n"] == 0:
        return f"{s.get('name', '')}: 无信号".strip()
    return (
        f"{s.get('name', '')}: n={s['n']} 胜率{s['winrate']:.1f}% "
        f"均值{s['mean']:+.2f}% 中位{s['median']:+.2f}% "
        f"盈亏比{s['pf']:.2f} Sharpe{s['sharpe']:.2f} MDD{s['mdd']:.1f}%"
    ).strip()


def excess_vs_benchmark(stock_rets: Iterable[float], bench_rets: Iterable[float]) -> dict:
    s = np.asarray(list(stock_rets), dtype=float)
    b = np.asarray(list(bench_rets), dtype=float)
    n = min(len(s), len(b))
    if n == 0:
        return summarize_returns([], "超额")
    ex = s[:n] - b[:n]
    out = summarize_returns(ex, "超额vs基准")
    return out


def equity_curve_from_event_returns(rets: Iterable[float]) -> pd.Series:
    a = np.asarray([float(x) for x in rets if x == x], dtype=float)
    if len(a) == 0:
        return pd.Series(dtype=float)
    return pd.Series(np.cumprod(1 + a / 100.0))
