# -*- coding: utf-8 -*-
"""形态/指标全市场扫描。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pandas as pd

from .data import list_cached_symbols, load_fundamentals, load_kline_csv, market_regime
from .paths import OUTPUT
from .patterns import list_patterns, scan_patterns


def run_pattern_scan(
    pattern: Optional[str] = None,
    min_hits: int = 1,
) -> Dict[str, Any]:
    """
    pattern: 若指定则只保留含该形态的票；None=任意命中。
    """
    regime = market_regime()
    fund = load_fundamentals(False)
    syms = list_cached_symbols(mainboard_only=True, drop_st=True)

    def _load(sym: str):
        return load_kline_csv(sym, min_bars=280)

    rows = scan_patterns(syms, _load, fund=fund, only_hit=True)
    if pattern:
        rows = [r for r in rows if pattern in (r.get("hits") or [])]
    rows = [r for r in rows if r.get("n_hits", 0) >= min_hits]

    # 汇总计数
    counts = {name: 0 for name, _ in list_patterns()}
    for r in rows:
        for h in r.get("hits") or []:
            if h in counts:
                counts[h] += 1

    os.makedirs(OUTPUT, exist_ok=True)
    # 导出精简表
    export = [
        {
            "代码": r["sym"],
            "名称": r["name"],
            "现价": r["px"],
            "命中数": r["n_hits"],
            "形态": "、".join(r["hits"]),
        }
        for r in rows
    ]
    pd.DataFrame(export).to_csv(
        os.path.join(OUTPUT, "patterns_today.csv"), index=False, encoding="utf-8-sig"
    )

    light = (regime or {}).get("light", "?")
    return {
        "ok": True,
        "regime": regime,
        "pool_size": len(syms),
        "rows": rows[:200],
        "total_hits": len(rows),
        "counts": counts,
        "patterns": [{"name": n, "desc": d} for n, d in list_patterns()],
        "note": (
            f"大盘{light} | 主板{len(syms)}只中形态命中 {len(rows)} 只。"
            "形态辅助观察，≠买入推荐；红灯仍建议空仓。"
        ),
        "disclaimer": "黄金坑等为可复现定义，与市面软件可能不同；不构成投资建议。",
    }
