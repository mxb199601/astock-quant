# -*- coding: utf-8 -*-
"""交叉验证复核：MENGα × 红绿灯（第三份独立实现）。

重点：
  1) 独立复算特征/z/合成，并与 lib.own_alpha 逐点对照
  2) 同时算「系统实现版」与「题面公式字面版」两套 MENGα，看名单/结论差异
  3) 截面规模门（早期 18~566 只的截面是噪声源）—— 这是两份实现分歧的根因
  4) 聚类 t / bootstrap / 非重叠 / 多重检验
"""
from __future__ import annotations
import bisect, glob, math, os, sys, time
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CACHE = os.path.join(ROOT, "cache", "kline_sina")
OUT = os.path.join(ROOT, "output")
os.makedirs(OUT, exist_ok=True)
END = "2026-07-31"
MIN_TOTAL, MIN_AT_T, STEP = 300, 80, 5
HORIZONS = [5, 10, 20]
COST = 0.30
PREF = ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")
Z_COLS = ["ret5","ret20","ret60","bias20","bias60","ma20_slope","rsi","vr",
          "dist_low","dist_high","dd60","bounce","excess20","up_vol5","cd","vol_ratio_std"]
LOG = []
def say(s=""):
    print(s, flush=True); LOG.append(str(s))


def sdiv(a, b, d):
    a = np.asarray(a, float); b = np.asarray(b, float)
    out = np.full(a.shape, float(d))
    ok = np.isfinite(b) & (b != 0)
    out[ok] = a[ok] / b[ok]
    return out


def load_sym(fp):
    d = pd.read_csv(fp, usecols=["date","open","high","low","close","vol"])
    d["date"] = d["date"].astype(str)
    d = d[d["date"] <= END]
    d = d.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    for c in ("high","low","close","vol"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d["close"] > 0].reset_index(drop=True)
    return d if len(d) >= MIN_TOTAL else None


def feats(df):
    c = df["close"].astype(float); v = df["vol"].astype(float)
    hi = df["high"].astype(float); lo = df["low"].astype(float)
    ret5 = (c / c.shift(5) - 1) * 100
    ret20 = (c / c.shift(20) - 1) * 100
    ret60 = (c / c.shift(60) - 1) * 100
    ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean()
    ma20p = c.shift(5).rolling(20).mean()
    bias20 = (c.values / ma20.values - 1) * 100
    bias60 = (c.values / ma60.values - 1) * 100
    slope = (ma20.values / ma20p.values - 1) * 100
    dl = c.diff()
    g = dl.clip(lower=0).rolling(14).mean().values
    ls = (-dl.clip(upper=0)).rolling(14).mean().values
    with np.errstate(all="ignore"):
        rsi = 100 - 100 / (1 + g / ls)
    zl = ls < 1e-12
    rsi = np.where(zl, np.where(g > 0, 100.0, 50.0), rsi)
    vr = (v.rolling(5).mean() / (v.rolling(60).mean() + 1e-9)).values
    low20 = lo.rolling(20).min().values; high20 = hi.rolling(20).max().values
    high60 = hi.rolling(60).max().values
    dist_low = np.where(low20 > 0, (c.values / low20 - 1) * 100, 0.0)
    dist_high = (high20 / c.values - 1) * 100
    dd60 = np.where(high60 > 0, (c.values / high60 - 1) * 100, 0.0)
    bounce = (ret5 - ret20).values
    up = (c >= c.shift(1)).astype(float)
    up_vol5 = sdiv((v * up).rolling(5).sum().values, v.rolling(5).sum().values, 0.5)
    dn = (c < c.shift(1))
    cd = np.minimum(dn.groupby((~dn).cumsum()).cumsum().astype(float).values, 10.0)
    lc = np.log(c.values + 1e-9)
    dlg = pd.Series(lc).diff()
    vrs = sdiv(dlg.rolling(20).std(ddof=0).values * 100, dlg.rolling(60).std(ddof=0).values * 100, 1.0)
    return pd.DataFrame({
        "date": df["date"].values, "close": c.values,
        "ret5": np.round(ret5.values,3), "ret20": np.round(ret20.values,3),
        "ret60": np.round(ret60.values,3), "bias20": np.round(bias20,3),
        "bias60": np.round(bias60,3), "ma20_slope": np.round(slope,3),
        "rsi": np.round(rsi,2), "vr": np.round(vr,3),
        "dist_low": np.round(dist_low,3), "dist_high": np.round(dist_high,3),
        "dd60": np.round(dd60,3), "bounce": np.round(bounce,3),
        "up_vol5": np.round(up_vol5,3), "cd": cd,
        "vol_ratio_std": np.round(vrs,3)})


