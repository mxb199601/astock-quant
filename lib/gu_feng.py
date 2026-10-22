# -*- coding: utf-8 -*-
"""估值一谷 / 一峰 · 主板扫描（研究用，非荐股）。

口径（与对话定稿一致，大样本约 69–71%@20日）：
- 池：沪深主板（含 002），排除 ST/*ST、创业板 300/301、科创板 688/689、北交所
- 估值分位：个股自身近约 3 年（750 交易日）收盘价滚动分位（仅用过去数据）
  *本地无 PE 历史序列时，用价格分位作「估低/估高」代理；并附带当日 PE/PB 供参考*
- 一谷（买观察）：分位 ≤30%
- 可选买侧强化：一谷 ∧ 近 3～5 日底背离（复用 lib.patterns.pattern_底背离_简化）
- 可选买侧缩量：换手率%（基本面缓存 turn / 新浪 turnoverratio）< 阈值（默认 1.0，可 0.5）；可与底背离叠加
- 一峰（卖观察）：分位 ≥70%（卖侧不加形态 AND）
- 参考价：最新收盘（若有实时价则优先实时）
- 目标买入价（历史分位映射，非预测必成交价）：
  * buy_ceiling = 近3年过去收盘的 30% 分位价（一谷上沿 / 目标买入上限）
  * buy_prefer  = 20% 分位价（建议分批更优价）
  * 若现价 ≤ buy_ceiling：target_buy = 现价，标注「现价已在一谷」；否则 target_buy = buy_ceiling（待回落）
  * 卖侧对称：sell_floor = 70% 分位价（一峰下沿 / 目标卖出参考）

不进 joint/action；不构成投资建议。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

VALLEY = 0.30
PEAK = 0.70
PREFER = 0.20  # 建议分批价分位
ROLL = 750
MIN_OBS = 500


@dataclass
class GuFengSignal:
    sym: str
    name: str
    side: str  # buy | sell
    date: str
    price: float
    val_pct: float
    pe: Optional[float]
    pb: Optional[float]
    metric: str
    divergence: Optional[bool] = None  # 近 lookback 日底背离（仅买侧有意义）
    div_ago: Optional[int] = None  # 最近一次底背离距今交易日数，0=今日
    turnover: Optional[float] = None  # 换手率%（新浪/基本面 turn）
    buy_ceiling: Optional[float] = None  # 30%分位价 = 一谷上沿 / 目标买入上限
    buy_prefer: Optional[float] = None  # 20%分位价 = 建议分批价
    target_buy: Optional[float] = None  # 目标买入价（在谷内=现价，否则=buy_ceiling）
    target_note: Optional[str] = None  # 如「现价已在一谷」
    sell_floor: Optional[float] = None  # 70%分位价 = 一峰下沿 / 目标卖出参考


def rolling_past_percentile(close: np.ndarray, window: int = ROLL, min_obs: int = MIN_OBS) -> np.ndarray:
    """Past-only rolling percentile of close in (0,1]; NaN until enough history."""
    n = len(close)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        x = close[i]
        if not np.isfinite(x):
            continue
        lo = max(0, i - window)
        hist = close[lo:i]
        hist = hist[np.isfinite(hist)]
        if len(hist) < min_obs:
            continue
        out[i] = float(np.mean(hist <= x))
    return out


def _past_hist(close: np.ndarray, window: int = ROLL, min_obs: int = MIN_OBS) -> Optional[np.ndarray]:
    """Past-only closes used for percentile mapping (exclude latest bar)."""
    if close is None or len(close) < min_obs + 1:
        return None
    hist = close[max(0, len(close) - 1 - window) : -1]
    hist = hist[np.isfinite(hist)]
    if len(hist) < min_obs:
        return None
    return hist


def latest_past_percentile(close: np.ndarray, window: int = ROLL, min_obs: int = MIN_OBS) -> float:
    """Only the latest past-only percentile — O(window)."""
    hist = _past_hist(close, window=window, min_obs=min_obs)
    if hist is None:
        return float("nan")
    x = float(close[-1])
    if not np.isfinite(x):
        return float("nan")
    return float(np.mean(hist <= x))


def percentile_price(
    close: np.ndarray,
    q: float,
    window: int = ROLL,
    min_obs: int = MIN_OBS,
) -> float:
    """Price at quantile q∈(0,1] of the same past-only ~3y window (historical map, not a forecast)."""
    hist = _past_hist(close, window=window, min_obs=min_obs)
    if hist is None:
        return float("nan")
    try:
        qf = float(q)
    except (TypeError, ValueError):
        return float("nan")
    if not (0.0 < qf <= 1.0):
        return float("nan")
    return float(np.percentile(hist, qf * 100.0))


def compute_target_levels(
    close: np.ndarray,
    price: float,
    *,
    valley: float = VALLEY,
    peak: float = PEAK,
    prefer: float = PREFER,
    window: int = ROLL,
    min_obs: int = MIN_OBS,
) -> Dict[str, Any]:
    """Map historical percentiles → buy_ceiling / buy_prefer / target_buy / sell_floor.

    口径：目标价来自历史分位映射，不是预测必成交价。
    """
    buy_ceiling = percentile_price(close, valley, window=window, min_obs=min_obs)
    buy_prefer = percentile_price(close, prefer, window=window, min_obs=min_obs)
    sell_floor = percentile_price(close, peak, window=window, min_obs=min_obs)
    px = float(price) if price is not None and np.isfinite(price) else float("nan")

    target_buy = None
    target_note = None
    if np.isfinite(buy_ceiling) and np.isfinite(px):
        if px <= buy_ceiling + 1e-9:
            target_buy = round(px, 4)
            target_note = "现价已在一谷"
        else:
            target_buy = round(float(buy_ceiling), 4)
            target_note = "待回落至一谷上沿"

    def _r(v: float) -> Optional[float]:
        if v is None or not np.isfinite(v):
            return None
        return round(float(v), 4)

    return {
        "buy_ceiling": _r(buy_ceiling),
        "buy_prefer": _r(buy_prefer),
        "sell_floor": _r(sell_floor),
        "target_buy": target_buy,
        "target_note": target_note,
    }


def refresh_target_buy(sig: GuFengSignal) -> GuFengSignal:
    """After live price update: recompute target_buy / note vs frozen buy_ceiling."""
    if sig.buy_ceiling is None or not np.isfinite(sig.buy_ceiling):
        return sig
    px = float(sig.price)
    if px <= float(sig.buy_ceiling) + 1e-9:
        sig.target_buy = round(px, 4)
        sig.target_note = "现价已在一谷"
    else:
        sig.target_buy = round(float(sig.buy_ceiling), 4)
        sig.target_note = "待回落至一谷上沿"
    return sig


def score_symbol(
    df: pd.DataFrame,
    sym: str,
    name: str = "",
    pe: Optional[float] = None,
    pb: Optional[float] = None,
    live_px: Optional[float] = None,
    valley: float = VALLEY,
    peak: float = PEAK,
) -> Optional[GuFengSignal]:
    if df is None or len(df) < MIN_OBS + 5:
        return None
    d = df
    if "close" not in d.columns:
        return None
    if not d["date"].is_monotonic_increasing:
        d = d.sort_values("date")
    close = d["close"].astype(float).to_numpy()
    vp = latest_past_percentile(close)
    if not np.isfinite(vp):
        return None
    last_date = str(d["date"].iloc[-1])[:10]
    px = float(live_px) if live_px is not None and np.isfinite(live_px) else float(close[-1])
    levels = compute_target_levels(close, px, valley=valley, peak=peak)
    if vp <= valley:
        side = "buy"
    elif vp >= peak:
        side = "sell"
    else:
        return None
    return GuFengSignal(
        sym=sym,
        name=name or sym,
        side=side,
        date=last_date,
        price=round(px, 4),
        val_pct=round(vp, 4),
        pe=None if pe is None or not np.isfinite(pe) else round(float(pe), 2),
        pb=None if pb is None or not np.isfinite(pb) else round(float(pb), 3),
        metric="price_3y_pct",
        buy_ceiling=levels["buy_ceiling"],
        buy_prefer=levels["buy_prefer"],
        target_buy=levels["target_buy"],
        target_note=levels["target_note"],
        sell_floor=levels["sell_floor"],
    )


def scan_universe(
    symbols: Sequence[str],
    *,
    valley: float = VALLEY,
    peak: float = PEAK,
    fund: Optional[Dict[str, dict]] = None,
    live: Optional[Dict[str, dict]] = None,
    min_bars: int = 280,
    progress_every: int = 200,
) -> Tuple[List[GuFengSignal], List[GuFengSignal]]:
    from lib.data import _read_fund_raw, is_mainboard_a, is_st_name, load_kline_csv, resolve_name

    buys: List[GuFengSignal] = []
    sells: List[GuFengSignal] = []
    fund = fund or {}
    live = live or {}
    # 原始基本面保留 ST 简称；过滤后的 fund 会丢掉 ST 条目导致漏网
    raw_fund = _read_fund_raw()
    for i, sym in enumerate(symbols):
        if not is_mainboard_a(sym):
            continue
        f = fund.get(sym) or {}
        raw_rec = raw_fund.get(sym) or {}
        seed_name = str(raw_rec.get("name") or f.get("name") or "")
        name = resolve_name(sym, seed_name, raw_fund or fund)
        if raw_rec.get("st") or is_st_name(name):
            continue
        df = load_kline_csv(sym, min_bars=min_bars)
        if df is None:
            continue
        pe = f.get("pe")
        pb = f.get("pb")
        try:
            pe = float(pe) if pe is not None else None
        except (TypeError, ValueError):
            pe = None
        try:
            pb = float(pb) if pb is not None else None
        except (TypeError, ValueError):
            pb = None
        live_px = None
        if sym in live:
            try:
                live_px = float(live[sym].get("price") or live[sym].get("px"))
            except (TypeError, ValueError):
                live_px = None
        sig = score_symbol(df, sym, name=name, pe=pe, pb=pb, live_px=live_px, valley=valley, peak=peak)
        if sig is None:
            continue
        # soft PE sanity: skip obvious garbage PE for buy list display
        if sig.side == "buy" and pe is not None and pe <= 0:
            # still allow if using price pct; keep but pe shown
            pass
        if sig.side == "buy":
            buys.append(sig)
        else:
            sells.append(sig)
        if progress_every and (i + 1) % progress_every == 0:
            print(f"  …已扫 {i+1}/{len(symbols)}  买={len(buys)} 卖={len(sells)}", flush=True)
    buys.sort(key=lambda s: s.val_pct)
    sells.sort(key=lambda s: -s.val_pct)
    return buys, sells


TARGET_CSV_FIELDS = [
    "buy_ceiling",
    "buy_prefer",
    "target_buy",
    "target_note",
    "sell_floor",
]


def signals_to_rows(sigs: Sequence[GuFengSignal]) -> List[Dict[str, Any]]:
    rows = []
    for s in sigs:
        row = {
            "side": "买入观察" if s.side == "buy" else "卖出观察",
            "code": s.sym.replace("sh", "").replace("sz", ""),
            "sym": s.sym,
            "name": s.name,
            "ref_price": s.price,
            "val_pct": s.val_pct,
            "val_pct_pct": round(s.val_pct * 100, 1),
            "pe": s.pe,
            "pb": s.pb,
            "asof": s.date,
            "metric": s.metric,
            "buy_ceiling": s.buy_ceiling,
            "buy_prefer": s.buy_prefer,
            "target_buy": s.target_buy,
            "target_note": s.target_note,
            "sell_floor": s.sell_floor,
        }
        if s.divergence is not None:
            row["divergence"] = bool(s.divergence)
            row["div_ago"] = s.div_ago
            if s.side == "buy" and s.divergence:
                row["side"] = "强化买入"  # 一谷∩底背离（观察层）
        if s.turnover is not None:
            row["turnover"] = s.turnover
        rows.append(row)
    return rows


def _parse_float(v) -> Optional[float]:
    if v is None or v == "" or str(v).strip().lower() in ("none", "nan", "—", "-"):
        return None
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def enrich_rows_target_prices(
    rows: Sequence[Dict[str, Any]],
    *,
    min_bars: int = 280,
    valley: float = VALLEY,
    peak: float = PEAK,
    prefer: float = PREFER,
) -> List[Dict[str, Any]]:
    """若行缺少目标价列，按缓存 K 线补算（与扫描同一窗口）。"""
    from lib.data import load_kline_csv

    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        has = _parse_float(row.get("buy_ceiling")) is not None
        if has and _parse_float(row.get("target_buy")) is not None:
            # 仍刷新 target_buy 相对现价（CSV 可能只有 ceiling）
            ceil = _parse_float(row.get("buy_ceiling"))
            px = _parse_float(row.get("ref_price"))
            if ceil is not None and px is not None:
                if px <= ceil + 1e-9:
                    row["target_buy"] = round(px, 4)
                    row["target_note"] = row.get("target_note") or "现价已在一谷"
                else:
                    row["target_buy"] = round(ceil, 4)
                    row.setdefault("target_note", "待回落至一谷上沿")
            out.append(row)
            continue
        sym = str(row.get("sym") or "")
        if not sym:
            code = str(row.get("code") or "")
            if code.startswith(("6", "9")):
                sym = "sh" + code
            elif code:
                sym = "sz" + code
        df = load_kline_csv(sym, min_bars=min_bars) if sym else None
        if df is None or "close" not in df.columns:
            row.setdefault("buy_ceiling", None)
            row.setdefault("buy_prefer", None)
            row.setdefault("target_buy", None)
            row.setdefault("target_note", None)
            row.setdefault("sell_floor", None)
            out.append(row)
            continue
        d = df if df["date"].is_monotonic_increasing else df.sort_values("date")
        close = d["close"].astype(float).to_numpy()
        px = _parse_float(row.get("ref_price"))
        if px is None:
            px = float(close[-1])
        levels = compute_target_levels(close, px, valley=valley, peak=peak, prefer=prefer)
        row["buy_ceiling"] = levels["buy_ceiling"]
        row["buy_prefer"] = levels["buy_prefer"]
        row["target_buy"] = levels["target_buy"]
        row["target_note"] = levels["target_note"]
        row["sell_floor"] = levels["sell_floor"]
        out.append(row)
    return out


def clamp_div_lookback(lookback: int = 5) -> int:
    """回测口径：近 3～5 日底背离。"""
    try:
        lb = int(lookback)
    except (TypeError, ValueError):
        lb = 5
    return max(3, min(5, lb))


def annotate_buy_divergence(
    buys: Sequence[GuFengSignal],
    *,
    lookback: int = 5,
    min_bars: int = 60,
) -> List[GuFengSignal]:
    """只对一谷买侧候选算底背离（复用 patterns），卖侧不调用。"""
    from lib.data import load_kline_csv
    from lib.patterns import recent_pattern_hit

    lb = clamp_div_lookback(lookback)
    out: List[GuFengSignal] = []
    for s in buys:
        df = load_kline_csv(s.sym, min_bars=min_bars)
        hit, ago = recent_pattern_hit(df, "底背离", lookback=lb)
        s.divergence = bool(hit)
        s.div_ago = ago if hit else None
        out.append(s)
    return out


def annotate_rows_divergence(
    rows: Sequence[Dict[str, Any]],
    *,
    lookback: int = 5,
    min_bars: int = 60,
    buy_only: bool = True,
) -> List[Dict[str, Any]]:
    """对 CSV/API 行字典标注 divergence（默认只处理买侧）。"""
    from lib.data import load_kline_csv
    from lib.patterns import recent_pattern_hit

    lb = clamp_div_lookback(lookback)
    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        side = str(row.get("side") or "")
        is_buy = ("买" in side) or (str(row.get("side_raw") or "") == "buy")
        if buy_only and not is_buy:
            row.setdefault("divergence", None)
            row.setdefault("div_ago", None)
            out.append(row)
            continue
        sym = str(row.get("sym") or "")
        if not sym:
            code = str(row.get("code") or "")
            if code.startswith(("6", "9")):
                sym = "sh" + code
            elif code:
                sym = "sz" + code
        df = load_kline_csv(sym, min_bars=min_bars) if sym else None
        hit, ago = recent_pattern_hit(df, "底背离", lookback=lb)
        row["divergence"] = bool(hit)
        row["div_ago"] = ago if hit else None
        if is_buy and hit and "强化" not in side:
            row["side"] = "强化买入"
        out.append(row)
    return out


def resolve_turnover_max(
    *,
    low_turnover: bool = False,
    turnover_max: Optional[float] = None,
    default: float = 1.0,
) -> Optional[float]:
    """解析缩量阈值：--low-turnover → 默认 1.0%；--turnover-max 显式覆盖。单位=换手率%。"""
    if turnover_max is not None:
        try:
            v = float(turnover_max)
        except (TypeError, ValueError):
            return None
        return v if np.isfinite(v) and v >= 0 else None
    if low_turnover:
        return float(default)
    return None


def _fund_turn(fund: Optional[Dict], sym: str) -> Optional[float]:
    if not fund or not sym:
        return None
    raw = (fund.get(sym) or {}).get("turn")
    return _parse_float(raw)


def annotate_buy_turnover(
    buys: Sequence[GuFengSignal],
    fund: Optional[Dict] = None,
) -> List[GuFengSignal]:
    """给一谷买侧候选挂上基本面换手率%（不筛，只标注）。"""
    out: List[GuFengSignal] = []
    for s in buys:
        s.turnover = _fund_turn(fund, s.sym)
        out.append(s)
    return out


def annotate_rows_turnover(
    rows: Sequence[Dict[str, Any]],
    fund: Optional[Dict] = None,
    *,
    buy_only: bool = False,
) -> List[Dict[str, Any]]:
    """对行字典标注 turnover（缺列时从 fund 补）。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        side = str(row.get("side") or "")
        is_buy = ("买" in side) or (str(row.get("side_raw") or "") == "buy")
        if buy_only and not is_buy:
            out.append(row)
            continue
        existing = _parse_float(row.get("turnover"))
        if existing is not None:
            row["turnover"] = existing
            out.append(row)
            continue
        sym = str(row.get("sym") or "")
        if not sym:
            code = str(row.get("code") or "")
            if code.startswith(("6", "9")):
                sym = "sh" + code
            elif code:
                sym = "sz" + code
        row["turnover"] = _fund_turn(fund, sym)
        out.append(row)
    return out


def filter_rows_by_turnover(
    rows: Sequence[Dict[str, Any]],
    turnover_max: float,
    *,
    buy_only: bool = True,
) -> List[Dict[str, Any]]:
    """买侧保留换手率 < turnover_max（单位%）；缺换手数据的买侧剔除。卖侧默认原样保留。"""
    thr = float(turnover_max)
    out: List[Dict[str, Any]] = []
    for r in rows:
        side = str(r.get("side") or "")
        is_buy = ("买" in side) or (str(r.get("side_raw") or "") == "buy")
        if buy_only and not is_buy:
            out.append(r)
            continue
        if not is_buy and buy_only:
            out.append(r)
            continue
        t = _parse_float(r.get("turnover"))
        if t is not None and t < thr:
            out.append(r)
    return out
