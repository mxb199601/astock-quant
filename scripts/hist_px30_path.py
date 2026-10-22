#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PATH tight：robust 入场按入场收盘 <30 vs ≥30。研究用，非荐股。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib.data import list_cached_symbols, load_kline_csv, market_regime
from lib.factors import formula3_mask
from lib.metrics import summarize_returns
from lib.paths import OUTPUT
from lib.stress import exit_path_daily, exit_window_optimistic


def _fresh(sig: pd.Series) -> pd.Series:
    p = sig.shift(1)
    p = p.where(p.notna(), False).astype(bool)
    return sig.fillna(False).astype(bool) & ~p


def _regime_green(idx: pd.DataFrame, d: str) -> bool:
    sub = idx[idx["date"].astype(str) <= str(d)]
    if len(sub) < 60:
        return False
    c = sub["close"].astype(float)
    return bool(c.iloc[-1] >= c.tail(20).mean() and c.iloc[-1] >= c.tail(60).mean())


def collect(sample_n: int = 400, seed: int = 42, period: int = 20, cost: float = 15.0):
    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    sample = list(rng.choice(pool, size=min(sample_n, len(pool)), replace=False))
    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None:
        raise SystemExit("missing sh000300")
    idx = idx.copy()
    idx["date"] = idx["date"].astype(str)
    rows = []
    for sym in sample:
        df = load_kline_csv(sym, min_bars=320)
        if df is None:
            continue
        try:
            f = _fresh(formula3_mask(df, idx, mode="robust", require_low_vol=True))
        except Exception:
            continue
        dates = df["date"].astype(str).values
        c = df["close"].astype(float).values
        h = df["high"].astype(float).values
        l = df["low"].astype(float).values
        for i in np.where(f.values)[0]:
            if i + period >= len(c):
                continue
            entry = float(c[i])
            d = str(dates[i])
            path = exit_path_daily(entry, h[i : i + period + 1], l[i : i + period + 1], c[i : i + period + 1], "tight", cost, period, "sl")
            opt = exit_window_optimistic(entry, h[i : i + period + 1], l[i : i + period + 1], c[i : i + period + 1], "tight", cost, period)
            rows.append(
                {
                    "sym": sym,
                    "date": d,
                    "year": d[:4],
                    "entry": entry,
                    "px30": entry < 30,
                    "green": _regime_green(idx, d),
                    "path": path,
                    "opt": opt,
                }
            )
    return pd.DataFrame(rows)


def _line(name, rets):
    s = summarize_returns(list(rets), name)
    if not s["n"]:
        print(f"{name:28s} n=0")
        return s
    print(
        f"{name:28s} n={s['n']:4d}  PATH胜率 {s['winrate']:5.1f}%  均值 {s['mean']:+6.2f}%  Sharpe {s['sharpe']:.2f}"
    )
    return s


def main() -> int:
    rg = market_regime(live=True)
    print("regime", rg.get("light"), "ma20", rg.get("above_ma20"), "ma60", rg.get("above_ma60"))
    print("PATH tight TP4/SL3 cost15bps hold20 robust+低波  sample=400 seed=42")
    print("决策只看 PATH；OPT 只作水分对照。观察层过滤不是买入向。")
    df = collect()
    os.makedirs(OUTPUT, exist_ok=True)
    out = os.path.join(OUTPUT, "hist_px30_path.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print("events", len(df), "file", out)
    print()
    _line("ALL PATH", df["path"])
    _line("entry<30 PATH", df.loc[df["px30"], "path"])
    _line("entry>=30 PATH", df.loc[~df["px30"], "path"])
    print()
    _line("ALL OPT (水分)", df["opt"])
    _line("entry<30 OPT (水分)", df.loc[df["px30"], "opt"])
    print()
    _line("<30 ∩ 绿灯 PATH", df.loc[df["px30"] & df["green"], "path"])
    _line("<30 ∩ 非绿灯 PATH", df.loc[df["px30"] & ~df["green"], "path"])
    _line(">=30 ∩ 绿灯 PATH", df.loc[~df["px30"] & df["green"], "path"])
    print()
    print("by year PATH <30:")
    for y, g in df[df["px30"]].groupby("year"):
        _line(f"  {y} <30", g["path"])
    print()
    print("不构成投资建议。红灯空仓。现价<30 不是主策略。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
