#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逢3必变批量回测（缓存主板）— 研究用，非荐股。

conf 规则随 lib/feng3.py：v2 起 conf高=struct_agree（不再要求 near∧struct）。
环境闸 / 任意摆动×红灯对照见 scripts/backtest_feng3_opt_v2.py → output/feng3/opt_v2/

用法:
  /Users/meng/测试/.venv/bin/python scripts/backtest_feng3.py --sample 800 --seed 42
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib.data import list_cached_symbols, load_kline_csv
from lib.feng3 import scan_symbol
from lib.metrics import summarize_returns
from lib.paths import OUTPUT


def _agg(df: pd.DataFrame, mask: Optional[pd.Series], name: str) -> Dict[str, Any]:
    sub = df if mask is None else df.loc[mask]
    n = int(len(sub))
    if n == 0:
        return {
            "slice": name,
            "n": 0,
            "hit_extreme_pct": None,
            "mae_mean": None,
            "path_n": 0,
            "path_winrate": None,
            "path_mean": None,
            "fixed_up_pct": None,
            "fixed_down_pct": None,
            "fixed_mean": None,
        }
    hit = sub["hit_extreme"].astype(bool)
    path = sub["path_ret"].dropna()
    fixed = sub["ret_fixed"].dropna()
    ps = summarize_returns(path.tolist(), name) if len(path) else {"n": 0, "winrate": None, "mean": None}
    return {
        "slice": name,
        "n": n,
        "hit_extreme_pct": round(float(hit.mean()) * 100, 2),
        "mae_mean": round(float(sub["mae"].dropna().mean()), 3) if sub["mae"].notna().any() else None,
        "path_n": int(ps.get("n") or 0),
        "path_winrate": None if ps.get("winrate") != ps.get("winrate") else (
            None if ps.get("winrate") is None else round(float(ps["winrate"]), 2)
        ),
        "path_mean": None if ps.get("mean") != ps.get("mean") else (
            None if ps.get("mean") is None else round(float(ps["mean"]), 3)
        ),
        "fixed_up_pct": round(float((fixed > 0).mean()) * 100, 2) if len(fixed) else None,
        "fixed_down_pct": round(float((fixed < 0).mean()) * 100, 2) if len(fixed) else None,
        "fixed_mean": round(float(fixed.mean()), 3) if len(fixed) else None,
    }


