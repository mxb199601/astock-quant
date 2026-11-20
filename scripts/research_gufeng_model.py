#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""谷峰选股模型（研究用，非荐股）：走步检验的「评分卡」逻辑回归，只做选股；择时交给止跌信号（规则 B）。

- 样本：沪深300日历每 5 个交易日一次（另加最后一天=今天），所有一谷票（3 年分位 ≤30%）
- 特征：只用当日及以前数据；连续特征先在同一天的一谷票里排成百分位，再切 5 档做哑变量（类似评分卡）
- 标签：次日开盘买，40 个交易日内摸到 +5% 就卖 → 1；摸不到第 40 天收盘卖 → 0（收益扣往返 0.3%）
- 走步：按月重训；训练只用预测日之前已经出结果的样本（样本日 + 40 个交易日 < 预测日）
- 模型：numpy 实现的 L2 逻辑回归（牛顿法），不引入新依赖
- 局限：缓存只有现存股票（幸存者偏差）；只有约 2.4 年数据
输出：output/gufeng_model_dataset.csv（样本+特征+标签+走步得分）、终端表格
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

COST = 0.30
TP, W = 0.05, 40
STEP = 5
OOS_FROM = "2025-01"
L2 = 10.0
CONT = [
    "val_pct", "ret5", "ret20", "ret60", "volr", "volr5", "volat", "dma20", "dma60",
    "rsi14", "dd60", "up20", "body", "nup10", "lowshadow", "logprice",
]
CN = {
    "val_pct": "3年位置", "ret5": "5日涨跌", "ret20": "20日涨跌", "ret60": "60日涨跌", "volr": "今日量比",
    "volr5": "5日量比", "volat": "20日波动", "dma20": "离20日线", "dma60": "离60日线", "rsi14": "RSI",
    "dd60": "离60日高点", "up20": "离20日低点", "body": "今日实体", "nup10": "10日上涨天数",
    "lowshadow": "下影线", "logprice": "股价高低", "div5": "底背离",
}
DS_PATH = os.path.join(OUTPUT, "gufeng_model_dataset.csv")


def tp_exit(o, h, c, t, tp, w):
    if t + w >= len(c) or not (o[t + 1] > 0):
        return np.nan, np.nan
    e = o[t + 1]
    up = e * (1 + tp)
    for j in range(t + 1, t + w + 1):
        if j > t + 1 and o[j] >= up:
            return 1.0, (o[j] / e - 1) * 100 - COST
        if h[j] >= up:
            return 1.0, tp * 100 - COST
    return 0.0, (c[t + w] / e - 1) * 100 - COST


def build() -> pd.DataFrame:
    cal = sorted(set(pd.read_csv(os.path.join(CACHE, "sh000300.csv"), dtype={"date": str})["date"].str[:10]))
    steps = set(cal[::STEP]) | {cal[-1]}
    fund = load_fundamentals(force=False)
    syms = filter_mainboard(list_cached_symbols(), fund=fund, drop_st=True)
    rows, scored = [], {}
    for k, sym in enumerate(syms):
        df = load_kline_csv(sym, min_bars=gf.MIN_OBS + 70)
        if df is None or not {"open", "high", "low", "vol"} <= set(df.columns):
            continue
        df = df.sort_values("date").reset_index(drop=True)
        c, o, h, l, v = (df[x].astype(float) for x in ("close", "open", "high", "low", "vol"))
        dates = df["date"].astype(str).str[:10].to_numpy()
        ca, oa, ha = c.to_numpy(), o.to_numpy(), h.to_numpy()
        vp = gf.rolling_past_percentile(ca)
        div5 = pd.Series(PATTERN_REGISTRY["底背离"]["fn"](df).fillna(False).astype(bool)).rolling(5, min_periods=1).max().to_numpy()
        pct = c.pct_change()
        d = c.diff()
        rs = d.clip(lower=0).rolling(14).mean() / (-d.clip(upper=0)).rolling(14).mean()
        vmean = v.shift(1).rolling(20).mean().replace(0, np.nan)
        feats = {
            "ret5": c / c.shift(5) - 1,
            "ret20": c / c.shift(20) - 1,
            "ret60": c / c.shift(60) - 1,
            "volr": v / vmean,
            "volr5": v.rolling(5).mean() / v.shift(5).rolling(20).mean().replace(0, np.nan),
            "volat": pct.rolling(20).std(),
            "dma20": c / c.rolling(20).mean() - 1,
            "dma60": c / c.rolling(60).mean() - 1,
            "rsi14": 100 - 100 / (1 + rs),
            "dd60": c / h.rolling(60).max() - 1,
            "up20": c / l.rolling(20).min() - 1,
            "body": c / o - 1,
            "nup10": (pct > 0).astype(float).rolling(10).mean(),
            "lowshadow": (np.minimum(o, c) - l) / c,
            "logprice": np.log(c),
        }
        fa = {kk: vv.to_numpy() for kk, vv in feats.items()}
        for t in range(len(df)):
            if dates[t] not in steps or t + 1 < gf.MIN_OBS + 5 or not np.isfinite(vp[t]):
                continue
            scored[dates[t]] = scored.get(dates[t], 0) + 1
            if vp[t] > gf.VALLEY:
                continue
            rec = {"date": dates[t], "sym": sym, "val_pct": vp[t], "div5": float(div5[t]), "price": ca[t]}
            for kk, arr in fa.items():
                rec[kk] = arr[t]
            rec["hit"], rec["ret"] = tp_exit(oa, ha, ca, t, TP, W)
            rows.append(rec)
        if (k + 1) % 500 == 0:
            print(f"  …{k+1}/{len(syms)}", flush=True)
    ds = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    ds["breadth"] = ds["date"].map(ds.groupby("date").size() / pd.Series(scored))
    pos = {dd: i for i, dd in enumerate(cal)}
    ds["cal_i"] = ds["date"].map(pos)
    return ds


