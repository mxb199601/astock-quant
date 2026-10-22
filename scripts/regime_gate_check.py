#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红绿灯择时的独立对照实验（补 output/hist_regime_gate.csv 全 n=0 的坑）。

系统自带的 hist-validate 无法验证「红灯该空仓」——因为 strict/elite/robust/winrate
的入场条件本身就要求指数站上 MA20/MA60，历史上「非绿灯」样本恒为 0。

本脚本绕开全部选股信号，直接测最根本的问题：
  历史上「红灯日随机买全市场」vs「绿灯日随机买全市场」，未来 h 日收益差多少？

口径（与 lib/data.py market_regime 的日线版一致）：
  ma20=mean(c[t-19..t]) ma60=mean(c[t-59..t]) ma20_prev=mean(c[t-24..t-5])
  绿灯 = c[t]>ma20 且 c[t]>ma60 且 ma20>=ma20_prev；红灯 = c[t]<=ma20；其余黄灯
  t 日收盘买、t+h 日收盘卖（按个股自身K线，停牌不算交易日），扣往返 30bps

统计纪律：同一截面日的数千只股票由同一市场因子驱动，把它们当独立样本会把
自由度虚增数千倍。因此除池化 t 外，必须看**按截面日聚类**的 t 与 bootstrap 区间。

用法:
  python3 scripts/regime_gate_check.py            # 全总体（约3000只，较慢）
  python3 scripts/regime_gate_check.py --sample 400 --seed 42
输出:
  output/regime_gate_stats.csv / regime_gate_diffs.csv