def compose(panel, literal=False):
    p = panel
    gb = p.groupby("date", sort=False)
    Zs = {}
    for col in Z_COLS:
        mu = gb[col].transform("mean").values; sd = gb[col].transform("std").values
        Zs[col] = np.where(sd < 1e-12, 0.0, (p[col].values - mu) / sd)
    heat = Zs["ret60"]*.40 + Zs["bias60"]*.25 + Zs["excess20"]*.20 + Zs["ma20_slope"]*.10 + Zs["rsi"]*.05
    wash = -Zs["dd60"]*.50 + Zs["cd"]*.10 - Zs["ret20"]*.30 + Zs["dist_high"]*.10
    spark = Zs["bounce"]*.55 + Zs["up_vol5"]*.20 - Zs["vol_ratio_std"]*.10 + (p["ret5"].values > 0)*0.15
    if not literal:  # 系统实现：spark 二次标准化
        s = pd.Series(spark, index=p.index)
        gs = s.groupby(p["date"].values)
        spark = ((s - gs.transform("mean")) / (gs.transform("std") + 1e-9)).values
    if literal:
        crowd = Zs["vr"]*.45 + Zs["bias20"]*.55
    else:      # 系统实现：crowd 的 z 先 clip(-3,3)
        crowd = np.clip(Zs["vr"],-3,3)*.45 + np.clip(Zs["bias20"],-3,3)*.55
    return -0.60*heat + 0.15*wash + 0.20*spark - 0.10*crowd


def welch(a, b):
    a = np.asarray(a,float); b = np.asarray(b,float)
    n1,n2 = len(a),len(b)
    if n1 < 2 or n2 < 2: return (np.nan,)*4
    v1,v2 = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(v1/n1 + v2/n2)
    if se <= 0: return (np.nan,)*4
    t = (a.mean()-b.mean())/se
    df = (v1/n1+v2/n2)**2 / ((v1/n1)**2/(n1-1) + (v2/n2)**2/(n2-1))
    return t, df, math.erfc(abs(t)/math.sqrt(2)), student_p(t, df)