def run_backtest(
    sample_n: int = 800,
    seed: int = 42,
    swing_k: int = 3,
    near_pct: float = 2.0,
    hit_n: int = 10,
    hold_m: int = 20,
    mae_tol: float = 2.0,
    cost_bps: float = 15.0,
    exit_mode: str = "tight",
    min_bars: int = 200,
    out_subdir: str = "feng3",
) -> Dict[str, Any]:
    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))

    rows: List[Dict[str, Any]] = []
    n_ok = 0
    t0 = time.time()
    for j, sym in enumerate(pool):
        df = load_kline_csv(sym, min_bars=min_bars)
        if df is None or len(df) < min_bars:
            continue
        try:
            evs = scan_symbol(
                df,
                sym,
                swing_k=swing_k,
                near_pct=near_pct,
                hit_n=hit_n,
                hold_m=hold_m,
                mae_tol=mae_tol,
                cost_bps=cost_bps,
                exit_mode=exit_mode,
            )
        except Exception:
            continue
        if evs:
            rows.extend(evs)
            n_ok += 1
        if (j + 1) % 200 == 0:
            print(f"  … {j+1}/{len(pool)} symbols, events={len(rows)}, elapsed={time.time()-t0:.1f}s")

    out_dir = os.path.join(OUTPUT, out_subdir)
    os.makedirs(out_dir, exist_ok=True)
    events_path = os.path.join(out_dir, "feng3_events.csv")
    summary_path = os.path.join(out_dir, "feng3_summary.csv")
    html_path = os.path.join(out_dir, "feng3_summary.html")
    md_path = os.path.join(out_dir, "feng3_summary.md")

    if not rows:
        empty = pd.DataFrame()
        empty.to_csv(events_path, index=False, encoding="utf-8-sig")
        return {
            "ok": False,
            "error": "无事件",
            "n_symbols": len(pool),
            "n_with_events": 0,
            "events_path": events_path,
        }

    edf = pd.DataFrame(rows)
    edf.to_csv(events_path, index=False, encoding="utf-8-sig")

    slices = [
        _agg(edf, None, "ALL"),
        _agg(edf, edf["pred"] == "LOW", "LOW"),
        _agg(edf, edf["pred"] == "HIGH", "HIGH"),
        _agg(edf, (edf["pred"] == "LOW") & (edf["conf"] == "高"), "LOW_conf高"),
        _agg(edf, (edf["pred"] == "HIGH") & (edf["conf"] == "高"), "HIGH_conf高"),
        _agg(edf, (edf["pred"] == "LOW") & (edf["conf"] == "中"), "LOW_conf中"),
        _agg(edf, (edf["pred"] == "HIGH") & (edf["conf"] == "中"), "HIGH_conf中"),
        _agg(edf, edf["conf"] == "低", "conf低"),
    ]
    sdf = pd.DataFrame(slices)
    sdf.to_csv(summary_path, index=False, encoding="utf-8-sig")

    # 诚实结论
    low = next(s for s in slices if s["slice"] == "LOW")
    high = next(s for s in slices if s["slice"] == "HIGH")
    alls = slices[0]
    verdict_parts = []
    if low["n"] >= 30 and low["hit_extreme_pct"] is not None:
        if low["hit_extreme_pct"] >= 55 and (low["path_mean"] or 0) > 0.1:
            verdict_parts.append("LOW 侧 hit@N 与 PATH 均值略正 → 薄边可继续观察")
        elif low["hit_extreme_pct"] >= 50:
            verdict_parts.append("LOW 侧 hit@N 贴近/略高于随机，PATH 需看均值是否塌")
        else:
            verdict_parts.append("LOW 侧 hit@N 偏弱")
    if high["n"] >= 30 and high["hit_extreme_pct"] is not None:
        # HIGH：看跌用 fixed_down；PATH 仍是做多路径（对照）
        if high["hit_extreme_pct"] >= 55 and (high.get("fixed_down_pct") or 0) >= 52:
            verdict_parts.append("HIGH 侧极端命中尚可，固定持有下跌占比略高 → 观察用")
        else:
            verdict_parts.append("HIGH 侧证据不足或偏弱（A股不做空，PATH做多仅对照）")
    if not verdict_parts:
        verdict = "样本不足，扩大 sample 再下结论"
    else:
        verdict = "；".join(verdict_parts)
        # 总评
        weak = True
        if low["n"] >= 50 and (low["path_mean"] or -1) > 0.2 and (low["hit_extreme_pct"] or 0) >= 55:
            weak = False
        if weak:
            verdict += "。总评：首轮边很可能偏弱，保持研究层，不升格 joint/action。"

    lines = [
        "# 逢3必变 回测摘要（feng3 conf=v2）",
        "",
        f"- 样本股: **{len(pool)}**（有事件 **{n_ok}**） seed={seed}",
        f"- 参数: swing_k={swing_k} near_pct={near_pct}% hit_n={hit_n} hold_m={hold_m} mae_tol={mae_tol}%",
        f"- PATH: exit={exit_mode} cost={cost_bps}bps（决策看 PATH；HIGH 的 PATH 为做多对照）",
        f"- 耗时: {time.time()-t0:.1f}s",
        "",
        "> 研究工具，不构成投资建议。历史命中 ≠ 下一笔可预测。",
        "",
        "## 汇总",
        "",
        "| slice | n | hit_extreme% | MAE均值 | PATH n | PATH胜率% | PATH均值% | 固定上涨% | 固定下跌% | 固定均值% |",
        "|-------|---|--------------|---------|--------|-----------|-----------|-----------|-----------|-----------|",
    ]
    for s in slices:
        def _f(v, suf=""):
            if v is None:
                return "—"
            return f"{v}{suf}"
        lines.append(
            f"| {s['slice']} | {s['n']} | {_f(s['hit_extreme_pct'])} | {_f(s['mae_mean'])} | "
            f"{s['path_n']} | {_f(s['path_winrate'])} | {_f(s['path_mean'])} | "
            f"{_f(s['fixed_up_pct'])} | {_f(s['fixed_down_pct'])} | {_f(s['fixed_mean'])} |"
        )
    lines.extend([
        "",
        "## 结论（自动草稿）",
        "",
        verdict,
        "",
        f"- 事件明细: `{events_path}`",
        f"- 汇总表: `{summary_path}`",
        "",
        "## 怎么读",
        "",
        "- hit_extreme@N：信号价是否接近窗内真实极值（tol=mae_tol）；≈50% 接近碰运气。",
        "- LOW 的 PATH 均值/胜率：若贴近 robust 薄边或更差 → 不升格。",
        "- HIGH：A股难融券；看 hit + 固定持有下跌占比，PATH 做多仅对照。",
    ])
    md = "\n".join(lines)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>逢3必变 回测（conf=v2）</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;max-width:1100px}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ccc;padding:6px 8px;font-size:13px}}
