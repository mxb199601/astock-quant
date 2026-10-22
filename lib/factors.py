# -*- coding: utf-8 -*-
"""因子 / 公式三 / 打分。

模式（全主板抽样回测，差异主要被出场压缩）：
  base    — 原公式三
  strict  — v2：MA60 + RSI<65 + 个股MA20上行 + 涨幅5~40% + bias<12%
  elite   — 再加强RS + 量能带 + RSI<62
  winrate — v3：elite + RSI45~62 + zf60 5~25 + bias 0~8（OPT 好看，PATH 一般）
  robust  — v5：elite + zf60 5~15 + bias 0~4（反热）+ **默认要求指数低波日**
            网格(500股 elite 事件)：PATH tight 胜率约63% 均值+1.15%（n≈90）
            对比无低波 robust_like 约55% / +0.54%
  require_low_vol — robust 默认 True；传 False / CLI --no-low-vol 可关闭
  名单按 quality_score 排序（见 lib/quality.py）
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from .data import fetch_kline, load_kline_csv, market_regime


def fnum(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:
            return None
        return v
    except Exception:
        return None


def rsi(series: pd.Series, n: int = 14) -> float:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(n).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs))
    v = val.iloc[-1]
    return float(v) if v == v else 50.0


def rsi_series(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(n).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _align_index(df: pd.DataFrame, idx_df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
    idx = idx_df.copy()
    idx["date"] = idx["date"].astype(str)
    dfd = df.copy()
    dfd["date"] = dfd["date"].astype(str)
    imap = dict(zip(idx["date"], idx["close"]))
    ic = pd.Series([imap.get(d, np.nan) for d in dfd["date"]], index=df.index).ffill()
    ima20 = ic.rolling(20).mean()
    ima60 = ic.rolling(60).mean()
    return ic, ima20, ima60


def index_realized_vol(close: pd.Series, n: int = 20) -> pd.Series:
    """指数近 n 日对数收益标准差（×100，便于阅读）。"""
    c = close.astype(float)
    lr = np.log(c / c.shift(1))
    return lr.rolling(n).std() * 100.0


def index_low_vol_series(
    idx_df: pd.DataFrame,
    n: int = 20,
    lookback: int = 252,
    q: float = 0.5,
) -> pd.Series:
    """逐日：指数波动是否处于近 lookback 日的低分位（默认 ≤ 中位数）。"""
    ic = idx_df["close"].astype(float)
    vol = index_realized_vol(ic, n=n)
    thr = vol.rolling(lookback, min_periods=max(60, n + 5)).quantile(q)
    return (vol <= thr).fillna(False)


def index_low_vol_today(
    idx_df: Optional[pd.DataFrame] = None,
    n: int = 20,
    lookback: int = 252,
    q: float = 0.5,
) -> Dict[str, Any]:
    """最新一日是否指数低波。"""
    if idx_df is None:
        idx_df = load_kline_csv("sh000300", min_bars=max(lookback, 100))
    if idx_df is None or len(idx_df) < max(80, n + 10):
        return {"ok": False, "low_vol": False, "vol": None, "thr": None}
    ic = idx_df["close"].astype(float)
    vol = index_realized_vol(ic, n=n)
    thr = vol.rolling(lookback, min_periods=max(60, n + 5)).quantile(q)
    v = float(vol.iloc[-1]) if vol.iloc[-1] == vol.iloc[-1] else None
    t = float(thr.iloc[-1]) if thr.iloc[-1] == thr.iloc[-1] else None
    low = bool(v is not None and t is not None and v <= t)
    return {
        "ok": True,
        "low_vol": low,
        "vol": round(v, 4) if v is not None else None,
        "thr": round(t, 4) if t is not None else None,
        "n": n,
        "lookback": lookback,
        "q": q,
    }


def formula3_mask(
    df: pd.DataFrame,
    idx_df: pd.DataFrame,
    mode: str = "strict",
    require_low_vol: bool = True,
    low_vol_q: float = 0.5,
) -> pd.Series:
    """历史序列入场信号。

    mode:
      base|strict|elite|winrate|robust — 自研公式三体系
      dual_mom|donchian|… — 公开经典公式（见 lib/formulas.py）
    require_low_vol:
      robust v5 默认 True：仅指数低波日放行；False 则退回 v4 反热带
    """
    mode = (mode or "strict").lower()
    # 经典公式直接委托
    classic = {
        "dual_mom", "donchian", "ma_squeeze", "golden_cross", "boll_sq",
        "macd_trend", "atr_break", "rps_break", "composite",
    }
    if mode in classic:
        from .formulas import get_signal
        return get_signal(mode, df, idx_df)

    c = df["close"]
    v = df["vol"]
    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    ma250 = c.rolling(250).mean()

    ic, ima20, ima60 = _align_index(df, idx_df)
    DP = ic > ima20
    stock_20 = c / c.shift(20) - 1
    idx_20 = ic / ic.shift(20) - 1
    RS = stock_20 > idx_20
    T1 = (c > ma250) & (ma250 >= ma250.shift(20))
    T2 = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
    vr = v.rolling(5).mean() / v.rolling(60).mean()
    T4 = (vr > 1) & (vr < 3)
    zf = (c / c.shift(60) - 1) * 100
    T6 = (zf > 0) & (zf < 50)
    base = (DP & RS & T1 & T2 & T4 & T6).fillna(False)

    if mode == "base":
        return base

    # strict v2（全主板回测，asym TP8/SL5 下略优于旧 strict）:
    # 大盘 MA60 + RSI<65 + 个股MA20上行 + 涨幅5~40% + 距MA20不过热
    r = rsi_series(c)
    DP60 = ic > ima60
    bias = (c / ma20 - 1) * 100
    ma20_up = ma20 >= ma20.shift(5)
    strict = (
        base & DP60 & (r < 65) & (zf > 5) & (zf < 40) & ma20_up & (bias < 12)
    ).fillna(False)
    if mode == "strict":
        return strict

    # elite: 更严 — 强RS + 量能带 + 贴近均线
    elite = (
        strict
        & (bias.abs() < 8)
        & (stock_20 > idx_20 + 0.03)
        & (vr > 1.2)
        & (vr < 2.5)
        & (r < 62)
    ).fillna(False)
    if mode == "elite":
        return elite

    # winrate v3：在 elite 上再收窄甜蜜区（OPT 胜率好看）
    winrate = (
        elite
        & (r >= 45)
        & (r <= 62)
        & (zf > 5)
        & (zf <= 25)
        & (bias >= 0)
        & (bias <= 8)
    ).fillna(False)
    if mode in ("winrate", "wr", "v3"):
        return winrate

    # robust v5：反热 + 默认指数低波（entry_grid 最优）
    robust = (
        elite
        & (zf > 5)
        & (zf <= 15)
        & (bias >= 0)
        & (bias <= 4)
    ).fillna(False)
    if require_low_vol and mode in ("robust", "path", "v4", "v5", "boost", "r_boost"):
        idx2 = idx_df.copy()
        idx2["date"] = idx2["date"].astype(str)
        lv = index_low_vol_series(idx2, q=low_vol_q)
        lv_map = dict(zip(idx2["date"], lv.astype(bool)))
        dfd = df["date"].astype(str)
        low_aligned = pd.Series([bool(lv_map.get(d, False)) for d in dfd], index=df.index)
        robust = (robust & low_aligned).fillna(False)
    if mode in ("robust", "path", "v4", "v5"):
        return robust.fillna(False)

    # boost：robust + 指数20日涨幅>0（edge 阶梯 PATH 薄增量，可选）
    if mode in ("boost", "r_boost", "r_idx_mom0"):
        idx_ret20 = (ic / ic.shift(20) - 1.0) * 100.0
        return (robust & (idx_ret20 > 0)).fillna(False)

    return elite


def structure_flags_last(
    df: pd.DataFrame,
    idx20: float,
    idx_up: bool,
    idx_up60: bool,
    require_low_vol: bool = True,
    low_vol_q: float = 0.5,
    idx_df: Optional[pd.DataFrame] = None,
    idx_low_vol: Optional[bool] = None,
) -> Dict[str, Any]:
    """最新一根 K 的结构标志（与 formula3_mask 末根语义对齐的唯一源）。

    robust v5：反热带 + 默认指数低波。
    scan / formula3_today / score 共用。
    """
    if df is None or len(df) < 80:
        return {
            "ok": False,
            "base": False, "strict": False, "elite": False,
            "winrate": False, "robust": False,
        }
    c = df["close"]
    v = df["vol"]
    ma5 = float(c.rolling(5).mean().iloc[-1])
    ma10 = float(c.rolling(10).mean().iloc[-1])
    ma20 = float(c.rolling(20).mean().iloc[-1])
    ma60 = float(c.rolling(60).mean().iloc[-1])
    ma250 = c.rolling(250).mean()
    px = float(c.iloc[-1])
    vol60 = float(v.rolling(60).mean().iloc[-1])
    vr = float(v.rolling(5).mean().iloc[-1] / max(vol60, 1e-9))
    zf60 = (px / float(c.iloc[-61]) - 1) * 100 if len(c) > 61 else 0.0
    rs20 = (px / float(c.iloc[-21]) - 1) * 100 if len(c) > 21 else 0.0
    bias = (px / ma20 - 1) * 100 if ma20 else 0.0
    has250 = bool(ma250.iloc[-1] == ma250.iloc[-1])
    ma250_last = float(ma250.iloc[-1]) if has250 else float("nan")
    ma250_up = bool(has250 and ma250_last >= float(ma250.iloc[-21]))
    rsi_val = rsi(c, 14)
    ma20_up = bool(ma20 >= float(c.rolling(20).mean().iloc[-6])) if len(c) > 25 else False

    # base — 与 formula3_mask 一致（需大盘 idx_up）
    base = bool(
        idx_up
        and rs20 > idx20
        and has250 and px > ma250_last and ma250_up
        and ma5 > ma10 > ma20 > ma60
        and 1 < vr < 3
        and 0 < zf60 < 50
    )
    # strict v2
    strict = bool(
        base
        and idx_up60
        and rsi_val < 65
        and 5 < zf60 < 40
        and ma20_up
        and bias < 12
    )
    # elite
    elite = bool(
        strict
        and abs(bias) < 8
        and rs20 > idx20 + 3
        and 1.2 < vr < 2.5
        and rsi_val < 62
    )
    # winrate v3
    winrate = bool(
        elite
        and 45 <= rsi_val <= 62
        and 5 < zf60 <= 25
        and 0 <= bias <= 8
    )
    # robust v5：反热 + 默认低波
    robust = bool(elite and 5 < zf60 <= 15 and 0 <= bias <= 4)
    if idx_low_vol is None:
        lv = index_low_vol_today(idx_df, q=low_vol_q)
        idx_low_vol = bool(lv.get("low_vol"))
        idx_vol, idx_vol_thr = lv.get("vol"), lv.get("thr")
    else:
        idx_vol = idx_vol_thr = None
        lv = {}
    if require_low_vol:
        robust = bool(robust and idx_low_vol)

    return {
        "ok": True,
        "base": base,
        "strict": strict,
        "elite": elite,
        "winrate": winrate,
        "robust": robust,
        "px": px,
        "rsi": rsi_val,
        "zf60": zf60,
        "bias": bias,
        "vr": vr,
        "rs20": rs20,
        "ma20_up": ma20_up,
        "has250": has250,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "strong_rs": rs20 > idx20 + 3,
        "near_ma20": abs(bias) < 8,
        "idx_up": bool(idx_up),
        "idx_up60": bool(idx_up60),
        "idx_low_vol": bool(idx_low_vol),
        "idx_vol": idx_vol if idx_vol is not None else lv.get("vol"),
        "idx_vol_thr": idx_vol_thr if idx_vol_thr is not None else lv.get("thr"),
        "require_low_vol": bool(require_low_vol),
    }


def formula3_today(
    df: pd.DataFrame,
    idx20: float,
    idx_up: bool,
    idx_up60: bool,
    mode: str = "strict",
    require_low_vol: bool = True,
    low_vol_q: float = 0.5,
    idx_df: Optional[pd.DataFrame] = None,
) -> Tuple[bool, dict]:
    """最新一根 K 是否满足公式（薄封装 → structure_flags_last）。"""
    # 仅 robust 使用低波开关；其它模式 flags 内 robust 字段也按开关算，无妨
    flags = structure_flags_last(
        df, idx20, idx_up, idx_up60,
        require_low_vol=bool(require_low_vol),
        low_vol_q=low_vol_q,
        idx_df=idx_df,
    )
    if not flags.get("ok"):
        return False, flags
    mode = (mode or "strict").lower()
    detail = dict(flags)
    if mode == "base":
        return bool(flags["base"]), detail
    if mode == "strict":
        return bool(flags["strict"]), detail
    if mode == "elite":
        return bool(flags["elite"]), detail
    if mode in ("winrate", "wr", "v3"):
        return bool(flags["winrate"]), detail
    if mode in ("robust", "path", "v4"):
        return bool(flags["robust"]), detail
    return bool(flags["elite"]), detail


def score_one(
    sym: str,
    fund: Dict,
    idx20: float,
    umom: np.ndarray,
    force_fetch: bool = False,
    mode: str = "strict",
    require_low_vol: bool = True,
    low_vol_q: float = 0.5,
) -> Tuple[str, str, Optional[dict]]:
    """个股自检：一票否决 + 加分。返回 (name, sym, result|None)。robust 默认低波。"""
    if force_fetch:
        df = fetch_kline(sym, force=True)
    else:
        df = load_kline_csv(sym)
        if df is None:
            df = fetch_kline(sym, force=False)
    f = fund.get(sym, {})
    name = f.get("name", sym)
    if df is None or len(df) < 260:
        return name, sym, None

    c = df["close"]
    ma5 = c.rolling(5).mean().iloc[-1]
    ma10 = c.rolling(10).mean().iloc[-1]
    ma20 = c.rolling(20).mean().iloc[-1]
    ma60 = c.rolling(60).mean().iloc[-1]
    ma250 = c.rolling(250).mean()
    px = float(c.iloc[-1])
    vr = float(df["vol"].rolling(5).mean().iloc[-1] / df["vol"].rolling(60).mean().iloc[-1])
    zf60 = (px / float(c.iloc[-61]) - 1) * 100
    rs20 = (px / float(c.iloc[-21]) - 1) * 100
    bias20 = (px / float(ma20) - 1) * 100
    pe, pb, turn = fnum(f.get("pe")), fnum(f.get("pb")), fnum(f.get("turn"))
    pctile = (float(np.searchsorted(umom, zf60) / len(umom) * 100) if len(umom) else None)
    has250 = ma250.iloc[-1] == ma250.iloc[-1]
    rsi_val = rsi(c, 14)
    vol_surge = float(df["vol"].iloc[-1]) > float(df["vol"].rolling(5).mean().iloc[-1] * 1.5)

    regime = market_regime()
    idx_up = regime.get("above_ma20", False)
    idx_up60 = regime.get("above_ma60", False)

    from .data import is_mainboard_a, is_st_name

    veto = []
    veto.append(("主板A股(非创业/科创)", is_mainboard_a(sym)))
    veto.append(("非ST", not is_st_name(str(name))))
    veto.append(("趋势向上(站上年线+上翘)", bool(has250 and px > ma250.iloc[-1] and ma250.iloc[-1] >= ma250.iloc[-21])))
    veto.append(("不破位(站上20日+未创60日新低)", bool(px > df["low"].rolling(60).min().iloc[-1] * 1.05 and px > ma20)))
    veto.append(("估值不透支(PB<10且PE>0)", (pb is None or pb < 10) and (pe is None or pe > 0)))
    # 高胜率模式：把环境过滤并入一票否决，避免红灯硬做
    ma20_up = bool(ma20 >= c.rolling(20).mean().iloc[-6]) if len(c) > 25 else False
    if mode in ("strict", "elite", "winrate", "wr", "v3", "robust", "path", "v4"):
        veto.append(("大盘站上MA20", bool(idx_up)))
        veto.append(("大盘站上MA60", bool(idx_up60)))
        veto.append(("RSI未过热(<65)", rsi_val < 65))
        veto.append(("60日涨幅健康(5~40%)", 5 < zf60 < 40))
        veto.append(("个股MA20上行", ma20_up))
        veto.append(("未过度偏离MA20(<12%)", bias20 < 12))
    if mode in ("elite", "winrate", "wr", "v3", "robust", "path", "v4"):
        veto.append(("贴近MA20(|bias|<8%)", abs(bias20) < 8))
        veto.append(("强于大盘≥3%", rs20 > idx20 + 3))
        veto.append(("量能带(1.2~2.5)", 1.2 < vr < 2.5))
        veto.append(("RSI<62", rsi_val < 62))
    if mode in ("winrate", "wr", "v3"):
        veto.append(("RSI甜蜜区(45~62)", 45 <= rsi_val <= 62))
        veto.append(("60日涨幅甜蜜(5~25%)", 5 < zf60 <= 25))
        veto.append(("正乖离不过热(0~8%)", 0 <= bias20 <= 8))
    if mode in ("robust", "path", "v4"):
        veto.append(("反热涨幅(5~15%)", 5 < zf60 <= 15))
        veto.append(("反热乖离(0~4%)", 0 <= bias20 <= 4))
        if require_low_vol:
            lv = index_low_vol_today(q=low_vol_q)
            veto.append(
                (
                    f"指数低波(vol≤近1年{int(low_vol_q*100)}分位)",
                    bool(lv.get("low_vol")),
                )
            )
    veto_pass = all(v for _, v in veto)

    plus = []
    plus.append(("均线多头排列", bool(ma5 > ma10 > ma20 > ma60)))
    plus.append(("强于大盘(20日跑赢沪深300)", bool(rs20 > idx20)))
    plus.append(("相对强度分位≥70%", pctile is not None and pctile >= 70))
    plus.append(("换手率健康(2~15%)", turn is not None and 2 <= turn <= 15))
    plus.append(("量能温和(1<VR<3)", 1 < vr < 3))
    plus.append(("60日涨幅适中(0~60%)", 0 < zf60 < 60))
    plus.append(("不超买(距20日线<15%)", bias20 < 15))
    plus.append(("RSI未超买(<70)", rsi_val < 70))
    plus.append(("放量确认(>1.5x5日均量)", vol_surge))
    psum = sum(1 for _, v in plus if v)

    f3, fdetail = formula3_today(
        df, idx20, idx_up, idx_up60, mode=mode,
        require_low_vol=require_low_vol, low_vol_q=low_vol_q,
    )

    return name, sym, dict(
        px=round(px, 2), pe=pe, pb=pb, turn=turn,
        zf60=round(zf60, 1), rs20=round(rs20, 1), idx20=round(idx20, 1),
        bias=round(bias20, 1), pctile=round(pctile) if pctile is not None else None,
        rsi=round(rsi_val, 1), vol_surge=vol_surge,
        veto=veto, veto_pass=veto_pass, plus=plus, psum=psum, f3=f3,
        mode=mode, light=regime.get("light", "未知"),
        require_low_vol=bool(require_low_vol),
        idx_low_vol=fdetail.get("idx_low_vol"),
        idx_vol=fdetail.get("idx_vol"),
        idx_vol_thr=fdetail.get("idx_vol_thr"),
        exit_hint=(
            "PATH实盘预期：胜率约45~58%（非OPT七成）。"
            "建议 tight=TP4/SL3 或 wr=TP5/SL3；勿只看乐观窗回测。"
            + (" 【robust v5 指数低波：开】" if require_low_vol else " 【低波已关 --no-low-vol】")
            if mode in ("robust", "path", "v4", "v5", "winrate", "wr", "v3", "elite")
            else "建议: wr=TP5/SL3 或 asym=TP8/SL5；PATH口径更近实盘"
        ),
    )


def verdict(r: dict) -> str:
    if not r["veto_pass"]:
        return "✗ 不合格(触发一票否决)"
    if r.get("f3") and r["psum"] >= 5:
        return "★ 高胜率候选"
    if r["psum"] >= 6:
        return "★ 强势候选"
    if r["psum"] >= 4:
        return "○ 中性观察"
    return "△ 偏弱"
