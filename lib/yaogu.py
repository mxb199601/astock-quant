# -*- coding: utf-8 -*-
"""妖股「启动前」画像扫描。

定义（研究用，可复现）:
  历史妖股波段 = 自某日起 20 个交易日内最高价相对该日收盘 涨幅 ≥ 50%，
  且启动日收盘仍接近近 20 日低点（未提前飞太高）。

从 3000+ 次历史启动日倒推共性（全主板缓存）:
  - RSI 中偏低（中位~44），少见超买
  - 多在 MA20 下方或附近（bias 中位~-3%）
  - 近 5/20 日偏弱或横盘，尚未均线多头（~85% 非多头）
  - 量能不极端放大（启动日当天往往还不暴量）
  - 常有「浅坑」（近10日低点低于 MA20）

今日扫描 = 匹配上述「启动前」画像 + 打分排序。
注意: 命中的绝大多数不会变成妖股（低精度高召回）；仅观察用。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .data import (
    attach_live_prices,
    list_cached_symbols,
    load_fundamentals,
    load_kline_csv,
    market_regime,
)
from .factors import rsi
from .paths import OUTPUT
from .patterns import eval_patterns_today


def _score_prelaunch(row: dict) -> float:
    """分数越高越像历史启动前画像。"""
    s = 0.0
    rsi_v = row["rsi"]
    # RSI 甜蜜：40~55 最佳
    if 40 <= rsi_v <= 55:
        s += 20
    elif 35 <= rsi_v < 40 or 55 < rsi_v <= 62:
        s += 12
    elif rsi_v > 70:
        s -= 15
    elif rsi_v < 30:
        s += 5  # 超卖也可

    bias = row["bias20"]
    if -8 <= bias <= 2:
        s += 18
    elif -12 <= bias < -8 or 2 < bias <= 6:
        s += 8
    elif bias > 12:
        s -= 12

    # 近20日不太强（还没启动）
    if -15 <= row["ret20"] <= 10:
        s += 12
    elif row["ret20"] > 25:
        s -= 10

    # 未完全多头
    if not row["ma_bull"]:
        s += 10
    else:
        s -= 5

    # 浅坑
    if row["pit"] < -2:
        s += 10
    if row["pit"] < -5:
        s += 5

    # 量能温和
    if 0.6 <= row["vol_ratio"] <= 1.5:
        s += 8
    elif row["vol_ratio"] > 3:
        s -= 6

    # 靠近20日低点
    if row["dist_low20"] <= 0.12:
        s += 12
    if row["dist_low20"] <= 0.06:
        s += 6

    # 形态加分
    for p in row.get("patterns") or []:
        if p in ("黄金坑", "底背离", "缩量回踩", "启明星", "回踩年线"):
            s += 6
        if p in ("一阳穿三线", "放量突破", "金叉"):
            s += 3  # 可能已启动，分低一点

    # 流通市值：中小盘更像妖（有 nmc 时）
    nmc = row.get("nmc")
    if nmc is not None:
        # nmc 单位约万元（新浪）
        if nmc < 500000:  # 很小
            s += 8
        elif nmc < 1500000:
            s += 5
        elif nmc > 5000000:
            s -= 6

    return round(s, 1)


def scan_yaogu_prelaunch(top_n: int = 80) -> Dict[str, Any]:
    regime = market_regime()
    fund = load_fundamentals(False)
    syms = list_cached_symbols(mainboard_only=True, drop_st=True)
    idx = load_kline_csv("sh000300", min_bars=100)
    ic = idx["close"] if idx is not None else None
    idx20 = float((ic.iloc[-1] / ic.iloc[-21] - 1) * 100) if ic is not None and len(ic) > 21 else 0.0

    rows: List[dict] = []
    for sym in syms:
        df = load_kline_csv(sym, min_bars=280)
        if df is None or len(df) < 280:
            continue
        c = df["close"].astype(float)
        v = df["vol"].astype(float)
        px = float(c.iloc[-1])
        ma5 = float(c.rolling(5).mean().iloc[-1])
        ma10 = float(c.rolling(10).mean().iloc[-1])
        ma20 = float(c.rolling(20).mean().iloc[-1])
        ma60 = float(c.rolling(60).mean().iloc[-1])
        ma250 = float(c.rolling(250).mean().iloc[-1])
        low20 = float(c.iloc[-20:].min())
        dist_low20 = px / low20 - 1 if low20 > 0 else 9
        # 已大幅远离低点 → 不像「启动前」
        if dist_low20 > 0.18:
            continue

        ret5 = (px / float(c.iloc[-6]) - 1) * 100
        ret20 = (px / float(c.iloc[-21]) - 1) * 100
        zf60 = (px / float(c.iloc[-61]) - 1) * 100
        bias20 = (px / ma20 - 1) * 100 if ma20 else 0
        rsi_v = rsi(c, 14)
        vr = float(v.iloc[-5:].mean() / max(float(v.iloc[-60:].mean()), 1e-9))
        vol_ratio = float(v.iloc[-1] / max(float(v.iloc[-21:-1].mean()), 1e-9))
        pit = float(c.iloc[-11:].min() / ma20 - 1) * 100 if ma20 else 0
        ma_bull = ma5 > ma10 > ma20 > ma60
        f = fund.get(sym, {})
        name = f.get("name", sym)
        try:
            nmc = float(f.get("nmc") or 0) or None
        except Exception:
            nmc = None

        # 硬过滤：匹配历史启动前主体分布
        if not (28 <= rsi_v <= 68):
            continue
        if bias20 > 10 or bias20 < -18:
            continue
        if ret20 > 30 or ret20 < -35:
            continue
        if vol_ratio > 4.5:
            continue
        # 已经大涨过的不要
        if zf60 > 80:
            continue

        pats = []
        try:
            hits = eval_patterns_today(df)
            pats = [k for k, v in hits.items() if v]
        except Exception:
            pass

        row = {
            "sym": sym,
            "name": name,
            "px": round(px, 2),
            "rsi": round(rsi_v, 1),
            "bias20": round(bias20, 1),
            "ret5": round(ret5, 1),
            "ret20": round(ret20, 1),
            "zf60": round(zf60, 1),
            "excess20": round(ret20 - idx20, 1),
            "vr": round(vr, 2),
            "vol_ratio": round(vol_ratio, 2),
            "pit": round(pit, 1),
            "dist_low20": round(dist_low20, 3),
            "ma_bull": bool(ma_bull),
            "above_ma20": px > ma20,
            "above_ma250": px > ma250,
            "nmc": nmc,
            "patterns": pats,
        }
        row["yaogu_score"] = _score_prelaunch(row)
        # 最低分门槛
        if row["yaogu_score"] < 40:
            continue
        rows.append(row)

    rows.sort(key=lambda x: x["yaogu_score"], reverse=True)
    top = attach_live_prices(rows[:top_n])

    os.makedirs(OUTPUT, exist_ok=True)
    export = []
    for r in top:
        export.append(
            {
                "代码": r["sym"],
                "名称": r["name"],
                "妖股分": r["yaogu_score"],
                "现价": r["px"],
                "日K价": r.get("px_bar"),
                "实时涨跌%": r.get("live_chg_pct"),
                "实时时间": r.get("live_time"),
                "RSI": r["rsi"],
                "距MA20%": r["bias20"],
                "20日%": r["ret20"],
                "距20日低%": round(r["dist_low20"] * 100, 1),
                "坑深%": r["pit"],
                "量比": r["vol_ratio"],
                "多头": r["ma_bull"],
                "形态": "、".join(r["patterns"]),
            }
        )
    pd.DataFrame(export).to_csv(
        os.path.join(OUTPUT, "yaogu_prelaunch.csv"), index=False, encoding="utf-8-sig"
    )

    light = (regime or {}).get("light", "?")
    return {
        "ok": True,
        "regime": regime,
        "pool_size": len(syms),
        "candidates": len(rows),
        "rows": top,
        "live_prices": True,
        "note": (
            f"大盘{light} | 匹配「妖股启动前画像」{len(rows)} 只，展示 Top{len(top)}。"
            "现价已套实时快照；RSI/bias 等仍按日K截面。"
            "历史：启动前多在低位/均线附近、RSI中偏低、尚未完全多头；"
            "绝大多数命中不会变成妖股（低精度）。非荐股。"
        ),
        "profile": {
            "rsi_med": 44,
            "bias_med": -2.9,
            "ret20_med": -3.3,
            "ma_bull_rate": "15%",
            "above_ma20_rate": "31%",
            "definition": "20日内最大浮盈≥50% 且启动时贴近20日低点",
            "events_studied": 3016,
        },
        "disclaimer": "无法预知谁会成为妖股；此为启动前状态相似度排序，风险极大。",
    }
