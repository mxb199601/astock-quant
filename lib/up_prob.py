# -*- coding: utf-8 -*-
"""固定持有期「上涨概率」（收盘价对收盘价，非 OPT 虚高）。

定义（研究用）:
  入场日 = 结构信号 fresh 日收盘
  h 日上涨 = close[i+h] > close[i]（扣往返成本前可先算毛胜率；另给扣成本后均值）
  上涨概率 = 上涨笔数 / 总笔数

默认 horizons = 1, 3, 5, 10, 20
模式 = elite, robust, winrate, strict

用法:
  python3 cli.py up-prob --sample 600
输出:
  output/up_prob_by_horizon.csv
  output/up_prob_summary.md
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .data import list_cached_symbols, load_kline_csv
from .factors import formula3_mask
from .metrics import apply_roundtrip_cost
from .paths import OUTPUT

DEFAULT_HORIZONS = (1, 3, 5, 10, 20)
DEFAULT_MODES = ("elite", "robust", "winrate", "strict")
# 缓存引用（hist 验证量级），供 action 在未重跑时展示
CACHED_REF = {
    "robust": {"h20_path_tight_wr": 0.53, "note": "PATH tight≈53~56%（非固定持有）"},
}


def _fresh(sig: pd.Series) -> pd.Series:
    cur = sig.fillna(False).astype(bool)
    prev = sig.shift(1)
    prev = prev.where(prev.notna(), False).astype(bool)
    return cur & ~prev


def _load_cache(sample_n: int, seed: int) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))
    cache = {}
    for s in pool:
        df = load_kline_csv(s, min_bars=120)
        if df is not None and len(df) >= 120:
            cache[s] = df
    idx = load_kline_csv("sh000300", min_bars=300)
    if idx is None:
        raise RuntimeError("缺少沪深300")
    return cache, idx


def compute_up_prob(
    sample_n: int = 600,
    seed: int = 42,
    modes: Optional[List[str]] = None,
    horizons: Optional[Tuple[int, ...]] = None,
    cost_bps: float = 15.0,
) -> Dict[str, Any]:
    modes = list(modes or DEFAULT_MODES)
    horizons = tuple(horizons or DEFAULT_HORIZONS)
    max_h = max(horizons)
    cache, idx = _load_cache(sample_n, seed)

    # mode -> list of ret arrays per horizon key
    bucket: Dict[str, Dict[int, List[float]]] = {m: {h: [] for h in horizons} for m in modes}

    for sym, df in cache.items():
        if len(df) < 320:
            continue
        c = df["close"].astype(float).values
        for mode in modes:
            try:
                f = _fresh(formula3_mask(df, idx, mode))
            except Exception:
                continue
            for i in np.where(f.values)[0]:
                if i + max_h >= len(c):
                    continue
                entry = float(c[i])
                if entry <= 0:
                    continue
                for h in horizons:
                    ret = (float(c[i + h]) / entry - 1.0) * 100.0
                    bucket[mode][h].append(ret)

    rows = []
    for mode in modes:
        for h in horizons:
            rets = np.asarray(bucket[mode][h], dtype=float)
            n = int(len(rets))
            if n == 0:
                rows.append({
                    "mode": mode, "horizon_d": h, "n": 0,
                    "up_prob": None, "up_prob_pct": None,
                    "mean_gross": None, "mean_net": None,
                    "median": None, "p25": None, "p75": None,
                })
                continue
            up = rets > 0
            mean_g = float(np.mean(rets))
            mean_n = float(np.mean([apply_roundtrip_cost(x, cost_bps) for x in rets]))
            rows.append({
                "mode": mode,
                "horizon_d": h,
                "n": n,
                "up_prob": round(float(up.mean()), 4),
                "up_prob_pct": round(float(up.mean()) * 100, 2),
                "mean_gross": round(mean_g, 3),
                "mean_net": round(mean_n, 3),
                "median": round(float(np.median(rets)), 3),
                "p25": round(float(np.percentile(rets, 25)), 3),
                "p75": round(float(np.percentile(rets, 75)), 3),
            })

    os.makedirs(OUTPUT, exist_ok=True)
    path_csv = os.path.join(OUTPUT, "up_prob_by_horizon.csv")
    df_out = pd.DataFrame(rows)
    df_out.to_csv(path_csv, index=False, encoding="utf-8-sig")

    # markdown
    lines = [
        "# 固定持有期上涨概率（真实 K 线）",
        "",
        f"- 样本股≈**{len(cache)}**（seed={seed}）",
        f"- 入场：结构信号 fresh 日**收盘**；上涨 = 持有 h 日后收盘价更高",
        f"- 单边成本 {cost_bps} bps（仅 mean_net 扣往返）",
        f"- **不是** OPT 窗内止盈胜率；**不是** 必涨承诺",
        "",
        "> 研究工具，不构成投资建议。历史概率 ≠ 下一笔会涨。",
        "",
        "## 总表",
        "",
        "| mode | 1日 | 3日 | 5日 | 10日 | 20日 | n@20 |",
        "|------|-----|-----|-----|------|------|------|",
    ]
    for mode in modes:
        cells = []
        n20 = ""
        for h in horizons:
            sub = df_out[(df_out["mode"] == mode) & (df_out["horizon_d"] == h)]
            if len(sub) == 0 or sub.iloc[0]["n"] == 0:
                cells.append("—")
            else:
                r = sub.iloc[0]
                cells.append(f"{r['up_prob_pct']:.1f}%")
                if h == 20:
                    n20 = str(int(r["n"]))
        while len(cells) < 5:
            cells.append("—")
        lines.append(f"| {mode} | " + " | ".join(cells) + f" | {n20} |")

    lines.extend(["", "## 明细（含均收益）", ""])
    lines.append("| mode | h | n | 上涨概率 | 毛均值 | 净均值 | 中位 |")
    lines.append("|------|---|---|----------|--------|--------|------|")
    for _, r in df_out.iterrows():
        if not r["n"]:
            continue
        lines.append(
            f"| {r['mode']} | {int(r['horizon_d'])} | {int(r['n'])} | "
            f"**{r['up_prob_pct']}%** | {r['mean_gross']:+.2f}% | {r['mean_net']:+.2f}% | {r['median']:+.2f}% |"
        )

    # 对照随机
    lines.extend([
        "",
        "## 怎么读",
        "",
        "- 随机/无边基线 ≈ **50%**。",
        "- 若 1～5 日概率长期贴近 50% → **短线几乎无预测力**。",
        "- 20 日略高于 50% 且净均值>0 → 仅有**薄边**，仍有近一半交易下跌。",
        "- 系统默认纪律出场看 PATH+tight（约 53%～56% 盈利概率），与本表「拿满 h 日」不同。",
        "",
        f"文件: `{path_csv}`",
    ])
    path_md = os.path.join(OUTPUT, "up_prob_summary.md")
    with open(path_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 给 action 用的精简引用
    ref = {}
    for mode in modes:
        ref[mode] = {}
        for h in horizons:
            sub = df_out[(df_out["mode"] == mode) & (df_out["horizon_d"] == h)]
            if len(sub) and sub.iloc[0]["n"]:
                ref[mode][f"h{h}"] = {
                    "up_prob_pct": float(sub.iloc[0]["up_prob_pct"]),
                    "mean_net": float(sub.iloc[0]["mean_net"]),
                    "n": int(sub.iloc[0]["n"]),
                }

    ref_path = os.path.join(OUTPUT, "up_prob_ref.json")
    import json
    with open(ref_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "sample_n": len(cache),
                "seed": seed,
                "cost_bps": cost_bps,
                "horizons": list(horizons),
                "by_mode": ref,
                "disclaimer": "历史固定持有上涨概率，非预测、非荐股",
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    return {
        "ok": True,
        "n_stocks": len(cache),
        "rows": rows,
        "ref": ref,
        "file": path_csv,
        "summary": path_md,
        "ref_json": ref_path,
    }


def load_up_prob_ref() -> Optional[Dict[str, Any]]:
    """读最近一次 up-prob 结果，供 action/Web 展示。"""
    import json
    path = os.path.join(OUTPUT, "up_prob_ref.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def format_up_prob_hint(mode: str = "robust") -> str:
    """一行中文提示。"""
    ref = load_up_prob_ref()
    if not ref or "by_mode" not in ref:
        return (
            "历史参考（未跑 up-prob 时）：PATH+tight 盈利概率约53%~56%；"
            "固定持有20日上涨概率需 cli.py up-prob。非必涨。"
        )
    m = (ref.get("by_mode") or {}).get(mode) or {}
    parts = []
    for key in ("h1", "h5", "h20"):
        if key in m:
            h = key[1:]
            parts.append(f"{h}日上涨{m[key]['up_prob_pct']:.0f}%")
    if not parts:
        return "上涨概率：暂无数据"
    return (
        f"历史固定持有({mode})：" + "、".join(parts)
        + f"（n@20≈{(m.get('h20') or {}).get('n', '?')}；非预测、非必涨）"
    )
