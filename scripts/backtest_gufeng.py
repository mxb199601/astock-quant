#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""估值谷峰历史回放（研究用，非荐股）：过去每个调仓日只用当日及以前的收盘，按 lib.gu_feng 口径分一谷/一峰/中间，
次日开盘买入，持有 H 个交易日收盘卖出，扣往返成本，看之后真实涨跌，并与同日全体主板对比。

局限：缓存只有现存股票（幸存者偏差，退市股不在）；ST 用当前简称剔除（无历史 ST 状态）。
输出：output/gufeng_bt_events.csv（逐笔）、output/gufeng_bt_summary.csv
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib import gu_feng as gf
from lib.data import filter_mainboard, list_cached_symbols, load_fundamentals, load_kline_csv
from lib.metrics import DEFAULT_COST_BPS
from lib.patterns import PATTERN_REGISTRY, recent_pattern_hit
from lib.paths import OUTPUT

HORIZONS = (5, 20, 60)


def zone_of(vp: float) -> str:
    if vp <= gf.VALLEY:
        return "一谷"
    if vp >= gf.PEAK:
        return "一峰"
    return "中间"


def build_events(syms, step: int, cost_pct: float, check_n: int):
    rows = []
    rng = np.random.default_rng(7)
    checked = 0
    for k, sym in enumerate(syms):
        df = load_kline_csv(sym, min_bars=gf.MIN_OBS + 70)
        if df is None or "open" not in df.columns:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].to_numpy(float)
        opn = df["open"].to_numpy(float)
        dates = df["date"].astype(str).str[:10].to_numpy()
        vp_all = gf.rolling_past_percentile(close)  # 与扫描同一函数：只用过去 750 根
        # 页面「叠加底背离」= 近 5 根内触发过 patterns.底背离（形态只用当日及以前数据）
        div_sig = PATTERN_REGISTRY["底背离"]["fn"](df).fillna(False).astype(bool).to_numpy()
        div5 = pd.Series(div_sig).rolling(5, min_periods=1).max().astype(bool).to_numpy()
        for t in range(len(df)):
            # 与扫描同门槛：assess 要求截至当日至少 MIN_OBS+5 根
            if t + 1 < gf.MIN_OBS + 5 or not np.isfinite(vp_all[t]):
                continue
            # 抽查：回放分位 == 截到 t 日调用 assess（即当天收盘后扫描）的结果
            if checked < check_n and rng.random() < 0.002:
                a = gf.assess(df.iloc[: t + 1])
                assert a is not None and abs(a["val_pct"] - vp_all[t]) < 1e-12, (sym, dates[t])
                hit, _ = recent_pattern_hit(df.iloc[: t + 1], "底背离", lookback=5)
                assert hit == bool(div5[t]), (sym, dates[t], "底背离")
                checked += 1
            rec = {"sym": sym, "date": dates[t], "val_pct": vp_all[t], "zone": zone_of(vp_all[t]), "div5": bool(div5[t])}
            if t + 1 < len(df) and np.isfinite(opn[t + 1]) and opn[t + 1] > 0:
                for h in HORIZONS:
                    j = t + h
                    rec[f"r{h}"] = (close[j] / opn[t + 1] - 1) * 100 - cost_pct if j < len(df) else np.nan
            rows.append(rec)
        if (k + 1) % 500 == 0:
            print(f"  …{k+1}/{len(syms)}", flush=True)
    ev = pd.DataFrame(rows)
    # 统一调仓日：取全市场共同交易日序列，每 step 天一次
    cal = sorted(ev["date"].unique())
    keep = set(cal[::step])
    ev = ev[ev["date"].isin(keep)].reset_index(drop=True)
    return ev, checked


