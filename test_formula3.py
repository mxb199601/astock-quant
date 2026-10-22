# -*- coding: utf-8 -*-
"""用数据检验"公式三"(大盘多头+强于大盘+趋势+多头排列+量能+涨幅适中)。
回测: 历史上每次个股"首次满足公式三"后, 20/60日真实收益 vs 全市场基准。并统计今日通过数量。
"""
import glob, os, warnings
import numpy as np, pandas as pd
warnings.simplefilter("ignore")
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache", "kline_sina")

def load(sym):
    fp = os.path.join(CACHE, sym + ".csv")
    if not os.path.exists(fp): return None
    df = pd.read_csv(fp, dtype={"date": str})
    return df if len(df) >= 320 else None

idx = load("sh000300")
idx["iclose"] = idx["close"]; idx["iret20"] = (idx["iclose"]/idx["iclose"].shift(20)-1)*100
idx["iup"] = idx["iclose"] > idx["iclose"].rolling(20).mean()
imap = dict(zip(idx["date"].astype(str), zip(idx["iret20"], idx["iup"])))

sig_rows, base_rows = [], []
files = glob.glob(os.path.join(CACHE, "sz*.csv")) + glob.glob(os.path.join(CACHE, "sh6*.csv")) + glob.glob(os.path.join(CACHE, "sh0*.csv"))
nstk = 0; today_pass = 0; today_total = 0
for fp in files:
    sym = os.path.basename(fp)[:-4]
    if sym == "sh000300": continue
    df = load(sym)
    if df is None: continue
    nstk += 1
    c = df["close"]; dates = df["date"].astype(str).values
    ma5,ma10,ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
    ma60,ma250 = c.rolling(60).mean(), c.rolling(250).mean()
    vr = df["vol"].rolling(5).mean()/df["vol"].rolling(60).mean()
    zf60 = (c/c.shift(60)-1)*100
    ret20 = (c/c.shift(20)-1)*100
    iret = np.array([imap.get(d,(np.nan,False))[0] for d in dates])
    iup  = np.array([imap.get(d,(np.nan,False))[1] for d in dates])
    trend = (c>ma250)&(ma250>=ma250.shift(20))
    bull  = (ma5>ma10)&(ma10>ma20)&(ma20>ma60)
    sig = (pd.Series(iup,index=df.index)) & (ret20>pd.Series(iret,index=df.index)) & trend & bull & (vr>1)&(vr<3) & (zf60>0)&(zf60<50)
    sig = sig.fillna(False)
    fresh = sig & (~sig.shift(1, fill_value=False))
    cc = c.values
    for i in np.where(fresh.values)[0]:
        row = {}
        for n in (20,60):
            row[n] = (cc[i+n]/cc[i]-1)*100 if i+n < len(cc) else np.nan
        row["pre"] = dates[i] < "2025-01-01"
        sig_rows.append(row)
    vidx = df.index[ma250.notna()]
    for i in vidx[::5]:
        if i+60 < len(cc):
            base_rows.append({20:(cc[i+20]/cc[i]-1)*100, 60:(cc[i+60]/cc[i]-1)*100})
    today_total += 1
    if bool(sig.iloc[-1]): today_pass += 1

sg, bs = pd.DataFrame(sig_rows), pd.DataFrame(base_rows)
def st(a):
    a=np.asarray([x for x in a if x==x]);
    if len(a)==0: return "n=0"
    w=a[a>0]; l=a[a<=0]; pf=w.mean()/abs(l.mean()) if len(l) and l.mean()!=0 else float('nan')
    return "n=%-4d 胜率%5.1f%% 均值%+6.2f%% 中位%+6.2f%% 盈亏比%4.2f"%(len(a),(a>0).mean()*100,a.mean(),np.median(a),pf)

print("载入 %d 只(本地缓存)\n"%nstk)
print("=== 公式三 信号 vs 全市场基准 ===")
for n in (20,60):
    print("  持有%2d日  信号: %s"%(n, st(sg[n])))
    print("           基准: %s"%st(bs[n]))
print("\n=== 环境拆分(信号20日) ===")
print("  前半(2022-2024): %s"%st(sg[sg["pre"]][20]))
print("  后半(2025-2026): %s"%st(sg[~sg["pre"]][20]))
print("\n=== 今日(最新交易日)通过公式三的数量 ===")
print("  %d / %d 只 (样本内, 占 %.1f%%)"%(today_pass, today_total, today_pass/max(today_total,1)*100))