"""
import argparse
import csv
import math
import os
import random

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache", "kline_sina")
OUT = os.path.join(ROOT, "output")
MINB = 300
STEP = 5
COST = 0.30
H = [5, 10, 20]
PRE = ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")


def load(path, cutoff):
    m = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        rd = csv.reader(f)
        try:
            hd = next(rd)
        except StopIteration:
            return [], np.array([])
        try:
            i_d, i_c = hd.index("date"), hd.index("close")
        except ValueError:
            return [], np.array([])
        for row in rd:
            if len(row) <= max(i_d, i_c):
                continue
            d = row[i_d].strip()
            if len(d) != 10 or d > cutoff:
                continue
            try:
                c = float(row[i_c])
            except ValueError:
                continue
            if c > 0:
                m[d] = c
    ds = sorted(m)
    return ds, np.array([m[d] for d in ds], float)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2026-07-31", help="只用该日期及以前的K线")
    ap.add_argument("--sample", type=int, default=0, help="随机抽N只（0=全总体）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    idates, iclose = load(os.path.join(CACHE, "sh000300.csv"), args.cutoff)
    if len(iclose) < 120:
        print("缺少沪深300缓存")
        return 1
    n = len(iclose)
    lights = [None] * n
    for t in range(59, n):
        px = iclose[t]
        ma20 = iclose[t - 19:t + 1].mean()
        ma60 = iclose[t - 59:t + 1].mean()
        prev = iclose[t - 24:t - 4].mean()
        lights[t] = "绿灯" if (px > ma20 and px > ma60 and ma20 >= prev) else (
            "红灯" if not px > ma20 else "黄灯")
    valid_t = [t for t in range(n) if lights[t]]
    cross = [(idates[t], lights[t]) for t in valid_t[::STEP]]
    lm = dict(cross)

    files = sorted(f for f in os.listdir(CACHE)
                   if f.endswith(".csv") and not f.startswith("_") and f.startswith(PRE))
    if args.sample:
        files = random.Random(args.seed).sample(files, min(args.sample, len(files)))

    buckets = {(l, h): [] for l in ("红灯", "黄灯", "绿灯") for h in H}
    day = {}
    nstock = 0
    for f in files:
        d, c = load(os.path.join(CACHE, f), args.cutoff)
        if len(d) < MINB:
            continue
        nstock += 1
        m = {dd: i for i, dd in enumerate(d)}
        ns = len(d)
        for cd, L in cross:
            i = m.get(cd)
            if i is None:
                continue
            p0 = c[i]
            for h in H:
                j = i + h
                if j >= ns:
                    continue
                v = (c[j] / p0 - 1) * 100 - COST
                buckets[(L, h)].append(v)
                s = day.setdefault((cd, h), [0.0, 0])
                s[0] += v
                s[1] += 1
    DM = {h: {cd: s / cnt for (cd, hh), (s, cnt) in day.items() if hh == h and cnt} for h in H}

    print(f"样本：{nstock} 只主板股 × {len(cross)} 个截面日，{cross[0][0]} ~ {cross[-1][0]}")
    cl = [l for _, l in cross]
    print(f"截面日灯色：红 {cl.count('红灯')} / 黄 {cl.count('黄灯')} / 绿 {cl.count('绿灯')}")
    print("\n" + "=" * 96)
    print(f"{'灯色':<6}{'h':>4}{'n':>10}{'胜率%':>9}{'均值':>9}{'中位数':>9}{'std':>9}{'p05':>10}{'p95':>10}")
    print("=" * 96)
    rows = []
    for L in ("绿灯", "黄灯", "红灯"):
        for h in H:
            a = np.array(buckets[(L, h)])
            if not len(a):
                continue
            r = dict(light=L, h=h, n=len(a), winrate=(a > 0).mean() * 100, mean=a.mean(),
                     median=float(np.median(a)), std=a.std(ddof=1),
                     p05=float(np.percentile(a, 5)), p95=float(np.percentile(a, 95)))
            rows.append(r)
            print(f"{L:<6}{h:>4}{len(a):>10}{r['winrate']:>9.3f}{r['mean']:>9.3f}"
                  f"{r['median']:>9.3f}{r['std']:>9.3f}{r['p05']:>10.3f}{r['p95']:>10.3f}")
        print("-" * 96)

    print("\n绿减红（池化 t 是伪的，以按日聚类为准）")
    rs = np.random.default_rng(0)
    drows = []
    for h in H:
        g = np.array(buckets[("绿灯", h)])
        r = np.array(buckets[("红灯", h)])
        se = math.sqrt(g.var(ddof=1) / len(g) + r.var(ddof=1) / len(r))
        t = (g.mean() - r.mean()) / se
        gd = np.array([DM[h][cd] for cd in DM[h] if lm[cd] == "绿灯"])
        rd = np.array([DM[h][cd] for cd in DM[h] if lm[cd] == "红灯"])
        se2 = math.sqrt(gd.var(ddof=1) / len(gd) + rd.var(ddof=1) / len(rd))
        t2 = (gd.mean() - rd.mean()) / se2
        bs = np.array([rs.choice(gd, len(gd), True).mean() - rs.choice(rd, len(rd), True).mean()
                       for _ in range(10000)])
        print(f" h={h:>2}: 绿{g.mean():+.3f} 红{r.mean():+.3f} 差{g.mean()-r.mean():+.3f}"
              f" | 池化t={t:+.2f}(伪)"
              f" | 按日聚类 t={t2:+.2f} p={math.erfc(abs(t2)/math.sqrt(2)):.3f}"
              f" CI[{np.percentile(bs,2.5):+.3f},{np.percentile(bs,97.5):+.3f}]"
              f" 有效样本 绿{len(gd)}天/红{len(rd)}天")
        drows.append(dict(h=h, green_mean=g.mean(), red_mean=r.mean(), diff=g.mean() - r.mean(),
                          t_pooled=t, diff_day=gd.mean() - rd.mean(), t_day=t2,
                          p_day=math.erfc(abs(t2) / math.sqrt(2)),
                          ci_lo=np.percentile(bs, 2.5), ci_hi=np.percentile(bs, 97.5),
                          n_green_days=len(gd), n_red_days=len(rd)))

    print("\n稳健口径（中位数/胜率，不受极端值影响）")
    for h in H:
        g = np.array(buckets[("绿灯", h)])
        r = np.array(buckets[("红灯", h)])
        print(f" h={h:>2}: 中位数 绿{np.median(g):+.3f} 红{np.median(r):+.3f}"
              f" 差{np.median(g)-np.median(r):+.3f}"
              f" | 胜率 绿{(g>0).mean()*100:.2f}% 红{(r>0).mean()*100:.2f}%"
              f" 差{((g>0).mean()-(r>0).mean())*100:+.2f}pp")

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "regime_gate_stats.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, "regime_gate_diffs.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(drows[0]))
        w.writeheader()
        w.writerows(drows)
    print(f"\n[输出] {OUT}/regime_gate_stats.csv, regime_gate_diffs.csv")
    print("\n已知边界：幸存者偏差（缓存是今日存量名单，退市股整只缺席，且退市集中在红灯期"
          "→ 本实验高估红灯表现）；只测随机裸买入，未测与选股信号的交互；"
          "单一市场单一时段（约4年）；未处理涨跌停不可成交。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
