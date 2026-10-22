# -*- coding: utf-8 -*-
"""牛股公式回测 v3 —— 验证改进方向:
 (A) 入场信号 + 退出规则(硬止损/移动止盈) 能否改善纯固定持有
 (B) 公式二(蓄势突破) vs 公式一(追涨)
 (C) 把条件当"负面过滤器":强势组 vs 弱势破位组 的前瞻收益差
 (D) 环境依赖:窗口前半 vs 后半
数据: 新浪(池) + 腾讯(前复权日线), 本地缓存。
"""
import subprocess, json, os, warnings
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
warnings.simplefilter("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache", "kline")
os.makedirs(CACHE, exist_ok=True)
N_SAMPLE = 400
SPLIT = "2025-01-01"   # 环境前后分界

def curl(url):
    r = subprocess.run(["curl", "-s", "-m", "15", "-A", "Mozilla/5.0",
                        "-e", "https://finance.sina.com.cn", url], capture_output=True)
    return r.stdout.decode("utf-8", "ignore")

def get_universe(pages=45, num=100):
    fp = os.path.join(CACHE, "_universe.json")
    if os.path.exists(fp):
        return json.load(open(fp))
    codes = []
    for p in range(1, pages + 1):
        u = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page=%d&num=%d&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=page" % (p, num))
        try:
            arr = json.loads(curl(u))
        except Exception:
            continue
        if not arr:
            break
        for d in arr:
            sym, name = d.get("symbol", ""), d.get("name", "")
            if "ST" in name.upper():
                continue
            if sym.startswith("sh6") or sym.startswith("sz00") or sym.startswith("sz30"):
                codes.append(sym)
    json.dump(codes, open(fp, "w"))
    return codes

def get_kline(sym):
    fp = os.path.join(CACHE, sym + ".csv")
    if os.path.exists(fp):
        try:
            df = pd.read_csv(fp, dtype={"date": str})
            if len(df) >= 320:
                return df
        except Exception:
            pass
    u = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,2020-01-01,2026-06-17,2000,qfq" % sym)
    try:
        js = json.loads(curl(u))
    except Exception:
        return None
    data = (js.get("data") or {}).get(sym) or {}
    rows = data.get("qfqday") or data.get("day") or []
    if len(rows) < 320:
        return None
    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "close", "high", "low", "vol"])
    for c in ["open", "close", "high", "low", "vol"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().reset_index(drop=True)
    df.to_csv(fp, index=False)
    return df

def indis(df):
    c = df["close"]
    df["ma5"], df["ma10"], df["ma20"] = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
    df["ma60"], df["ma250"] = c.rolling(60).mean(), c.rolling(250).mean()
    df["vr"] = df["vol"].rolling(5).mean() / df["vol"].rolling(60).mean()
    df["zf60"] = (c / c.shift(60) - 1) * 100
    return df

def sig_chase(df):
    c = df["close"]
    T1 = (c > df["ma250"]) & (df["ma250"] >= df["ma250"].shift(20))
    T2 = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]) & (df["ma20"] > df["ma60"])
    T3 = (c > df["low"].rolling(60).min() * 1.05) & (c > df["ma20"])
    T4 = (df["vr"] > 1) & (df["vr"] < 3)
    T6 = (df["zf60"] > 0) & (df["zf60"] < 60)
    return (T1 & T2 & T3 & T4 & T6).fillna(False)

def sig_break(df):
    c = df["close"]
    mas = pd.concat([df["ma5"], df["ma10"], df["ma20"], df["ma60"]], axis=1)
    jc = (mas.max(axis=1) - mas.min(axis=1)) / df["ma60"] * 100
    a1 = df["ma250"] >= df["ma250"].shift(10)
    a2 = jc.shift(3) < 6
    a3 = (c > df["ma250"]) & (c.shift(1) <= df["ma250"].shift(1))
    a4 = df["vol"] > df["vol"].rolling(5).mean() * 1.5
    return (a1 & a2 & a3 & a4).fillna(False)

def fresh(sig):
    return sig & (~sig.shift(1, fill_value=False))

def sim(c, i, stop=None, trail=None, maxhold=40):
    entry = c[i]; peak = entry
    end = min(i + maxhold, len(c) - 1)
    for j in range(i + 1, end + 1):
        px = c[j]; peak = max(peak, px)
        if stop and px <= entry * (1 - stop):
            return (px / entry - 1) * 100, j - i
        if trail and px <= peak * (1 - trail):
            return (px / entry - 1) * 100, j - i
    return (c[end] / entry - 1) * 100, end - i

