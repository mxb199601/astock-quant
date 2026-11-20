#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""止跌信号长历史验证（研究用，非荐股）。规则全部事先定死，看完结果不再改。

- 数据：cache/kline_long（东财后复权，2012 起；先跑 scripts/fetch_long_history.py）
- 调仓：沪深300日历每 5 个交易日；只用当日及以前数据；次日开盘买，扣往返 0.3%
- 择时：任何时候 / 普跌（一谷占比≥30%）/ 止跌信号（≥30% 且比近 4 个调仓日最高回落≥5 个百分点）
- 选股：一谷全部 / 强化（10–30% 且近 5 日底背离）/ 现行规则（强化里去掉波动最大的三分之一）
- 卖法：涨 5% 就卖、最多 40 天（主）；涨 3%/20 天、涨 8%/60 天（参考）。摸到目标按目标价算（跳空高开多赚的不计，偏保守）
- 时期：2024-06 起是设计止跌信号用过的数据；之前是从没见过的数据
- 局限：只有现存股票（退市股缺失，幸存者偏差，对抄底偏乐观）；ST 用今天的名单剔
"""
from __future__ import annotations

import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from lib import gu_feng as gf
from lib.patterns import PATTERN_REGISTRY
from lib.paths import OUTPUT

LONG = os.path.join(ROOT, "cache", "kline_long")
COST = 0.30
STEP = 5
EXITS = [(40, 0.05, "涨5%卖·最多40天"), (20, 0.03, "涨3%卖·最多20天"), (60, 0.08, "涨8%卖·最多60天")]
DESIGN_FROM = "2024-06-01"
MIN_SCORED = 800  # 当天能算分位的票太少时，一谷占比不可靠，不参与评估
DS_PATH = os.path.join(OUTPUT, "gufeng_long_valley.csv")


def load(sym_or_path: str) -> pd.DataFrame:
    p = sym_or_path if sym_or_path.endswith(".gz") else os.path.join(LONG, f"{sym_or_path}.csv.gz")
    df = pd.read_csv(p, dtype={"date": str})
    return df.sort_values("date").reset_index(drop=True)


def build() -> pd.DataFrame:
    cal = load("sh000300")["date"].tolist()
    steps = np.array(sorted(set(cal[::STEP])))
    rows, scored = [], {}
    files = [f for f in sorted(glob.glob(os.path.join(LONG, "*.csv.gz"))) if not f.endswith("sh000300.csv.gz")]
    for k, f in enumerate(files):
        df = load(f)
        n = len(df)
        if n < gf.MIN_OBS + 70:
            continue
        c, o, h = (df[x].to_numpy(float) for x in ("close", "open", "high"))
        dates = df["date"].to_numpy()
        div5 = pd.Series(PATTERN_REGISTRY["底背离"]["fn"](df).fillna(False).astype(bool)).rolling(5, min_periods=1).max().to_numpy()
        volat = pd.Series(c).pct_change().rolling(20).std().to_numpy()
        fmax = {}
        for w, _, _ in EXITS:
            arr = np.full(n, np.nan)
            if n > w:
                m = sliding_window_view(h, w).max(axis=1)  # m[i] = max(h[i..i+w-1])
                arr[: n - w] = m[1 : n - w + 1]  # fmax[t] = max(h[t+1..t+w])
            fmax[w] = arr
        sym = os.path.basename(f).replace(".csv.gz", "")
        for t in np.where(np.isin(dates, steps))[0]:
            if t + 1 < gf.MIN_OBS + 5:
                continue
            vp = gf.latest_past_percentile(c[: t + 1])
            if not np.isfinite(vp):
                continue
            scored[dates[t]] = scored.get(dates[t], 0) + 1
            if vp > gf.VALLEY:
                continue
            rec = {"date": dates[t], "sym": sym, "val_pct": vp, "div5": bool(div5[t]), "volat": volat[t]}
            e = o[t + 1] if t + 1 < n else np.nan
            for w, tp, name in EXITS:
                if t + w < n and e > 0:
                    hit = fmax[w][t] >= e * (1 + tp)
                    rec[f"hit_{w}"] = float(hit)
                    rec[f"ret_{w}"] = tp * 100 - COST if hit else (c[t + w] / e - 1) * 100 - COST
                else:
                    rec[f"hit_{w}"] = rec[f"ret_{w}"] = np.nan
            rows.append(rec)
        if (k + 1) % 500 == 0:
            print(f"  …{k+1}/{len(files)}", flush=True)
    ds = pd.DataFrame(rows)
    sc = pd.Series(scored)
    ds["scored"] = ds["date"].map(sc)
    ds["breadth"] = ds["date"].map(ds.groupby("date").size() / sc)
    return ds


def segments(dates) -> list:
    ds = sorted(set(dates))
    segs = []
    for d in ds:
        if segs and (pd.Timestamp(d) - pd.Timestamp(segs[-1][-1])).days <= 30:
            segs[-1].append(d)
        else:
            segs.append([d])
    return segs


def stats(x: pd.DataFrame, w: int = 40) -> dict:
    x = x.dropna(subset=[f"hit_{w}"])
    if x.empty:
        return {"笔数": 0}
    miss = x[x[f"hit_{w}"] == 0][f"ret_{w}"]
    return {
        "笔数": len(x),
        "调仓日": x["date"].nunique(),
        "行情段": len(segments(x["date"])),
        "胜率%": round(x[f"hit_{w}"].mean() * 100, 1),
        "按日平均胜率%": round(x.groupby("date")[f"hit_{w}"].mean().mean() * 100, 1),
        "每笔平均%": round(x[f"ret_{w}"].mean(), 2),
        "没涨到时平均%": round(miss.mean(), 1) if len(miss) else np.nan,
        "亏超15%占%": round((x[f"ret_{w}"] < -15).mean() * 100, 1),
    }


def main() -> int:
    if not os.path.exists(DS_PATH) or "--rebuild" in sys.argv:
        ds = build()
        ds.to_csv(DS_PATH, index=False, encoding="utf-8-sig")
    else:
        ds = pd.read_csv(DS_PATH)
    ds["div5"] = ds["div5"].astype(bool)
    print(f"一谷样本 {len(ds)} 笔；日期 {ds['date'].min()}～{ds['date'].max()}；每天可算分位的票 {int(ds['scored'].min())}～{int(ds['scored'].max())} 只")
    ds = ds[ds["scored"] >= MIN_SCORED].copy()
    print(f"只评估可算分位≥{MIN_SCORED} 只的日子：从 {ds['date'].min()} 起")

    grp = (ds["val_pct"] > 0.10) & ds["div5"]
    vr = ds[grp].groupby("date")["volat"].rank(pct=True)
    ds["rule"] = grp & (vr.reindex(ds.index) <= 2 / 3)
    ds["grp"] = grp
    br = ds.groupby("date")["breadth"].first().sort_index()
    peak4 = br.rolling(4, min_periods=1).max()
    ds["P"] = ds["date"].map(br >= 0.30)
    ds["B"] = ds["date"].map((br >= 0.30) & ((peak4 - br) >= 0.05))
    new = ds["date"] < DESIGN_FROM

    pd.set_option("display.width", 240)
    pd.set_option("display.unicode.east_asian_width", True)
    for per_name, pm in [("从没见过的数据（设计前）", new), ("设计用过的数据（2024-06 起）", ~new)]:
        out = []
        for tn, tm in [("任何时候", pd.Series(True, index=ds.index)), ("普跌就买", ds["P"]), ("止跌信号", ds["B"])]:
            for sn, sm in [("一谷全部", pd.Series(True, index=ds.index)), ("强化", ds["grp"]), ("现行规则", ds["rule"])]:
                out.append({"择时": tn, "选股": sn, **stats(ds[pm & tm & sm])})
        print(f"\n== {per_name}：涨5%卖·最多40天 ==")
        print(pd.DataFrame(out).to_string(index=False))

    print("\n== 止跌信号 × 现行规则：逐段行情（涨5%卖·最多40天）==")
    b = ds[ds["B"] & ds["rule"]]
    for seg in segments(b["date"]):
        x = b[b["date"].isin(seg)]
        st = stats(x)
        brs = [round(br[d] * 100, 1) for d in seg]
        tag = "设计用过" if seg[0] >= DESIGN_FROM else "没见过"
        print(f"  [{tag}] {seg[0]}～{seg[-1]}（{len(seg)} 个调仓日，占比 {brs}）: 笔数{st.get('笔数',0)} 胜率{st.get('胜率%','—')}% 每笔{st.get('每笔平均%','—')}% 没涨到{st.get('没涨到时平均%','—')}%")

    print("\n== 止跌信号 × 现行规则：其他卖法（从没见过的数据）==")
    for w, tp, name in EXITS:
        print(f"  {name}: {stats(ds[new & ds['B'] & ds['rule']], w)}")
    print("\n== 普跌就买 × 现行规则：其他卖法（从没见过的数据，对照）==")
    for w, tp, name in EXITS:
        print(f"  {name}: {stats(ds[new & ds['P'] & ds['rule']], w)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
