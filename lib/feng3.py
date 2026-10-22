# -*- coding: utf-8 -*-
"""逢3必变 · 局部高低点研究模块（v2 conf）— 非荐股、不进 action。

主定义（编码 A）：交替 zigzag 上连续 3 个同侧摆动极值单调延伸，
第 3 个确认日触发 pred=LOW/HIGH；支撑/阻力邻近仍记录，但 conf高仅看峰结构同意（v2）。

规格：docs/plans/2026-10-13-yijing-feng3-hl.md；峰谷正名 v3：docs/plans/2026-10-13-yijing-fenggu-define.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


@dataclass
class Swing:
    center_idx: int
    conf_idx: int
    kind: str  # "top" | "bot"
    price: float


def detect_swings(
    high: np.ndarray,
    low: np.ndarray,
    swing_k: int = 3,
) -> List[Swing]:
    """确认分型摆动：中心 i 在 [i-k,i+k] 严格极值，确认日 i+k。无前视。"""
    n = len(high)
    k = int(swing_k)
    if n < 2 * k + 1 or k < 1:
        return []
    raw: List[Swing] = []
    for i in range(k, n - k):
        window_h = high[i - k : i + k + 1]
        window_l = low[i - k : i + k + 1]
        conf = i + k
        # 严格唯一极值：中心为窗口内唯一最高/最低
        if float(high[i]) == float(window_h.max()) and np.sum(window_h == high[i]) == 1:
            raw.append(Swing(i, conf, "top", float(high[i])))
        if float(low[i]) == float(window_l.min()) and np.sum(window_l == low[i]) == 1:
            raw.append(Swing(i, conf, "bot", float(low[i])))

    # 按确认日排序；同确认日顶底都出现则丢弃
    raw.sort(key=lambda s: (s.conf_idx, 0 if s.kind == "bot" else 1))
    cleaned: List[Swing] = []
    i = 0
    while i < len(raw):
        if i + 1 < len(raw) and raw[i].conf_idx == raw[i + 1].conf_idx and raw[i].kind != raw[i + 1].kind:
            i += 2
            continue
        cleaned.append(raw[i])
        i += 1
    return cleaned


def build_zigzag(swings: Sequence[Swing]) -> List[Swing]:
    """顶底交替；同向取更极端。"""
    zz: List[Swing] = []
    for s in swings:
        if not zz:
            zz.append(s)
            continue
        last = zz[-1]
        if s.kind == last.kind:
            if s.kind == "top" and s.price >= last.price:
                zz[-1] = s
            elif s.kind == "bot" and s.price <= last.price:
                zz[-1] = s
            # 同向但不更极端：忽略
        else:
            zz.append(s)
    return zz


def _structure_tag(zz: Sequence[Swing]) -> str:
    """用最近两侧顶底描述峰结构：HH/HL / LH/LL / mixed / flat。"""
    tops = [s for s in zz if s.kind == "top"]
    bots = [s for s in zz if s.kind == "bot"]
    if len(tops) < 2 or len(bots) < 2:
        return "flat"
    t1, t2 = tops[-2], tops[-1]
    b1, b2 = bots[-2], bots[-1]
    hh = t2.price > t1.price
    hl = b2.price > b1.price
    lh = t2.price < t1.price
    ll = b2.price < b1.price
    if hh and hl:
        return "HH_HL"
    if lh and ll:
        return "LH_LL"
    if hh and ll:
        return "HH_LL"
    if lh and hl:
        return "LH_HL"
    return "mixed"


def _near_cluster(price: float, refs: Sequence[float], near_pct: float) -> Tuple[bool, Optional[float]]:
    if not refs:
        return False, None
    # 最近若干参考价的中位数作为簇
    arr = np.asarray(refs[-5:], dtype=float)
    cluster = float(np.median(arr))
    if cluster <= 0:
        return False, cluster
    dist = abs(price - cluster) / cluster * 100.0
    return dist <= float(near_pct), round(cluster, 4)


def detect_feng3_events(
    df: pd.DataFrame,
    swing_k: int = 3,
    near_pct: float = 2.0,
) -> List[Dict[str, Any]]:
    """对单票 OHLC 产出逢3事件列表（确认日、无前视）。"""
    if df is None or len(df) < 2 * swing_k + 10:
        return []
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    dates = df["date"].astype(str).values

    swings = detect_swings(high, low, swing_k=swing_k)
    zz = build_zigzag(swings)
    if len(zz) < 6:
        return []

    events: List[Dict[str, Any]] = []
    # 扫描每个新 zigzag 点加入后，检查是否形成三连同侧
    for end in range(5, len(zz)):
        sub = zz[: end + 1]
        last = sub[-1]
        conf_i = int(last.conf_idx)
        if conf_i >= len(close):
            continue

        pred = None
        trigger = float(last.price)
        if last.kind == "bot":
            bots = [s for s in sub if s.kind == "bot"]
            if len(bots) >= 3:
                L1, L2, L3 = bots[-3], bots[-2], bots[-1]
                if L3 is last and L3.price <= L2.price <= L1.price:
                    pred = "LOW"
        elif last.kind == "top":
            tops = [s for s in sub if s.kind == "top"]
            if len(tops) >= 3:
                H1, H2, H3 = tops[-3], tops[-2], tops[-1]
                if H3 is last and H3.price >= H2.price >= H1.price:
                    pred = "HIGH"
        if pred is None:
            continue

        # Layer B: SR 邻近（用触发前的同侧摆动价，不含当前）
        if pred == "LOW":
            refs = [s.price for s in sub[:-1] if s.kind == "bot"][-8:]
            near_ok, cluster = _near_cluster(trigger, refs, near_pct)
            sr_side = "support"
        else:
            refs = [s.price for s in sub[:-1] if s.kind == "top"][-8:]
            near_ok, cluster = _near_cluster(trigger, refs, near_pct)
            sr_side = "resist"

        # Layer C: 峰结构（用触发前的 zz，避免把当前点算进结构偏置过大）
        struct = _structure_tag(sub[:-1] if len(sub) > 1 else sub)
        if pred == "LOW":
            struct_ok = struct in ("LH_LL", "HH_LL", "mixed", "LH_HL")
            # 空头耗尽更理想
            struct_agree = struct in ("LH_LL", "HH_LL")
        else:
            struct_agree = struct in ("HH_HL", "LH_HL")
            struct_ok = struct_agree or struct in ("mixed", "HH_LL")

        # v2 conf: drop near_SR from 高 — near hurt PATH; 高=struct_agree alone
        # near alone still 中; neither → 低. Symmetric for LOW/HIGH.
        if struct_agree:
            conf = "高"
        elif near_ok:
            conf = "中"
        else:
            conf = "低"

        events.append(
            {
                "date": str(dates[conf_i]),
                "bar_idx": conf_i,
                "center_idx": int(last.center_idx),
                "pred": pred,
                "conf": conf,
                "trigger_price": round(trigger, 4),
                "close": round(float(close[conf_i]), 4),
                "sr_side": sr_side,
                "sr_cluster": cluster,
                "near_ok": bool(near_ok),
                "structure": struct,
                "struct_agree": bool(struct_agree),
                "swing_k": int(swing_k),
            }
        )

    # 同一确认日去重（保留置信更高）
    rank = {"高": 2, "中": 1, "低": 0}
    by_day: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in events:
        key = (e["date"], e["pred"])
        if key not in by_day or rank[e["conf"]] > rank[by_day[key]["conf"]]:
            by_day[key] = e
    out = list(by_day.values())
    out.sort(key=lambda x: x["bar_idx"])
    return out


def label_series(df: pd.DataFrame, swing_k: int = 3, near_pct: float = 2.0) -> pd.DataFrame:
    """返回与 df 对齐的标签列（研究用）。"""
    n = len(df)
    pred = np.array([""] * n, dtype=object)
    conf = np.array([""] * n, dtype=object)
    for e in detect_feng3_events(df, swing_k=swing_k, near_pct=near_pct):
        i = e["bar_idx"]
        if 0 <= i < n:
            pred[i] = e["pred"]
            conf[i] = e["conf"]
    out = df.copy()
    out["feng3_pred"] = pred
    out["feng3_conf"] = conf
    return out


def evaluate_event_forward(
    df: pd.DataFrame,
    event: Dict[str, Any],
    hit_n: int = 10,
    hold_m: int = 20,
    mae_tol: float = 2.0,
    cost_bps: float = 15.0,
    exit_mode: str = "tight",
) -> Dict[str, Any]:
    """单事件前瞻。

    hit_extreme / MAE：从**摆动中心**向前 N 根，看标记价是否仍接近窗内真极值
    （衡量「标到的点是否站得住」；确认有滞后，入场仍用确认日收盘）。

    PATH / 固定持有：自**确认日收盘**起算（无前视成交）。
    """
    from .metrics import apply_roundtrip_cost
    from .stress import exit_path_daily

    i = int(event["bar_idx"])  # 确认日
    center = int(event.get("center_idx", i))
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    n = len(close)
    pred = event["pred"]
    trigger = float(event["trigger_price"])
    entry = float(close[i])

    end_n = min(n - 1, center + hit_n)
    end_m = min(n - 1, i + hold_m)
    row = dict(event)

    if center >= n or entry <= 0 or trigger <= 0:
        row.update(
            {
                "hit_extreme": False,
                "mae": None,
                "ret_fixed": None,
                "path_ret": None,
                "up_fixed": None,
                "down_fixed": None,
            }
        )
        return row

    # 持续性极值：中心之后 N 根内是否又明显创新低/高
    w_h = high[center : end_n + 1]
    w_l = low[center : end_n + 1]
    if pred == "LOW":
        mn = float(w_l.min()) if len(w_l) else trigger
        mae = (trigger - mn) / trigger * 100.0  # >0 表示确认后/窗内又下探
        hit = mae <= float(mae_tol)
    else:
        mx = float(w_h.max()) if len(w_h) else trigger
        mae = (mx - trigger) / trigger * 100.0
        hit = mae <= float(mae_tol)

    ret_fixed = (float(close[end_m]) / entry - 1.0) * 100.0 if end_m > i else None
    path_ret = None
    if i + hold_m < n:
        path_ret = exit_path_daily(
            entry,
            high[i : i + hold_m + 1],
            low[i : i + hold_m + 1],
            close[i : i + hold_m + 1],
            exit_mode,
            cost_bps,
            hold_m,
            "sl",
        )
    elif end_m > i and ret_fixed is not None:
        path_ret = apply_roundtrip_cost(ret_fixed, cost_bps)

    row["hit_extreme"] = bool(hit)
    row["mae"] = round(float(mae), 3)
    row["ret_fixed"] = None if ret_fixed is None else round(float(ret_fixed), 3)
    row["path_ret"] = None if path_ret is None else round(float(path_ret), 3)
    row["up_fixed"] = None if ret_fixed is None else bool(ret_fixed > 0)
    row["down_fixed"] = None if ret_fixed is None else bool(ret_fixed < 0)
    return row


def scan_symbol(
    df: pd.DataFrame,
    sym: str,
    swing_k: int = 3,
    near_pct: float = 2.0,
    hit_n: int = 10,
    hold_m: int = 20,
    mae_tol: float = 2.0,
    cost_bps: float = 15.0,
    exit_mode: str = "tight",
) -> List[Dict[str, Any]]:
    events = detect_feng3_events(df, swing_k=swing_k, near_pct=near_pct)
    rows = []
    for e in events:
        r = evaluate_event_forward(
            df,
            e,
            hit_n=hit_n,
            hold_m=hold_m,
            mae_tol=mae_tol,
            cost_bps=cost_bps,
            exit_mode=exit_mode,
        )
        r["sym"] = sym
        rows.append(r)
    return rows


def find_next_opposite_swing(
    zz: Sequence[Swing],
    after_conf_idx: int,
    after_center_idx: int,
    pred: str,
    max_bars: int,
) -> Optional[Swing]:
    """确认日之后、max_bars 内（对侧摆动中心相对 after_center）的下一对侧摆动。

    LOW → 下一 top；HIGH → 下一 bot。无则 None。
    """
    want = "top" if pred == "LOW" else "bot"
    deadline = int(after_center_idx) + int(max_bars)
    best: Optional[Swing] = None
    for s in zz:
        if s.conf_idx <= after_conf_idx:
            continue
        if s.kind != want:
            continue
        if int(s.center_idx) > deadline:
            continue
        if best is None or s.conf_idx < best.conf_idx:
            best = s
    return best


def evaluate_hl_level_error(
    df: pd.DataFrame,
    event: Dict[str, Any],
    zz: Sequence[Swing],
    n_bars: int = 20,
) -> Dict[str, Any]:
    """区间高低价预测误差（固定 N 前瞻窗，避免「含起点极值」近零误差）。

    预测价 = 第3峰/谷 trigger_price。
    真实价 = [center, center+N] 窗内真实极值（LOW→min low，HIGH→max high）。
    另记录 N 内下一对侧摆动（found_opp），不作为真实价终点（否则几乎永为 pred）。
    """
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    n = len(high)
    conf_i = int(event["bar_idx"])
    center = int(event.get("center_idx", conf_i))
    pred = str(event["pred"])
    pred_level = float(event["trigger_price"])

    row: Dict[str, Any] = {
        "n_bars": int(n_bars),
        "pred_level": round(pred_level, 4),
        "true_level": None,
        "abs_err": None,
        "pct_err": None,
        "signed_pct_err": None,
        "hit_2pct": False,
        "hit_5pct": False,
        "hit_10pct": False,
        "found_opp": False,
        "opp_price": None,
        "opp_center_idx": None,
        "opp_conf_idx": None,
        "opp_bars_from_center": None,
        "window_end_idx": None,
        "censored": False,
    }
    if center < 0 or center >= n or pred_level <= 0:
        return row

    end = min(n - 1, center + int(n_bars))
    row["window_end_idx"] = int(end)
    # 样本末不足 N 根 → censored 标记（仍用可得窗）
    row["censored"] = bool(center + int(n_bars) > n - 1)

    opp = find_next_opposite_swing(zz, conf_i, center, pred, n_bars)
    if opp is not None:
        row["found_opp"] = True
        row["opp_price"] = round(float(opp.price), 4)
        row["opp_center_idx"] = int(opp.center_idx)
        row["opp_conf_idx"] = int(opp.conf_idx)
        row["opp_bars_from_center"] = int(opp.center_idx - center)

    if pred == "LOW":
        true_level = float(low[center : end + 1].min())
        signed = (true_level - pred_level) / pred_level * 100.0
    else:
        true_level = float(high[center : end + 1].max())
        signed = (true_level - pred_level) / pred_level * 100.0

    abs_err = abs(true_level - pred_level)
    pct_err = abs_err / pred_level * 100.0
    row["true_level"] = round(true_level, 4)
    row["abs_err"] = round(abs_err, 4)
    row["pct_err"] = round(pct_err, 4)
    row["signed_pct_err"] = round(signed, 4)
    row["hit_2pct"] = bool(pct_err <= 2.0)
    row["hit_5pct"] = bool(pct_err <= 5.0)
    row["hit_10pct"] = bool(pct_err <= 10.0)
    return row


# ---------------------------------------------------------------------------
# v3：周易峰/谷（阴穷/阳穷）— 不替换 v2 detect_feng3_events
# 规格：docs/plans/2026-10-13-yijing-fenggu-define.md
# ---------------------------------------------------------------------------


def _leg_amp_ok(zz: Sequence[Swing], a: Swing, b: Swing, min_leg_pct: float) -> bool:
    """a→b 之间至少有一段折点腿波幅 ≥ min_leg_pct（实质消长）。"""
    if min_leg_pct <= 0:
        return True
    zz_list = list(zz)
    try:
        ia = zz_list.index(a)
        ib = zz_list.index(b)
    except ValueError:
        return False
    if ib <= ia:
        return False
    thr = float(min_leg_pct)
    for j in range(ia, ib):
        p0 = float(zz_list[j].price)
        p1 = float(zz_list[j + 1].price)
        base = min(p0, p1)
        if base <= 0:
            continue
        if abs(p1 - p0) / base * 100.0 >= thr:
            return True
    return False


def detect_fenggu_v3_events(
    df: pd.DataFrame,
    swing_k: int = 3,
    min_leg_pct: float = 3.0,
    require_bian: bool = True,
    near_pct: float = 2.0,
    relax_equal: bool = False,
) -> List[Dict[str, Any]]:
    """周易峰/谷：仅阴穷/阳穷点。

    谷：严格三连降底 + 腿波幅 +（可选）确认日收盘 > 昨收（阳始）。
    峰：严格三连升顶 + 腿波幅 +（可选）确认日收盘 < 昨收（阴始）。

    require_bian=False → 对照 v3-B（去掉收盘翻转门）。
    relax_equal=True → 允许 ≤/≥（接近 v2 三连，仍保留波幅/翻转）。
    """
    if df is None or len(df) < 2 * swing_k + 10:
        return []
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    dates = df["date"].astype(str).values

    swings = detect_swings(high, low, swing_k=swing_k)
    zz = build_zigzag(swings)
    if len(zz) < 6:
        return []

    events: List[Dict[str, Any]] = []
    for end in range(5, len(zz)):
        sub = zz[: end + 1]
        last = sub[-1]
        conf_i = int(last.conf_idx)
        if conf_i >= len(close) or conf_i < 1:
            continue

        kind = None  # "谷" | "峰"
        trigger = float(last.price)
        pred = None

        if last.kind == "bot":
            bots = [s for s in sub if s.kind == "bot"]
            if len(bots) >= 3:
                L1, L2, L3 = bots[-3], bots[-2], bots[-1]
                if L3 is last:
                    mono = (
                        (L3.price < L2.price < L1.price)
                        if not relax_equal
                        else (L3.price <= L2.price <= L1.price)
                    )
                    amp_ok = _leg_amp_ok(sub, L1, L3, min_leg_pct) and _leg_amp_ok(
                        sub, L2, L3, min_leg_pct
                    )
                    bian = (not require_bian) or (float(close[conf_i]) > float(close[conf_i - 1]))
                    if mono and amp_ok and bian:
                        kind = "谷"
                        pred = "LOW"
        elif last.kind == "top":
            tops = [s for s in sub if s.kind == "top"]
            if len(tops) >= 3:
                H1, H2, H3 = tops[-3], tops[-2], tops[-1]
                if H3 is last:
                    mono = (
                        (H3.price > H2.price > H1.price)
                        if not relax_equal
                        else (H3.price >= H2.price >= H1.price)
                    )
                    amp_ok = _leg_amp_ok(sub, H1, H3, min_leg_pct) and _leg_amp_ok(
                        sub, H2, H3, min_leg_pct
                    )
                    bian = (not require_bian) or (float(close[conf_i]) < float(close[conf_i - 1]))
                    if mono and amp_ok and bian:
                        kind = "峰"
                        pred = "HIGH"

        if kind is None or pred is None:
            continue

        if pred == "LOW":
            refs = [s.price for s in sub[:-1] if s.kind == "bot"][-8:]
            near_ok, cluster = _near_cluster(trigger, refs, near_pct)
            sr_side = "support"
        else:
            refs = [s.price for s in sub[:-1] if s.kind == "top"][-8:]
            near_ok, cluster = _near_cluster(trigger, refs, near_pct)
            sr_side = "resist"

        struct = _structure_tag(sub[:-1] if len(sub) > 1 else sub)
        if pred == "LOW":
            struct_agree = struct in ("LH_LL", "HH_LL")
        else:
            struct_agree = struct in ("HH_HL", "LH_HL")

        if require_bian and not relax_equal:
            def_id = "v3"
        elif (not require_bian) and not relax_equal:
            def_id = "v3B"
        else:
            def_id = "v3_relax"

        events.append(
            {
                "date": str(dates[conf_i]),
                "bar_idx": conf_i,
                "center_idx": int(last.center_idx),
                "pred": pred,
                "fenggu": kind,
                "conf": "高" if struct_agree else ("中" if near_ok else "低"),
                "trigger_price": round(trigger, 4),
                "close": round(float(close[conf_i]), 4),
                "sr_side": sr_side,
                "sr_cluster": cluster,
                "near_ok": bool(near_ok),
                "structure": struct,
                "struct_agree": bool(struct_agree),
                "swing_k": int(swing_k),
                "min_leg_pct": float(min_leg_pct),
                "require_bian": bool(require_bian),
                "def_id": def_id,
            }
        )

    rank = {"高": 2, "中": 1, "低": 0}
    by_day: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in events:
        key = (e["date"], e["pred"])
        if key not in by_day or rank[e["conf"]] > rank[by_day[key]["conf"]]:
            by_day[key] = e
    out = list(by_day.values())
    out.sort(key=lambda x: x["bar_idx"])
    return out


def scan_symbol_v3(
    df: pd.DataFrame,
    sym: str,
    swing_k: int = 3,
    min_leg_pct: float = 3.0,
    require_bian: bool = True,
    near_pct: float = 2.0,
    hit_n: int = 10,
    hold_m: int = 20,
    mae_tol: float = 2.0,
    cost_bps: float = 15.0,
    exit_mode: str = "tight",
    relax_equal: bool = False,
) -> List[Dict[str, Any]]:
    """v3 峰谷事件 + 既有前瞻标签（hit/MAE/tight PATH）。"""
    events = detect_fenggu_v3_events(
        df,
        swing_k=swing_k,
        min_leg_pct=min_leg_pct,
        require_bian=require_bian,
        near_pct=near_pct,
        relax_equal=relax_equal,
    )
    rows = []
    for e in events:
        r = evaluate_event_forward(
            df,
            e,
            hit_n=hit_n,
            hold_m=hold_m,
            mae_tol=mae_tol,
            cost_bps=cost_bps,
            exit_mode=exit_mode,
        )
        r["sym"] = sym
        rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# 波段 band：在细 zigzag 上筛「大摆动」，连顶/连底成上/下轨（无前视）
# Zoey：连接 major swings，叠在周易峰谷 v3 上；作 PATH 切片上下文
# ---------------------------------------------------------------------------


def filter_major_swings(
    zz: Sequence[Swing],
    min_leg_pct: float = 8.0,
) -> List[Swing]:
    """从细 zigzag 筛 major 波段折点：腿波幅 ≥ min_leg_pct，顶底交替，同向取更极端。

    小反抽不够幅度则忽略（继续沿原方向找更极端点）。无前视：调用方只传入已确认的 zz。
    """
    if not zz:
        return []
    thr = float(min_leg_pct)
    major: List[Swing] = [zz[0]]
    for s in zz[1:]:
        last = major[-1]
        if s.kind == last.kind:
            if s.kind == "top" and s.price >= last.price:
                major[-1] = s
            elif s.kind == "bot" and s.price <= last.price:
                major[-1] = s
            continue
        base = float(last.price)
        if base <= 0:
            continue
        amp = abs(float(s.price) - base) / base * 100.0
        if amp >= thr:
            major.append(s)
        else:
            # 反抽不足：若同向能创更极端，仍更新 last（越过噪声）
            # 实际 s 是对侧；不够幅度则丢弃该对侧，不改 major
            pass
    # 若末点后还有同向更极端（在 zz 尾部因对侧不足未收录），已在同向分支处理
    return major


def _line_at(
    a: Swing,
    b: Swing,
    x: int,
) -> Optional[float]:
    """两点折线在横坐标 x（用 center_idx）上的线性外推。"""
    x0, y0 = int(a.center_idx), float(a.price)
    x1, y1 = int(b.center_idx), float(b.price)
    if x1 == x0:
        return y1
    t = (float(x) - x0) / float(x1 - x0)
    return y0 + t * (y1 - y0)


def band_context_at(
    major: Sequence[Swing],
    bar_idx: int,
    price: float,
    *,
    near_frac: float = 0.25,
) -> Dict[str, Any]:
    """在 bar_idx 用「已确认」major 摆动连顶/连底，给出波段轨上下文。

    仅使用 conf_idx <= bar_idx 的 major 点（确认后才知）。
    """
    empty: Dict[str, Any] = {
        "band_ok": False,
        "band_upper": None,
        "band_lower": None,
        "band_mid": None,
        "band_width_pct": None,
        "band_pos": None,
        "band_pos_bucket": "未知",
        "band_slope": "未知",
        "band_slope_pct_per_bar": None,
        "band_n_major": 0,
        "band_last_top": None,
        "band_last_bot": None,
    }
    confirmed = [s for s in major if int(s.conf_idx) <= int(bar_idx)]
    empty["band_n_major"] = len(confirmed)
    tops = [s for s in confirmed if s.kind == "top"]
    bots = [s for s in confirmed if s.kind == "bot"]
    if tops:
        empty["band_last_top"] = round(float(tops[-1].price), 4)
    if bots:
        empty["band_last_bot"] = round(float(bots[-1].price), 4)
    if len(tops) < 2 or len(bots) < 2:
        return empty

    upper = _line_at(tops[-2], tops[-1], bar_idx)
    lower = _line_at(bots[-2], bots[-1], bar_idx)
    if upper is None or lower is None:
        return empty
    # 若轨交叉/倒置，交换以保持 upper>=lower 语义弱化：记 inverted
    inverted = bool(upper < lower)
    if inverted:
        upper, lower = lower, upper

    mid = 0.5 * (upper + lower)
    width = upper - lower
    if mid <= 0 or width <= 0:
        return empty

    pos = (float(price) - lower) / width  # 0=下轨 1=上轨
    nf = float(near_frac)
    if pos < -0.05:
        bucket = "破下"
    elif pos <= nf:
        bucket = "下轨"
    elif pos < 0.5 - nf * 0.5:
        bucket = "中下"
    elif pos <= 0.5 + nf * 0.5:
        bucket = "中轨"
    elif pos < 1.0 - nf:
        bucket = "中上"
    elif pos <= 1.05:
        bucket = "上轨"
    else:
        bucket = "破上"

    # 下轨斜率（%/bar）：用最近两底
    b0, b1 = bots[-2], bots[-1]
    dx = int(b1.center_idx) - int(b0.center_idx)
    if dx > 0 and b0.price > 0:
        slope_ppb = (float(b1.price) - float(b0.price)) / float(b0.price) * 100.0 / dx
    else:
        slope_ppb = 0.0
    # 也参考上轨斜率，合成：两者同号才标方向
    t0, t1 = tops[-2], tops[-1]
    dxt = int(t1.center_idx) - int(t0.center_idx)
    if dxt > 0 and t0.price > 0:
        slope_top = (float(t1.price) - float(t0.price)) / float(t0.price) * 100.0 / dxt
    else:
        slope_top = 0.0
    # 阈值：约 0.02%/bar ≈ 慢倾
    thr = 0.02
    if slope_ppb > thr and slope_top > -thr:
        slope_tag = "上倾"
    elif slope_ppb < -thr and slope_top < thr:
        slope_tag = "下倾"
    else:
        slope_tag = "走平"

    return {
        "band_ok": True,
        "band_upper": round(float(upper), 4),
        "band_lower": round(float(lower), 4),
        "band_mid": round(float(mid), 4),
        "band_width_pct": round(width / mid * 100.0, 3),
        "band_pos": round(float(pos), 4),
        "band_pos_bucket": bucket,
        "band_slope": slope_tag,
        "band_slope_pct_per_bar": round(float(slope_ppb), 5),
        "band_n_major": len(confirmed),
        "band_last_top": round(float(tops[-1].price), 4),
        "band_last_bot": round(float(bots[-1].price), 4),
        "band_inverted": inverted,
    }


def build_band_series(
    df: pd.DataFrame,
    swing_k: int = 3,
    band_min_leg_pct: float = 8.0,
) -> Tuple[List[Swing], List[Swing], pd.DataFrame]:
    """整段 K 线：细 zz、major zz、逐 bar 波段轨（研究/画图用；逐 bar 用当时已确认点）。"""
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    swings = detect_swings(high, low, swing_k=swing_k)
    zz = build_zigzag(swings)
    major = filter_major_swings(zz, min_leg_pct=band_min_leg_pct)
    rows = []
    for i in range(len(df)):
        ctx = band_context_at(major, i, float(close[i]))
        ctx["bar_idx"] = i
        rows.append(ctx)
    return zz, major, pd.DataFrame(rows)


def annotate_fenggu_with_band(
    df: pd.DataFrame,
    events: Sequence[Dict[str, Any]],
    swing_k: int = 3,
    band_min_leg_pct: float = 8.0,
) -> List[Dict[str, Any]]:
    """给峰谷事件叠波段上下文（major 摆动无前视）。"""
    if not events:
        return []
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    swings = detect_swings(high, low, swing_k=swing_k)
    zz = build_zigzag(swings)
    major = filter_major_swings(zz, min_leg_pct=band_min_leg_pct)
    out: List[Dict[str, Any]] = []
    for e in events:
        r = dict(e)
        i = int(e["bar_idx"])
        px = float(e.get("close", close[i] if 0 <= i < len(close) else 0.0))
        ctx = band_context_at(major, i, px)
        r.update(ctx)
        r["band_min_leg_pct"] = float(band_min_leg_pct)
        # 买点友好标签：谷 + 下轨附近 + 上倾
        pos_b = ctx.get("band_pos_bucket")
        slope = ctx.get("band_slope")
        if e.get("pred") == "LOW":
            r["band_buy_zone"] = bool(
                ctx.get("band_ok")
                and pos_b in ("下轨", "中下", "破下")
                and slope in ("上倾", "走平")
            )
            r["band_sell_zone"] = False
        else:
            r["band_buy_zone"] = False
            r["band_sell_zone"] = bool(
                ctx.get("band_ok")
                and pos_b in ("上轨", "中上", "破上")
                and slope in ("下倾", "走平", "上倾")
            )
        out.append(r)
    return out
