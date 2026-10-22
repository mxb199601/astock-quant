# -*- coding: utf-8 -*-
"""自研指标体系 MENGα v5（数据驱动，非教科书指标堆叠）。

研究样本（主板缓存 IC，见 output/alpha_factor_ic.csv）:
  - 目标：未来 5/10/20 日收益的 Rank IC
  - 关键结论：主板短中期 **均值回归主导**，追涨/强 RS/多头排列 IC 为负
  - 最强单方向：反「过热」ret60/bias60/excess20（IC20 ≈ −0.06~−0.07）
  - bounce（ret5−ret20）IC20 ≈ +0.044 — 洗后短弹唯一稳定正向
  - 经典高 excess20 五分位未来收益最差，低 excess 反而更好

v5 相对 v4 的改动（对齐 IC + 多日截面复核）：
  - Heat 外部权重升至 0.60（反热是最稳边；纯 antiHeat 更优但可解释性差）
  - Wash / Spark 降权作辅（6 日截面：v5_final mean IC20≈+0.074 > v4≈+0.069）
  - Crowd 仅风险扣分 0.10
  - Heat 内部：ret60 0.40；Spark 内部：bounce 0.55（展示/冷静池仍用芽）

MENGα = -0.60·Heat + 0.15·Wash + 0.20·Spark - 0.10·Crowd
展示分 = 截面百分位 0~100（越高越偏「冷+蓄势+萌芽」，非荐股）

用途：研究排序、冷静池、联合信号副过滤；不是买卖建议。
"""
from __future__ import annotations

import bisect
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .data import (
    attach_live_prices,
    list_cached_symbols,
    load_fundamentals,
    load_kline_csv,
    market_regime,
)
from .paths import OUTPUT


# v5 权重（IC + 多日截面校准；改前请重跑校验脚本）
ALGO_VERSION = "menga_v5"
W_HEAT = 0.60
W_WASH = 0.15
W_SPARK = 0.20
W_CROWD = 0.10


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    if b is None or b == 0 or b != b:
        return default
    return a / b


