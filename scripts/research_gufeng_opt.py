#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""谷峰胜率研究（研究用，非荐股）：候选规则先写死，学习期挑、检验期只验一次。

- 调仓：沪深300交易日每 5 天一次；只用当日及以前数据；次日开盘买，扣往返 0.3%
- 胜率默认 = 持有 20 日收盘卖后 >0；同时看均值、同日超额（相对全体主板同日均值）
- 学习期 < SPLIT ≤ 检验期；自动组合只看学习期
- 出场对比用逐日路径（同日先止损；跳空按开盘价成交）
输出：output/gufeng_research_valley.csv（一谷逐笔+因子）、终端表格
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
from lib.patterns import PATTERN_REGISTRY
from lib.paths import CACHE, OUTPUT

COST = 0.30  # 往返 %
SPLIT = "2025-10-01"
H = 20
EXITS = [
    ("拿满20天", None, None), ("止盈4%/止损3%", 0.04, 0.03), ("止盈8%/止损5%", 0.08, 0.05), ("止盈15%/止损8%", 0.15, 0.08),
    # 只挂止盈、不止损：20 天内摸到就卖，摸不到第 20 天收盘卖
    ("涨1%就卖", 0.01, None), ("涨3%就卖", 0.03, None), ("涨5%就卖", 0.05, None), ("涨10%就卖", 0.10, None),
]


def path_ret(o, h, l, c, t, tp, sl):
    e = o[t + 1]
    if t + H >= len(c) or not (e > 0):
        return np.nan
    if tp is None:
        return (c[t + H] / e - 1) * 100 - COST
    up = e * (1 + tp)
    dn = e * (1 - sl) if sl is not None else -np.inf
    for j in range(t + 1, t + H + 1):
        if j > t + 1:
            if o[j] <= dn or o[j] >= up:  # 跳空越过止损/止盈：按开盘成交
                return (o[j] / e - 1) * 100 - COST
        if l[j] <= dn:  # 同日双触先止损
            return -sl * 100 - COST
        if h[j] >= up:
            return tp * 100 - COST
    return (c[t + H] / e - 1) * 100 - COST


def build(step: int):
    idx = pd.read_csv(os.path.join(CACHE, "sh000300.csv"), dtype={"date": str}).sort_values("date")
    cal = set(idx["date"].str[:10].tolist()[::step])
    fund = load_fundamentals(force=False)
    syms = filter_mainboard(list_cached_symbols(), fund=fund, drop_st=True)
    base_rows, valley_rows = [], []
    for k, sym in enumerate(syms):
        df = load_kline_csv(sym, min_bars=gf.MIN_OBS + 70)
        if df is None or not {"open", "high", "low", "vol"} <= set(df.columns):
            continue
        df = df.sort_values("date").reset_index(drop=True)
        c, o, h, l, v = (df[x].to_numpy(float) for x in ("close", "open", "high", "low", "vol"))
        dates = df["date"].astype(str).str[:10].to_numpy()
        vp = gf.rolling_past_percentile(c)
        div5 = pd.Series(PATTERN_REGISTRY["底背离"]["fn"](df).fillna(False).astype(bool)).rolling(5, min_periods=1).max().astype(bool).to_numpy()
        cs = pd.Series(c)
        ret5 = (cs / cs.shift(5) - 1).to_numpy() * 100
        ret20 = (cs / cs.shift(20) - 1).to_numpy() * 100
        volr = (pd.Series(v) / pd.Series(v).shift(1).rolling(20).mean()).to_numpy()
        volat = (cs.pct_change().rolling(20).std() * 100).to_numpy()
        dma20 = (cs / cs.rolling(20).mean() - 1).to_numpy() * 100
        for t in range(len(df) - 1):
            if dates[t] not in cal or t + 1 < gf.MIN_OBS + 5 or not np.isfinite(vp[t]):
                continue
            r20 = path_ret(o, h, l, c, t, None, None)
            base_rows.append((dates[t], r20))
            if vp[t] > gf.VALLEY:
                continue
            rec = {
                "date": dates[t], "sym": sym, "val_pct": vp[t], "div5": bool(div5[t]),
                "ret5": ret5[t], "ret20": ret20[t], "volr": volr[t], "volat": volat[t],
                "dma20": dma20[t], "price": c[t], "r20": r20,
            }
            for name, tp, sl in EXITS[1:]:
                rec[name] = path_ret(o, h, l, c, t, tp, sl)
            valley_rows.append(rec)
        if (k + 1) % 500 == 0:
            print(f"  …{k+1}/{len(syms)}", flush=True)
    base = pd.DataFrame(base_rows, columns=["date", "r20"])
    va = pd.DataFrame(valley_rows)
    n_all = base.groupby("date").size()
    va["breadth"] = va["date"].map(va.groupby("date").size() / n_all)
    va["base_r20"] = va["date"].map(base.dropna().groupby("date")["r20"].mean())
    # 同日一谷内波动率分位（排除最躁的 30%）
    va["volat_rank"] = va.groupby("date")["volat"].rank(pct=True)
    return va


