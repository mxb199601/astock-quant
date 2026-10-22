#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逢3必变 v1 历史准确性深挖 — 切片报告（研究用，非荐股）。

输入: output/feng3/feng3_events.csv（首轮 800 主板 seed=42）
输出: output/feng3/accuracy/*.csv + accuracy_report.md

切片: year / HS300红绿灯 / bull-bear(MA60) / conf / near_ok / LOW-HIGH
持有: PATH 5/10/20d（tight, 15bps）
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib.data import load_kline_csv
from lib.metrics import apply_roundtrip_cost, summarize_returns
from lib.paths import OUTPUT
from lib.stress import exit_path_daily

HOLDS = (5, 10, 20)
HIT_NS = (5, 10, 20)
MAE_TOL = 2.0
COST_BPS = 15.0
EXIT_MODE = "tight"


def _build_index_map(idx: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """按日预计算红绿灯 / bull(MA60上)。"""
    d = idx.copy()
    d["date"] = d["date"].astype(str)
    c = d["close"].astype(float)
    d["ma20"] = c.rolling(20).mean()
    d["ma60"] = c.rolling(60).mean()
    out: Dict[str, Dict[str, Any]] = {}
    for i, row in d.iterrows():
        date = str(row["date"])
        px = float(row["close"])
        ma20 = row["ma20"]
        ma60 = row["ma60"]
        if pd.isna(ma60) or pd.isna(ma20):
            out[date] = {
                "light": "未知",
                "bull_bear": "未知",
                "above_ma20": None,
                "above_ma60": None,
                "idx_close": px,
            }
            continue
        a20 = px > float(ma20)
        a60 = px > float(ma60)
        if a20 and a60:
            light = "绿灯"
        elif a20 or a60:
            light = "黄灯"
        else:
            light = "红灯"
        out[date] = {
            "light": light,
            "bull_bear": "牛" if a60 else "熊",
            "above_ma20": a20,
            "above_ma60": a60,
            "idx_close": px,
        }
    return out


def _enrich_symbol_events(
    df: pd.DataFrame,
    evs: pd.DataFrame,
    mae_tol: float = MAE_TOL,
    cost_bps: float = COST_BPS,
    exit_mode: str = EXIT_MODE,
) -> List[Dict[str, Any]]:
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    n = len(close)
    rows: List[Dict[str, Any]] = []
    for _, e in evs.iterrows():
        i = int(e["bar_idx"])
        center = int(e["center_idx"])
        pred = str(e["pred"])
        trigger = float(e["trigger_price"])
        entry = float(close[i]) if 0 <= i < n else float("nan")
        row = e.to_dict()
        if not (0 <= i < n) or entry <= 0 or trigger <= 0:
            for h in HOLDS:
                row[f"path_{h}"] = None
                row[f"fixed_{h}"] = None
            for hn in HIT_NS:
                row[f"hit_{hn}"] = None
                row[f"mae_{hn}"] = None
            rows.append(row)
            continue

        for hn in HIT_NS:
            end_n = min(n - 1, center + hn)
            w_h = high[center : end_n + 1]
            w_l = low[center : end_n + 1]
            if pred == "LOW":
                mn = float(w_l.min()) if len(w_l) else trigger
                mae = (trigger - mn) / trigger * 100.0
            else:
                mx = float(w_h.max()) if len(w_h) else trigger
                mae = (mx - trigger) / trigger * 100.0
            row[f"mae_{hn}"] = round(mae, 3)
            row[f"hit_{hn}"] = bool(mae <= mae_tol)

        for h in HOLDS:
            end_m = min(n - 1, i + h)
            if end_m > i:
                rf = (float(close[end_m]) / entry - 1.0) * 100.0
                row[f"fixed_{h}"] = round(rf, 3)
            else:
                row[f"fixed_{h}"] = None
            if i + h < n:
                pr = exit_path_daily(
                    entry,
                    high[i : i + h + 1],
                    low[i : i + h + 1],
                    close[i : i + h + 1],
                    exit_mode,
                    cost_bps,
                    h,
                    "sl",
                )
                row[f"path_{h}"] = round(float(pr), 3)
            elif end_m > i and row.get(f"fixed_{h}") is not None:
                row[f"path_{h}"] = round(apply_roundtrip_cost(float(row[f"fixed_{h}"]), cost_bps), 3)
            else:
                row[f"path_{h}"] = None
        rows.append(row)
    return rows


def _agg(sub: pd.DataFrame, name: str) -> Dict[str, Any]:
    n = int(len(sub))
    out: Dict[str, Any] = {"slice": name, "n": n}
    if n == 0:
        for hn in HIT_NS:
            out[f"hit{hn}_pct"] = None
            out[f"mae{hn}_mean"] = None
        for h in HOLDS:
            out[f"path{h}_n"] = 0
            out[f"path{h}_win"] = None
            out[f"path{h}_mean"] = None
            out[f"fixed{h}_up"] = None
            out[f"fixed{h}_down"] = None
            out[f"fixed{h}_mean"] = None
        return out

    for hn in HIT_NS:
        col = f"hit_{hn}"
        if col in sub.columns and sub[col].notna().any():
            out[f"hit{hn}_pct"] = round(float(sub[col].astype(bool).mean()) * 100, 2)
            out[f"mae{hn}_mean"] = round(float(sub[f"mae_{hn}"].dropna().mean()), 3)
        else:
            out[f"hit{hn}_pct"] = None
            out[f"mae{hn}_mean"] = None

    for h in HOLDS:
        path = sub[f"path_{h}"].dropna()
        fixed = sub[f"fixed_{h}"].dropna()
        ps = summarize_returns(path.tolist(), name) if len(path) else {"n": 0, "winrate": None, "mean": None}
        out[f"path{h}_n"] = int(ps.get("n") or 0)
        wr = ps.get("winrate")
        mn = ps.get("mean")
        out[f"path{h}_win"] = None if wr is None or wr != wr else round(float(wr), 2)
        out[f"path{h}_mean"] = None if mn is None or mn != mn else round(float(mn), 3)
        if len(fixed):
            out[f"fixed{h}_up"] = round(float((fixed > 0).mean()) * 100, 2)
            out[f"fixed{h}_down"] = round(float((fixed < 0).mean()) * 100, 2)
            out[f"fixed{h}_mean"] = round(float(fixed.mean()), 3)
        else:
            out[f"fixed{h}_up"] = None
            out[f"fixed{h}_down"] = None
            out[f"fixed{h}_mean"] = None
    return out


def _fmt(v: Any, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _md_table(df: pd.DataFrame, cols: List[str]) -> List[str]:
    lines = [
        "| " + " | ".join(cols) + " |",
        "|" + "|".join(["-------"] * len(cols)) + "|",
    ]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r.get(c)
            if c == "n" or str(c).endswith("_n"):
                cells.append(_fmt(v, 0) if v is not None else "0")
            elif "mean" in str(c) or "mae" in str(c):
                cells.append(_fmt(v, 3))
            else:
                cells.append(_fmt(v, 2))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def run() -> Dict[str, Any]:
    t0 = time.time()
    events_path = os.path.join(OUTPUT, "feng3", "feng3_events.csv")
    out_dir = os.path.join(OUTPUT, "feng3", "accuracy")
    os.makedirs(out_dir, exist_ok=True)

    edf0 = pd.read_csv(events_path)
    edf0["date"] = edf0["date"].astype(str)
    edf0["year"] = edf0["date"].str[:4]
    edf0["near_ok"] = edf0["near_ok"].astype(bool)
    edf0["struct_agree"] = edf0["struct_agree"].astype(bool)

    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None:
        idx = load_kline_csv("sh000001", min_bars=100)
    idx_name = "sh000300" if idx is not None and "000300" in str(getattr(idx, "attrs", {})) else None
    # load_kline_csv may not set attrs; detect by trying 300 first
    if load_kline_csv("sh000300", min_bars=100) is not None:
        idx = load_kline_csv("sh000300", min_bars=100)
        idx_name = "沪深300"
    else:
        idx_name = "上证综指"
    imap = _build_index_map(idx)

    # attach regime
    lights, bbs = [], []
    for d in edf0["date"]:
        info = imap.get(d, {"light": "未知", "bull_bear": "未知"})
        lights.append(info["light"])
        bbs.append(info["bull_bear"])
    edf0["light"] = lights
    edf0["bull_bear"] = bbs

    # recompute multi-hold PATH / hit
    rows: List[Dict[str, Any]] = []
    syms = sorted(edf0["sym"].unique())
    for j, sym in enumerate(syms):
        df = load_kline_csv(sym, min_bars=50)
        sub = edf0[edf0["sym"] == sym]
        if df is None or len(df) < 50:
            for _, e in sub.iterrows():
                rows.append(e.to_dict())
            continue
        rows.extend(_enrich_symbol_events(df, sub))
        if (j + 1) % 200 == 0:
            print(f"  … enrich {j+1}/{len(syms)} symbols, elapsed={time.time()-t0:.1f}s")

    edf = pd.DataFrame(rows)
    # keep regime cols if enrich dropped somehow
    if "year" not in edf.columns:
        edf["year"] = edf["date"].astype(str).str[:4]
    if "light" not in edf.columns:
        edf["light"] = [imap.get(str(d), {}).get("light", "未知") for d in edf["date"]]
        edf["bull_bear"] = [imap.get(str(d), {}).get("bull_bear", "未知") for d in edf["date"]]

    enriched_path = os.path.join(out_dir, "feng3_events_enriched.csv")
    edf.to_csv(enriched_path, index=False, encoding="utf-8-sig")

    slice_defs = []
    # overall
    slice_defs.append((None, "ALL"))
    slice_defs.append((edf["pred"] == "LOW", "LOW"))
    slice_defs.append((edf["pred"] == "HIGH", "HIGH"))
    # conf
    for c in ("高", "中", "低"):
        slice_defs.append((edf["conf"] == c, f"conf_{c}"))
        slice_defs.append(((edf["pred"] == "LOW") & (edf["conf"] == c), f"LOW_conf_{c}"))
        slice_defs.append(((edf["pred"] == "HIGH") & (edf["conf"] == c), f"HIGH_conf_{c}"))
    # near SR
    slice_defs.append((edf["near_ok"] == True, "near_SR"))
    slice_defs.append((edf["near_ok"] == False, "not_near_SR"))
    slice_defs.append(((edf["pred"] == "LOW") & (edf["near_ok"] == True), "LOW_near_SR"))
    slice_defs.append(((edf["pred"] == "LOW") & (edf["near_ok"] == False), "LOW_not_near"))
    slice_defs.append(((edf["pred"] == "HIGH") & (edf["near_ok"] == True), "HIGH_near_SR"))
    slice_defs.append(((edf["pred"] == "HIGH") & (edf["near_ok"] == False), "HIGH_not_near"))
    # struct
    slice_defs.append((edf["struct_agree"] == True, "struct_agree"))
    slice_defs.append((edf["struct_agree"] == False, "struct_disagree"))
    # year
    for y in sorted(edf["year"].dropna().unique()):
        slice_defs.append((edf["year"] == y, f"year_{y}"))
        slice_defs.append(((edf["pred"] == "LOW") & (edf["year"] == y), f"LOW_year_{y}"))
        slice_defs.append(((edf["pred"] == "HIGH") & (edf["year"] == y), f"HIGH_year_{y}"))
    # light / bull-bear
    for lt in ("绿灯", "黄灯", "红灯", "未知"):
        slice_defs.append((edf["light"] == lt, f"light_{lt}"))
        slice_defs.append(((edf["pred"] == "LOW") & (edf["light"] == lt), f"LOW_light_{lt}"))
        slice_defs.append(((edf["pred"] == "HIGH") & (edf["light"] == lt), f"HIGH_light_{lt}"))
    for bb in ("牛", "熊", "未知"):
        slice_defs.append((edf["bull_bear"] == bb, f"bb_{bb}"))
        slice_defs.append(((edf["pred"] == "LOW") & (edf["bull_bear"] == bb), f"LOW_bb_{bb}"))
        slice_defs.append(((edf["pred"] == "HIGH") & (edf["bull_bear"] == bb), f"HIGH_bb_{bb}"))
    # cross: LOW + near + conf / LOW + bull
    slice_defs.append(
        ((edf["pred"] == "LOW") & (edf["near_ok"] == True) & (edf["bull_bear"] == "牛"), "LOW_near_牛")
    )
    slice_defs.append(
        ((edf["pred"] == "LOW") & (edf["near_ok"] == True) & (edf["bull_bear"] == "熊"), "LOW_near_熊")
    )
    slice_defs.append(
        ((edf["pred"] == "LOW") & (edf["conf"] == "中") & (edf["bull_bear"] == "牛"), "LOW_conf中_牛")
    )
    slice_defs.append(
        ((edf["pred"] == "LOW") & (edf["conf"] == "高") & (edf["bull_bear"] == "牛"), "LOW_conf高_牛")
    )
    slice_defs.append(
        ((edf["pred"] == "HIGH") & (edf["near_ok"] == True) & (edf["bull_bear"] == "熊"), "HIGH_near_熊")
    )

    all_slices = []
    for mask, name in slice_defs:
        sub = edf if mask is None else edf.loc[mask]
        all_slices.append(_agg(sub, name))
    sdf = pd.DataFrame(all_slices)
    summary_path = os.path.join(out_dir, "accuracy_by_slice.csv")
    sdf.to_csv(summary_path, index=False, encoding="utf-8-sig")

    # focused CSVs
    def _pick(prefixes_or_exact):
        # prefixes_or_exact: list of callables or startswith strings
        rows = []
        for r in all_slices:
            s = r["slice"]
            ok = False
            for p in prefixes_or_exact:
                if callable(p) and p(s):
                    ok = True
                elif isinstance(p, str) and (s == p or s.startswith(p)):
                    ok = True
            if ok:
                rows.append(r)
        return pd.DataFrame(rows)

    by_year = _pick(["year_", "LOW_year_", "HIGH_year_", "ALL", "LOW", "HIGH"])
    # cleaner year tables
    by_year = sdf[sdf["slice"].str.match(r"^(ALL|LOW|HIGH|year_|LOW_year_|HIGH_year_)")].copy()
    by_conf = sdf[sdf["slice"].str.contains(r"conf_|ALL|LOW$|HIGH$", regex=True) & ~sdf["slice"].str.contains("year|light|bb_|near|struct")].copy()
    by_near = sdf[sdf["slice"].str.contains(r"near|ALL|LOW$|HIGH$", regex=True) & ~sdf["slice"].str.contains("year|light|bb_|conf|struct|LOW_near_牛|LOW_near_熊")].copy()
    by_regime = sdf[sdf["slice"].str.contains(r"light_|bb_|ALL|LOW$|HIGH$|LOW_near_牛|LOW_near_熊|LOW_conf|HIGH_near", regex=True) & ~sdf["slice"].str.contains("year")].copy()

    by_year.to_csv(os.path.join(out_dir, "accuracy_by_year.csv"), index=False, encoding="utf-8-sig")
    by_conf.to_csv(os.path.join(out_dir, "accuracy_by_conf.csv"), index=False, encoding="utf-8-sig")
    by_near.to_csv(os.path.join(out_dir, "accuracy_by_near_sr.csv"), index=False, encoding="utf-8-sig")
    by_regime.to_csv(os.path.join(out_dir, "accuracy_by_regime.csv"), index=False, encoding="utf-8-sig")

    # hold-window focus (LOW only primary)
    hold_rows = []
    for name, mask in [
        ("ALL", None),
        ("LOW", edf["pred"] == "LOW"),
        ("HIGH", edf["pred"] == "HIGH"),
        ("LOW_conf高", (edf["pred"] == "LOW") & (edf["conf"] == "高")),
        ("LOW_conf中", (edf["pred"] == "LOW") & (edf["conf"] == "中")),
        ("LOW_near", (edf["pred"] == "LOW") & (edf["near_ok"] == True)),
        ("LOW_not_near", (edf["pred"] == "LOW") & (edf["near_ok"] == False)),
        ("LOW_牛", (edf["pred"] == "LOW") & (edf["bull_bear"] == "牛")),
        ("LOW_熊", (edf["pred"] == "LOW") & (edf["bull_bear"] == "熊")),
        ("HIGH_near", (edf["pred"] == "HIGH") & (edf["near_ok"] == True)),
        ("HIGH_熊", (edf["pred"] == "HIGH") & (edf["bull_bear"] == "熊")),
    ]:
        a = _agg(edf if mask is None else edf.loc[mask], name)
        for h in HOLDS:
            hold_rows.append({
                "slice": name,
                "hold_d": h,
                "n": a["n"],
                "path_n": a[f"path{h}_n"],
                "path_winrate": a[f"path{h}_win"],
                "path_mean": a[f"path{h}_mean"],
                "fixed_up_pct": a[f"fixed{h}_up"],
                "fixed_down_pct": a[f"fixed{h}_down"],
                "fixed_mean": a[f"fixed{h}_mean"],
                "hit10_pct": a.get("hit10_pct"),
            })
    hdf = pd.DataFrame(hold_rows)
    hdf.to_csv(os.path.join(out_dir, "accuracy_by_hold.csv"), index=False, encoding="utf-8-sig")

    # --- honest findings for tweaks ---
    def g(name: str) -> Dict[str, Any]:
        for r in all_slices:
            if r["slice"] == name:
                return r
        return {"n": 0}

    low = g("LOW")
    high = g("HIGH")
    low_near = g("LOW_near_SR")
    low_not = g("LOW_not_near")
    low_highc = g("LOW_conf_高")
    low_midc = g("LOW_conf_中")
    low_bull = g("LOW_bb_牛")
    low_bear = g("LOW_bb_熊")
    high_near = g("HIGH_near_SR")
    high_bear = g("HIGH_bb_熊")
    low_near_bull = g("LOW_near_牛")
    low_mid_bull = g("LOW_conf中_牛")

    # path20 lift near vs not for LOW
    def lift(a, b, key="path20_mean"):
        if a.get(key) is None or b.get(key) is None:
            return None
        return round(float(a[key]) - float(b[key]), 3)

    findings = []
    findings.append(
        f"LOW 全样本 PATH20 胜率={_fmt(low.get('path20_win'))}% 均值={_fmt(low.get('path20_mean'),3)}% "
        f"(n={low['n']})；相对任意摆动基线仍约持平（见首轮）。"
    )
    findings.append(
        f"LOW near_SR vs not: PATH20 均值 {_fmt(low_near.get('path20_mean'),3)} vs {_fmt(low_not.get('path20_mean'),3)} "
        f"(Δ={_fmt(lift(low_near, low_not),3)}pt)；n_near={low_near['n']} 极少。"
    )
    findings.append(
        f"LOW conf高 vs 中: PATH20 {_fmt(low_highc.get('path20_mean'),3)} vs {_fmt(low_midc.get('path20_mean'),3)}；"
        f"conf高 n={low_highc['n']}（高置信未优于中，警惕过拟合分层）。"
    )
    findings.append(
        f"LOW 牛 vs 熊 (HS300>MA60): PATH20 {_fmt(low_bull.get('path20_mean'),3)} vs {_fmt(low_bear.get('path20_mean'),3)}；"
        f"n_牛={low_bull['n']} n_熊={low_bear['n']}。"
    )
    findings.append(
        f"HIGH 固定20d下跌%={_fmt(high.get('fixed20_down'))}；near={_fmt(high_near.get('fixed20_down'))} "
        f"熊市={_fmt(high_bear.get('fixed20_down'))}（A股不做空，仅观察）。"
    )

    # propose tweaks ranked by expected lift (honest)
    low_red = g("LOW_light_红灯")
    low_green = g("LOW_light_绿灯")
    tweaks = []
    d_near = lift(low_not, low_near)  # not_near minus near
    tweaks.append({
        "rank": 1,
        "name": "弱化/移除 Layer B near_SR 对 conf 的加分（conf高不再要求 near∧struct）",
        "rationale": (
            "LOW near_SR PATH 弱于 not_near；conf高强制 near∧struct，PATH 反弱于 conf中。"
            "near 稀有且有害，应降权。"
        ),
        "expected_lift": (
            f"相对 near 桶 PATH20 Δ≈{_fmt(d_near,3)}pt；相对全 LOW 接近持平"
            if d_near is not None else "见 near 对比表"
        ),
        "risk": "去掉分层后相对任意摆动基线仍可能只有 ~1pt hit；不创造新 alpha。",
        "n_est": low_not.get("n"),
        "path20_mean": low_not.get("path20_mean"),
        "path20_win": low_not.get("path20_win"),
    })
    d_red = lift(low_red, low)
    tweaks.append({
        "rank": 2,
        "name": "可选环境闸：LOW 仅在 HS300 红灯（价≤MA20 且 ≤MA60）观察/计数",
        "rationale": (
            "红灯 PATH20 略正且胜率高于全 LOW/绿灯；更像弱势均值回归环境过滤，非逢3结构边。"
        ),
        "expected_lift": (
            f"PATH20 均值约 +{_fmt(d_red,3)}pt vs 全 LOW" if d_red is not None else "见红灯表"
        ),
        "risk": "与年份纠缠；未相对「红灯×任意摆动低」做基线，可能只是熊市反弹共性。",
        "n_est": low_red.get("n"),
        "path20_mean": low_red.get("path20_mean"),
        "path20_win": low_red.get("path20_win"),
    })

    # hold comparison lines
    hold_cols = ["slice", "hold_d", "path_n", "path_winrate", "path_mean", "fixed_up_pct", "fixed_down_pct", "fixed_mean"]
    conf_cols = [
        "slice", "n", "hit10_pct", "path5_win", "path5_mean", "path10_win", "path10_mean",
        "path20_win", "path20_mean", "fixed20_up", "fixed20_down", "fixed20_mean",
    ]
    year_cols = conf_cols
    near_cols = conf_cols
    regime_cols = conf_cols

    md: List[str] = [
        "# 逢3必变 v1 · 历史准确性深挖",
        "",
        f"- 事件源: `{events_path}`（首轮 800 主板 seed=42）",
        f"- 指数环境: **{idx_name}** 日线红绿灯 / 牛熊(收盘相对 MA60)",
        f"- PATH: exit=`{EXIT_MODE}` cost={COST_BPS}bps；持有窗 **5/10/20** 交易日",
        f"- hit@N: 自摆动中心起 N∈{{5,10,20}}，mae_tol={MAE_TOL}%",
        f"- 耗时: {time.time()-t0:.1f}s · 富化事件: `{enriched_path}`",
        "",
        "> 研究工具，**不构成投资建议**。历史切片 ≠ 下一笔可交易边。HIGH 的 PATH 为做多对照。",
        "",
        "## 1. 总览（多持有窗 PATH）",
        "",
    ]
    md.extend(_md_table(hdf[hdf["slice"].isin(["ALL", "LOW", "HIGH"])], hold_cols))
    md.extend([
        "",
        "## 2. 按置信度 conf",
        "",
    ])
    # rebuild focused conf table from sdf
    conf_focus = sdf[sdf["slice"].isin([
        "ALL", "LOW", "HIGH", "conf_高", "conf_中", "conf_低",
        "LOW_conf_高", "LOW_conf_中", "LOW_conf_低",
        "HIGH_conf_高", "HIGH_conf_中", "HIGH_conf_低",
    ])].copy()
    conf_focus.to_csv(os.path.join(out_dir, "accuracy_by_conf.csv"), index=False, encoding="utf-8-sig")
    md.extend(_md_table(conf_focus, conf_cols))

    md.extend(["", "## 3. 按支撑/阻力邻近 near_SR", ""])
    near_focus = sdf[sdf["slice"].isin([
        "ALL", "LOW", "HIGH", "near_SR", "not_near_SR",
        "LOW_near_SR", "LOW_not_near", "HIGH_near_SR", "HIGH_not_near",
    ])].copy()
    near_focus.to_csv(os.path.join(out_dir, "accuracy_by_near_sr.csv"), index=False, encoding="utf-8-sig")
    md.extend(_md_table(near_focus, near_cols))

    md.extend(["", "## 4. 按年份", ""])
    year_focus = sdf[sdf["slice"].str.match(r"^(ALL|LOW|HIGH|year_\d{4}|LOW_year_\d{4}|HIGH_year_\d{4})$")].copy()
    year_focus.to_csv(os.path.join(out_dir, "accuracy_by_year.csv"), index=False, encoding="utf-8-sig")
    md.extend(_md_table(year_focus, year_cols))

    md.extend(["", f"## 5. 按指数环境（{idx_name}）", ""])
    regime_focus = sdf[sdf["slice"].str.match(
        r"^(ALL|LOW|HIGH|light_|LOW_light_|HIGH_light_|bb_|LOW_bb_|HIGH_bb_|LOW_near_牛|LOW_near_熊|LOW_conf中_牛|LOW_conf高_牛|HIGH_near_熊)"
    )].copy()
    # filter unknown empties with n=0 optional keep
    regime_focus = regime_focus[regime_focus["n"] > 0].copy()
    regime_focus.to_csv(os.path.join(out_dir, "accuracy_by_regime.csv"), index=False, encoding="utf-8-sig")
    md.extend(_md_table(regime_focus, regime_cols))

    md.extend(["", "## 6. 持有窗细表（关键切片）", ""])
    md.extend(_md_table(hdf, hold_cols))

    md.extend([
        "",
        "## 7. 关键发现（诚实）",
        "",
    ])
    for f in findings:
        md.append(f"- {f}")

    md.extend([
        "",
        "## 8. 建议规则微调（尚未实现）",
        "",
        "仅提案，**本回合不改** `lib/feng3.py` 除非后续确认。按期望提升排序；若整体仍弱，保持观察层。",
        "",
    ])
    for t in tweaks:
        md.extend([
            f"### #{t['rank']} {t['name']}",
            "",
            f"- 理由: {t['rationale']}",
            f"- 期望提升: {t['expected_lift']}",
            f"- 该桶 n≈{t['n_est']} · PATH20 胜率={_fmt(t.get('path20_win'))}% 均值={_fmt(t.get('path20_mean'),3)}%",
            f"- 风险: {t['risk']}",
            "",
        ])

    # honesty footer
    still_weak = True
    best = max(
        [low_mid_bull, low_bull, low_midc, low_not, low],
        key=lambda x: (x.get("path20_mean") is not None, x.get("path20_mean") or -999),
    )
    if best.get("path20_mean") is not None and best["path20_mean"] > 0.2 and (best.get("path20_win") or 0) >= 48:
        still_weak = False
    md.extend([
        "## 9. 总评",
        "",
        (
            "切片后仍未见稳定正 PATH 边；微调最多是「少做亏的环境/桶」，不是独立 alpha。"
            if still_weak
            else "个别切片 PATH 略正，但仍需相对任意摆动基线复核，且可能过拟合年份/牛熊。"
        ),
        "",
        "**不升格** joint/action；不荐股。",
        "",
        "## 输出文件",
        "",
        f"- `{summary_path}`",
        f"- `{os.path.join(out_dir, 'accuracy_by_year.csv')}`",
        f"- `{os.path.join(out_dir, 'accuracy_by_conf.csv')}`",
        f"- `{os.path.join(out_dir, 'accuracy_by_near_sr.csv')}`",
        f"- `{os.path.join(out_dir, 'accuracy_by_regime.csv')}`",
        f"- `{os.path.join(out_dir, 'accuracy_by_hold.csv')}`",
        f"- `{enriched_path}`",
    ])

    report_path = os.path.join(out_dir, "accuracy_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # tweaks json-ish csv
    pd.DataFrame(tweaks).to_csv(os.path.join(out_dir, "proposed_tweaks.csv"), index=False, encoding="utf-8-sig")

    print("\n".join(md))
    return {
        "ok": True,
        "report_path": report_path,
        "out_dir": out_dir,
        "n_events": len(edf),
        "tweaks": tweaks,
        "still_weak": still_weak,
        "elapsed_s": round(time.time() - t0, 1),
        "key": {
            "LOW_path20": (low.get("path20_win"), low.get("path20_mean")),
            "LOW_near_path20": (low_near.get("path20_win"), low_near.get("path20_mean"), low_near.get("n")),
            "LOW_conf高_path20": (low_highc.get("path20_win"), low_highc.get("path20_mean")),
            "LOW_conf中_path20": (low_midc.get("path20_win"), low_midc.get("path20_mean")),
            "LOW_牛_path20": (low_bull.get("path20_win"), low_bull.get("path20_mean")),
            "LOW_熊_path20": (low_bear.get("path20_win"), low_bear.get("path20_mean")),
            "LOW_conf中_牛": (low_mid_bull.get("path20_win"), low_mid_bull.get("path20_mean"), low_mid_bull.get("n")),
            "HIGH_fixed20_down": high.get("fixed20_down"),
        },
    }


if __name__ == "__main__":
    print("=== 逢3必变 历史准确性深挖 ===")
    r = run()
    print(f"\n→ {r.get('report_path')}")
    print(f"still_weak={r.get('still_weak')} key={r.get('key')}")
