# -*- coding: utf-8 -*-
"""缠论最小可程序化实现（v1）— 研究用，非荐股。

规格见 docs/缠论最小规格.md。
纪律：信号永不单独作为 action_today 买入；默认否决/观察。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .data import list_cached_symbols, load_fundamentals, load_kline_csv, market_regime, to_sym
from .factors import formula3_mask
from .metrics import summarize_returns
from .paths import OUTPUT
from .stress import exit_path_daily


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Fractal:
    m_idx: int          # 合并序列下标
    raw_idx: int        # 原 K 确认相关 index（分型中心对应原序列）
    conf_idx: int       # 确认日（右侧 K）原序列 index
    kind: str           # "top" | "bot"
    price: float


@dataclass
class Bi:
    start_raw: int
    end_raw: int
    conf_idx: int       # 笔结束确认日
    direction: str      # "up" | "down"
    start_price: float
    end_price: float

    @property
    def high(self) -> float:
        return max(self.start_price, self.end_price)

    @property
    def low(self) -> float:
        return min(self.start_price, self.end_price)


@dataclass
class Zhongshu:
    zd: float
    zg: float
    conf_idx: int       # 第 3 笔确认后生效
    bi_end: int         # 构成中枢的最后一笔在 bi 列表中的下标


# ---------------------------------------------------------------------------
# 核心几何
# ---------------------------------------------------------------------------

def _merge_klines(
    high: np.ndarray,
    low: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """包含处理。返回 MH, ML, raw_end_idx（每根合并棒对应原序列最后一根）。"""
    n = len(high)
    if n == 0:
        return np.array([]), np.array([]), np.array([], dtype=int)

    mh = [float(high[0])]
    ml = [float(low[0])]
    raw_end = [0]
    # 初始方向：看第二根相对第一根
    direction = 0  # 1 up, -1 down

    for i in range(1, n):
        h, l = float(high[i]), float(low[i])
        ph, pl = mh[-1], ml[-1]
        contained = (h <= ph and l >= pl) or (h >= ph and l <= pl)
        if contained:
            if direction >= 0:
                # 向上合并：高低都取高侧
                mh[-1] = max(ph, h)
                ml[-1] = max(pl, l)
            else:
                mh[-1] = min(ph, h)
                ml[-1] = min(pl, l)
            raw_end[-1] = i
        else:
            if h > ph and l > pl:
                direction = 1
            elif h < ph and l < pl:
                direction = -1
            else:
                # 交叉：按中点关系兜底
                direction = 1 if (h + l) >= (ph + pl) else -1
            mh.append(h)
            ml.append(l)
            raw_end.append(i)

    return (
        np.asarray(mh, dtype=float),
        np.asarray(ml, dtype=float),
        np.asarray(raw_end, dtype=int),
    )


def _fractals(mh: np.ndarray, ml: np.ndarray, raw_end: np.ndarray) -> List[Fractal]:
    out: List[Fractal] = []
    m = len(mh)
    if m < 3:
        return out
    for i in range(1, m - 1):
        conf = int(raw_end[i + 1])  # 右侧合并棒走完
        center_raw = int(raw_end[i])
        if mh[i] > mh[i - 1] and mh[i] > mh[i + 1]:
            out.append(Fractal(i, center_raw, conf, "top", float(mh[i])))
        if ml[i] < ml[i - 1] and ml[i] < ml[i + 1]:
            out.append(Fractal(i, center_raw, conf, "bot", float(ml[i])))
    # 同一合并点可能极端情况双分型：保留价格更极端的一个（极少）
    out.sort(key=lambda f: (f.m_idx, 0 if f.kind == "bot" else 1))
    cleaned: List[Fractal] = []
    for f in out:
        if cleaned and cleaned[-1].m_idx == f.m_idx:
            if f.kind == cleaned[-1].kind:
                continue
            # 同 bar 顶底：丢弃（合并异常），两都丢
            cleaned.pop()
            continue
        cleaned.append(f)
    return cleaned


def _build_bi(fractals: List[Fractal], min_gap: int = 4) -> List[Bi]:
    if not fractals:
        return []
    # 先取交替候选
    alt: List[Fractal] = []
    for f in fractals:
        if not alt:
            alt.append(f)
            continue
        if f.kind == alt[-1].kind:
            # 同向：更极端则替换
            if f.kind == "top" and f.price >= alt[-1].price:
                alt[-1] = f
            elif f.kind == "bot" and f.price <= alt[-1].price:
                alt[-1] = f
        else:
            alt.append(f)

    bi_list: List[Bi] = []
    i = 0
    while i < len(alt) - 1:
        a, b = alt[i], alt[i + 1]
        if a.kind == b.kind:
            i += 1
            continue
        gap = b.m_idx - a.m_idx
        if gap < min_gap:
            # 间距不足：尝试用更远的同向分型
            j = i + 2
            while j < len(alt) and alt[j].kind == b.kind:
                if alt[j].m_idx - a.m_idx >= min_gap:
                    if b.kind == "top" and alt[j].price > b.price:
                        b = alt[j]
                    elif b.kind == "bot" and alt[j].price < b.price:
                        b = alt[j]
                    elif alt[j].m_idx - a.m_idx > gap:
                        b = alt[j]
                j += 1
            if b.m_idx - a.m_idx < min_gap:
                # 丢掉后方，让 a 与更后面配对
                i += 1
                continue
        direction = "up" if a.kind == "bot" else "down"
        bi_list.append(
            Bi(
                start_raw=a.raw_idx,
                end_raw=b.raw_idx,
                conf_idx=b.conf_idx,
                direction=direction,
                start_price=a.price,
                end_price=b.price,
            )
        )
        i += 1
        # 下一笔从 b 开始
        # i 已指向 b 的位置：找到 alt 中 b
        # 简单：i 设为 b 在 alt 的位置
        try:
            i = alt.index(b)
        except ValueError:
            i += 1
    return bi_list


def _build_zhongshu(bi_list: List[Bi], min_width_pct: float = 0.5) -> List[Zhongshu]:
    """min_width_pct: 中枢宽度相对中点至少 0.5%，过滤毛刺重叠。"""
    zs: List[Zhongshu] = []
    for k in range(len(bi_list) - 2):
        b1, b2, b3 = bi_list[k], bi_list[k + 1], bi_list[k + 2]
        lows = [b1.low, b2.low, b3.low]
        highs = [b1.high, b2.high, b3.high]
        zd = max(lows)
        zg = min(highs)
        if zg <= zd:
            continue
        mid = (zg + zd) / 2.0
        if mid <= 0:
            continue
        width_pct = (zg - zd) / mid * 100.0
        if width_pct < min_width_pct:
            continue
        zs.append(Zhongshu(zd=zd, zg=zg, conf_idx=b3.conf_idx, bi_end=k + 2))
    return zs


# ---------------------------------------------------------------------------
# 信号序列
# ---------------------------------------------------------------------------

def compute_chan(
    df: pd.DataFrame,
    min_gap: int = 4,
    min_bars: int = 80,
) -> Dict[str, Any]:
    """对单票 OHLCV 计算缠论结构与信号序列。

    返回:
      bi, zhongshu, buy1, buy2, sell3, structure_ok (均为与 df 等长的 bool Series 或 list)
    """
    n = len(df)
    empty = {
        "ok": False,
        "bi": [],
        "zhongshu": [],
        "buy1": pd.Series(False, index=df.index),
        "buy2": pd.Series(False, index=df.index),
        "sell3": pd.Series(False, index=df.index),
        "structure_ok": pd.Series(True, index=df.index),
        "n_bi": 0,
        "n_zs": 0,
        "last": {},
    }
    if n < min_bars:
        return empty
    if not {"high", "low", "close"}.issubset(df.columns):
        return empty

    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values

    mh, ml, raw_end = _merge_klines(high, low)
    fr = _fractals(mh, ml, raw_end)
    bi_list = _build_bi(fr, min_gap=min_gap)
    zs_list = _build_zhongshu(bi_list)

    buy1 = np.zeros(n, dtype=bool)
    buy2 = np.zeros(n, dtype=bool)
    sell3 = np.zeros(n, dtype=bool)
    # structure_ok 默认 True，进入 sell3 后置 False 直到结构修复
    structure_ok = np.ones(n, dtype=bool)

    # 用事件时间线扫描
    last_zs: Optional[Zhongshu] = None
    left_down = False          # 是否已向下离开中枢
    left_up = False
    buy1_low: Optional[float] = None
    buy1_idx: Optional[int] = None
    in_sell3 = False
    leave_low: Optional[float] = None

    # 将 bi / zs 按 conf_idx 注入
    bi_by_conf: Dict[int, List[Bi]] = {}
    for b in bi_list:
        bi_by_conf.setdefault(b.conf_idx, []).append(b)
    zs_by_conf: Dict[int, List[Zhongshu]] = {}
    for z in zs_list:
        zs_by_conf.setdefault(z.conf_idx, []).append(z)

    # 底/顶分型确认日
    bot_conf: Dict[int, Fractal] = {}
    top_conf: Dict[int, Fractal] = {}
    for f in fr:
        if f.kind == "bot":
            bot_conf[f.conf_idx] = f
        else:
            top_conf[f.conf_idx] = f

    for i in range(n):
        if i in zs_by_conf:
            # 取最新中枢；新中枢重置离开状态，但不自动清 buy1 轨迹
            last_zs = zs_by_conf[i][-1]
            left_down = False
            left_up = False
            leave_low = None
            buy1_idx = None  # 新中枢后重新等离开+一买
            buy1_low = None
            in_sell3 = False

        if last_zs is not None and i in bi_by_conf:
            for b in bi_by_conf[i]:
                if b.direction == "down" and b.end_price < last_zs.zd:
                    left_down = True
                    leave_low = b.end_price if leave_low is None else min(leave_low, b.end_price)
                if b.direction == "up" and b.end_price > last_zs.zg:
                    left_up = True
                if left_up and b.direction == "down" and b.end_price < last_zs.zd:
                    # 向上离开后再跌破 → 进入三卖观察
                    in_sell3 = True

        # 一买：向下离开后，底分型确认（每个离开段只记一次）
        if last_zs is not None and left_down and i in bot_conf and buy1_idx is None:
            f = bot_conf[i]
            thr = leave_low if leave_low is not None else last_zs.zd
            # 需有效跌破中枢下沿后再确认底
            if leave_low is not None and leave_low < last_zs.zd * 0.995 and f.price <= thr * 1.005:
                buy1[i] = True
                buy1_low = f.price
                buy1_idx = i
                in_sell3 = False

        # 二买：一买后回踩不破（每个一买段只允许一个二买）
        if (
            buy1_low is not None
            and buy1_idx is not None
            and i > buy1_idx
            and i in bot_conf
            and not buy2[buy1_idx:i].any()
        ):
            f = bot_conf[i]
            if f.price > buy1_low:
                if buy1_idx + 1 < i:
                    mid_high = float(np.max(high[buy1_idx : i + 1]))
                    if mid_high > buy1_low * 1.03 and f.price <= mid_high * 0.98:
                        buy2[i] = True

        # 三卖确认：否决区内顶分型且高点 ≤ ZG
        if in_sell3 and last_zs is not None and i in top_conf:
            f = top_conf[i]
            if f.price <= last_zs.zg:
                sell3[i] = True

        structure_ok[i] = not in_sell3

        # 修复：收盘重新站上 ZG 且非 sell3 当日 → 解除否决
        if in_sell3 and last_zs is not None and close[i] > last_zs.zg:
            in_sell3 = False
            structure_ok[i] = True

    last_info: Dict[str, Any] = {}
    if bi_list:
        b = bi_list[-1]
        last_info["last_bi"] = b.direction
        last_info["last_bi_end"] = float(b.end_price)
    if zs_list:
        z = zs_list[-1]
        last_info["zd"] = round(z.zd, 4)
        last_info["zg"] = round(z.zg, 4)
    last_info["structure_ok"] = bool(structure_ok[-1])
    last_info["buy1_today"] = bool(buy1[-1])
    last_info["buy2_today"] = bool(buy2[-1])
    last_info["sell3_today"] = bool(sell3[-1])

    return {
        "ok": True,
        "bi": bi_list,
        "zhongshu": zs_list,
        "buy1": pd.Series(buy1, index=df.index),
        "buy2": pd.Series(buy2, index=df.index),
        "sell3": pd.Series(sell3, index=df.index),
        "structure_ok": pd.Series(structure_ok, index=df.index),
        "n_bi": len(bi_list),
        "n_zs": len(zs_list),
        "last": last_info,
    }


def _fresh(sig: pd.Series) -> pd.Series:
    cur = sig.fillna(False).astype(bool)
    prev = sig.shift(1)
    prev = prev.where(prev.notna(), False).astype(bool)
    return cur & ~prev


# ---------------------------------------------------------------------------
# 截面扫描
# ---------------------------------------------------------------------------

def run_chan_scan(
    codes: Optional[Sequence[str]] = None,
    min_bars: int = 120,
    limit: int = 200,
) -> Dict[str, Any]:
    """主板扫描：输出今日缠论状态（观察用）。"""
    regime = market_regime()
    fund = load_fundamentals(False)
    if codes:
        syms = [to_sym(c) for c in codes]
    else:
        syms = list_cached_symbols(mainboard_only=True, drop_st=True)

    rows: List[Dict[str, Any]] = []
    n_ok = n_buy1 = n_buy2 = n_sell3 = n_veto = 0

    for sym in syms:
        df = load_kline_csv(sym, min_bars=min_bars)
        if df is None or len(df) < min_bars:
            continue
        ch = compute_chan(df)
        if not ch.get("ok"):
            continue
        n_ok += 1
        last = ch["last"]
        name = (fund.get(sym) or {}).get("name", "") if fund else ""
        px = float(df["close"].iloc[-1])
        flags = []
        if last.get("buy1_today"):
            flags.append("一买")
            n_buy1 += 1
        if last.get("buy2_today"):
            flags.append("二买")
            n_buy2 += 1
        if last.get("sell3_today"):
            flags.append("三卖")
            n_sell3 += 1
        if not last.get("structure_ok", True):
            flags.append("否决中")
            n_veto += 1
        rows.append({
            "sym": sym,
            "name": name,
            "px": round(px, 2),
            "n_bi": ch["n_bi"],
            "n_zs": ch["n_zs"],
            "zd": last.get("zd"),
            "zg": last.get("zg"),
            "last_bi": last.get("last_bi"),
            "structure_ok": last.get("structure_ok", True),
            "flags": "、".join(flags) if flags else "—",
            "buy1": bool(last.get("buy1_today")),
            "buy2": bool(last.get("buy2_today")),
            "sell3": bool(last.get("sell3_today")),
        })

    # 有信号的优先
    rows.sort(key=lambda r: (
        0 if r["buy1"] or r["buy2"] else 1,
        0 if not r["structure_ok"] else 1,
        -r["n_zs"],
    ))
    rows = rows[:limit]

    os.makedirs(OUTPUT, exist_ok=True)
    export = [{
        "代码": r["sym"],
        "名称": r["name"],
        "现价": r["px"],
        "笔数": r["n_bi"],
        "中枢数": r["n_zs"],
        "ZD": r["zd"],
        "ZG": r["zg"],
        "末笔": r["last_bi"],
        "结构OK": r["structure_ok"],
        "标记": r["flags"],
    } for r in rows]
    out_path = os.path.join(OUTPUT, "chan_today.csv")
    pd.DataFrame(export).to_csv(out_path, index=False, encoding="utf-8-sig")

    light = (regime or {}).get("light", "?")
    return {
        "ok": True,
        "regime": regime,
        "pool": len(syms),
        "computed": n_ok,
        "n_buy1": n_buy1,
        "n_buy2": n_buy2,
        "n_sell3": n_sell3,
        "n_veto": n_veto,
        "rows": rows,
        "path": out_path,
        "note": (
            f"大盘{light} | 扫描{n_ok}只 | 一买{n_buy1} 二买{n_buy2} 三卖{n_sell3} 否决中{n_veto}。"
            "观察层，≠买入；红灯空仓。"
        ),
        "disclaimer": "缠论v1为自研可程序化子集，与市面软件不必一致；不构成投资建议。",
    }


# ---------------------------------------------------------------------------
# 对照回测：robust vs robust∩structure_ok vs buy1/2
# ---------------------------------------------------------------------------

def compare_chan_vs_robust(
    sample_n: int = 300,
    seed: int = 42,
    period: int = 20,
    exit_mode: str = "tight",
    cost_bps: float = 15.0,
    require_low_vol: bool = True,
) -> Dict[str, Any]:
    """PATH 口径对照。"""
    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))
    idx = load_kline_csv("sh000300", min_bars=300)
    if idx is None:
        return {"ok": False, "error": "缺少 sh000300，请先 refresh"}

    variants = ("robust", "robust_chan_ok", "chan_buy1", "chan_buy2", "chan_buy12")
    buckets: Dict[str, List[float]] = {v: [] for v in variants}
    event_rows: List[Dict[str, Any]] = []

    for sym in pool:
        df = load_kline_csv(sym, min_bars=320)
        if df is None or len(df) < 320:
            continue
        try:
            robust = formula3_mask(df, idx, mode="robust", require_low_vol=require_low_vol)
            ch = compute_chan(df)
        except Exception:
            continue
        if not ch.get("ok"):
            continue

        ok_s = ch["structure_ok"].fillna(True).astype(bool)
        buy1 = ch["buy1"].fillna(False).astype(bool)
        buy2 = ch["buy2"].fillna(False).astype(bool)

        masks = {
            "robust": _fresh(robust),
            "robust_chan_ok": _fresh(robust & ok_s),
            "chan_buy1": _fresh(buy1),
            "chan_buy2": _fresh(buy2),
            "chan_buy12": _fresh(buy1 | buy2),
        }

        c = df["close"].astype(float).values
        hi = df["high"].astype(float).values
        lo = df["low"].astype(float).values
        dates = df["date"].astype(str).values

        for name, m in masks.items():
            for i in np.where(m.values)[0]:
                if i + period >= len(c):
                    continue
                entry = float(c[i])
                if entry <= 0:
                    continue
                ret = exit_path_daily(
                    entry,
                    hi[i : i + period + 1],
                    lo[i : i + period + 1],
                    c[i : i + period + 1],
                    exit_mode,
                    cost_bps,
                    period,
                    "sl",
                )
                buckets[name].append(ret)
                event_rows.append({
                    "sym": sym,
                    "date": dates[i],
                    "variant": name,
                    "ret": ret,
                })

    summary_rows = []
    for name in variants:
        rets = buckets[name]
        st = summarize_returns(rets, name)
        def _f(k, default=0.0):
            v = st.get(k)
            if v is None or (isinstance(v, float) and v != v):
                return default
            return float(v)
        summary_rows.append({
            "variant": name,
            "n": int(st.get("n") or 0),
            "winrate": _f("winrate"),
            "mean": _f("mean"),
            "median": _f("median"),
            "sharpe": _f("sharpe"),
        })

    os.makedirs(OUTPUT, exist_ok=True)
    sum_df = pd.DataFrame(summary_rows)
    sum_path = os.path.join(OUTPUT, "chan_compare.csv")
    sum_df.to_csv(sum_path, index=False, encoding="utf-8-sig")
    if event_rows:
        pd.DataFrame(event_rows).to_csv(
            os.path.join(OUTPUT, "chan_compare_events.csv"),
            index=False,
            encoding="utf-8-sig",
        )

    # 结论草稿
    by = {r["variant"]: r for r in summary_rows}
    base = by.get("robust") or {}
    filt = by.get("robust_chan_ok") or {}
    verdict = "数据不足"
    if base.get("n", 0) >= 30 and filt.get("n", 0) >= 10:
        dw = filt["winrate"] - base["winrate"]
        dm = filt["mean"] - base["mean"]
        if dm >= 0.1 and dw >= -2:
            verdict = "否决过滤略有正向（仍需更大样本/样本外）；可继续观察，暂不进 joint"
        elif dm >= -0.15 and filt["n"] < base["n"] * 0.85 and dw >= 0:
            verdict = "胜率持平或略好、信号变少；薄改善，不够升格"
        else:
            verdict = "相对 robust 无稳定增量 → 保持观察层，不宣称提高上涨概率"
    elif base.get("n", 0) < 30:
        verdict = "robust 事件过少，扩大 sample 再下结论"

    return {
        "ok": True,
        "sample_n": len(pool),
        "period": period,
        "exit_mode": exit_mode,
        "cost_bps": cost_bps,
        "require_low_vol": require_low_vol,
        "summary": summary_rows,
        "path": sum_path,
        "verdict": verdict,
        "disclaimer": "PATH tight 扣成本；历史≠未来；不构成投资建议。",
    }