def stat(a):
    a = np.asarray([x for x in a if x == x])
    if len(a) == 0:
        return "n=0"
    return "n=%-5d 胜率%5.1f%% 均值%+6.2f%% 中位%+6.2f%%" % (len(a), (a > 0).mean() * 100, a.mean(), np.median(a))

def main():
    uni = get_universe()
    step = max(1, len(uni) // N_SAMPLE)
    sample = uni[::step][:N_SAMPLE]
    print("沪深A股(剔除ST) %d 只, 抽样 %d 只\n" % (len(uni), len(sample)))

    R = {"chase_fix20": [], "chase_stop": [], "chase_trail": [], "chase_hold_stop": [], "chase_hold_trail": [],
         "break_fix20": [], "chase_pre": [], "chase_post": []}
    NF = {"强势": {20: [], 60: []}, "中性": {20: [], 60: []}, "弱势破位": {20: [], 60: []}}
    nstk = 0

    def work(sym):
        df = get_kline(sym)
        if df is None or len(df) < 320:
            return None
        return (sym, indis(df))

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = [f.result() for f in as_completed([ex.submit(work, s) for s in sample])]

    for r in results:
        if r is None:
            continue
        nstk += 1
        sym, df = r
        c = df["close"].values
        dates = df["date"].values
        ch, br = sig_chase(df), sig_break(df)
        for i in np.where(fresh(ch).values)[0]:
            if i + 20 < len(c):
                R["chase_fix20"].append((c[i + 20] / c[i] - 1) * 100)
                (R["chase_pre"] if dates[i] < SPLIT else R["chase_post"]).append((c[i + 20] / c[i] - 1) * 100)
            r1, h1 = sim(c, i, stop=0.08, maxhold=40);  R["chase_stop"].append(r1); R["chase_hold_stop"].append(h1)
            r2, h2 = sim(c, i, trail=0.12, maxhold=60);  R["chase_trail"].append(r2); R["chase_hold_trail"].append(h2)
        for i in np.where(fresh(br).values)[0]:
            if i + 20 < len(c):
                R["break_fix20"].append((c[i + 20] / c[i] - 1) * 100)
        # 负面过滤器: 每5天取样, 按当日趋势状态分组
        for i in range(250, len(c) - 60, 5):
            if not (df["ma250"].notna().values[i]):
                continue
            strong = c[i] > df["ma250"].values[i] and df["ma5"].values[i] > df["ma10"].values[i] > df["ma20"].values[i] > df["ma60"].values[i]
            weak = c[i] < df["ma250"].values[i] and df["ma5"].values[i] < df["ma10"].values[i]
            grp = "强势" if strong else ("弱势破位" if weak else "中性")
            NF[grp][20].append((c[i + 20] / c[i] - 1) * 100)
            NF[grp][60].append((c[i + 60] / c[i] - 1) * 100)

    print("成功 %d 只\n" % nstk)
    print("=== (A) 公式一追涨: 退出规则对比 ===")
    print("  纯持有20日 : " + stat(R["chase_fix20"]))
    print("  +8%硬止损  : " + stat(R["chase_stop"]) + "  平均持有%.0f日" % np.mean(R["chase_hold_stop"]))
    print("  +12%移动止盈: " + stat(R["chase_trail"]) + "  平均持有%.0f日" % np.mean(R["chase_hold_trail"]))

    print("\n=== (B) 公式二突破 vs 公式一追涨 (纯持有20日) ===")
    print("  公式一追涨 : " + stat(R["chase_fix20"]))
    print("  公式二突破 : " + stat(R["break_fix20"]))

    print("\n=== (C) 负面过滤器: 不同趋势状态的前瞻收益 ===")
    for grp in ["强势", "中性", "弱势破位"]:
        print("  [%s] 20日: %s" % (grp, stat(NF[grp][20])))
        print("  %s 60日: %s" % ("    " + " " * len(grp), stat(NF[grp][60])))

    print("\n=== (D) 环境依赖: 追涨信号20日收益 前/后半段 (界=%s) ===" % SPLIT)
    print("  前半段 : " + stat(R["chase_pre"]))
    print("  后半段 : " + stat(R["chase_post"]))

if __name__ == "__main__":
    import time
    t0 = time.time()
    main()
    print("\n耗时 %.0fs" % (time.time() - t0))