def design(ds: pd.DataFrame, feats=CONT, bins: int = 5) -> np.ndarray:
    cols = [ds["div5"].fillna(0).to_numpy()[:, None]]
    for f in feats:
        r = ds.groupby("date")[f].rank(pct=True).fillna(0.5).to_numpy()
        b = np.minimum((r * bins).astype(int), bins - 1)  # 0 档做基准
        cols.append(np.stack([(b == k).astype(float) for k in range(1, bins)], axis=1))
    return np.hstack(cols)


# numpy 2.0 + macOS Accelerate 的 matmul 会误报 overflow/divide 警告（已与 einsum 逐元素核对一致）；
# 用 errstate 关掉误报，再用 isfinite 断言兜住真正的数值问题。
def fit_logit(X, y, l2=L2, iters=30):
    n, p = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(p + 1)
    R = l2 * np.eye(p + 1)
    R[0, 0] = 0
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        for _ in range(iters):
            pr = 1 / (1 + np.exp(-(Xb @ w)))
            g = Xb.T @ (pr - y) + R @ w
            H = (Xb * (pr * (1 - pr))[:, None]).T @ Xb + R
            step = np.linalg.solve(H, g)
            w -= step
            if np.abs(step).max() < 1e-7:
                break
    assert np.isfinite(w).all(), "逻辑回归权重出现非有限值"
    return w


def predict(w, X):
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        p = 1 / (1 + np.exp(-(np.hstack([np.ones((len(X), 1)), X]) @ w)))
    assert np.isfinite(p).all(), "预测出现非有限值"
    return p


def auc(score, y):
    y = np.asarray(y)
    if y.min() == y.max():
        return np.nan
    r = pd.Series(score).rank().to_numpy()
    n1 = y.sum()
    n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def walk_forward(ds: pd.DataFrame, X: np.ndarray, l2: float = L2) -> pd.Series:
    score = pd.Series(np.nan, index=ds.index)
    for m in sorted(set(ds["date"].str[:7])):
        if m < OOS_FROM:
            continue
        pm = ds["date"].str[:7] == m
        first_i = ds.loc[pm, "cal_i"].min()
        tr = (ds["cal_i"] + W < first_i) & ds["hit"].notna()
        if tr.sum() < 5000:
            continue
        w = fit_logit(X[tr.to_numpy()], ds.loc[tr, "hit"].to_numpy(), l2=l2)
        score[pm] = predict(w, X[pm.to_numpy()])
    return score


