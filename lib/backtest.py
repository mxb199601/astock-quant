# -*- coding: utf-8 -*-
"""主题回测：入场模式 base/strict/elite + 出场 fixed/tpsl/asym。"""
from __future__ import annotations

import os
from typing import List, Optional

import numpy as np
import pandas as pd

from .data import load_kline_csv
from .factors import formula3_mask
from .metrics import apply_roundtrip_cost, format_stats, summarize_returns
from .paths import OUTPUT
from .themes import DEFAULT_BACKTEST


def _simulate(
    df: pd.DataFrame,
    period: int,
    exit_mode: str,
    tp: float,
    sl: float,
    cost_bps: float,
) -> pd.DataFrame:
    """exit_mode: fixed | tpsl | asym | wr | tight
    asym  = TP+8 / SL-5（单笔空间）
    wr    = TP+5 / SL-3（胜率优先，全主板抽样~70%）
    tight = TP+4 / SL-3（更高胜率~78%，均值仍正）
    """
    c = df["close"].values
    dates = df["date"].values
    results = []
    fresh = df["fresh"].values
    for i in range(len(df)):
        if not fresh[i]:
            continue
        if i + period >= len(c):
            continue
        entry = c[i]
        ret = (c[i + period] / entry - 1) * 100
        if exit_mode in ("tpsl", "asym", "wr", "tight"):
            high = df["high"].iloc[i : i + period + 1].max()
            low = df["low"].iloc[i : i + period + 1].min()
            if exit_mode == "asym":
                tp_u, sl_u = 8.0, -5.0
            elif exit_mode == "wr":
                tp_u, sl_u = 5.0, -3.0
            elif exit_mode == "tight":
                tp_u, sl_u = 4.0, -3.0
            else:
                tp_u, sl_u = tp, sl
            if high >= entry * (1 + tp_u / 100):
                ret = tp_u
            elif low <= entry * (1 + sl_u / 100):
                ret = sl_u
        ret = apply_roundtrip_cost(ret, cost_bps)
        if "idx_close" in df.columns:
            ie = df["idx_close"].iloc[i]
            ix = df["idx_close"].iloc[i + period]
            if ie and ie == ie and ix == ix and ie > 0:
                bench = (ix / ie - 1) * 100
            else:
                bench = np.nan
        else:
            bench = np.nan
        results.append({
            "date": dates[i],
            "ret": ret,
            "bench": bench,
            "excess": ret - bench if bench == bench else np.nan,
            "period": period,
            "exit": exit_mode,
        })
    return pd.DataFrame(results)


