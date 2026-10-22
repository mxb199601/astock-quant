#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub 公开规则接入观察层：rsrs / kdj_cross / oversold_rebound。

失败条件：三套信号不存在、大盘跌破 MA20 仍为真、或被写进 action 买入出口。
"""
from __future__ import annotations

import inspect
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.formulas import (  # noqa: E402
    FORMULA_REGISTRY,
    formula_composite,
    formula_kdj_cross,
    formula_oversold_rebound,
    formula_rsrs,
    get_signal,
)


def _frame(close, high=None, low=None, vol=None) -> pd.DataFrame:
    c = np.asarray(close, dtype=float)
    n = len(c)
    dates = pd.bdate_range("2018-01-02", periods=n).strftime("%Y-%m-%d")
    if high is None:
        high = c * 1.01
    if low is None:
        low = c * 0.99
    if vol is None:
        vol = np.full(n, 1_000_000.0)
    return pd.DataFrame({
        "date": dates,
        "open": c,
        "high": np.asarray(high, dtype=float),
        "low": np.asarray(low, dtype=float),
        "close": c,
        "vol": np.asarray(vol, dtype=float),
    })


def _index(n: int, up: bool) -> pd.DataFrame:
    if up:
        close = 3000 + np.arange(n, dtype=float) * 2.0
    else:
        close = 9000 - np.arange(n, dtype=float) * 4.0
    return _frame(close)


def _rsrs_bars():
    n, m = 18, 600
    low = np.arange(n + m, dtype=float) + 10.0
    high = low * 1.02
    start = len(low) - n
    for i in range(start, len(low)):
        high[i] = high[start - 1] + 3.0 * (low[i] - low[start - 1])
    close = (high + low) / 2.0
    return _frame(close, high=high, low=low)


def _kdj_bars():
    close = [20 - i * 0.3 for i in range(25)] + [13.0]
    high = [x + 0.4 for x in close]
    low = [x - 0.4 for x in close]
    return _frame(close, high=high, low=low)


def _oversold_bars():
    px = 10.0
    close = []
    for _ in range(60):
        px *= 1.012
        close.append(px)
    for _ in range(8):
        px *= 0.996
        close.append(px)
    for _ in range(6):
        px *= 1.002
        close.append(px)
    vol = np.full(len(close), 1_000_000.0)
    vol[-3:] = 5_000_000.0
    return _frame(close, vol=vol)


def main() -> None:
    for key in ("rsrs", "kdj_cross", "oversold_rebound"):
        assert key in FORMULA_REGISTRY, key
        assert "fn" in FORMULA_REGISTRY[key]

    rsrs_df = _rsrs_bars()
    assert bool(formula_rsrs(rsrs_df, _index(len(rsrs_df), True)).iloc[-1])
    assert not bool(formula_rsrs(rsrs_df, _index(len(rsrs_df), False)).iloc[-1])
    short = _frame(np.linspace(10, 12, 40))
    assert not bool(formula_rsrs(short, _index(len(short), True)).iloc[-1])

    kdj_df = _kdj_bars()
    assert bool(formula_kdj_cross(kdj_df, _index(len(kdj_df), True)).iloc[-1])
    assert not bool(formula_kdj_cross(kdj_df, _index(len(kdj_df), False)).iloc[-1])

    ov_df = _oversold_bars()
    assert bool(formula_oversold_rebound(ov_df, _index(len(ov_df), True)).iloc[-1])
    assert not bool(formula_oversold_rebound(ov_df, _index(len(ov_df), False)).iloc[-1])

    sig = get_signal("rsrs", rsrs_df, _index(len(rsrs_df), True))
    assert len(sig) == len(rsrs_df)
    try:
        get_signal("not_a_formula", rsrs_df, _index(len(rsrs_df), True))
        raise SystemExit("未知公式应抛 KeyError")
    except KeyError:
        pass

    src = inspect.getsource(formula_composite)
    for name in ("formula_rsrs", "formula_kdj_cross", "formula_oversold_rebound"):
        assert name not in src, name

    action = open(os.path.join(ROOT, "lib", "action_today.py"), encoding="utf-8").read()
    for key in ("rsrs", "kdj_cross", "oversold_rebound"):
        assert key not in action, key

    print("github formulas ok", len(FORMULA_REGISTRY))


if __name__ == "__main__":
    main()
