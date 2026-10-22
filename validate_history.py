# -*- coding: utf-8 -*-
"""历史快照验证: 回到固定过去日期, 用公式三(技术核心)选股, 看其后20/60日真实表现。
日期提前定死(2025-08-15 / 2025-11-17 / 2026-02-17), 不挑好看的。历史前复权数据无未来函数。
注: 历史PE/PB拿不到, 故此处只验证公式三技术核心(大盘多头+强于大盘+趋势+多头排列+量能+涨幅), 不含估值否决。
"""
import glob, os, warnings
import numpy as np, pandas as pd
warnings.simplefilter("ignore")
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache", "kline_sina")
DATES=["2025-08-15","2025-11-17","2026-02-17"]

def load(sym):
    fp=os.path.join(CACHE,sym+".csv")
    if not os.path.exists(fp): return None
    df=pd.read_csv(fp,dtype={"date":str})
    return df if len(df)>=320 else None

idx=load("sh000300"); idates=idx["date"].astype(str).values
iclose=idx["close"].values; ima20=idx["close"].rolling(20).mean().values

def idx_at(date):
    pos=np.searchsorted(idates,date,side="right")-1
    if pos<20: return None
    return iclose[pos]>ima20[pos], (iclose[pos]/iclose[pos-20]-1)*100

files=[f for f in glob.glob(os.path.join(CACHE,"sz*.csv"))+glob.glob(os.path.join(CACHE,"sh6*.csv"))+glob.glob(os.path.join(CACHE,"sh0*.csv")) if not f.endswith("sh000300.csv")]

for date in DATES:
    r=idx_at(date)
    if r is None: continue
    iup,iret=r
    picks=[]
    for fp in files:
        df=load(fp.split("/")[-1][:-4])
        if df is None: continue
        d=df["date"].astype(str).values
        pos=np.searchsorted(d,date,side="right")-1
        if pos<250 or pos+60>=len(df): continue
        c=df["close"].values
        ma5=c[pos-4:pos+1].mean(); ma10=c[pos-9:pos+1].mean(); ma20=c[pos-19:pos+1].mean()
        ma60=c[pos-59:pos+1].mean(); ma250=c[pos-249:pos+1].mean(); ma250_20=c[pos-269:pos-19].mean()
        px=c[pos]; rs20=(px/c[pos-20]-1)*100; zf60=(px/c[pos-60]-1)*100
        vr=df["vol"].values[pos-4:pos+1].mean()/df["vol"].values[pos-59:pos+1].mean()
        low60=df["low"].values[pos-59:pos+1].min()
        f3=iup and (rs20>iret) and (px>ma250 and ma250>=ma250_20) and (ma5>ma10>ma20>ma60) \
           and (1<vr<3) and (0<zf60<50) and (px>low60*1.05 and px>ma20)
        if f3:
            r20=(c[pos+20]/px-1)*100; r60=(c[pos+60]/px-1)*100
            picks.append((r60, r20, fp.split("/")[-1][:-4]))
    if not picks:
        print("[%s] 大盘%s 无信号\n"%(date,"多头" if iup else "空头")); continue
    a20=np.array([p[1] for p in picks]); a60=np.array([p[0] for p in picks])
    print("=== %s | 大盘%s ==="%(date,"多头(可操作)" if iup else "空头(本不该买)"))
    print("  选出 %d 只 | 后20日: 胜率%.0f%% 均值%+.2f%% | 后60日: 胜率%.0f%% 均值%+.2f%%"%(
        len(picks),(a20>0).mean()*100,a20.mean(),(a60>0).mean()*100,a60.mean()))
    picks.sort(reverse=True)
    for r60,r20,sym in picks[:4]:
        print("    %s  后20日%+.1f%%  后60日%+.1f%%"%(sym,r20,r60))
    if len(picks)>4:
        print("    ...(共%d只,上面是后60日最好的4只)"%len(picks))
    print()