def run_theme_backtest(
    codes: Optional[List[str]] = None,
    cost_bps: float = 15.0,
    hold_periods: Optional[List[int]] = None,
    tp: float = 6.0,
    sl: float = -6.0,
    split: str = "2025-01-01",
    make_plots: bool = True,
    mode: str = "strict",
    exit_mode: str = "asym",
    compare: bool = True,
    require_low_vol: bool = True,
    low_vol_q: float = 0.5,
) -> pd.DataFrame:
    hold_periods = hold_periods or [10, 20]
    codes = codes or DEFAULT_BACKTEST
    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None:
        print("缺少 sh000300，请先: python cli.py refresh --theme")
        return pd.DataFrame()

    print("=== 主题回测（高胜率优化）===")
    print("代码:", " ".join(codes))
    print(
        f"入场: {mode} | 出场: {exit_mode} | 成本单边 {cost_bps:.0f}bps"
        f" | 指数低波(robust v5)={'开' if require_low_vol else '关'}"
    )
    if exit_mode == "asym":
        print("出场细节: 止盈+8% / 止损-5%（路径触及即平）")
    elif exit_mode == "wr":
        print("出场细节: 止盈+5% / 止损-3%（胜率优先 wr）")
    elif exit_mode == "tight":
        print("出场细节: 止盈+4% / 止损-3%（极致胜率 tight）")
    elif exit_mode == "tpsl":
        print(f"出场细节: 止盈{tp}% / 止损{sl}%")
    print()

    modes = [mode]
    if compare and mode not in ("base",):
        # 对照：base + 当前模式
        modes = list(dict.fromkeys(["base", "strict", mode]))
    exits = [exit_mode]
    if compare and exit_mode != "fixed":
        # 对照 fixed + asym + 当前（若不同）
        exits = list(dict.fromkeys(["fixed", "asym", exit_mode]))

    all_rets = []
    for m in modes:
        for sym in codes:
            df = load_kline_csv(sym, min_bars=300)
            if df is None:
                continue
            # 低波开关仅作用于 robust 类模式
            use_lv = bool(require_low_vol and str(m).lower() in ("robust", "path", "v4", "v5"))
            sig = formula3_mask(df, idx, mode=m, require_low_vol=use_lv, low_vol_q=low_vol_q)
            df = df.copy()
            # avoid FutureWarning
            prev = sig.shift(1)
            prev = prev.where(prev.notna(), False).astype(bool)
            df["fresh"] = (sig & ~prev).astype(bool)
            imap = dict(zip(idx["date"].astype(str), idx["close"]))
            df["idx_close"] = pd.Series(
                [imap.get(str(d), np.nan) for d in df["date"]]
            ).ffill().values

            for per in hold_periods:
                for em in exits:
                    rets = _simulate(df, per, em, tp, sl, cost_bps)
                    if rets.empty:
                        continue
                    rets["sym"] = sym
                    rets["hold"] = per
                    rets["mode"] = m
                    all_rets.append(rets)

    if not all_rets:
        print("无有效信号")
        return pd.DataFrame()

    combined = pd.concat(all_rets, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])

    print("=== 汇总（已扣成本）— 关注胜率与均值双指标 ===")
    for m in modes:
        for em in exits:
            for hold in hold_periods:
                sub = combined[(combined["mode"] == m) & (combined["exit"] == em) & (combined["hold"] == hold)]
                if sub.empty:
                    continue
                print(f"\n[{m} | {em} | 持仓{hold}日]")
                print(" ", format_stats(summarize_returns(sub["ret"], "整体")))
                post = sub[sub["date"] >= split]
                print(" ", format_stats(summarize_returns(post["ret"], f"{split[:4]}+")))
                if sub["excess"].notna().any():
                    print(" ", format_stats(summarize_returns(sub["excess"].dropna(), "超额")))

    # 对照表：默认 hold=20
    h = 20 if 20 in hold_periods else hold_periods[0]
    print(f"\n=== 对照表 持仓{h}日（胜率↑ 优先，均值不能塌）===")
    print(f"{'入场':8s} {'出场':8s} {'n':>4s} {'胜率':>7s} {'均值':>8s} {'Sharpe':>7s}")
    for m in modes:
        for em in exits:
            sub = combined[(combined["mode"] == m) & (combined["exit"] == em) & (combined["hold"] == h)]
            s = summarize_returns(sub["ret"])
            if s["n"] == 0:
                continue
            print(f"{m:8s} {em:8s} {s['n']:4d} {s['winrate']:6.1f}% {s['mean']:+7.2f}% {s['sharpe']:7.2f}")

    os.makedirs(OUTPUT, exist_ok=True)
    out_csv = os.path.join(OUTPUT, "backtest_events.csv")
    combined.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n事件明细: {out_csv}")

    # 推荐结论
    pref = combined[(combined["mode"] == mode) & (combined["exit"] == exit_mode) & (combined["hold"] == h)]
    base = combined[(combined["mode"] == "base") & (combined["exit"] == "fixed") & (combined["hold"] == h)]
    if len(pref) and len(base):
        sp, sb = summarize_returns(pref["ret"]), summarize_returns(base["ret"])
        print("\n【相对原公式三固定持仓 · OPT乐观窗】")
        print(f"  胜率: {sb['winrate']:.1f}% → {sp['winrate']:.1f}%  ({sp['winrate']-sb['winrate']:+.1f}pt)")
        print(f"  均值: {sb['mean']:+.2f}% → {sp['mean']:+.2f}%")
        print("  说明: OPT=窗内极值+先止盈，会高估胜率；下面 PATH 更近实盘。")

    # PATH 双口径（主模式 × 主出场 × 持仓 h）
    try:
        from .stress import exit_path_daily

        path_rets = []
        for m in [mode]:
            use_lv = bool(require_low_vol and str(m).lower() in ("robust", "path", "v4", "v5"))
            for sym in codes:
                df = load_kline_csv(sym, min_bars=300)
                if df is None:
                    continue
                sig = formula3_mask(df, idx, mode=m, require_low_vol=use_lv, low_vol_q=low_vol_q)
                prev = sig.shift(1)
                prev = prev.where(prev.notna(), False).astype(bool)
                fresh = (sig & ~prev).astype(bool)
                c = df["close"].astype(float).values
                hi = df["high"].astype(float).values
                lo = df["low"].astype(float).values
                for i in range(len(df)):
                    if not bool(fresh.iloc[i]) or i + h >= len(c):
                        continue
                    entry = float(c[i])
                    path_rets.append(
                        exit_path_daily(
                            entry, hi[i : i + h + 1], lo[i : i + h + 1], c[i : i + h + 1],
                            exit_mode if exit_mode in ("asym", "wr", "tight") else "wr",
                            cost_bps, h, "sl",
                        )
                    )
        if path_rets:
            so = summarize_returns(pref["ret"], "OPT") if len(pref) else None
            spth = summarize_returns(path_rets, "PATH")
            print(f"\n=== 双口径 [{mode}|{exit_mode}|{h}日] 扣成本 ===")
            if so and so["n"]:
                print(f"  OPT 乐观窗: n={so['n']} 胜率{so['winrate']:.1f}% 均值{so['mean']:+.2f}% Sharpe{so['sharpe']:.2f}")
            print(f"  PATH 路径:   n={spth['n']} 胜率{spth['winrate']:.1f}% 均值{spth['mean']:+.2f}% Sharpe{spth['sharpe']:.2f}")
            if so and so["n"] and spth["n"]:
                print(f"  胜率水分(OPT-PATH): {so['winrate']-spth['winrate']:+.1f}pt  ← 预期请看 PATH")
    except Exception as e:
        print(f"\nPATH 双口径跳过: {e}")

    if make_plots:
        try:
            import matplotlib.pyplot as plt
            plot_dir = os.path.join(OUTPUT, "backtest_plots")
            os.makedirs(plot_dir, exist_ok=True)
            dist = pref["ret"].dropna() if len(pref) else combined["ret"].dropna()
            if len(dist) > 0:
                plt.figure(figsize=(8, 5))
                plt.hist(dist, bins=15, color="steelblue", edgecolor="black", alpha=0.7)
                plt.axvline(dist.mean(), color="red", linestyle="--", label=f"Mean {dist.mean():.2f}%")
                plt.title(f"Return Dist ({mode}/{exit_mode}/{h}d, cost applied)")
                plt.legend()
                plt.grid(True, alpha=0.3)
                fp = os.path.join(plot_dir, "returns_hist_20d_fixed.png")
                plt.savefig(fp, dpi=150, bbox_inches="tight")
                plt.close()
                print("Saved:", fp)
        except Exception as e:
            print("Plot skipped:", e)

    print("\n不构成投资建议。红灯环境仍应空仓。决策请优先看 PATH 而非 OPT。")
    return combined
