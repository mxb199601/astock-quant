# -*- coding: utf-8 -*-
"""牛股自检公式 历史回测 v2
数据源: 新浪(股票池) + 腾讯ifzq(前复权日线)。Python 经 curl 子进程抓数。
复现通达信公式一的技术核心(去掉需流通股本的换手率T5): T1站上年线+上翘 / T2多头排列 /
T3不创新低+站上20日 / T4量能温和放大 / T6 60日涨幅适中。
统计信号日后 5/10/20/60 交易日真实收益、胜率,并与全市场基准对比。
"""
import subprocess, json, time
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

N_SAMPLE = 400
FWD = [5, 10, 20, 60]

def curl(url):
    r = subprocess.run(["curl", "-s", "-m", "15", "-A", "Mozilla/5.0",
                        "-e", "https://finance.sina.com.cn", url], capture_output=True)
    return r.stdout.decode("utf-8", "ignore")

def get_universe(pages=45, num=100):
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
    return codes

def get_kline(sym):
    u = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,2020-01-01,2026-06-17,2000,qfq" % sym)
    js = json.loads(curl(u))
    data = (js.get("data") or {}).get(sym) or {}
    rows = data.get("qfqday") or data.get("day") or []
    if len(rows) < 320:
        return None
    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "close", "high", "low", "vol"])
    for c in ["open", "close", "high", "low", "vol"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna().reset_index(drop=True)

def add_signal(df):
    c = df["close"]
    ma5, ma10, ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
    ma60, ma250 = c.rolling(60).mean(), c.rolling(250).mean()
    T1 = (c > ma250) & (ma250 >= ma250.shift(20))
    T2 = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
    T3 = (c > df["low"].rolling(60).min() * 1.05) & (c > ma20)
    vr = df["vol"].rolling(5).mean() / df["vol"].rolling(60).mean()
    T4 = (vr > 1) & (vr < 3)
    zf = (c / c.shift(60) - 1) * 100
    T6 = (zf > 0) & (zf < 60)
    df["ma250"] = ma250
    df["sig"] = (T1 & T2 & T3 & T4 & T6).fillna(False)
    df["fresh"] = df["sig"] & (~df["sig"].shift(1).fillna(False))
    return df

def fwd(df, i):
    c = df["close"].values
    return {n: (c[i + n] / c[i] - 1) * 100 if i + n < len(c) else np.nan for n in FWD}

def main():
    uni = get_universe()
    step = max(1, len(uni) // N_SAMPLE)
    sample = uni[::step][:N_SAMPLE]
    print("沪深A股(剔除ST)抓到 %d 只, 抽样 %d 只" % (len(uni), len(sample)))

    sig_rows, base_rows, ok, spans = [], [], 0, []

    def work(sym):
        try:
            df = get_kline(sym)
            return (sym, add_signal(df)) if df is not None else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        for fut in as_completed([ex.submit(work, s) for s in sample]):
            r = fut.result()
            if r is None:
                continue
            ok += 1
            sym, df = r
            spans.append((df["date"].iloc[0], df["date"].iloc[-1], len(df)))
            valid = df.index[df["ma250"].notna()]
            for i in df.index[df["fresh"]]:
                sig_rows.append(fwd(df, i))
            for i in valid[::5]:
                base_rows.append(fwd(df, i))

    sg, bs = pd.DataFrame(sig_rows), pd.DataFrame(base_rows)
    if spans:
        print("样本日线区间约 %s ~ %s, 每只约 %d 根" %
              (min(s[0] for s in spans), max(s[1] for s in spans), int(np.median([s[2] for s in spans]))))
    print("成功 %d 只 | 信号事件 %d | 基准样本 %d\n" % (ok, len(sg), len(bs)))

    print("%-5s | %-30s | %-30s | 超额" % ("持有", "【信号】胜率 / 均值 / 中位", "【基准】胜率 / 均值 / 中位"))
    print("-" * 86)
    for n in FWD:
        s, b = sg[n].dropna(), bs[n].dropna()
        print("%-5s | %5.1f%% / %+6.2f%% / %+6.2f%%        | %5.1f%% / %+6.2f%% / %+6.2f%%        | %+5.2f%%" % (
            "%d日" % n, (s > 0).mean() * 100, s.mean(), s.median(),
            (b > 0).mean() * 100, b.mean(), b.median(), s.mean() - b.mean()))

    print("\n=== 用户持仓验证(是否能被公式选中) ===")
    for sym in ["sz002094", "sh600610"]:
        try:
            df = add_signal(get_kline(sym))
            last = df.iloc[-1]
            fd = df["date"][df["fresh"]].tolist()
            print("%s: 最新日 %s 信号=%s | 历史触发 %d 次 | 最近一次 %s" %
                  (sym, last["date"], bool(last["sig"]), int(df["fresh"].sum()), fd[-1] if fd else "从未"))
        except Exception as e:
            print("%s: 失败 %s" % (sym, e))

if __name__ == "__main__":
    t0 = time.time()
    main()
    print("\n耗时 %.0fs" % (time.time() - t0))
