# -*- coding: utf-8 -*-
"""冷静观察池 v2：在 MENGα 高分上叠加风险过滤。

用途：红灯/研究时的「冷热地图精炼名单」，**不是买入建议**。

v2 相对 v1：
  - 新增 min_spark：避免「纯深跌无萌芽」的死亡螺旋票堆高 α
  - 新增 max_heat 更严默认 28；RSI 极低标记/可剔除
  - 波动压缩优先排序（vol_ratio_std 低更好，若列存在）
  - 风险标签更细

过滤（在 menga 全表上）：
  - α 百分位 ≥ min_alpha（默认 85）
  - 热 Heat ≤ max_heat（默认 28）
  - 洗 Wash ≥ min_wash（默认 55）
  - 芽 Spark ≥ min_spark（默认 40）
  - 拥挤 Crowd ≤ max_crowd（默认 65）
  - 60 日跌幅不过分极端（ret60 ≥ min_ret60，默认 -45）
  - 现价 ≥ min_px（默认 3）
  - RSI 不过低（默认 ≥ 22，避免极端超卖陷阱）

输出: output/cool_pool.csv
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pandas as pd

from .data import attach_live_prices, market_regime
from .own_alpha import scan_own_alpha
from .paths import OUTPUT

ALGO_VERSION = "cool_v2"


def build_cool_pool(
    top_n: int = 40,
    min_alpha: float = 85.0,
    max_heat: float = 28.0,
    min_wash: float = 55.0,
    min_spark: float = 40.0,
    max_crowd: float = 65.0,
    min_ret60: float = -45.0,
    min_px: float = 3.0,
    min_rsi: float = 22.0,
    rescan: bool = True,
) -> Dict[str, Any]:
    regime = market_regime()
    light = (regime or {}).get("light") or "未知"

    if rescan:
        full = scan_own_alpha(top_n=10**9)
        if not full.get("ok"):
            return {"ok": False, "error": full.get("error") or "MENGα 扫描失败"}
        path = full.get("file")
    else:
        path = os.path.join(OUTPUT, "menga_today.csv")
        if not os.path.exists(path):
            full = scan_own_alpha(top_n=10**9)
            if not full.get("ok"):
                return {"ok": False, "error": full.get("error") or "MENGα 扫描失败"}
            path = full.get("file")

    df = pd.read_csv(path)
    n0 = len(df)

    m = (
        (df["menga_pct"] >= min_alpha)
        & (df["heat_pct"] <= max_heat)
        & (df["wash_pct"] >= min_wash)
        & (df["spark_pct"] >= min_spark)
        & (df["crowd_pct"] <= max_crowd)
        & (df["ret60"] >= min_ret60)
        & (df["px"] >= min_px)
        & (df["rsi"].fillna(50) >= min_rsi)
    )
    sub = df.loc[m].copy()
    # 排序：α 为主，萌芽为辅，热度越低越好
    sub["_rank"] = (
        sub["menga_pct"] * 1.0
        + sub["spark_pct"] * 0.25
        - sub["heat_pct"] * 0.15
    )
    sub = sub.sort_values("_rank", ascending=False).head(int(top_n))

    rows: List[dict] = []
    for _, r in sub.iterrows():
        risk = []
        if float(r["ret60"]) < -40:
            risk.append("深跌>40%")
        if float(r.get("rsi") or 50) < 28:
            risk.append("RSI偏低")
        if float(r["px"]) < 5:
            risk.append("低价股")
        if float(r.get("spark_pct") or 0) < 50:
            risk.append("萌芽偏弱")
        if float(r.get("crowd_pct") or 0) > 55:
            risk.append("拥挤偏高")
        rows.append({
            "sym": r["sym"],
            "name": r["name"],
            "menga": round(float(r["menga_pct"]), 1),
            "heat": round(float(r["heat_pct"]), 1),
            "wash": round(float(r["wash_pct"]), 1),
            "spark": round(float(r["spark_pct"]), 1),
            "crowd": round(float(r["crowd_pct"]), 1),
            "px": round(float(r["px"]), 2),
            "rsi": round(float(r["rsi"]), 1) if pd.notna(r.get("rsi")) else None,
            "ret60": round(float(r["ret60"]), 1),
            "excess20": round(float(r["excess20"]), 1) if "excess20" in r and pd.notna(r["excess20"]) else None,
            "bounce": round(float(r["bounce"]), 1) if "bounce" in r and pd.notna(r.get("bounce")) else None,
            "tag": r.get("tag") or "",
            "risk": "、".join(risk) if risk else "—",
        })

    rows = attach_live_prices(rows)

    os.makedirs(OUTPUT, exist_ok=True)
    out = os.path.join(OUTPUT, "cool_pool.csv")
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")

    note = (
        f"大盘{light} | 冷静观察池 {ALGO_VERSION}："
        f"α≥{min_alpha} 热≤{max_heat} 洗≥{min_wash} 芽≥{min_spark} "
        f"拥≤{max_crowd} 60日≥{min_ret60}% 价≥{min_px} RSI≥{min_rsi}。"
        f"全市场{n0}→入池{len(rows)}。现价已套实时。研究观察用，高α≠建议买；红灯更应空仓。"
    )
    return {
        "ok": True,
        "algo": ALGO_VERSION,
        "note": note,
        "light": light,
        "n_universe": n0,
        "n": len(rows),
        "rows": rows,
        "file": out,
        "filters": {
            "min_alpha": min_alpha,
            "max_heat": max_heat,
            "min_wash": min_wash,
            "min_spark": min_spark,
            "max_crowd": max_crowd,
            "min_ret60": min_ret60,
            "min_px": min_px,
            "min_rsi": min_rsi,
        },
        "disclaimer": "冷静观察池不是买入名单，不构成投资建议。",
    }
