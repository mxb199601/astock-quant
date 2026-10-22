# -*- coding: utf-8 -*-
"""A股常见形态/指标（仅 OHLCV，无未来函数）。

说明：市面「黄金坑」等定义不一，这里用可复现、可回测的版本；
命中 ≠ 必涨，仅作辅助观察。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _ma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def pattern_黄金坑(df: pd.DataFrame) -> pd.Series:
    """黄金坑（可复现版）:
    近 15 日曾跌破 MA20 至少 2%，随后缩量，今日收盘重新站上 MA20，
    且收阳、站上 MA5，量能较坑底放大。
    """
    c, o, v = df["close"], df["open"], df["vol"]
    ma5, ma20 = _ma(c, 5), _ma(c, 20)
    # 坑：近15日最低价相对 MA20 的深度
    low = df["low"]
    pit_depth = (low.rolling(15).min() / ma20 - 1) * 100
    was_pit = pit_depth < -2.0
    # 今日收回
    reclaim = (c > ma20) & (c.shift(1) <= ma20.shift(1))
    yang = c > o
    above5 = c > ma5
    # 量：今日量 > 近10日均量 * 0.9，且坑内有过缩量
    vol_ma10 = v.rolling(10).mean()
    vol_ok = v > vol_ma10 * 0.9
    vol_shrink = v.rolling(10).min() < vol_ma10 * 0.7
    return (was_pit & reclaim & yang & above5 & vol_ok & vol_shrink).fillna(False)


def pattern_一阳穿三线(df: pd.DataFrame) -> pd.Series:
    """一阳穿三线：大阳线实体同时上穿 MA5/10/20。"""
    c, o = df["close"], df["open"]
    ma5, ma10, ma20 = _ma(c, 5), _ma(c, 10), _ma(c, 20)
    yang = c > o
    body = (c - o).abs()
    big = body > body.rolling(20).mean() * 1.5
    # 开盘在三线下方或附近，收盘在三线上方
    cross = (o <= ma5) | (o <= ma10) | (o <= ma20)
    above = (c > ma5) & (c > ma10) & (c > ma20)
    prev_below = (c.shift(1) < ma5.shift(1)) | (c.shift(1) < ma20.shift(1))
    return (yang & big & cross & above & prev_below).fillna(False)


def pattern_金叉(df: pd.DataFrame) -> pd.Series:
    """MA5 上穿 MA20（短线金叉）。"""
    c = df["close"]
    ma5, ma20 = _ma(c, 5), _ma(c, 20)
    return ((ma5 > ma20) & (ma5.shift(1) <= ma20.shift(1))).fillna(False)


def pattern_底背离_简化(df: pd.DataFrame) -> pd.Series:
    """简化底背离：价格创新低但 RSI 未创新低（20日窗口）。"""
    c = df["close"]
    r = _rsi(c, 14)
    price_ll = c <= c.rolling(20).min()
    rsi_hl = r > r.rolling(20).min() * 1.02
    yang = c > df["open"]
    return (price_ll.shift(1) & rsi_hl.shift(1) & yang & (c > c.shift(1))).fillna(False)


def pattern_放量突破平台(df: pd.DataFrame) -> pd.Series:
    """放量突破近 20 日高点（不含今日），量比 > 1.8。"""
    c, h, v = df["close"], df["high"], df["vol"]
    hh = h.shift(1).rolling(20).max()
    vol_ma = v.rolling(20).mean()
    return ((c > hh) & (v > vol_ma * 1.8) & (c > df["open"])).fillna(False)


def pattern_回踩年线(df: pd.DataFrame) -> pd.Series:
    """回踩年线不破：近 10 日低点触及 MA250±3%，今日收在年线上方收阳。"""
    c, o, low = df["close"], df["open"], df["low"]
    ma250 = _ma(c, 250)
    touch = (low.rolling(10).min() <= ma250 * 1.03) & (low.rolling(10).min() >= ma250 * 0.97)
    above = c > ma250
    yang = c > o
    return (touch & above & yang & (c > _ma(c, 5))).fillna(False)


def pattern_均线多头(df: pd.DataFrame) -> pd.Series:
    """均线多头排列：MA5>10>20>60 且今日刚形成（昨日不满足）。"""
    c = df["close"]
    ma5, ma10, ma20, ma60 = _ma(c, 5), _ma(c, 10), _ma(c, 20), _ma(c, 60)
    now = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
    prev = (ma5.shift(1) > ma10.shift(1)) & (ma10.shift(1) > ma20.shift(1)) & (ma20.shift(1) > ma60.shift(1))
    return (now & ~prev.fillna(False)).fillna(False)


def pattern_缩量回踩(df: pd.DataFrame) -> pd.Series:
    """缩量回踩 MA10：收盘靠近 MA10（±2%），量能 < 5日均量 0.7，趋势仍 MA20 上。"""
    c, v = df["close"], df["vol"]
    ma10, ma20 = _ma(c, 10), _ma(c, 20)
    near = (c / ma10 - 1).abs() < 0.02
    shrink = v < v.rolling(5).mean() * 0.7
    trend = c > ma20
    return (near & shrink & trend).fillna(False)


def pattern_启明星_简化(df: pd.DataFrame) -> pd.Series:
    """简化启明星：大阴 + 小实体 + 大阳，三根组合。"""
    c, o = df["close"], df["open"]
    body = c - o
    b1, b2, b3 = body.shift(2), body.shift(1), body
    big_yin = b1 < -body.abs().rolling(20).mean()
    small = b2.abs() < body.abs().rolling(20).mean() * 0.4
    big_yang = b3 > body.abs().rolling(20).mean()
    gapish = df["low"].shift(1) < df["low"].shift(2)  # 中间更低
    return (big_yin & small & big_yang & gapish).fillna(False)


PATTERN_REGISTRY: Dict[str, Dict[str, Any]] = {
    "黄金坑": {
        "fn": pattern_黄金坑,
        "desc": "跌破MA20挖坑后缩量，再收阳站回MA20",
    },
    "一阳穿三线": {
        "fn": pattern_一阳穿三线,
        "desc": "大阳线同时上穿MA5/10/20",
    },
    "金叉": {
        "fn": pattern_金叉,
        "desc": "MA5上穿MA20",
    },
    "底背离": {
        "fn": pattern_底背离_简化,
        "desc": "价新低RSI未新低后收阳（简化）",
    },
    "放量突破": {
        "fn": pattern_放量突破平台,
        "desc": "放量收盘突破20日高点",
    },
    "回踩年线": {
        "fn": pattern_回踩年线,
        "desc": "回踩年线附近不破后收阳",
    },
    "均线多头": {
        "fn": pattern_均线多头,
        "desc": "MA5>10>20>60 刚形成",
    },
    "缩量回踩": {
        "fn": pattern_缩量回踩,
        "desc": "趋势中缩量回踩MA10",
    },
    "启明星": {
        "fn": pattern_启明星_简化,
        "desc": "大阴+小实体+大阳 简化启明星",
    },
}


def recent_pattern_hit(
    df: pd.DataFrame,
    pattern_name: str = "底背离",
    lookback: int = 5,
) -> Tuple[bool, Optional[int]]:
    """近 lookback 根 K 线内是否触发指定形态。

    返回 (是否命中, 距今交易日数)；今日命中 days_ago=0。
    复用 PATTERN_REGISTRY，避免各处复制形态逻辑。
    """
    meta = PATTERN_REGISTRY.get(pattern_name)
    if meta is None or df is None or len(df) < 30:
        return False, None
    lb = max(1, int(lookback))
    try:
        sig = meta["fn"](df).fillna(False)
    except Exception:
        return False, None
    if len(sig) == 0:
        return False, None
    window = sig.iloc[-lb:]
    if not bool(window.any()):
        return False, None
    # 从最近一根往前找首次 True
    for ago in range(len(window)):
        if bool(window.iloc[-(ago + 1)]):
            return True, ago
    return False, None


def list_patterns() -> List[Tuple[str, str]]:
    return [(k, v["desc"]) for k, v in PATTERN_REGISTRY.items()]


def eval_patterns_today(df: pd.DataFrame) -> Dict[str, bool]:
    """返回各形态今日是否触发。"""
    out = {}
    if df is None or len(df) < 280:
        return {k: False for k in PATTERN_REGISTRY}
    for name, meta in PATTERN_REGISTRY.items():
        try:
            sig = meta["fn"](df)
            out[name] = bool(sig.iloc[-1]) if len(sig) else False
        except Exception:
            out[name] = False
    return out


def scan_patterns(
    symbols: List[str],
    load_df: Callable[[str], Optional[pd.DataFrame]],
    fund: Optional[Dict] = None,
    only_hit: bool = True,
) -> List[dict]:
    """扫描股票池，返回命中形态的行。"""
    rows = []
    for sym in symbols:
        df = load_df(sym)
        if df is None:
            continue
        hits = eval_patterns_today(df)
        hit_names = [k for k, v in hits.items() if v]
        if only_hit and not hit_names:
            continue
        name = (fund or {}).get(sym, {}).get("name", sym) if fund else sym
        px = float(df["close"].iloc[-1])
        rows.append(
            {
                "sym": sym,
                "name": name,
                "px": round(px, 2),
                "hits": hit_names,
                "n_hits": len(hit_names),
                **{f"p_{k}": v for k, v in hits.items()},
            }
        )
    rows.sort(key=lambda x: x["n_hits"], reverse=True)
    return rows
