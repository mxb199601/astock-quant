# -*- coding: utf-8 -*-
"""公开文献/经典体系里常见的量化入场公式（仅用 OHLCV，无未来函数）。

来源说明（公开知识，非神秘指标）：
  dual_mom   — Dual Momentum（Antonacci）：绝对动量 + 相对大盘
  rps_break  — 欧奈尔式相对强度：RPS(120)高位 + 价格新高突破
  donchian   — 海龟/唐奇安通道 N 日高点突破
  ma_squeeze — 均线粘合后大阳突破（通达信常见变体）
  golden_cross — 均线金叉（MA20 上穿 MA60）+ 年线之上
  boll_sq    — 布林带收窄后向上扩张
  macd_trend — MACD 金叉且 DIF>0、价格在 MA60 上
  atr_break  — 收盘突破前高 + ATR 波动过滤
  super_rs   — 双重相对强度：跑赢大盘且 60 日涨幅分位高（需截面）
  composite  — 多公式共振（≥2 个经典信号 + 大盘多头）
  rsrs       — 阻力支撑相对强度（jadepeng/pytrader rsrs.py 的择时口径）
  kdj_cross  — KDJ 进入超卖金叉区（fzcmike/stock_quant strategies/kdj.py）
  oversold_rebound — 超跌反弹五项里至少三项（amainoyo/a-stock-analysis 选股器）

全部返回 bool Series，与 df 等长；信号用「当日首次满足」在回测里 fresh 处理。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .data import list_cached_symbols, load_kline_csv


def _ic(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    idx = idx_df.copy()
    idx["date"] = idx["date"].astype(str)
    dates = df["date"].astype(str)
    imap = dict(zip(idx["date"], idx["close"]))
    return pd.Series([imap.get(d, np.nan) for d in dates], index=df.index).ffill()


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    g = d.where(d > 0, 0.0).rolling(n).mean()
    loss = (-d.where(d < 0, 0.0)).rolling(n).mean()
    rs = g / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def formula_dual_mom(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """Dual Momentum: 12月动量简化为 120 日绝对收益>0，且跑赢指数。"""
    c = df["close"]
    ic = _ic(df, idx_df)
    abs_mom = c / c.shift(120) - 1 > 0
    rel_mom = (c / c.shift(120) - 1) > (ic / ic.shift(120) - 1)
    trend = c > c.rolling(20).mean()
    return (abs_mom & rel_mom & trend).fillna(False)


def formula_rps_break(df: pd.DataFrame, idx_df: pd.DataFrame, rps_thr: float = 80) -> pd.Series:
    """相对强度突破：20日新高 + 跑赢大盘 + 不远离年线过远。
    注：完整 RPS 需全市场截面，这里用「相对指数 120 日超额」作代理，见 formula_super_rs 做真截面。
    """
    c = df["close"]
    ic = _ic(df, idx_df)
    hh20 = c.shift(1).rolling(20).max()
    breakout = c > hh20
    excess = (c / c.shift(120) - 1) - (ic / ic.shift(120) - 1)
    strong = excess > 0.05  # 120 日超额 >5%
    above = c > c.rolling(60).mean()
    return (breakout & strong & above).fillna(False)


def formula_donchian(df: pd.DataFrame, idx_df: pd.DataFrame, n: int = 20) -> pd.Series:
    """唐奇安/海龟：收盘突破过去 N 日最高价（不含当日）。"""
    c, h = df["close"], df["high"]
    upper = h.shift(1).rolling(n).max()
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return ((c > upper) & mkt).fillna(False)


def formula_ma_squeeze(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """均线粘合突破：MA30/60/90 极差<5%，收盘站上均价*1.04，年线之上，当日涨幅>3%。"""
    c = df["close"]
    ma30, ma60, ma90 = c.rolling(30).mean(), c.rolling(60).mean(), c.rolling(90).mean()
    ma250 = c.rolling(250).mean()
    a = pd.concat([ma30, ma60, ma90], axis=1).max(axis=1)
    b = pd.concat([ma30, ma60, ma90], axis=1).min(axis=1)
    e = (ma30 + ma60 + ma90) / 3
    squeeze = (a / b) < 1.05
    breakout = (c > e * 1.04) & (e > ma250) & (c > ma250) & (c / c.shift(1) > 1.03)
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return (squeeze & breakout & mkt).fillna(False)


def formula_golden_cross(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """金叉：MA20 上穿 MA60，且收盘在 MA250 上，大盘 MA20 多头。"""
    c = df["close"]
    ma20, ma60, ma250 = c.rolling(20).mean(), c.rolling(60).mean(), c.rolling(250).mean()
    cross = (ma20 > ma60) & (ma20.shift(1) <= ma60.shift(1))
    above = c > ma250
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return (cross & above & mkt).fillna(False)


def formula_boll_sq(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """布林收窄后上轨突破：带宽处于 120 日低 20% 分位，收盘上穿上轨。"""
    c = df["close"]
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    upper = mid + 2 * std
    width = (4 * std) / mid
    # 收窄：当前带宽 < 过去120日 20%分位
    q20 = width.rolling(120).quantile(0.2)
    squeeze = width <= q20
    breakout = (c > upper) & (c.shift(1) <= upper.shift(1))
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return (squeeze.shift(1).fillna(False) & breakout & mkt).fillna(False)


def formula_macd_trend(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """MACD 金叉 + DIF>0 + 价格>MA60。"""
    c = df["close"]
    ema12, ema26 = _ema(c, 12), _ema(c, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    cross = (dif > dea) & (dif.shift(1) <= dea.shift(1))
    ma60 = c.rolling(60).mean()
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return (cross & (dif > 0) & (c > ma60) & mkt).fillna(False)


def formula_atr_break(df: pd.DataFrame, idx_df: pd.DataFrame, n: int = 20) -> pd.Series:
    """波动突破：收盘 > 前高，且当日波幅 > 1.2*ATR，过滤假突破。"""
    c, h = df["close"], df["high"]
    atr = _atr(df, 14)
    upper = h.shift(1).rolling(n).max()
    rng = df["high"] - df["low"]
    strong = rng > 1.2 * atr
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return ((c > upper) & strong & mkt & (c > c.rolling(60).mean())).fillna(False)


def _kdj(df: pd.DataFrame, n: int = 9):
    """通达信式 KDJ：RSV 的 SMA 用 ewm(alpha=1/3)，与 fzcmike/stock_quant 的 kdj.py 一致。"""
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = ((df["close"] - low_n) / rng * 100).fillna(50)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def _rsrs_score(high: pd.Series, low: pd.Series, n: int = 18, m: int = 600) -> pd.Series:
    """右偏标准分：N 日 high~low 斜率的 M 日 zscore（总体标准差）再乘 R²。"""
    x = low.astype(float)
    y = high.astype(float)
    sum_x = x.rolling(n).sum()
    sum_y = y.rolling(n).sum()
    sum_xx = (x * x).rolling(n).sum()
    sum_yy = (y * y).rolling(n).sum()
    sum_xy = (x * y).rolling(n).sum()
    var_x = n * sum_xx - sum_x ** 2
    var_y = n * sum_yy - sum_y ** 2
    cov = n * sum_xy - sum_x * sum_y
    slope = cov / var_x.replace(0, np.nan)
    r2 = (cov ** 2) / (var_x * var_y).replace(0, np.nan)
    r2 = r2.clip(lower=0, upper=1)
    mu = slope.rolling(m).mean()
    sd = slope.rolling(m).std(ddof=0)
    z = (slope - mu) / sd.replace(0, np.nan)
    return z * r2


def formula_rsrs(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """RSRS 择时搬到个股：N=18、M=600、zscore×R²>0.7，再要求大盘站上 MA20。

    参数来自 https://github.com/jadepeng/pytrader/blob/main/rsrs.py
    get_timing_signal 的阈值 0.7，以及同文件 get_rsrs_score 的 N=18、示例里的 M=600。
    """
    score = _rsrs_score(df["high"], df["low"], 18, 600)
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return ((score > 0.7) & mkt).fillna(False)


def formula_kdj_cross(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """KDJ：当日进入「K>D 且 J<50」，前一日不在该区。对应 kdj.py 的 signal_change>0。

    来源 https://github.com/fzcmike/stock_quant （strategies/kdj.py，n=9,m1=3,m2=3）。
    另加本仓观察层闸门：大盘站上 MA20。
    """
    k, d, j = _kdj(df)
    zone = (k > d) & (j < 50)
    prev = zone.shift(1)
    prev = prev.where(prev.notna(), False).astype(bool)
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return (zone & ~prev & mkt).fillna(False)


def formula_oversold_rebound(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """五项至少中三项：RSI(14) 在 25–40、MACD 绿柱连缩 4 日且 DIF<0、
    3 日均量 > 1.5×20 日均量、J 自 15 下拐头向上、收盘站上 MA20。

    来源 https://github.com/amainoyo/a-stock-analysis 选股器说明。
    另加本仓观察层闸门：大盘站上 MA20。
    """
    c = df["close"]
    rsi = _rsi(c, 14)
    rsi_ok = (rsi >= 25) & (rsi <= 40)
    ema12, ema26 = _ema(c, 12), _ema(c, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    hist = dif - dea
    shrink = (hist < 0) & (hist > hist.shift(1))
    macd_ok = (dif < 0) & shrink & shrink.shift(1) & shrink.shift(2) & shrink.shift(3)
    if "vol" in df.columns:
        vol = df["vol"].astype(float)
        vol_ok = vol.rolling(3).mean() > vol.rolling(20).mean() * 1.5
    else:
        vol_ok = pd.Series(False, index=df.index)
    _k, _d, j = _kdj(df)
    j_turn = (j.shift(1) < 15) & (j > j.shift(1))
    above = c > c.rolling(20).mean()
    parts = [rsi_ok, macd_ok, vol_ok, j_turn, above]
    score = pd.concat([p.fillna(False).astype(int) for p in parts], axis=1).sum(axis=1)
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return ((score >= 3) & mkt).fillna(False)


# 截面 RPS 缓存（一次计算）
_RPS_CACHE: Dict[str, pd.Series] = {}


def build_cross_sectional_rps(n: int = 120) -> Dict[str, float]:
    """最新交易日的 N 日收益截面百分位 0-100。"""
    rets = {}
    for sym in list_cached_symbols():
        df = load_kline_csv(sym, min_bars=n + 5)
        if df is None or len(df) < n + 1:
            continue
        r = float(df["close"].iloc[-1] / df["close"].iloc[-(n + 1)] - 1)
        rets[sym] = r
    if not rets:
        return {}
    arr = np.array(list(rets.values()))
    out = {}
    for sym, r in rets.items():
        out[sym] = float(np.mean(arr <= r) * 100)
    return out


def formula_super_rs_today(df: pd.DataFrame, idx_df: pd.DataFrame, rps_map: Dict[str, float], sym: str) -> bool:
    """当日：RPS120>=80 + 收盘创新高20日 + 大盘多头。"""
    if len(df) < 130:
        return False
    c = df["close"]
    ic = _ic(df, idx_df)
    rps = rps_map.get(sym, 0)
    hh = c.iloc[-21:-1].max()
    breakout = c.iloc[-1] > hh
    mkt = ic.iloc[-1] > ic.iloc[-20:].mean()
    return bool(rps >= 80 and breakout and mkt)


def formula_composite(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    """共振：至少 2 个经典公式同日为真，且大盘站上 MA20。"""
    parts = [
        formula_dual_mom(df, idx_df),
        formula_donchian(df, idx_df),
        formula_ma_squeeze(df, idx_df),
        formula_golden_cross(df, idx_df),
        formula_macd_trend(df, idx_df),
        formula_atr_break(df, idx_df),
        formula_boll_sq(df, idx_df),
    ]
    stack = pd.concat(parts, axis=1).astype(int).sum(axis=1)
    ic = _ic(df, idx_df)
    mkt = ic > ic.rolling(20).mean()
    return ((stack >= 2) & mkt).fillna(False)


FORMULA_REGISTRY: Dict[str, Dict] = {
    "dual_mom": {
        "name": "双重动量 Dual Momentum",
        "fn": formula_dual_mom,
        "desc": "120日绝对动量>0 且跑赢指数 + 站上MA20",
    },
    "rps_break": {
        "name": "相对强度突破",
        "fn": formula_rps_break,
        "desc": "20日新高 + 120日超额>5% + 站上MA60",
    },
    "donchian": {
        "name": "唐奇安/海龟突破",
        "fn": formula_donchian,
        "desc": "收盘突破20日最高 + 大盘多头",
    },
    "ma_squeeze": {
        "name": "均线粘合突破",
        "fn": formula_ma_squeeze,
        "desc": "MA30/60/90粘合后放量阳线突破",
    },
    "golden_cross": {
        "name": "均线金叉",
        "fn": formula_golden_cross,
        "desc": "MA20上穿MA60 + 年线上方",
    },
    "boll_sq": {
        "name": "布林收窄突破",
        "fn": formula_boll_sq,
        "desc": "带宽低分位后收盘上穿上轨",
    },
    "macd_trend": {
        "name": "MACD趋势金叉",
        "fn": formula_macd_trend,
        "desc": "MACD金叉 DIF>0 价在MA60上",
    },
    "atr_break": {
        "name": "ATR波动突破",
        "fn": formula_atr_break,
        "desc": "突破20日高 + 波幅>1.2ATR",
    },
    "composite": {
        "name": "多因子共振",
        "fn": formula_composite,
        "desc": "≥2个经典信号同日触发",
    },
    "rsrs": {
        "name": "RSRS阻力支撑",
        "fn": formula_rsrs,
        "desc": "18日高低斜率标准分×R²>0.7 + 大盘MA20",
    },
    "kdj_cross": {
        "name": "KDJ超卖金叉",
        "fn": formula_kdj_cross,
        "desc": "进入K>D且J<50的首日 + 大盘MA20",
    },
    "oversold_rebound": {
        "name": "超跌反弹评分",
        "fn": formula_oversold_rebound,
        "desc": "RSI/MACD缩柱/放量/J拐头/站上MA20 至少3项 + 大盘MA20",
    },
}


def list_formulas() -> List[Tuple[str, str, str]]:
    return [(k, v["name"], v["desc"]) for k, v in FORMULA_REGISTRY.items()]


def get_signal(name: str, df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.Series:
    if name not in FORMULA_REGISTRY:
        raise KeyError(f"未知公式: {name}，可选: {list(FORMULA_REGISTRY)}")
    return FORMULA_REGISTRY[name]["fn"](df, idx_df)


def evaluate_today(sym: str, df: pd.DataFrame, idx_df: pd.DataFrame, rps_map: Optional[Dict[str, float]] = None) -> Dict[str, bool]:
    """返回各公式今日是否触发。"""
    out = {}
    for key, meta in FORMULA_REGISTRY.items():
        sig = meta["fn"](df, idx_df)
        out[key] = bool(sig.iloc[-1]) if len(sig) else False
    if rps_map is not None:
        out["super_rs"] = formula_super_rs_today(df, idx_df, rps_map, sym)
    return out