def _betacf(a,b,x,itmax=400,eps=3e-14):
    qab,qap,qam = a+b,a+1,a-1
    cc,dd = 1.0, 1-qab*x/qap
    if abs(dd) < 1e-30: dd = 1e-30
    dd = 1/dd; h = dd
    for m in range(1, itmax+1):
        m2 = 2*m
        aa = m*(b-m)*x/((qam+m2)*(a+m2))
        dd = 1+aa*dd
        if abs(dd)<1e-30: dd=1e-30
        cc = 1+aa/cc
        if abs(cc)<1e-30: cc=1e-30
        dd = 1/dd; h *= dd*cc
        aa = -(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        dd = 1+aa*dd
        if abs(dd)<1e-30: dd=1e-30
        cc = 1+aa/cc
        if abs(cc)<1e-30: cc=1e-30
        dd = 1/dd; de = dd*cc; h *= de
        if abs(de-1) < eps: break
    return h


def betainc(a,b,x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    lb = (math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log(1-x))
    if x < (a+1)/(a+b+2): return math.exp(lb)*_betacf(a,b,x)/a
    return 1-math.exp(lb)*_betacf(b,a,1-x)/b


def student_p(t, df):
    if not np.isfinite(t) or not np.isfinite(df) or df <= 0: return float("nan")
    return betainc(df/2, 0.5, df/(df+t*t))


def boot(a, b, n=10000, seed=7):
    rng = np.random.default_rng(seed)
    a = np.asarray(a,float); b = np.asarray(b,float)
    d = rng.choice(a,(n,len(a)),True).mean(1) - rng.choice(b,(n,len(b)),True).mean(1)
    return float(np.percentile(d,2.5)), float(np.percentile(d,97.5))


def main():
    t0 = time.time()
    say("="*100); say("交叉验证复核 · 第三份独立实现"); say("="*100)

    idx = load_sym(os.path.join(CACHE, "sh000300.csv"))
    ic = idx["close"].astype(float).values
    dts = idx["date"].tolist()
    say(f"沪深300 {len(idx)} 根 {dts[0]} ~ {dts[-1]}")
    lights = {}
    for t in range(59, len(idx)):
        px = ic[t]; ma20 = ic[t-19:t+1].mean(); ma60 = ic[t-59:t+1].mean(); mp = ic[t-24:t-4].mean()
        lights[t] = "绿灯" if (px>ma20 and px>ma60 and ma20>=mp) else ("红灯" if not px>ma20 else "黄灯")
    rows = sorted(lights)[::STEP]
    cs_dates = [dts[i] for i in rows]
    light_map = {dts[i]: lights[i] for i in rows}
    idx20_map = {}
    for i in rows:
        idx20_map[dts[i]] = (ic[i]/ic[i-20]-1)*100 if i >= 20 else 0.0
    from collections import Counter
    say(f"截面日 {len(rows)} 个 {cs_dates[0]} ~ {cs_dates[-1]}  灯色 {dict(Counter(light_map.values()))}")

    files = [f for f in sorted(glob.glob(os.path.join(CACHE,"*.csv")))
             if not os.path.basename(f).startswith("_")
             and os.path.basename(f)[:-4] not in ("sh000300","sh000001")
             and os.path.basename(f)[:-4].startswith(PREF)]
    say(f"主板候选文件 {len(files)}")
    cs_set = set(cs_dates)
    parts = []; nok = nshort = 0; fp_sum = 0.0
    for k, f in enumerate(files):
        df = load_sym(f)
        if df is None:
            nshort += 1; continue
        nok += 1
        fp_sum += float(df["close"].sum())
        ft = feats(df)
        pos = np.arange(len(df))
        keep = ft["date"].isin(cs_set).values & (pos >= MIN_AT_T-1)
        if not keep.any(): continue
        sub = ft[keep].copy(); p = pos[keep]; cl = df["close"].values; n = len(df)
        for h in HORIZONS:
            q = p+h; ok = q < n
            r = np.full(len(p), np.nan)
            r[ok] = (cl[q[ok]]/cl[p[ok]]-1)*100 - COST
            sub[f"r{h}"] = r
        sub["sym"] = os.path.basename(f)[:-4]
        parts.append(sub)
    say(f"入池 {nok} 只 (不足{MIN_TOTAL}根剔 {nshort})   数据指纹(全历史收盘合计)={fp_sum:.2f}")
    panel = pd.concat(parts, ignore_index=True); del parts
    panel["excess20"] = np.round(panel["ret20"].values - panel["date"].map(idx20_map).values, 3)
    panel["light"] = panel["date"].map(light_map)
    fin = np.isfinite(panel[Z_COLS].values).all(axis=1)
    say(f"剔除 NaN/inf 特征观测 {int((~fin).sum())}")
    panel = panel[fin].reset_index(drop=True)
    panel["menga"] = compose(panel, literal=False)
    panel["menga_lit"] = compose(panel, literal=True)
    say(f"面板 {len(panel):,} 行，用时 {time.time()-t0:.0f}s")

    # ---------- 与系统函数逐点对照 ----------
    from lib.own_alpha import compute_raw_features, _z_cols, _compose_scores
    rng = np.random.default_rng(11)
    say("\n" + "-"*100)
    say("【校验1】原始特征 vs lib.own_alpha.compute_raw_features（随机 30 只 × 5 时点）")
    store = {}
    smp = list(rng.choice([os.path.basename(f)[:-4] for f in files], 30, replace=False))
    maxd = 0.0; ncmp = 0
    for s in smp:
        d = load_sym(os.path.join(CACHE, f"{s}.csv"))
        if d is None: continue
        store[s] = d
        for i in rng.choice(np.arange(100, len(d)), 5, replace=False):
            f0 = compute_raw_features(d.iloc[:i+1], 0.0)
            mine = feats(d.iloc[:i+1]).iloc[-1]
            for kk in Z_COLS:
                if kk == "excess20":
                    a = round(f0["ret20"] - 0.0, 3); b = mine["ret20"]
                else:
                    a, b = f0[kk], mine[kk]
                maxd = max(maxd, abs(float(a)-float(b))); ncmp += 1
    say(f"  比对 {ncmp} 个特征值，maxdiff = {maxd:.3e}   -> {'一致' if maxd<1e-9 else '★不一致★'}")

    say("【校验2】截面 z + v5 合成 vs _z_cols/_compose_scores（3 个截面日全截面）")
    for d in ["2023-06-27","2024-11-08","2025-06-13"]:
        sub = panel[panel.date == d]
        if len(sub) < 100: continue
        fr = sub[["sym"]+Z_COLS].copy().reset_index(drop=True)
        fr = _z_cols(fr, Z_COLS); fr = _compose_scores(fr)
        dd = float(np.abs(fr["menga"].values - sub["menga"].values).max())
        rk = pd.Series(fr["menga"].values).rank().corr(pd.Series(sub["menga"].values).rank())
        n10 = max(1, int(len(sub)*0.10))
        s1 = set(sub.sort_values("menga",ascending=False).head(n10)["sym"])
        s2 = set(fr.assign(m=fr["menga"]).sort_values("m",ascending=False).head(n10)["sym"])
        say(f"  {d} n={len(sub)} maxdiff={dd:.2e} rank_corr={rk:.10f} Top10%重合={len(s1&s2)}/{n10}")

    say("【校验3】系统实现版 MENGα vs 题面字面公式版（无 spark 二次标准化 / 无 crowd clip）")
    for d in ["2023-06-27","2024-11-08","2025-06-13"]:
        sub = panel[panel.date == d]
        if len(sub) < 100: continue
        n10 = max(1, int(len(sub)*0.10))
        s1 = set(sub.sort_values("menga",ascending=False).head(n10)["sym"])
        s2 = set(sub.sort_values("menga_lit",ascending=False).head(n10)["sym"])
        rc = sub["menga"].rank().corr(sub["menga_lit"].rank())
        say(f"  {d} rank_corr={rc:.6f}  Top10%重合={len(s1&s2)}/{n10} ({len(s1&s2)/n10*100:.1f}%)")

    # ---------- 组合与日观测 ----------
    def build(score_col):
        daily = []
        for d, sub in panel.groupby("date", sort=True):
            sub = sub.sort_values(score_col, ascending=False)
            n_all = len(sub); n10 = max(1, int(round(n_all*0.10)))
            sel = {"Top10pct": sub.iloc[:n10], "Top40": sub.iloc[:min(40,n_all)], "全市场": sub}
            for h in HORIZONS:
                mk = sub[f"r{h}"].dropna()
                if len(mk) == 0: continue
                cov = len(mk)/n_all; mm = float(mk.mean())
                for pn, fr in sel.items():
                    r = fr[f"r{h}"].dropna()
                    if len(r) == 0: continue
                    daily.append({"date":d,"light":light_map[d],"h":h,"portfolio":pn,
                                  "n_cs":n_all,"n_ret":len(r),"covr":cov,
                                  "ret":float(r.mean()),"excess":float(r.mean())-mm,
                                  "mkt":mm})
        return pd.DataFrame(daily)

    D = build("menga"); Dl = build("menga_lit")
    D.to_csv(os.path.join(OUT,"RV_daily.csv"), index=False, encoding="utf-8-sig")

    # 池化统计（对齐 A/B 的表1/表2）
    say("\n" + "="*100); say("【表1】池化绝对净收益（★池化 n 是伪自由度，仅作口径核对★）"); say("="*100)
    rowsg = []
    for d, sub in panel.groupby("date", sort=True):
        pass
    tr = []
    for d, sub in panel.groupby("date", sort=True):
        s = sub.sort_values("menga", ascending=False)
        n_all = len(s); n10 = max(1,int(round(n_all*0.10)))
        sel = {"Top10pct": s.iloc[:n10], "Top40": s.iloc[:min(40,n_all)], "全市场": s}
        for h in HORIZONS:
            mk = s[f"r{h}"].dropna()
            if len(mk)==0: continue
            mm = float(mk.mean())
            for pn, fr in sel.items():
                r = fr[f"r{h}"].dropna()
                if len(r)==0: continue
                tr.append(pd.DataFrame({"date":d,"light":light_map[d],"h":h,"portfolio":pn,
                                        "n_cs":n_all,"net":r.values,"excess":r.values-mm}))
    T = pd.concat(tr, ignore_index=True); del tr
    say(f"{'灯':<4}{'h':>4}{'组合':>12}{'笔数':>9}{'胜率%':>8}{'均值':>9}{'中位':>9}{'p05':>9}{'超额均值':>10}{'超额胜率':>9}")
    for lt in ["红灯","绿灯","黄灯"]:
        for h in HORIZONS:
            for pn in ["Top10pct","Top40","全市场"]:
                s = T[(T.light==lt)&(T.h==h)&(T.portfolio==pn)]
                if not len(s): continue
                a = s["net"].values; e = s["excess"].values
                say(f"{lt:<4}{h:>4}{pn:>12}{len(a):>9}{(a>0).mean()*100:>8.2f}{a.mean():>9.3f}"
                    f"{np.median(a):>9.3f}{np.percentile(a,5):>9.2f}{e.mean():>10.3f}{(e>0).mean()*100:>9.2f}")
                rowsg.append({"light":lt,"h":h,"portfolio":pn,"n":len(a),
                              "winrate":round((a>0).mean()*100,4),"mean":round(a.mean(),4),
                              "median":round(np.median(a),4),"p05":round(np.percentile(a,5),4),
                              "excess_mean":round(e.mean(),4),"excess_winrate":round((e>0).mean()*100,4)})
    pd.DataFrame(rowsg).to_csv(os.path.join(OUT,"RV_groups.csv"), index=False, encoding="utf-8-sig")

    # 池化 t（伪）对照
    say("\n【表2】池化 t（绿−红）★伪★ vs 聚类 t —— 展示自由度虚增的后果")
    say(f"{'h':>4}{'组合':>12}{'口径':>8}{'池化t':>10}{'池化p':>12}{'聚类t':>9}{'聚类p':>9}")
    for h in HORIZONS:
        for pn in ["Top10pct","全市场"]:
            for mt in ["net","excess"]:
                if pn=="全市场" and mt=="excess": continue
                g = T[(T.light=="绿灯")&(T.h==h)&(T.portfolio==pn)][mt].values
                r = T[(T.light=="红灯")&(T.h==h)&(T.portfolio==pn)][mt].values
                tp = welch(g,r)
                col = "ret" if mt=="net" else "excess"
                dg = D[(D.light=="绿灯")&(D.h==h)&(D.portfolio==pn)][col].values
                dr = D[(D.light=="红灯")&(D.h==h)&(D.portfolio==pn)][col].values
                tc = welch(dg,dr)
                say(f"{h:>4}{pn:>12}{mt:>8}{tp[0]:>10.2f}{tp[3]:>12.2e}{tc[0]:>9.2f}{tc[3]:>9.4f}")

    # ---------- 聚类检验：全样本 vs 截面规模门 ----------
    def cluster_report(Dx, tag, min_cs=0, min_cov=0.0):
        sub = Dx[(Dx.n_cs >= min_cs) & (Dx["covr"] >= min_cov)]
        out = []
        say("\n" + "="*100)
        say(f"【聚类口径 · {tag}】1 截面日 = 1 观测，Welch t + 10000 次 bootstrap")
        say("="*100)
        say(f"{'h':>4}{'组合':>12}{'口径':>8}{'绿n日':>7}{'红n日':>7}{'绿均值':>9}{'红均值':>9}"
            f"{'绿−红':>9}{'t':>8}{'p':>9}{'boot95%CI':>22}{'红≠0的p':>10}")
        for h in HORIZONS:
            for pn in ["Top10pct","Top40","全市场"]:
                for mt in ["ret","excess"]:
                    if pn=="全市场" and mt=="excess": continue
                    g = sub[(sub.light=="绿灯")&(sub.h==h)&(sub.portfolio==pn)][mt].values
                    r = sub[(sub.light=="红灯")&(sub.h==h)&(sub.portfolio==pn)][mt].values
                    if len(g)<2 or len(r)<2: continue
                    t,df,pn_,ps = welch(g,r)
                    lo,hi = boot(g,r)
                    # 红灯组自身 ≠0 的单样本 t
                    t1 = r.mean()/(r.std(ddof=1)/math.sqrt(len(r)))
                    p1 = student_p(t1, len(r)-1)
                    say(f"{h:>4}{pn:>12}{mt:>8}{len(g):>7}{len(r):>7}{g.mean():>9.3f}{r.mean():>9.3f}"
                        f"{g.mean()-r.mean():>9.3f}{t:>8.3f}{ps:>9.4f}   [{lo:>7.3f},{hi:>7.3f}]{p1:>10.4f}")
                    out.append({"scope":tag,"h":h,"portfolio":pn,"metric":mt,
                                "n_green_days":len(g),"n_red_days":len(r),
                                "green_mean":round(g.mean(),4),"red_mean":round(r.mean(),4),
                                "diff":round(g.mean()-r.mean(),4),"t":round(t,4),
                                "p_student":round(ps,5),"p_normal":round(pn_,5),
                                "ci_lo":round(lo,4),"ci_hi":round(hi,4),
                                "red_vs0_t":round(t1,4),"red_vs0_p":round(p1,5)})
        return out

    res = []
    res += cluster_report(D, "全样本（含 18/40/183/566 只的早期薄截面）")
    res += cluster_report(D, "截面≥1000只（剔早期薄截面）", min_cs=1000)
    res += cluster_report(D, "截面≥1000只 且 收益覆盖≥90%", min_cs=1000, min_cov=0.90)
    res += cluster_report(Dl, "题面字面公式版 MENGα（截面≥1000只）", min_cs=1000)
    pd.DataFrame(res).to_csv(os.path.join(OUT,"RV_cluster.csv"), index=False, encoding="utf-8-sig")

    # ---------- 早期薄截面的影响 ----------
    say("\n" + "="*100); say("【薄截面诊断】前 6 个截面日"); say("="*100)
    say(f"{'date':<12}{'light':<6}{'n_cs':>6}{'Top10只数':>9}{'h=5超额':>10}{'h=10超额':>10}{'h=20超额':>10}")
    for d in cs_dates[:6]:
        r = D[(D.date==d)&(D.portfolio=="Top10pct")]
        if not len(r): say(f"{d:<12}{'':6}{'(无数据)':>6}"); continue
        n_cs = int(r["n_cs"].iloc[0]); n10 = max(1,int(round(n_cs*0.10)))
        vals = {int(x.h): x.excess for x in r.itertuples()}
        say(f"{d:<12}{light_map[d]:<6}{n_cs:>6}{n10:>9}"
            f"{vals.get(5,float('nan')):>10.3f}{vals.get(10,float('nan')):>10.3f}{vals.get(20,float('nan')):>10.3f}")

    # ---------- 非重叠子样本 ----------
    say("\n" + "="*100); say("【非重叠持有窗口】步长5 < h 导致相邻日观测重叠；这里每 h/5 个截面日取 1 个"); say("="*100)
    sub = D[D.n_cs >= 1000]
    for h in HORIZONS:
        step = max(1, h // STEP)
        for pn in ["Top10pct"]:
            for mt in ["ret","excess"]:
                x = sub[(sub.h==h)&(sub.portfolio==pn)].sort_values("date").reset_index(drop=True)
                x = x.iloc[::step]
                g = x[x.light=="绿灯"][mt].values; r = x[x.light=="红灯"][mt].values
                if len(g)<2 or len(r)<2: continue
                t,df,pnn,ps = welch(g,r)
                say(f"  h={h:<3}{pn:<10}{mt:<8}绿{len(g):>3}日 红{len(r):>3}日  绿−红={g.mean()-r.mean():>7.3f}  t={t:>6.2f} p={ps:.4f}")

    # ---------- 逐年 ----------
    say("\n" + "="*100); say("【逐年】红灯 Top10pct 日超额（截面≥1000只）"); say("="*100)
    sub = D[(D.n_cs>=1000)&(D.portfolio=="Top10pct")&(D.light=="红灯")].copy()
    sub["yr"] = sub["date"].str[:4]
    for h in HORIZONS:
        x = sub[sub.h==h].groupby("yr")["excess"].agg(["count","mean"])
        say(f"  h={h}: " + "  ".join(f"{y}:{r['mean']:+.2f}(n={int(r['count'])})" for y,r in x.iterrows()))

    # ---------- 多重检验 ----------
    say("\n" + "="*100); say("【多重检验】绿−红 这一族的 12 个检验（截面≥1000只）"); say("="*100)
    R = pd.DataFrame(res)
    fam = R[(R.scope=="截面≥1000只（剔早期薄截面）") & (R.portfolio!="全市场")].copy()
    fam = fam.sort_values("p_student").reset_index(drop=True)
    m = len(fam)
    fam["bonferroni"] = (fam["p_student"]*m).clip(upper=1)
    fam["BH_q"] = [min(1.0, min((fam["p_student"].iloc[j]*m/(j+1)) for j in range(i, m))) for i in range(m)]
    say(f"  族内检验数 m={m}")
    for r in fam.itertuples():
        say(f"  h={r.h:<3}{r.portfolio:<10}{r.metric:<8}p={r.p_student:.4f}  Bonferroni={r.bonferroni:.4f}  BH_q={r.BH_q:.4f}")

    with open(os.path.join(OUT,"RV_run.log"),"w",encoding="utf-8") as f:
        f.write("\n".join(LOG))
    say(f"\n完成，总用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
