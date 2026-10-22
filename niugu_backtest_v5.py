# -*- coding: utf-8 -*-
"""牛股公式回测 v5 —— 能否真正提高"质量"(不只是胜率)。
用本地缓存(已前复权)+ 沪深300大盘择时。对比策略并同时给出 胜率/均值/中位/盈亏比/期望,
以及前后半段稳健性,警惕过拟合与"虚荣胜率"。
"""
import subprocess, json, os, glob, warnings, time
import numpy as np, pandas as pd
warnings.simplefilter("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache", "kline_sina")
SPLIT = "2025-01-01"

def curl(url):
    r = subprocess.run(["curl", "-s", "-m", "15", "-A", "Mozilla/5.0",
                        "-e", "https://finance.sina.com.cn", url], capture_output=True)
    return r.stdout.decode("utf-8", "ignore")

def load_index_regime():
    """沪深300 站上20日线 = 大盘多头, 返回 date->bool"""
    u = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "CN_MarketData.getKLineData?symbol=sh000300&scale=240&ma=no&datalen=1023")
    arr = json.loads(curl(u))
    idf = pd.DataFrame(arr)
    idf["close"] = pd.to_numeric(idf["close"], errors="coerce")
    idf["ma20"] = idf["close"].rolling(20).mean()
    idf["up"] = idf["close"] > idf["ma20"]
    return dict(zip(idf["day"].astype(str), idf["up"]))

def indis(df):
    c = df["close"]
    df["ma5"], df["ma10"], df["ma20"] = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
    df["ma60"], df["ma250"] = c.rolling(60).mean(), c.rolling(250).mean()
    df["vr"] = df["vol"].rolling(5).mean() / df["vol"].rolling(60).mean()
    df["zf60"] = (c / c.shift(60) - 1) * 100
    return df

def fresh(sig):
    sig = sig.fillna(False)
    return sig & (~sig.shift(1, fill_value=False))

def sig_chase(df):
    c = df["close"]
    return ((c > df["ma250"]) & (df["ma250"] >= df["ma250"].shift(20)) &
            (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]) & (df["ma20"] > df["ma60"]) &
            (c > df["low"].rolling(60).min() * 1.05) & (c > df["ma20"]) &
            (df["vr"] > 1) & (df["vr"] < 3) & (df["zf60"] > 0) & (df["zf60"] < 60)).fillna(False)

def sig_revert(df):
    """上升趋势中的回调: 年线之上 + 60日线上行 + 回调到 ma20 与 ma60 之间"""
    c = df["close"]
    up = (c > df["ma250"]) & (df["ma60"] >= df["ma60"].shift(20)) & (df["ma250"] >= df["ma250"].shift(20))
    dip = (c < df["ma20"]) & (c > df["ma60"])
    return (up & dip).fillna(False)

def sim_tp(c, i, tp=0.06, stop=0.06, maxhold=20):
    entry = c[i]; end = min(i + maxhold, len(c) - 1)
    for j in range(i + 1, end + 1):
        px = c[j]
        if px >= entry * (1 + tp): return (px / entry - 1) * 100
        if px <= entry * (1 - stop): return (px / entry - 1) * 100
    return (c[end] / entry - 1) * 100

def hold(c, i, n=20):
    return (c[i + n] / c[i] - 1) * 100 if i + n < len(c) else np.nan

def run(strategies):
    files = [f for f in glob.glob(os.path.join(CACHE, "*.csv"))]
    reg = load_index_regime()
    acc = {k: [] for k in strategies}     # list of (ret, date)
    nstk = 0
    for fp in files:
        try:
            df = pd.read_csv(fp, dtype={"date": str})
        except Exception:
            continue
        if len(df) < 320:
            continue
        nstk += 1
        df = indis(df)
        c = df["close"].values; dates = df["date"].astype(str).values
        ch = fresh(sig_chase(df)).values
        rv = fresh(sig_revert(df)).values
        for i in range(len(c)):
            d = dates[i]; up = reg.get(d, True)
            if ch[i]:
                if "追涨_持有20" in strategies:
                    r = hold(c, i, 20);  acc["追涨_持有20"].append((r, d)) if r == r else None
                if "追涨_止盈6止损6" in strategies:
                    acc["追涨_止盈6止损6"].append((sim_tp(c, i, 0.06, 0.06, 20), d))
                if "追涨_大盘择时" in strategies and up:
                    r = hold(c, i, 20);  acc["追涨_大盘择时"].append((r, d)) if r == r else None
            if rv[i]:
                if "回调入场_持有20" in strategies:
                    r = hold(c, i, 20);  acc["回调入场_持有20"].append((r, d)) if r == r else None
                if "回调入场_大盘择时" in strategies and up:
                    r = hold(c, i, 20);  acc["回调入场_大盘择时"].append((r, d)) if r == r else None
    print("载入 %d 只(本地缓存), 大盘择时基于沪深300>MA20\n" % nstk)
    return acc

def report(acc):
    hdr = "%-18s | %-5s | %-6s | %-7s | %-7s | %-6s | %-7s | %-7s"
    print(hdr % ("策略", "笔数", "胜率", "均值", "中位", "盈亏比", "前半均值", "后半均值"))
    print("-" * 92)
    for k, rows in acc.items():
        a = np.array([r for r, d in rows if r == r])
        if len(a) == 0:
            print("%-18s | n=0" % k); continue
        wins, losses = a[a > 0], a[a <= 0]
        pf = (wins.mean() / abs(losses.mean())) if len(losses) and losses.mean() != 0 else float("nan")
        pre = np.array([r for r, d in rows if r == r and d < SPLIT])
        post = np.array([r for r, d in rows if r == r and d >= SPLIT])
        print("%-18s | %-5d | %5.1f%% | %+6.2f%% | %+6.2f%% | %5.2f | %+6.2f%% | %+6.2f%%" % (
            k, len(a), (a > 0).mean() * 100, a.mean(), np.median(a), pf,
            pre.mean() if len(pre) else float("nan"), post.mean() if len(post) else float("nan")))

if __name__ == "__main__":
    t0 = time.time()
    strategies = ["追涨_持有20", "追涨_止盈6止损6", "追涨_大盘择时", "回调入场_持有20", "回调入场_大盘择时"]
    acc = run(strategies)
    report(acc)
    print("\n注: 盈亏比=平均盈利/平均亏损; 前/后半段界=%s; 仍受幸存者偏差影响(无退市股)" % SPLIT)
    print("耗时 %.0fs" % (time.time() - t0))