def compute_raw_features(df: pd.DataFrame, idx20: float) -> Optional[Dict[str, float]]:
    """单股最新一根 K 的原始特征。需要 ≥80 根。"""
    if df is None or len(df) < 80:
        return None
    c = df["close"].astype(float)
    v = df["vol"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    px = float(c.iloc[-1])
    if px <= 0:
        return None

    def ret(n: int) -> float:
        if len(c) <= n:
            return 0.0
        return (px / float(c.iloc[-1 - n]) - 1.0) * 100.0

    ret5 = ret(5)
    ret20 = ret(20)
    ret60 = ret(60) if len(c) > 60 else ret(min(40, len(c) - 1))

    ma20 = float(c.iloc[-20:].mean())
    ma60 = float(c.iloc[-60:].mean()) if len(c) >= 60 else ma20
    ma20_prev = float(c.iloc[-25:-5].mean()) if len(c) >= 25 else ma20
    bias20 = (px / ma20 - 1.0) * 100.0 if ma20 else 0.0
    bias60 = (px / ma60 - 1.0) * 100.0 if ma60 else 0.0
    ma20_slope = (ma20 / ma20_prev - 1.0) * 100.0 if ma20_prev else 0.0

    # RSI14
    delta = c.diff().iloc[-14:]
    gain = float(delta.clip(lower=0).mean())
    loss = float((-delta.clip(upper=0)).mean())
    if loss < 1e-12:
        rsi = 100.0 if gain > 0 else 50.0
    else:
        rsi = 100.0 - 100.0 / (1.0 + gain / loss)

    vr = float(v.iloc[-5:].mean() / (v.iloc[-60:].mean() + 1e-9)) if len(v) >= 60 else 1.0
    low20 = float(l.iloc[-20:].min())
    high20 = float(h.iloc[-20:].max())
    dist_low = (px / low20 - 1.0) * 100.0 if low20 > 0 else 0.0
    dist_high = (high20 / px - 1.0) * 100.0 if px > 0 else 0.0
    high60 = float(h.iloc[-60:].max()) if len(h) >= 60 else high20
    dd60 = (px / high60 - 1.0) * 100.0 if high60 > 0 else 0.0  # ≤0

    bounce = ret5 - ret20
    excess20 = ret20 - float(idx20 or 0.0)

    # 近 5 日上涨成交量占比
    up_vol = 0.0
    tot = 0.0
    for i in range(-5, 0):
        vv = float(v.iloc[i])
        tot += vv
        if float(c.iloc[i]) >= float(c.iloc[i - 1]):
            up_vol += vv
    up_vol5 = _safe_div(up_vol, tot, 0.5)

    # 连续阴线天数
    cd = 0
    for i in range(-1, -11, -1):
        if abs(i) >= len(c):
            break
        if float(c.iloc[i]) < float(c.iloc[i - 1]):
            cd += 1
        else:
            break

    # 波动压缩：近20波动 / 近60波动
    logc = np.log(c.astype(float).values + 1e-9)
    if len(logc) >= 61:
        vol20 = float(np.std(np.diff(logc[-21:]))) * 100.0
        vol60 = float(np.std(np.diff(logc[-61:]))) * 100.0
        vol_ratio_std = _safe_div(vol20, vol60, 1.0)
    else:
        vol_ratio_std = 1.0

    return {
        "px": px,
        "ret5": round(ret5, 3),
        "ret20": round(ret20, 3),
        "ret60": round(ret60, 3),
        "bias20": round(bias20, 3),
        "bias60": round(bias60, 3),
        "ma20_slope": round(ma20_slope, 3),
        "rsi": round(rsi, 2),
        "vr": round(vr, 3),
        "dist_low": round(dist_low, 3),
        "dist_high": round(dist_high, 3),
        "dd60": round(dd60, 3),
        "bounce": round(bounce, 3),
        "excess20": round(excess20, 3),
        "up_vol5": round(up_vol5, 3),
        "cd": float(cd),
        "vol_ratio_std": round(vol_ratio_std, 3),
    }


def _z_cols(frame: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in cols:
        s = out[col].astype(float)
        mu, sd = float(s.mean()), float(s.std())
        if sd < 1e-12:
            out[col + "_z"] = 0.0
        else:
            out[col + "_z"] = (s - mu) / sd
    return out


def _compose_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """在已有 *_z 列上合成 Heat/Wash/Spark/Crowd/MENGα（v5 IC 对齐）。"""
    f = frame
    # Heat：IC 最强的反动量侧（ret60/bias60/excess20 权重最高）
    heat = (
        f["ret60_z"] * 0.40
        + f["bias60_z"] * 0.25
        + f["excess20_z"] * 0.20
        + f["ma20_slope_z"] * 0.10
        + f["rsi_z"] * 0.05
    )
    # Wash：保留回撤与中期弱，弱 IC 项降权
    wash = (
        -f["dd60_z"] * 0.50
        + f["cd_z"] * 0.10
        - f["ret20_z"] * 0.30
        + f["dist_high_z"] * 0.10
    )
    # Spark：bounce 是少数 IC20 为正的因子 → 主导
    spark = (
        f["bounce_z"] * 0.55
        + f["up_vol5_z"] * 0.20
        - f["vol_ratio_std_z"] * 0.10
        + (f["ret5"] > 0).astype(float) * 0.15
    )
    spark = (spark - spark.mean()) / (spark.std() + 1e-9)

    # Crowd：量价过热扣分（IC 弱，作风险项而非主预测）
    crowd = f["vr_z"].clip(-3, 3) * 0.45 + f["bias20_z"].clip(-3, 3) * 0.55

    alpha = -W_HEAT * heat + W_WASH * wash + W_SPARK * spark - W_CROWD * crowd

    f = f.copy()
    f["heat"] = heat
    f["wash"] = wash
    f["spark"] = spark
    f["crowd"] = crowd
    f["menga"] = alpha
    f["menga_pct"] = f["menga"].rank(pct=True) * 100.0
    f["heat_pct"] = f["heat"].rank(pct=True) * 100.0
    f["wash_pct"] = f["wash"].rank(pct=True) * 100.0
    f["spark_pct"] = f["spark"].rank(pct=True) * 100.0
    f["crowd_pct"] = f["crowd"].rank(pct=True) * 100.0
    return f


def _tag_row(r: pd.Series) -> str:
    tags = []
    if r["menga_pct"] >= 85:
        tags.append("高α区")
    elif r["menga_pct"] >= 70:
        tags.append("偏冷蓄势")
    if r["heat_pct"] >= 85:
        tags.append("过热")
    elif r["heat_pct"] <= 20:
        tags.append("冷却")
    if r["wash_pct"] >= 75:
        tags.append("深洗")
    if r["spark_pct"] >= 75 and r["heat_pct"] <= 50:
        tags.append("萌芽")
    if r["crowd_pct"] >= 85:
        tags.append("拥挤")
    return "、".join(tags) if tags else "中性"


def _idx20_lookup(idx_df: pd.DataFrame):
    """按截止日对齐的指数20日动量：只用 date<=d 的指数K线。

    个股缓存可能滞后于指数，用统一的「指数最新窗口」会造成 excess20 两端窗口错位。"""
    dates = [str(x) for x in idx_df["date"].tolist()]
    closes = [float(x) for x in idx_df["close"].tolist()]

    def get(d: str) -> float:
        p = bisect.bisect_right(dates, str(d)) - 1
        if p < 20:
            return 0.0
        return (closes[p] / closes[p - 20] - 1.0) * 100.0

    return get


def scan_own_alpha(top_n: int = 50, refresh_stale: bool = False) -> Dict[str, Any]:
    """全主板截面扫 MENGα，返回排序结果。

    默认纯本地只读（不打网络、不写盘）：Web 端可并发调用。数据新鲜度只检测并如实
    上报（asof/stale_n/note），刷新是 `cli.py refresh --all` 的职责。
    refresh_stale=True 仅供单线程 CLI 显式调用。"""
    regime = market_regime()
    # 原始基本面：load_fundamentals 已剔 ST，用它会让 ST 股取不到名称从而绕过下面的兜底检查
    from .data import _read_fund_raw

    fund = _read_fund_raw()
    syms = list_cached_symbols(mainboard_only=True, drop_st=True)
    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None or len(idx) < 30:
        return {"ok": False, "error": "缺少沪深300缓存"}

    fresh_stats = None
    if refresh_stale:
        from .data import ensure_kline_fresh

        fresh_stats = ensure_kline_fresh(syms, ref_date=str(idx["date"].iloc[-1]))

    get_idx20 = _idx20_lookup(idx)

    records: List[dict] = []
    for sym in syms:
        df = load_kline_csv(sym, min_bars=80)
        if df is None:
            continue
        asof_sym = str(df["date"].iloc[-1])
        feat = compute_raw_features(df, get_idx20(asof_sym))
        if feat is None:
            continue
        meta = fund.get(sym) or {}
        name = meta.get("name") or sym
        if "ST" in str(name).upper().replace(" ", ""):
            continue
        records.append({
            "sym": sym, "name": name, **feat, "asof": asof_sym,
            "pe": meta.get("pe"), "nmc": meta.get("nmc"),
        })

    if len(records) < 50:
        return {"ok": False, "error": f"有效样本过少: {len(records)}"}

    frame = pd.DataFrame(records)
    z_cols = [
        "ret5", "ret20", "ret60", "bias20", "bias60", "ma20_slope", "rsi", "vr",
        "dist_low", "dist_high", "dd60", "bounce", "excess20", "up_vol5", "cd", "vol_ratio_std",
    ]
    frame = _z_cols(frame, z_cols)
    frame = _compose_scores(frame)
    frame["tag"] = frame.apply(_tag_row, axis=1)
    frame = frame.sort_values("menga", ascending=False)

    # 导出
    os.makedirs(OUTPUT, exist_ok=True)
    export_cols = [
        "sym", "name", "menga_pct", "heat_pct", "wash_pct", "spark_pct", "crowd_pct",
        "px", "rsi", "ret20", "ret60", "bias20", "bias60", "excess20", "bounce", "dd60", "vr", "tag",
    ]
    out_path = os.path.join(OUTPUT, "menga_today.csv")
    frame[export_cols].to_csv(out_path, index=False, encoding="utf-8-sig")

    top = frame.head(int(top_n))
    # 过热反面名单（Heat 最高）
    hot = frame.sort_values("heat", ascending=False).head(min(20, int(top_n)))

    rows = []
    for _, r in top.iterrows():
        rows.append({
            "sym": r["sym"],
            "name": r["name"],
            "menga": round(float(r["menga_pct"]), 1),
            "heat": round(float(r["heat_pct"]), 1),
            "wash": round(float(r["wash_pct"]), 1),
            "spark": round(float(r["spark_pct"]), 1),
            "crowd": round(float(r["crowd_pct"]), 1),
            "px": round(float(r["px"]), 2),
            "rsi": round(float(r["rsi"]), 1),
            "ret20": round(float(r["ret20"]), 1),
            "ret60": round(float(r["ret60"]), 1),
            "bias20": round(float(r["bias20"]), 1),
            "excess20": round(float(r["excess20"]), 1),
            "bounce": round(float(r["bounce"]), 1),
            "tag": r["tag"],
        })

    hot_rows = []
    for _, r in hot.iterrows():
        hot_rows.append({
            "sym": r["sym"],
            "name": r["name"],
            "heat": round(float(r["heat_pct"]), 1),
            "menga": round(float(r["menga_pct"]), 1),
            "px": round(float(r["px"]), 2),
            "ret60": round(float(r["ret60"]), 1),
            "rsi": round(float(r["rsi"]), 1),
            "tag": r["tag"],
        })

    rows = attach_live_prices(rows)
    hot_rows = attach_live_prices(hot_rows)

    light = (regime or {}).get("light") or "未知"
    asof = max(r["asof"] for r in records)
    stale_n = int(sum(1 for r in records if r["asof"] < asof))
    stale_txt = ""
    if stale_n:
        earliest = min(r["asof"] for r in records)
        stale_txt = (
            f"（{stale_n} 只滞后，最早 {earliest}；"
            f"排序基于滞后截面，跑 cli.py refresh --all 刷新后再看）"
        )
    note = (
        f"大盘{light} | MENGα {ALGO_VERSION}（均值回归主轴，IC 对齐权重）。"
        f"扫描 {len(frame)} 只主板，展示 Top{len(rows)}。"
        f"指标截面日={asof}{stale_txt}；现价已套实时快照。"
        f"历史 IC20(反热)≈+0.07，bounce IC20≈+0.04；"
        f"不是荐股，高α≠必涨。"
    )
    return {
        "ok": True,
        "algo": ALGO_VERSION,
        "note": note,
        "light": light,
        "asof": asof,
        "stale_n": stale_n,
        "freshness": fresh_stats,
        "n": len(frame),
        "rows": rows,
        "hot_rows": hot_rows,
        "live_prices": True,
        "file": out_path,
        "formula": {
            "version": ALGO_VERSION,
            "menga": f"-{W_HEAT}*Heat + {W_WASH}*Wash + {W_SPARK}*Spark - {W_CROWD}*Crowd",
            "heat": "0.40*ret60 + 0.25*bias60 + 0.20*excess20 + 0.10*ma20_slope + 0.05*rsi  (截面z)",
            "wash": "-0.50*dd60 + 0.10*cd - 0.30*ret20 + 0.10*dist_high  (截面z)",
            "spark": "0.55*bounce + 0.20*up_vol5 - 0.10*vol_ratio_std + 0.15*I(ret5>0)",
            "crowd": "0.45*vr + 0.55*bias20  (截面z)",
            "evidence": {
                "sample": "alpha_factor_ic.csv / ~600股×截面",
                "ic20_anti_heat": 0.072,
                "ic20_bounce": 0.044,
                "q5_q1_f20_pct": 1.20,
                "classic_high_rs_f20_mean": 0.25,
                "classic_low_rs_f20_mean": 1.22,
            },
        },
        "disclaimer": "研究工具：MENGα 衡量「历史上偏利于短中期反弹的冷/洗/萌芽状态」，不构成投资建议。",
    }


def score_symbols(codes: List[str]) -> Dict[str, Any]:
    """对指定代码在全市场截面中定位百分位（需先扫全市场）。"""
    full = scan_own_alpha(top_n=10**9)
    if not full.get("ok"):
        return full
    # re-read file for all ranks
    path = full["file"]
    df = pd.read_csv(path)
    from .data import to_sym
    want = {to_sym(c) for c in codes if c and str(c).strip()}
    sub = df[df["sym"].isin(want)]
    rows = []
    for _, r in sub.iterrows():
        rows.append({
            "sym": r["sym"],
            "name": r["name"],
            "menga": round(float(r["menga_pct"]), 1),
            "heat": round(float(r["heat_pct"]), 1),
            "wash": round(float(r["wash_pct"]), 1),
            "spark": round(float(r["spark_pct"]), 1),
            "crowd": round(float(r["crowd_pct"]), 1),
            "px": round(float(r["px"]), 2),
            "rsi": round(float(r["rsi"]), 1),
            "ret20": round(float(r["ret20"]), 1),
            "ret60": round(float(r["ret60"]), 1),
            "tag": r["tag"],
        })
    missing = sorted(want - set(sub["sym"]))
    return {
        "ok": True,
        "rows": rows,
        "missing": missing,
        "note": full.get("note"),
        "formula": full.get("formula"),
        "disclaimer": full.get("disclaimer"),
    }