def evaluate(ds: pd.DataFrame) -> None:
    e = ds[ds["score"].notna() & ds["hit"].notna()].copy()
    grp = (e["val_pct"] > 0.10) & (e["div5"] > 0)
    e["grp"] = grp
    e["vr"] = e[grp].groupby("date")["volat"].rank(pct=True)
    e["rule"] = grp & (e["vr"] <= 2 / 3)
    br = ds.groupby("date")["breadth"].first().sort_index()
    rule_b = (br >= 0.30) & ((br.rolling(4, min_periods=1).max() - br) >= 0.05)
    out = []
    for d, x in e.groupby("date"):
        rec = {"date": d, "月": d[:7], "规则B": bool(rule_b.get(d, False)), "一谷全部": x["hit"].mean(), "auc": auc(x["score"], x["hit"])}
        rul = x[x["rule"]]
        g = x[x["grp"]]
        rec["现行规则"] = rul["hit"].mean() if len(rul) >= 5 else np.nan
        rec["现行规则_收益"] = rul["ret"].mean() if len(rul) >= 5 else np.nan
        top = x.nlargest(10, "score")
        rec["模型前10"] = top["hit"].mean()
        rec["模型前10_收益"] = top["ret"].mean()
        gt = g.nlargest(10, "score") if len(g) >= 10 else g
        rec["强化里模型前10"] = gt["hit"].mean() if len(gt) >= 5 else np.nan
        rec["强化里模型前10_收益"] = gt["ret"].mean() if len(gt) >= 5 else np.nan
        bot = x.nsmallest(10, "score")
        rec["模型后10"] = bot["hit"].mean()
        out.append(rec)
    r = pd.DataFrame(out)
    cols = ["一谷全部", "现行规则", "模型前10", "强化里模型前10", "模型后10"]

    def summary(rr, title):
        print(f"\n== {title}（{len(rr)} 个调仓日；格子=按日平均的「40天内涨到5%」比例）==")
        line = {c: f"{rr[c].mean()*100:.1f}%" for c in cols}
        line["每日排序AUC"] = f"{rr['auc'].mean():.3f}"
        line["模型前10>现行规则的日子"] = f"{(rr['模型前10'] > rr['现行规则']).mean()*100:.0f}%"
        line["每笔收益 现行/模型前10/强化里前10"] = f"{rr['现行规则_收益'].mean():+.2f}% / {rr['模型前10_收益'].mean():+.2f}% / {rr['强化里模型前10_收益'].mean():+.2f}%"
        for kk, vv in line.items():
            print(f"  {kk}: {vv}")

    summary(r, "走步检验·全部日子")
    summary(r[r["规则B"]], "走步检验·止跌信号触发的日子")
    print("\n== 按月（模型前10 / 现行规则 / 一谷全部）==")
    for m, x in r.groupby("月"):
        print(f"  {m}: {x['模型前10'].mean()*100:.0f}% / {x['现行规则'].mean()*100:.0f}% / {x['一谷全部'].mean()*100:.0f}%  AUC {x['auc'].mean():.2f}")


def explain(ds: pd.DataFrame, X: np.ndarray) -> None:
    tr = ds["hit"].notna().to_numpy()
    w = fit_logit(X[tr], ds.loc[tr, "hit"].to_numpy())
    eff = [("div5", w[1])]
    print("\n== 模型学到了什么（全部已出结果样本拟合；数值=相对最低档的对数几率，越大越好）==")
    print(f"  底背离: {w[1]:+.2f}")
    k = 2
    for f in CONT:
        coefs = [0.0] + list(w[k : k + 4])
        k += 4
        print(f"  {CN[f]}（低→高 5档）: " + " ".join(f"{x:+.2f}" for x in coefs))
        eff.append((f, max(coefs) - min(coefs)))
    top = sorted(eff, key=lambda z: -abs(z[1]))[:6]
    print("  影响最大: " + "、".join(CN[f] for f, _ in top))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    if args.rebuild or not os.path.exists(DS_PATH):
        ds = build()
    else:
        ds = pd.read_csv(DS_PATH)
    X = design(ds)
    ds["score"] = walk_forward(ds, X)
    ds.to_csv(DS_PATH, index=False, encoding="utf-8-sig")
    print(f"样本 {len(ds)} 笔（一谷），已出结果 {int(ds['hit'].notna().sum())}，日期 {ds['date'].min()}~{ds['date'].max()}")
    evaluate(ds)
    explain(ds, X)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