def stats(g: pd.DataFrame, col: str = "r20") -> dict:
    g = g.dropna(subset=[col])
    if g.empty:
        return {"笔数": 0}
    by_d = g.groupby("date")[col].mean() - g.groupby("date")["base_r20"].first()
    return {
        "笔数": len(g),
        "胜率%": round((g[col] > 0).mean() * 100, 1),
        "均值%": round(g[col].mean(), 2),
        "同日超额%": round(by_d.mean(), 2),
    }


def rules(va: pd.DataFrame):
    return [
        ("只要：大盘普跌（一谷占比≥30%）", va.breadth >= 0.30),
        ("只要：大盘普跌（一谷占比≥40%）", va.breadth >= 0.40),
        ("只要：有底背离", va.div5),
        ("只要：近5日已拐头（5日涨幅>0）", va.ret5 > 0),
        ("只要：短期急跌（5日跌>5%）", va.ret5 < -5),
        ("只要：站上20日线", va.dma20 > 0),
        ("只要：缩量（量<20日均量0.7倍）", va.volr < 0.7),
        ("排除：深谷≤10%", va.val_pct > 0.10),
        ("排除：近20日跌超15%", va.ret20 >= -15),
        ("排除：放量（量>20日均量2倍）", va.volr <= 2),
        ("排除：波动最大的30%", va.volat_rank <= 0.70),
        ("排除：股价<3元", va.price >= 3),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    path = os.path.join(OUTPUT, "gufeng_research_valley.csv")
    if args.rebuild or not os.path.exists(path):
        va = build(args.step)
        va.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        va = pd.read_csv(path)
        va["div5"] = va["div5"].astype(bool)
    tr, te = va.date < SPLIT, va.date >= SPLIT
    pd.set_option("display.width", 220)
    pd.set_option("display.unicode.east_asian_width", True)
    print(f"一谷样本 {len(va)} 笔；学习期 {va[tr].date.min()}~{va[tr].date.max()}，检验期 {va[te].date.min()}~{va[te].date.max()}")

    rows = []
    for name, m in [("（基准）全部一谷", pd.Series(True, index=va.index))] + rules(va):
        a, b = stats(va[tr & m]), stats(va[te & m])
        rows.append({"规则": name, **{f"学·{k}": v for k, v in a.items()}, **{f"验·{k}": v for k, v in b.items()}})
    print("\n== 单条规则（持有20天）==")
    print(pd.DataFrame(rows).to_string(index=False))

    # 自动组合：只看学习期；每步加一条能让胜率 +≥1pp、超额不降、样本≥基准30% 的规则，挑胜率增幅最大的
    base_tr = stats(va[tr])
    cur = pd.Series(True, index=va.index)
    cur_st = base_tr
    chosen = []
    pool = rules(va)
    while True:
        best = None
        for name, m in pool:
            if name in chosen:
                continue
            st = stats(va[tr & cur & m])
            if st["笔数"] < 0.3 * base_tr["笔数"]:
                continue
            if st["胜率%"] >= cur_st["胜率%"] + 1 and st["同日超额%"] >= cur_st["同日超额%"]:
                if best is None or st["胜率%"] > best[2]["胜率%"]:
                    best = (name, m, st)
        if best is None:
            break
        chosen.append(best[0])
        cur = cur & best[1]
        cur_st = best[2]
    print("\n== 学习期自动组合 ==", " + ".join(chosen) if chosen else "（没有规则能稳定提高）")
    out = []
    for lab, m in [("全部一谷", pd.Series(True, index=va.index)), ("组合后", cur)]:
        for per, pm in [("学习期", tr), ("检验期", te)]:
            out.append({"组": lab, "期": per, **stats(va[pm & m])})
    print(pd.DataFrame(out).to_string(index=False))

    print("\n== 卖出方式对比（组合后的票；逐日路径，同日先止损）==")
    ex = []
    for name, _, _ in EXITS:
        col = "r20" if name == "拿满20天" else name
        for per, pm in [("学习期", tr), ("检验期", te)]:
            ex.append({"卖法": name, "期": per, **stats(va[pm & cur], col)})
    print(pd.DataFrame(ex).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