def summarize(ev: pd.DataFrame) -> pd.DataFrame:
    out = []
    base_by_date = {h: ev.groupby("date")[f"r{h}"].mean() for h in HORIZONS}
    groups = {
        "全体主板(基准)": ev,
        "一谷(≤30%)": ev[ev.zone == "一谷"],
        "一谷∩底背离(强化买入)": ev[(ev.zone == "一谷") & ev.div5],
        "一谷 无底背离": ev[(ev.zone == "一谷") & ~ev.div5],
        "深谷(≤10%)": ev[ev.val_pct <= 0.10],
        "深谷∩底背离": ev[(ev.val_pct <= 0.10) & ev.div5],
        "10–30%∩底背离": ev[(ev.val_pct > 0.10) & (ev.zone == "一谷") & ev.div5],
        "中间": ev[ev.zone == "中间"],
        "一峰(≥70%)": ev[ev.zone == "一峰"],
    }
    for name, g in groups.items():
        for h in HORIZONS:
            r = g[f"r{h}"].dropna()
            if r.empty:
                continue
            by_d = g.dropna(subset=[f"r{h}"]).groupby("date")[f"r{h}"].mean()
            ex = by_d - base_by_date[h].reindex(by_d.index)
            out.append({
                "组": name, "持有": h, "笔数": len(r), "调仓日": len(by_d),
                "胜率%": round((r > 0).mean() * 100, 1),
                "均值%": round(r.mean(), 2), "中位%": round(r.median(), 2),
                "超额均值%": round(ex.mean(), 2),
                "跑赢基准日占比%": round((ex > 0).mean() * 100, 1),
            })
    return pd.DataFrame(out)


def by_period(ev: pd.DataFrame, h: int = 20) -> pd.DataFrame:
    e = ev.dropna(subset=[f"r{h}"]).copy()
    e["期"] = e["date"].str[:4] + np.where(e["date"].str[5:7].astype(int) <= 6, "H1", "H2")
    base = e.groupby("期")[f"r{h}"].agg(lambda s: s.mean())
    rows = []
    for p, g in e.groupby("期"):
        v = g[g.zone == "一谷"][f"r{h}"]
        vd = g[(g.zone == "一谷") & g.div5][f"r{h}"]
        pk = g[g.zone == "一峰"][f"r{h}"]
        rows.append({
            "期": p, "基准胜率%": round((g[f"r{h}"] > 0).mean() * 100, 1), "基准均值%": round(base[p], 2),
            "一谷笔数": len(v), "一谷胜率%": round((v > 0).mean() * 100, 1), "一谷均值%": round(v.mean(), 2),
            "强化笔数": len(vd), "强化胜率%": round((vd > 0).mean() * 100, 1), "强化均值%": round(vd.mean(), 2),
            "一峰胜率%": round((pk > 0).mean() * 100, 1), "一峰均值%": round(pk.mean(), 2),
        })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=5, help="每几个交易日调仓一次")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    args = ap.parse_args()
    cost_pct = 2 * args.cost_bps / 100.0
    fund = load_fundamentals(force=False)
    syms = filter_mainboard(list_cached_symbols(), fund=fund, drop_st=True)
    if args.limit:
        syms = syms[: args.limit]
    print(f"回放池 {len(syms)} 只，每 {args.step} 个交易日调仓，往返成本 {cost_pct:.2f}%")
    ev, checked = build_events(syms, args.step, cost_pct, check_n=300)
    print(f"抽查 {checked} 个（股票,日期）：回放分位与 assess 一致")
    print(f"信号日 {ev['date'].min()} ~ {ev['date'].max()}，样本 {len(ev)} 笔")
    sm = summarize(ev)
    pdf = by_period(ev, 20)
    os.makedirs(OUTPUT, exist_ok=True)
    ev.to_csv(os.path.join(OUTPUT, "gufeng_bt_events.csv"), index=False, encoding="utf-8-sig")
    sm.to_csv(os.path.join(OUTPUT, "gufeng_bt_summary.csv"), index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print("\n== 分组 × 持有期（次日开盘买，H 日收盘卖，已扣成本）==")
    print(sm.to_string(index=False))
    print("\n== 20 日持有 · 分半年 ==")
    print(pdf.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
