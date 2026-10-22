# -*- coding: utf-8 -*-
"""牛股公式回测 v4 —— 数据源切到新浪(原始) + 近似前复权(按送转跳空缺口回溯调整)。
其余分析同 v3: (A)退出规则 (B)突破vs追涨 (C)负面过滤器 (D)环境依赖。
近似前复权说明: 检测相邻日 |今开/昨收-1| 超过该板块涨跌停阈值的跳空, 视为除权事件,
按比例回溯缩放此前价格。非精确(忽略分红细节),但能消除送转造成的伪缺口。
"""
import subprocess, json, os, warnings, time
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
warnings.simplefilter("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache", "kline_sina")
os.makedirs(CACHE, exist_ok=True)
N_SAMPLE = 400
SPLIT = "2025-01-01"

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

def lim_of(sym):
    return 0.205 if (sym.startswith("sz30") or sym.startswith("sh688")) else 0.105  # 涨跌停阈值

def qfq(df, sym):
    o, c = df["open"].values.astype(float), df["close"].values.astype(float)
    n = len(c); adj = np.ones(n); cum = 1.0; lim = lim_of(sym)
    for t in range(n - 1, 0, -1):
        if c[t - 1] > 0:
            gap = o[t] / c[t - 1] - 1.0
            if abs(gap) > lim + 0.02:        # 超过涨跌停 → 视为除权
                cum *= o[t] / c[t - 1]
        adj[t - 1] = cum
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float) * adj
    return df

def get_kline(sym):
    fp = os.path.join(CACHE, sym + ".csv")
    if os.path.exists(fp):
        try:
            df = pd.read_csv(fp, dtype={"date": str})
            if len(df) >= 320:
                return df
        except Exception:
            pass
    u = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=1023" % sym)
    try:
        arr = json.loads(curl(u))
    except Exception:
        return None
    if not arr or len(arr) < 320:
        return None
    df = pd.DataFrame(arr)
    df = df.rename(columns={"day": "date"})
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().reset_index(drop=True)
    df = df.rename(columns={"volume": "vol"})
    df = qfq(df, sym)
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
    entry = c[i]; peak = entry; end = min(i + maxhold, len(c) - 1)
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
    print("沪深A股(剔除ST) %d 只, 抽样 %d 只" % (len(uni), len(sample)))

    R = {"cfix": [], "cstop": [], "ctrail": [], "hstop": [], "htrail": [], "bfix": [], "pre": [], "post": []}
    NF = {"强势": {20: [], 60: []}, "中性": {20: [], 60: []}, "弱势破位": {20: [], 60: []}}
    nstk = 0; spans = []

    def work(sym):
        df = get_kline(sym)
        return (sym, indis(df)) if (df is not None and len(df) >= 320) else None

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = [f.result() for f in as_completed([ex.submit(work, s) for s in sample])]

    for r in results:
        if r is None:
            continue
        nstk += 1; sym, df = r
        c = df["close"].values; dates = df["date"].astype(str).values
        spans.append((dates[0], dates[-1], len(df)))
        for i in np.where(fresh(sig_chase(df)).values)[0]:
            if i + 20 < len(c):
                rr = (c[i + 20] / c[i] - 1) * 100
                R["cfix"].append(rr); (R["pre"] if dates[i] < SPLIT else R["post"]).append(rr)
            r1, h1 = sim(c, i, stop=0.08, maxhold=40); R["cstop"].append(r1); R["hstop"].append(h1)
            r2, h2 = sim(c, i, trail=0.12, maxhold=60); R["ctrail"].append(r2); R["htrail"].append(h2)
        for i in np.where(fresh(sig_break(df)).values)[0]:
            if i + 20 < len(c):
                R["bfix"].append((c[i + 20] / c[i] - 1) * 100)
        ma250 = df["ma250"].values; ma5 = df["ma5"].values; ma10 = df["ma10"].values
        ma20 = df["ma20"].values; ma60 = df["ma60"].values
        for i in range(250, len(c) - 60, 5):
            if ma250[i] != ma250[i]:
                continue
            strong = c[i] > ma250[i] and ma5[i] > ma10[i] > ma20[i] > ma60[i]
            weak = c[i] < ma250[i] and ma5[i] < ma10[i]
            grp = "强势" if strong else ("弱势破位" if weak else "中性")
            NF[grp][20].append((c[i + 20] / c[i] - 1) * 100)
            NF[grp][60].append((c[i + 60] / c[i] - 1) * 100)

    if spans:
        print("样本区间约 %s ~ %s, 每只约 %d 根\n" %
              (min(s[0] for s in spans), max(s[1] for s in spans), int(np.median([s[2] for s in spans]))))
    print("成功 %d 只\n" % nstk)

    print("=== (A) 公式一追涨: 退出规则对比 ===")
    print("  纯持有20日 : " + stat(R["cfix"]))
    print("  +8%硬止损  : " + stat(R["cstop"]) + ("  平均持有%.0f日" % np.mean(R["hstop"]) if R["hstop"] else ""))
    print("  +12%移动止盈: " + stat(R["ctrail"]) + ("  平均持有%.0f日" % np.mean(R["htrail"]) if R["htrail"] else ""))
    print("\n=== (B) 公式二突破 vs 公式一追涨 (纯持有20日) ===")
    print("  公式一追涨 : " + stat(R["cfix"]))
    print("  公式二突破 : " + stat(R["bfix"]))
    print("\n=== (C) 负面过滤器: 不同趋势状态前瞻收益 ===")
    for grp in ["强势", "中性", "弱势破位"]:
        print("  [%s] 20日: %s" % (grp, stat(NF[grp][20])))
        print("        60日: %s" % stat(NF[grp][60]))
    print("\n=== (D) 环境依赖: 追涨信号20日 前/后半段 (界=%s) ===" % SPLIT)
    print("  前半段 : " + stat(R["pre"]))
    print("  后半段 : " + stat(R["post"]))

    # 一致性自检: 600610 / 002094 复权后最近收盘 (应贴近真实 7.43 / 4.80)
    print("\n=== 自检: 近似前复权后最近收盘 ===")
    for sym in ["sh600610", "sz002094"]:
        df = get_kline(sym)
        if df is not None:
            print("  %s 最近3日收盘: %s" % (sym, [round(x, 2) for x in df["close"].values[-3:]]))

if __name__ == "__main__":
    t0 = time.time()
    main()
    print("\n耗时 %.0fs" % (time.time() - t0))
