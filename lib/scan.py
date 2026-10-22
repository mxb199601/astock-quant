# -*- coding: utf-8 -*-
"""全主板结构扫描 v2.1：结构条件统一走 factors.structure_flags_last。

tech_pass = 质量层 stock_veto + 公式 base（含大盘）+ plus≥5
strict/winrate/robust 标志与 formula3_mask / formula3_today 同源，避免双份漂移。
完整通过：仅绿灯；优先 robust → winrate → elite → strict。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .data import list_cached_symbols, load_fundamentals, load_kline_csv, market_regime, universe_mom60
from .factors import index_low_vol_today, structure_flags_last
from .paths import OUTPUT
from .quality import attach_quality


def run_structure_scan(
    min_bars: int = 260,
    require_low_vol: bool = True,
    low_vol_q: float = 0.5,
) -> Dict[str, Any]:
    """返回 regime / full_pass / tech_pass / strict_struct / rs_leaders / near。

    require_low_vol 默认 True（robust v5）；完整通过优先 robust（低波日）。
    """
    regime = market_regime()
    fund = load_fundamentals(False)
    umom = universe_mom60()
    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None:
        return {"ok": False, "error": "缺少沪深300缓存", "regime": regime}

    ic = idx["close"]
    idx20 = float((ic.iloc[-1] / ic.iloc[-21] - 1) * 100)
    lv_state = index_low_vol_today(idx, q=low_vol_q)
    idx_low_vol = bool(lv_state.get("low_vol"))
    # 与回测一致：用日线 regime 布尔（market_regime 可能 live）
    idx_up = bool(regime.get("above_ma20"))
    idx_up60 = bool(regime.get("above_ma60"))
    syms = list_cached_symbols(mainboard_only=True, drop_st=True)

    tech_pass: List[dict] = []
    near: List[dict] = []
    rs_pool: List[dict] = []

    for sym in syms:
        df = load_kline_csv(sym, min_bars=min_bars)
        if df is None or len(df) < min_bars:
            continue

        flags = structure_flags_last(
            df,
            idx20=idx20,
            idx_up=idx_up,
            idx_up60=idx_up60,
            require_low_vol=require_low_vol,
            low_vol_q=low_vol_q,
            idx_low_vol=idx_low_vol,
        )
        if not flags.get("ok"):
            continue

        c, v = df["close"], df["vol"]
        px = float(flags["px"])
        ma20 = float(flags["ma20"])
        zf60 = float(flags["zf60"])
        rs20 = float(flags["rs20"])
        bias = float(flags["bias"])
        vr = float(flags["vr"])
        rsi_val = float(flags["rsi"])
        ma20_up = bool(flags["ma20_up"])
        has250 = bool(flags["has250"])

        f = fund.get(sym, {})
        name = f.get("name", sym)
        pe = _f(f.get("pe"))
        pb = _f(f.get("pb"))
        turn = _f(f.get("turn"))
        pctile = float(np.searchsorted(umom, zf60) / len(umom) * 100) if len(umom) else 50.0

        # 扫盘额外质量层（估值/破位）— 不在 formula3_mask 内，有意保留
        ma250 = c.rolling(250).mean()
        stock_veto = (
            has250
            and px > float(ma250.iloc[-1])
            and float(ma250.iloc[-1]) >= float(ma250.iloc[-21])
            and px > float(df["low"].rolling(60).min().iloc[-1]) * 1.05
            and px > ma20
            and (pb is None or pb < 10)
            and (pe is None or pe > 0)
        )

        # 公式层（同源）
        stock_f3 = bool(flags["base"])  # base 已含大盘 idx_up
        strict_extra = bool(flags["strict"])
        # winrate 在 factors 是完整 winrate；scan 旧字段 winrate_extra 语义=winrate
        winrate_extra = bool(flags["winrate"])
        # elite 用于完整通过回退；robust 同源
        elite_ok = bool(flags["elite"])
        robust_extra = bool(flags["robust"])

        plus = sum(
            [
                flags["ma5"] > flags["ma10"] > flags["ma20"] > flags["ma60"],
                rs20 > idx20,
                pctile >= 70,
                turn is not None and 2 <= turn <= 15,
                1 < vr < 3,
                0 < zf60 < 60,
                bias < 15,
                rsi_val < 70,
                float(v.iloc[-1]) > float(v.rolling(5).mean().iloc[-1] * 1.5),
                ma20_up,
            ]
        )
        row = {
            "sym": sym,
            "name": name,
            "px": round(px, 2),
            "plus": int(plus),
            "pctile": round(pctile),
            "zf60": round(zf60, 1),
            "rsi": round(rsi_val, 1),
            "rs20": round(rs20, 1),
            "bias": round(bias, 1),
            "vr": round(vr, 2),
            "pe": round(pe, 1) if pe is not None else None,
            "pb": round(pb, 2) if pb is not None else None,
            "turn": round(turn, 2) if turn is not None else None,
            "stock_veto": bool(stock_veto),
            "stock_f3": stock_f3,
            "strict_extra": strict_extra,
            "elite_ok": elite_ok,
            "winrate_extra": winrate_extra,
            "robust_extra": robust_extra,
            "ma20_up": ma20_up,
            "excess20": round(rs20 - idx20, 1),
            "idx_low_vol": idx_low_vol,
            "structure_src": "structure_flags_last",
        }
        row = attach_quality(row, idx20)

        if stock_veto and stock_f3 and plus >= 5:
            tech_pass.append(row)
        elif (stock_veto and plus >= 5) or (stock_f3 and plus >= 4):
            near.append(row)

        # 红灯相对强观察（不依赖公式 base / 大盘）
        if (
            rs20 > idx20 + 3
            and px > ma20
            and has250
            and px > float(ma250.iloc[-1])
            and rsi_val < 70
            and bias < 15
            and zf60 > -5
        ):
            rs_pool.append(row)

    tech_pass.sort(key=lambda x: (x.get("qscore", 0), x["plus"], x["pctile"]), reverse=True)
    near.sort(key=lambda x: (x.get("qscore", 0), x["plus"], x["pctile"]), reverse=True)
    strict_struct = [r for r in tech_pass if r.get("strict_extra")]
    strict_struct.sort(key=lambda x: x.get("qscore", 0), reverse=True)
    winrate_struct = [r for r in tech_pass if r.get("winrate_extra")]
    winrate_struct.sort(key=lambda x: x.get("qscore", 0), reverse=True)
    robust_struct = [r for r in tech_pass if r.get("robust_extra")]
    robust_struct.sort(key=lambda x: x.get("qscore", 0), reverse=True)
    elite_struct = [r for r in tech_pass if r.get("elite_ok")]
    elite_struct.sort(key=lambda x: x.get("qscore", 0), reverse=True)

    rs_pool.sort(key=lambda x: (x.get("excess20", 0), x.get("qscore", 0)), reverse=True)
    rs_leaders = rs_pool[:40]

    market_ok = bool(regime.get("above_ma20") and regime.get("above_ma60"))
    light = regime.get("light") or "未知"
    if market_ok:
        if robust_struct:
            tier = "完整通过(v5-robust+低波)" if require_low_vol else "完整通过(v4-robust反热)"
            base_list = robust_struct
        elif winrate_struct:
            base_list, tier = winrate_struct, "完整通过(v3-winrate)"
        elif elite_struct:
            base_list, tier = elite_struct, "完整通过(v3-elite)"
        else:
            base_list, tier = strict_struct, "完整通过(strict)"
        full_pass = [dict(r, tier=tier) for r in base_list]
    else:
        full_pass = []

    os.makedirs(OUTPUT, exist_ok=True)
    pd.DataFrame(tech_pass).to_csv(
        os.path.join(OUTPUT, "pass_stock_structure.csv"), index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(near[:200]).to_csv(
        os.path.join(OUTPUT, "near_pass.csv"), index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(full_pass).to_csv(
        os.path.join(OUTPUT, "full_pass.csv"), index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(strict_struct).to_csv(
        os.path.join(OUTPUT, "strict_structure.csv"), index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(winrate_struct).to_csv(
        os.path.join(OUTPUT, "winrate_structure.csv"), index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(robust_struct).to_csv(
        os.path.join(OUTPUT, "robust_structure.csv"), index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(rs_leaders).to_csv(
        os.path.join(OUTPUT, "rs_leaders.csv"), index=False, encoding="utf-8-sig"
    )

    lv_txt = (
        f"指数低波={'是' if idx_low_vol else '否'}"
        f"(vol={lv_state.get('vol')} thr={lv_state.get('thr')})"
    )
    if market_ok:
        note = (
            f"大盘{light}：完整通过 {len(full_pass)} 只"
            f"（算法v5：robust=反热+低波默认；低波={'开' if require_low_vol else '关'}；"
            f"{lv_txt}；robust{len(robust_struct)}/winrate{len(winrate_struct)}/"
            f"elite{len(elite_struct)}/严{len(strict_struct)}）"
        )
    else:
        note = (
            f"大盘{light}：完整通过 0 只（需MA20+MA60）。"
            f"结构{len(tech_pass)}/严{len(strict_struct)}/robust{len(robust_struct)}/"
            f"相对强{len(rs_leaders)} 仅观察。v5 robust 默认要指数低波。"
            f"{lv_txt}。PATH 勿信 OPT；tight:TP4/SL3。"
        )

    return {
        "ok": True,
        "algo": "v5-robust-lowvol",
        "regime": regime,
        "pool_size": len(syms),
        "idx20": idx20,
        "market_ok": market_ok,
        "require_low_vol": bool(require_low_vol),
        "idx_low_vol": idx_low_vol,
        "idx_vol": lv_state.get("vol"),
        "idx_vol_thr": lv_state.get("thr"),
        "full_pass": full_pass,
        "full_pass_count": len(full_pass),
        "tech_pass": tech_pass,
        "strict_struct": strict_struct,
        "winrate_struct": winrate_struct,
        "robust_struct": robust_struct,
        "elite_struct": elite_struct,
        "rs_leaders": rs_leaders,
        "near": near[:100],
        "exit_hint": "tight=TP4/SL3（PATH较优）| wr=TP5/SL3；看PATH勿只看OPT",
        "note": note,
    }


def score_codes(codes: List[str], mode: str = "strict") -> List[dict]:
    from .data import index_ret20, to_sym
    from .factors import score_one, verdict

    fund = load_fundamentals(False)
    umom = universe_mom60()
    idx20 = index_ret20()
    out = []
    for code in codes:
        sym = to_sym(code)
        name, sym, r = score_one(sym, fund, idx20, umom, mode=mode)
        if r is None:
            out.append({"sym": sym, "name": name, "error": "数据不足"})
            continue
        item = {
            "sym": sym,
            "name": name,
            "verdict": verdict(r),
            "px": r["px"],
            "plus": r["psum"],
            "f3": r["f3"],
            "light": r["light"],
            "rsi": r["rsi"],
            "zf60": r["zf60"],
            "veto_pass": r["veto_pass"],
            "veto": [{"name": n, "ok": v} for n, v in r["veto"]],
            "plus_items": [{"name": n, "ok": v} for n, v in r["plus"]],
            "exit_hint": r.get("exit_hint", ""),
        }
        item["qscore"] = attach_quality(
            {
                "rs20": r["rs20"],
                "rsi": r["rsi"],
                "bias": r["bias"],
                "pctile": r["pctile"] or 50,
                "vr": 1.5,
                "plus": r["psum"],
                "zf60": r["zf60"],
                "pe": r["pe"],
                "pb": r["pb"],
            },
            idx20,
        )["qscore"]
        out.append(item)
    out.sort(key=lambda x: (x.get("veto_pass", False), x.get("qscore", 0)), reverse=True)
    return out


def _f(x):
    try:
        if x is None:
            return None
        v = float(x)
        return None if v != v else v
    except Exception:
        return None
