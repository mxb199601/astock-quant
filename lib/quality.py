# -*- coding: utf-8 -*-
"""质量分：用于名单排序（非二值过滤）。全主板回测显示 asym 出场下入场过滤差异小，
排序让用户先看相对更干净的结构。"""
from __future__ import annotations

from typing import Any, Dict, Optional


def quality_score(
    *,
    rs20: float,
    idx20: float,
    rsi: float,
    bias: float,
    pctile: float,
    vr: float,
    plus: int,
    zf60: float,
    pe: Optional[float] = None,
    pb: Optional[float] = None,
    ma20_up: bool = False,
) -> float:
    """0~100 左右，越高越优先展示。"""
    s = 0.0
    # 相对强度：适度超额加分；暴强（过热）反而扣（与 IC 反热一致）
    excess = rs20 - idx20
    if excess > 12:
        s += 8  # 过强不追满
    else:
        s += max(-12.0, min(18.0, excess)) * 1.1
    # RSI：偏好 45~62，过热扣分
    if 45 <= rsi <= 62:
        s += 12
    elif 40 <= rsi < 45 or 62 < rsi <= 68:
        s += 6
    elif rsi > 75:
        s -= 12
    elif rsi < 35:
        s -= 4
    # 距 MA20：略强最好，暴拉扣分
    if 0 <= bias <= 6:
        s += 12
    elif 6 < bias <= 10:
        s += 5
    elif bias >= 15:
        s -= 10
    elif bias < -5:
        s -= 3
    # 动量分位：中高即可，极高分位（追涨）不再线性加满
    if pctile <= 85:
        s += min(pctile, 85) * 0.08
    else:
        s += 85 * 0.08 - (pctile - 85) * 0.12
    # 量能
    if 1.15 <= vr <= 2.5:
        s += 8
    elif 1.0 < vr < 3.0:
        s += 3
    else:
        s -= 2
    # 加分项
    s += plus * 2.5
    # 涨幅带：甜蜜区收窄到 5~25（robust 对齐）
    if 5 <= zf60 <= 25:
        s += 8
    elif 25 < zf60 <= 40:
        s += 3
    elif zf60 > 50:
        s -= 8
    # 估值（有则用）
    if pe is not None and 0 < pe < 40:
        s += 3
    if pb is not None and 0 < pb < 5:
        s += 2
    if ma20_up:
        s += 5
    return round(s, 2)


def attach_quality(row: Dict[str, Any], idx20: float) -> Dict[str, Any]:
    row = dict(row)
    row["qscore"] = quality_score(
        rs20=float(row.get("rs20") or 0),
        idx20=idx20,
        rsi=float(row.get("rsi") or 50),
        bias=float(row.get("bias") or 0),
        pctile=float(row.get("pctile") or 50),
        vr=float(row.get("vr") or 1),
        plus=int(row.get("plus") or 0),
        zf60=float(row.get("zf60") or 0),
        pe=row.get("pe"),
        pb=row.get("pb"),
        ma20_up=bool(row.get("ma20_up")),
    )
    return row