th{{background:#f5f5f5}} .note{{color:#666;font-size:13px}}
</style></head><body>
<h1>逢3必变 回测摘要（conf=v2）</h1>
<p class="note">研究工具，非荐股。PATH 主结论；HIGH 的 PATH 为做多对照。</p>
<pre style="white-space:pre-wrap">{md}</pre>
</body></html>"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(md)
    return {
        "ok": True,
        "n_symbols": len(pool),
        "n_with_events": n_ok,
        "n_events": len(edf),
        "summary": slices,
        "verdict": verdict,
        "events_path": events_path,
        "summary_path": summary_path,
        "html_path": html_path,
        "md_path": md_path,
        "elapsed_s": round(time.time() - t0, 1),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="逢3必变高低点研究回测（非荐股）")
    p.add_argument("--sample", type=int, default=800, help="抽样主板数量")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--swing-k", type=int, default=3)
    p.add_argument("--near-pct", type=float, default=2.0)
    p.add_argument("--hit-n", type=int, default=10)
    p.add_argument("--hold-m", type=int, default=20)
    p.add_argument("--mae-tol", type=float, default=2.0)
    p.add_argument("--cost-bps", type=float, default=15.0)
    p.add_argument("--exit", default="tight", choices=["tight", "wr", "asym", "fixed"])
    p.add_argument("--min-bars", type=int, default=200)
    p.add_argument("--out-subdir", default="feng3", help="output 下子目录，默认 feng3；opt 可用 feng3/opt_v2")
    args = p.parse_args(argv)
    print("=== 逢3必变 批量回测（conf=v2）===")
    print(
        f"sample={args.sample} seed={args.seed} swing_k={args.swing_k} "
        f"near={args.near_pct}% hit_n={args.hit_n} hold_m={args.hold_m} exit={args.exit}"
    )
    print("默认池: 沪深主板非ST；决策看 PATH。不构成投资建议。")
    r = run_backtest(
        sample_n=args.sample,
        seed=args.seed,
        swing_k=args.swing_k,
        near_pct=args.near_pct,
        hit_n=args.hit_n,
        hold_m=args.hold_m,
        mae_tol=args.mae_tol,
        cost_bps=args.cost_bps,
        exit_mode=args.exit,
        min_bars=args.min_bars,
        out_subdir=args.out_subdir,
    )
    if not r.get("ok"):
        print("失败:", r.get("error"))
        return 1
    print(f"\n→ {r.get('md_path')}")
    print(f"→ {r.get('html_path')}")
    print(f"→ {r.get('events_path')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
