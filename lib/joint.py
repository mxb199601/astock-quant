# -*- coding: utf-8 -*-
"""联合信号 v2 = 完整通过 ∩ MENGα 冷静过滤。

v1 曾用「完整通过 ∩ 涨跌研究偏多」；walk-forward 涨跌研究仅 ~52–54%，接近随机，
作为联合闸门会引入噪声且极慢（每票相似检索）。

v2 改为有证据的副过滤：
  - 完整通过（结构 v4 robust 优先，本身要求大盘绿灯）
  - MENGα：不过热 + 有萌芽 + 不极端拥挤
  - 排序：qscore 为主，menga_pct 为辅

仍是研究「最严可关注」名单，不是荐股。红灯时完整通过为空 → 联合必为 0。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pandas as pd

from .paths import OUTPUT

ALGO_VERSION = "joint_v2"

# MENGα 副过滤（完整通过已经较严；这里只剔除「结构过了但仍过热/无萌芽」）
MIN_MENGA = 45.0
MAX_HEAT = 55.0
MIN_SPARK = 35.0
MAX_CROWD = 75.0


def _load_menga_map(rescan: bool = False) -> Dict[str, dict]:
    """sym -> menga 行。优先读缓存；必要时重扫。"""
    path = os.path.join(OUTPUT, "menga_today.csv")
    if rescan or not os.path.exists(path):
        from .own_alpha import scan_own_alpha

        r = scan_own_alpha(top_n=10**9)
        if not r.get("ok"):
            return {}
        path = r.get("file") or path
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    out: Dict[str, dict] = {}
    for _, r in df.iterrows():
        out[str(r["sym"])] = {
            "menga_pct": float(r["menga_pct"]),
            "heat_pct": float(r["heat_pct"]),
            "wash_pct": float(r["wash_pct"]),
            "spark_pct": float(r["spark_pct"]),
            "crowd_pct": float(r["crowd_pct"]),
            "bounce": float(r["bounce"]) if "bounce" in r and pd.notna(r.get("bounce")) else None,
            "tag": r.get("tag") or "",
        }
    return out


def run_joint_signals(
    require_confidence: tuple = ("中", "高"),  # 兼容旧 CLI，已忽略
    require_pred: int = 1,  # 兼容旧 CLI，已忽略
    use_llm: bool = False,  # 兼容旧接口，已忽略
    max_names: int = 40,
    rescan_menga: bool = False,
    min_menga: float = MIN_MENGA,
    max_heat: float = MAX_HEAT,
    min_spark: float = MIN_SPARK,
    max_crowd: float = MAX_CROWD,
    scan_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """完整通过 ∩ MENGα 冷静过滤。"""
    from .scan import run_structure_scan

    scan = scan_result if scan_result is not None else run_structure_scan()
    if not scan.get("ok"):
        return {"ok": False, "error": scan.get("error"), "joint": [], "algo": ALGO_VERSION}

    regime = scan.get("regime") or {}
    light = regime.get("light") or "未知"
    market_ok = bool(scan.get("market_ok"))
    full = list(scan.get("full_pass") or [])

    if not market_ok or not full:
        note = (
            f"大盘{light}：完整通过 {len(full)} 只 → 联合信号 0。"
            f"联合 v2 需要：完整通过 ∩ MENGα(α≥{min_menga}/热≤{max_heat}/芽≥{min_spark}/拥≤{max_crowd})。"
            "不再依赖涨跌研究（其边接近随机）。"
        )
        os.makedirs(OUTPUT, exist_ok=True)
        pd.DataFrame([]).to_csv(
            os.path.join(OUTPUT, "joint_signals.csv"), index=False, encoding="utf-8-sig"
        )
        return {
            "ok": True,
            "algo": ALGO_VERSION,
            "market_ok": market_ok,
            "light": light,
            "full_pass_count": len(full),
            "joint": [],
            "joint_count": 0,
            "rejected": [],
            "note": note,
            "filters": {
                "min_menga": min_menga,
                "max_heat": max_heat,
                "min_spark": min_spark,
                "max_crowd": max_crowd,
            },
            "scan": {
                "tech": len(scan.get("tech_pass") or []),
                "strict": len(scan.get("strict_struct") or []),
                "robust": len(scan.get("robust_struct") or []),
            },
            "disclaimer": "联合信号仍是研究筛选，不是荐股。",
        }

    menga_map = _load_menga_map(rescan=rescan_menga)
    joint: List[dict] = []
    rejected: List[dict] = []

    for r in full[:max_names]:
        sym = str(r.get("sym") or "")
        m = menga_map.get(sym)
        if not m:
            rejected.append({**r, "why": "无MENGα截面分（未入扫描）"})
            continue

        ok = (
            m["menga_pct"] >= min_menga
            and m["heat_pct"] <= max_heat
            and m["spark_pct"] >= min_spark
            and m["crowd_pct"] <= max_crowd
        )
        row = {
            **r,
            "menga": round(m["menga_pct"], 1),
            "heat": round(m["heat_pct"], 1),
            "wash": round(m["wash_pct"], 1),
            "spark": round(m["spark_pct"], 1),
            "crowd": round(m["crowd_pct"], 1),
            "menga_tag": m.get("tag") or "",
            # 兼容旧 Web 列名（不再是 forecast）
            "forecast_lean": f"α{m['menga_pct']:.0f}/热{m['heat_pct']:.0f}/芽{m['spark_pct']:.0f}",
            "forecast_conf": "结构+α" if ok else "未过α",
            "forecast_conf_score": round(m["menga_pct"] * 0.6 + (100 - m["heat_pct"]) * 0.4, 1),
            "forecast_pred": 1 if ok else None,
            "actionable": ok,
            "why": "通过" if ok else (
                f"未过α: α={m['menga_pct']:.0f} 热={m['heat_pct']:.0f} "
                f"芽={m['spark_pct']:.0f} 拥={m['crowd_pct']:.0f}"
            ),
        }
        if ok:
            joint.append(row)
        else:
            rejected.append(row)

    joint.sort(
        key=lambda x: (
            x.get("qscore") or 0,
            x.get("menga") or 0,
            x.get("forecast_conf_score") or 0,
        ),
        reverse=True,
    )

    os.makedirs(OUTPUT, exist_ok=True)
    pd.DataFrame(joint).to_csv(
        os.path.join(OUTPUT, "joint_signals.csv"), index=False, encoding="utf-8-sig"
    )

    note = (
        f"大盘{light}：完整通过 {len(full)} → 联合信号 {len(joint)} "
        f"（{ALGO_VERSION}：完整通过 ∩ α≥{min_menga} 热≤{max_heat} 芽≥{min_spark} 拥≤{max_crowd}；"
        f"已弃用涨跌研究闸门）"
    )
    return {
        "ok": True,
        "algo": ALGO_VERSION,
        "market_ok": market_ok,
        "light": light,
        "full_pass_count": len(full),
        "joint": joint,
        "joint_count": len(joint),
        "rejected": rejected[:50],
        "note": note,
        "filters": {
            "min_menga": min_menga,
            "max_heat": max_heat,
            "min_spark": min_spark,
            "max_crowd": max_crowd,
        },
        "disclaimer": "联合信号仍是研究筛选，不是荐股/不是预测必涨。",
    }
