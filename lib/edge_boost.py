# -*- coding: utf-8 -*-
"""边沿增强阶梯：在 robust v5 之上测「还能靠什么」。

原则：
  - 只测入场日可知特征（无未来函数）
  - 主结论看 PATH + tight + 扣成本
  - 胜出规则才建议写进操作层；否则只出报告

用法:
  python3 cli.py edge --sample 350
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .data import list_cached_symbols, load_kline_csv
from .factors import formula3_mask, index_low_vol_series, rsi_series
from .metrics import summarize_returns
from .paths import OUTPUT
from .stress import exit_path_daily


def _fresh(sig: pd.Series) -> pd.Series:
    cur = sig.fillna(False).astype(bool)
    prev = sig.shift(1)
    prev = prev.where(prev.notna(), False).astype(bool)
    return cur & ~prev


def _align_idx(df: pd.DataFrame, idx_df: pd.DataFrame) -> pd.DataFrame:
    """把指数 close/ma 对齐到个股日期。"""
    idx = idx_df.copy()
    idx["date"] = idx["date"].astype(str)
    ic = idx.set_index("date")["close"].astype(float)
    d = df["date"].astype(str)
    aligned = ic.reindex(d.values).ffill()
    out = pd.DataFrame(index=df.index)
    out["ic"] = aligned.values
    out["ima20"] = pd.Series(aligned.values).rolling(20).mean().values
    out["ima60"] = pd.Series(aligned.values).rolling(60).mean().values
    out["iret20"] = (out["ic"] / out["ic"].shift(20) - 1.0) * 100.0
    out["iret5"] = (out["ic"] / out["ic"].shift(5) - 1.0) * 100.0
    out["ima20_up"] = out["ima20"] >= out["ima20"].shift(5)
    # 连续站上 MA20 的天数（入场日可知）
    above = (out["ic"] > out["ima20"]).astype(int)
    streak = np.zeros(len(out), dtype=int)
    for i in range(len(out)):
        if above.iloc[i] == 1:
            streak[i] = (streak[i - 1] + 1) if i else 1
        else:
            streak[i] = 0
    out["green_days"] = streak
    return out


def build_variant_masks(
    df: pd.DataFrame,
    idx_df: pd.DataFrame,
    require_low_vol: bool = True,
) -> Dict[str, pd.Series]:
    """返回 name -> bool Series（非 fresh）。"""
    c = df["close"].astype(float)
    v = df["vol"].astype(float)
    ma20 = c.rolling(20).mean()
    bias = (c / ma20 - 1.0) * 100.0
    zf = (c / c.shift(60) - 1.0) * 100.0
    vr = v.rolling(5).mean() / v.rolling(60).mean()
    r = rsi_series(c)

    robust = formula3_mask(df, idx_df, mode="robust", require_low_vol=require_low_vol)
    # 无低波版（对照）
    robust_nolv = formula3_mask(df, idx_df, mode="robust", require_low_vol=False)
    elite = formula3_mask(df, idx_df, mode="elite", require_low_vol=False)

    ax = _align_idx(df, idx_df)
    iret20 = ax["iret20"]
    iret5 = ax["iret5"]
    ima20_up = ax["ima20_up"].fillna(False).astype(bool)
    green_days = ax["green_days"]

    # 指数低波序列（对齐）
    idx2 = idx_df.copy()
    idx2["date"] = idx2["date"].astype(str)
    lv = index_low_vol_series(idx2, q=0.5)
    lv_map = dict(zip(idx2["date"], lv.astype(bool)))
    low_vol = pd.Series(
        [bool(lv_map.get(str(d), False)) for d in df["date"]], index=df.index
    )

    masks: Dict[str, pd.Series] = {
        "elite": elite.fillna(False),
        "robust_nolv": robust_nolv.fillna(False),
        "robust": robust.fillna(False),
        # —— 环境增强（压力测试：持有期指数牛更赚）——
        "r_idx_mom0": (robust & (iret20 > 0)).fillna(False),
        "r_idx_mom1": (robust & (iret20 > 1)).fillna(False),
        "r_idx_mom2": (robust & (iret20 > 2)).fillna(False),
        "r_idx_up5": (robust & ima20_up).fillna(False),
        "r_green5": (robust & (green_days >= 5)).fillna(False),
        "r_green10": (robust & (green_days >= 10)).fillna(False),
        # —— 结构再收窄（entry_grid 曾接近最优）——
        "r_narrow": (
            robust_nolv & low_vol & (zf > 5) & (zf <= 12) & (bias >= 0) & (bias <= 3)
        ).fillna(False),
        "r_vr_tight": (robust & (vr >= 1.2) & (vr <= 2.0)).fillna(False),
        "r_rsi_sweet": (robust & (r >= 48) & (r <= 58)).fillna(False),
        # —— 组合：低波 + 指数动量 + 收窄 ——
        "r_boost": (
            robust & (iret20 > 0) & ima20_up & (green_days >= 5)
        ).fillna(False),
        "r_boost_narrow": (
            robust & (iret20 > 0) & ima20_up & (zf <= 12) & (bias <= 3)
        ).fillna(False),
    }
    return masks


def run_edge_ladder(
    sample_n: int = 350,
    seed: int = 42,
    period: int = 20,
    exit_mode: str = "tight",
    cost_bps: float = 15.0,
    require_low_vol: bool = True,
    # 同日最多取 top_k 只（按 bias 升序=更贴近均线优先）做「分散前的日切帽」
    daily_cap: Optional[int] = None,
) -> Dict[str, Any]:
    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))

    idx = load_kline_csv("sh000300", min_bars=300)
    if idx is None:
        return {"ok": False, "error": "缺少 sh000300"}

    # variant -> list of (date, ret) for optional daily cap
    events: Dict[str, List[Tuple[str, str, float, float]]] = {}
    # (sym, date, ret, sort_key)

    for sym in pool:
        df = load_kline_csv(sym, min_bars=320)
        if df is None or len(df) < 320:
            continue
        try:
            masks = build_variant_masks(df, idx, require_low_vol=require_low_vol)
        except Exception:
            continue
        c = df["close"].astype(float).values
        hi = df["high"].astype(float).values
        lo = df["low"].astype(float).values
        dates = df["date"].astype(str).values
        ma20 = df["close"].astype(float).rolling(20).mean()
        bias = ((df["close"].astype(float) / ma20 - 1.0) * 100.0).values

        for name, m in masks.items():
            fr = _fresh(m)
            for i in np.where(fr.values)[0]:
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
                sk = float(bias[i]) if bias[i] == bias[i] else 99.0
                events.setdefault(name, []).append((sym, dates[i], ret, sk))

    # optional daily cap: each date keep top_k lowest bias
    if daily_cap and daily_cap > 0:
        capped: Dict[str, List[Tuple[str, str, float, float]]] = {}
        for name, rows in events.items():
            by_day: Dict[str, List[Tuple[str, str, float, float]]] = {}
            for row in rows:
                by_day.setdefault(row[1], []).append(row)
            kept = []
            for d, lst in by_day.items():
                lst.sort(key=lambda x: x[3])  # bias asc
                kept.extend(lst[:daily_cap])
            capped[name] = kept
        events = capped

    summary = []
    for name in sorted(events.keys(), key=lambda n: -len(events[n])):
        rets = [r[2] for r in events[name]]
        st = summarize_returns(rets, name)
        summary.append({
            "variant": name,
            "n": int(st["n"]),
            "winrate": float(st["winrate"]) if st["n"] else 0.0,
            "mean": float(st["mean"]) if st["n"] else 0.0,
            "median": float(st["median"]) if st["n"] else 0.0,
            "sharpe": float(st["sharpe"]) if st["n"] and st["sharpe"] == st["sharpe"] else 0.0,
            "mdd": float(st["mdd"]) if st["n"] and st["mdd"] == st["mdd"] else 0.0,
            "pf": float(st["pf"]) if st["n"] and st["pf"] == st["pf"] else 0.0,
        })

    # rank: mean first, then sharpe, require n>=20
    ranked = [r for r in summary if r["n"] >= 20]
    ranked.sort(key=lambda r: (r["mean"], r["sharpe"], r["winrate"]), reverse=True)
    base = next((r for r in summary if r["variant"] == "robust"), None)

    picks = []
    if base and ranked:
        for r in ranked:
            if r["variant"] == "robust":
                continue
            dm = r["mean"] - base["mean"]
            dw = r["winrate"] - base["winrate"]
            # 有效改进：均值提升且 n 不太稀，或均值持平但胜率+夏普更好且 n 仍够
            if dm >= 0.15 and r["n"] >= max(20, int(base["n"] * 0.35)):
                picks.append({**r, "delta_mean": dm, "delta_wr": dw, "tag": "均值改善"})
            elif dm >= 0.05 and dw >= 1.0 and r["sharpe"] >= base["sharpe"] and r["n"] >= 20:
                picks.append({**r, "delta_mean": dm, "delta_wr": dw, "tag": "综合略优"})

    # 跨样本更稳的「环境类」优先于「更窄结构」（后者 seed 间易翻车）
    env_prefer = ("r_idx_mom0", "r_idx_mom1", "r_green5", "r_boost", "r_idx_up5")
    env_picks = [p for p in picks if p["variant"] in env_prefer]
    struct_picks = [p for p in picks if p["variant"] not in env_prefer]
    ordered_picks = env_picks + struct_picks

    # 默认推荐：底仓 robust + 环境闸门 + 组合纪律（不把单次 seed 冠军写死）
    recommend = {
        "entry": "robust v5（反热+指数低波）为底仓",
        "boost_if_any": {
            "variant": "r_idx_mom0 / env_gate",
            "how": "指数20日涨幅>0 时才按标准仓做新开；≤0 降仓/少开（action playbook 已挂）",
            "optional_mode": "boost",
            "note": "环境类在多种子上方向更稳；r_narrow 等结构再收窄勿默认启用",
        },
        "never": [
            "缠论单独买卖点",
            "elite/winrate 的 OPT 七成当实盘",
            "红灯逆势",
            "MENGα/形态/妖股单独买入",
            "单票梭哈/不设止损/补仓摊成本",
        ],
        "portfolio": {
            "max_names": 6,
            "max_weight_pct": 15,
            "cash_pct": 25,
            "exit": "PATH 纪律 tight TP4/SL3；破位不补仓",
            "note": "边在分散+止损+少做，不在单票神话",
        },
    }
    if ordered_picks:
        best = ordered_picks[0]
        recommend["sample_best"] = {
            "variant": best["variant"],
            "path_mean": best["mean"],
            "path_wr": best["winrate"],
            "n": best["n"],
            "delta_mean_vs_robust": best.get("delta_mean"),
            "how": _explain_variant(best["variant"]),
            "tag": best.get("tag"),
        }
    picks = ordered_picks[:8]

    os.makedirs(OUTPUT, exist_ok=True)
    sum_df = pd.DataFrame(summary).sort_values("mean", ascending=False)
    path = os.path.join(OUTPUT, "edge_ladder.csv")
    sum_df.to_csv(path, index=False, encoding="utf-8-sig")

    # markdown
    md_path = os.path.join(OUTPUT, "edge_ladder_summary.md")
    lines = [
        "# 边沿增强阶梯（PATH）",
        "",
        f"- sample={len(pool)} seed={seed} hold={period} exit={exit_mode} cost={cost_bps}bps",
        f"- daily_cap={daily_cap or '无'}",
        f"- 低波默认={'开' if require_low_vol else '关'}",
        "",
        "## 排名（按 PATH 均值）",
        "",
        "| variant | n | 胜率% | 均值% | Sharpe | MDD% |",
        "|---------|---|-------|-------|--------|------|",
    ]
    for r in sum_df.itertuples():
        lines.append(
            f"| {r.variant} | {r.n} | {r.winrate:.1f} | {r.mean:+.2f} | {r.sharpe:.2f} | {r.mdd:.1f} |"
        )
    lines += ["", "## 相对 robust 的候选", ""]
    if picks:
        for p in picks:
            lines.append(
                f"- **{p['variant']}** ({p['tag']}): n={p['n']} 胜率{p['winrate']:.1f}% "
                f"均值{p['mean']:+.2f}% (Δmean {p['delta_mean']:+.2f}pt)"
            )
    else:
        lines.append("- 无（本样本未出现稳定优于 robust 的过滤器）")
    lines += [
        "",
        "## 操作建议",
        "",
        f"- 入场底仓: {recommend['entry']}",
        f"- 增强: {recommend['boost_if_any']}",
        f"- 组合: 最多{recommend['portfolio']['max_names']}只 / 单只≤{recommend['portfolio']['max_weight_pct']}% / 现金≥{recommend['portfolio']['cash_pct']}%",
        f"- 出场: {recommend['portfolio']['exit']}",
        f"- 永远不做: {', '.join(recommend['never'])}",
        "",
        "不构成投资建议。历史≠未来。",
        "",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "ok": True,
        "sample_n": len(pool),
        "period": period,
        "exit_mode": exit_mode,
        "summary": summary,
        "ranked": ranked,
        "picks": picks,
        "recommend": recommend,
        "path": path,
        "md_path": md_path,
        "base": base,
        "disclaimer": "PATH tight 扣成本；研究用，非荐股。",
    }


def _explain_variant(name: str) -> str:
    return {
        "r_idx_mom0": "robust + 指数20日涨幅>0",
        "r_idx_mom1": "robust + 指数20日涨幅>1%",
        "r_idx_mom2": "robust + 指数20日涨幅>2%",
        "r_idx_up5": "robust + 指数MA20上行",
        "r_green5": "robust + 指数连续≥5日站上MA20",
        "r_green10": "robust + 指数连续≥10日站上MA20",
        "r_narrow": "更窄 zf/bias + 低波",
        "r_vr_tight": "robust + 量比 1.2~2.0",
        "r_rsi_sweet": "robust + RSI 48~58",
        "r_boost": "robust + 指数动量>0 + MA20上行 + 转绿≥5日",
        "r_boost_narrow": "r_boost 思路 + 更窄涨幅/乖离",
        "robust": "robust v5 原规则",
        "robust_nolv": "robust 关低波",
        "elite": "elite 对照",
    }.get(name, name)


# ---------------------------------------------------------------------------
# 组合级 PATH：同日 cap / 持仓上限 / 环境闸门
# ---------------------------------------------------------------------------

def run_portfolio_path(
    sample_n: int = 350,
    seed: int = 42,
    period: int = 20,
    exit_mode: str = "tight",
    cost_bps: float = 15.0,
    require_low_vol: bool = True,
) -> Dict[str, Any]:
    """事件池上叠加组合约束，比较 PATH 结果。

    政策：
      all          — 有信号就做（无 cap）
      cap3         — 同日最多 3 只（bias 升序=更贴均线优先）
      playbook     — 同日≤3 + 并发持仓≤6
      playbook_env — playbook + 指数20日>0 才新开
      boost_pb     — 入场用 boost 信号 + playbook 约束
    """
    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))
    idx = load_kline_csv("sh000300", min_bars=300)
    if idx is None:
        return {"ok": False, "error": "缺少 sh000300"}

    # 指数 iret20 by date
    idx2 = idx.copy()
    idx2["date"] = idx2["date"].astype(str)
    ic = idx2["close"].astype(float)
    iret20_s = (ic / ic.shift(20) - 1.0) * 100.0
    idx_mom_map = dict(zip(idx2["date"], iret20_s))
    # 交易日顺序
    cal = list(idx2["date"].values)
    cal_pos = {d: i for i, d in enumerate(cal)}

    events_robust: List[Dict[str, Any]] = []
    events_boost: List[Dict[str, Any]] = []

    for sym in pool:
        df = load_kline_csv(sym, min_bars=320)
        if df is None or len(df) < 320:
            continue
        try:
            masks = build_variant_masks(df, idx, require_low_vol=require_low_vol)
        except Exception:
            continue
        c = df["close"].astype(float).values
        hi = df["high"].astype(float).values
        lo = df["low"].astype(float).values
        dates = df["date"].astype(str).values
        ma20 = pd.Series(c).rolling(20).mean().values
        bias = np.where(ma20 > 0, (c / ma20 - 1.0) * 100.0, 99.0)

        for mode_name, bucket in (("robust", events_robust), ("boost", events_boost)):
            key = "robust" if mode_name == "robust" else "r_idx_mom0"
            # r_idx_mom0 == boost 语义；若 masks 无则用 robust & 现场算
            m = masks.get(key)
            if m is None:
                m = masks["robust"]
            fr = _fresh(m)
            for i in np.where(fr.values)[0]:
                if i + period >= len(c):
                    continue
                d = dates[i]
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
                im = idx_mom_map.get(d)
                if im is None or im != im:
                    continue
                # 持仓结束交易日
                p0 = cal_pos.get(d)
                if p0 is None:
                    continue
                p1 = min(p0 + period, len(cal) - 1)
                exit_d = cal[p1]
                bucket.append({
                    "sym": sym,
                    "date": d,
                    "exit_date": exit_d,
                    "ret": ret,
                    "bias": float(bias[i]) if bias[i] == bias[i] else 99.0,
                    "iret20": float(im),
                })

    def _simulate(
        events: List[Dict[str, Any]],
        daily_cap: Optional[int],
        max_hold: Optional[int],
        env_gate: bool,
        name: str,
    ) -> Dict[str, Any]:
        by_day: Dict[str, List[Dict[str, Any]]] = {}
        for e in events:
            by_day.setdefault(e["date"], []).append(e)
        days = sorted(by_day.keys(), key=lambda d: cal_pos.get(d, 10**9))
        open_pos: List[Dict[str, Any]] = []  # {exit_date, ret, sym}
        booked: List[float] = []
        n_skip_env = 0
        n_skip_cap = 0
        n_taken = 0

        for d in days:
            # 释放到期仓位
            still = []
            for p in open_pos:
                if cal_pos.get(p["exit_date"], -1) <= cal_pos.get(d, -1):
                    booked.append(p["ret"])
                else:
                    still.append(p)
            open_pos = still

            cands = by_day[d]
            if env_gate:
                # 当日指数动量：用候选上的 iret20（同日一致）
                im = cands[0]["iret20"]
                if im <= 0:
                    n_skip_env += len(cands)
                    continue
            cands = sorted(cands, key=lambda x: x["bias"])
            slots = 10**9 if not max_hold else max(0, max_hold - len(open_pos))
            lim = slots if daily_cap is None else min(daily_cap, slots)
            if lim <= 0:
                n_skip_cap += len(cands)
                continue
            take = cands[:lim]
            n_skip_cap += max(0, len(cands) - len(take))
            for e in take:
                # 避免同票重复持仓
                if any(p["sym"] == e["sym"] for p in open_pos):
                    continue
                open_pos.append({"exit_date": e["exit_date"], "ret": e["ret"], "sym": e["sym"]})
                n_taken += 1

        # 收尾：未到期也按已算 PATH ret 入账（研究简化）
        for p in open_pos:
            booked.append(p["ret"])

        st = summarize_returns(booked, name)
        return {
            "policy": name,
            "n": int(st["n"]),
            "winrate": float(st["winrate"]) if st["n"] else 0.0,
            "mean": float(st["mean"]) if st["n"] else 0.0,
            "median": float(st["median"]) if st["n"] else 0.0,
            "sharpe": float(st["sharpe"]) if st["n"] and st["sharpe"] == st["sharpe"] else 0.0,
            "mdd": float(st["mdd"]) if st["n"] and st["mdd"] == st["mdd"] else 0.0,
            "n_taken": n_taken,
            "n_skip_env": n_skip_env,
            "n_skip_cap": n_skip_cap,
        }

    policies = [
        _simulate(events_robust, None, None, False, "all_signals"),
        _simulate(events_robust, 3, None, False, "cap3"),
        _simulate(events_robust, 3, 6, False, "playbook"),
        _simulate(events_robust, 3, 6, True, "playbook_env"),
        _simulate(events_boost, 3, 6, False, "boost_pb"),  # boost 已含 mom>0
    ]

    os.makedirs(OUTPUT, exist_ok=True)
    df_out = pd.DataFrame(policies)
    path = os.path.join(OUTPUT, "portfolio_path.csv")
    df_out.to_csv(path, index=False, encoding="utf-8-sig")

    # 结论
    base = next((p for p in policies if p["policy"] == "all_signals"), None)
    best = max(
        (p for p in policies if p["n"] >= 15),
        key=lambda p: (p["mean"], p["sharpe"]),
        default=None,
    )
    verdict = "样本不足"
    if base and best:
        if best["policy"] != "all_signals" and best["mean"] > base["mean"] + 0.05:
            verdict = (
                f"组合约束有益：最优 {best['policy']} 均值 {best['mean']:+.2f}% "
                f"vs all {base['mean']:+.2f}%（n={best['n']}/{base['n']}）"
            )
        elif best["policy"] == "all_signals":
            verdict = "全信号均值不差；组合约束主要控风险/仓位，未必抬升单笔均值"
        else:
            verdict = (
                f"组合约束与全信号接近；仍推荐 playbook 控仓"
                f"（最优 {best['policy']} mean={best['mean']:+.2f}%）"
            )

    md_path = os.path.join(OUTPUT, "portfolio_path_summary.md")
    lines = [
        "# 组合级 PATH 模拟",
        "",
        f"- sample={len(pool)} seed={seed} hold={period} exit={exit_mode}",
        f"- 入场底：robust v5；boost = robust+指数20日>0",
        "",
        "| policy | n | 胜率% | 均值% | Sharpe | MDD% | skip_env | skip_cap |",
        "|--------|---|-------|-------|--------|------|----------|----------|",
    ]
    for p in policies:
        lines.append(
            f"| {p['policy']} | {p['n']} | {p['winrate']:.1f} | {p['mean']:+.2f} | "
            f"{p['sharpe']:.2f} | {p['mdd']:.1f} | {p['n_skip_env']} | {p['n_skip_cap']} |"
        )
    lines += ["", f"**结论:** {verdict}", "", "不构成投资建议。", ""]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "ok": True,
        "sample_n": len(pool),
        "policies": policies,
        "verdict": verdict,
        "path": path,
        "md_path": md_path,
        "n_events_robust": len(events_robust),
        "n_events_boost": len(events_boost),
        "disclaimer": "组合 PATH 为事件+并发约束近似；非完整资金曲线撮合。",
    }


# ---------------------------------------------------------------------------
# 等权/定额组合资金曲线（日频 NAV + PATH 出场）
# ---------------------------------------------------------------------------

def _tp_sl(exit_mode: str) -> Tuple[float, float]:
    return {
        "asym": (8.0, 5.0),
        "wr": (5.0, 3.0),
        "tight": (4.0, 3.0),
    }.get(exit_mode, (4.0, 3.0))


def run_equity_curve(
    sample_n: int = 350,
    seed: int = 42,
    period: int = 20,
    exit_mode: str = "tight",
    cost_bps: float = 15.0,
    require_low_vol: bool = True,
    max_hold: int = 6,
    daily_cap: int = 3,
    max_weight: float = 0.125,  # 单只占净值；0.125 → 6 只满仓约 75%，现金 25%
    env_gate: bool = True,
    use_boost: bool = False,
    max_per_industry: int = 0,  # 0=不限制；2=同行业最多2
) -> Dict[str, Any]:
    """日频资金曲线：定额开仓 + PATH 止盈止损 + 持有上限。

    - 开仓：当日信号按 bias 升序取 daily_cap，且持仓 < max_hold
    - 每笔名义 = 当时 NAV * max_weight（扣往返成本的一半作开仓费，平仓再扣一半）
    - 出场：逐日 high/low 触 TP/SL（同日先损）；或满 period 日按收盘
    - env_gate：指数 20 日 ≤0 不新开
    - max_per_industry：同行业持仓上限（需 industry 缓存）
    """
    from .metrics import apply_roundtrip_cost
    from .industry import industry_of, load_industry_map

    rng = np.random.RandomState(seed)
    pool = list_cached_symbols(mainboard_only=True, drop_st=True)
    if sample_n and sample_n < len(pool):
        pool = list(rng.choice(pool, size=sample_n, replace=False))
    idx = load_kline_csv("sh000300", min_bars=300)
    if idx is None:
        return {"ok": False, "error": "缺少 sh000300"}

    idx2 = idx.copy()
    idx2["date"] = idx2["date"].astype(str)
    cal = list(idx2["date"].values)
    cal_pos = {d: i for i, d in enumerate(cal)}
    ic = idx2["close"].astype(float).values
    iret20 = np.full(len(cal), np.nan)
    for i in range(20, len(cal)):
        if ic[i - 20] > 0:
            iret20[i] = (ic[i] / ic[i - 20] - 1.0) * 100.0

    # 信号：date -> list of candidates
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    # 行情缓存 sym -> arrays aligned? 用 dict per sym for OHLCV by date
    book: Dict[str, Dict[str, Any]] = {}

    for sym in pool:
        df = load_kline_csv(sym, min_bars=320)
        if df is None or len(df) < 320:
            continue
        try:
            masks = build_variant_masks(df, idx, require_low_vol=require_low_vol)
        except Exception:
            continue
        key = "r_idx_mom0" if use_boost else "robust"
        m = masks.get(key, masks.get("robust"))
        if m is None:
            continue
        fr = _fresh(m)
        c = df["close"].astype(float).values
        hi = df["high"].astype(float).values
        lo = df["low"].astype(float).values
        dates = df["date"].astype(str).values
        ma20 = pd.Series(c).rolling(20).mean().values
        bias = np.where(ma20 > 0, (c / ma20 - 1.0) * 100.0, 99.0)
        # date -> row index
        d2i = {dates[i]: i for i in range(len(dates))}
        book[sym] = {"c": c, "h": hi, "l": lo, "d2i": d2i, "dates": dates}
        for i in np.where(fr.values)[0]:
            d = dates[i]
            if d not in cal_pos:
                continue
            by_day.setdefault(d, []).append({
                "sym": sym,
                "i": i,
                "bias": float(bias[i]) if bias[i] == bias[i] else 99.0,
                "px": float(c[i]),
            })

    tp, sl = _tp_sl(exit_mode)
    half_cost = cost_bps / 10000.0  # fraction of notional one-way
    ind_map = load_industry_map() if max_per_industry and max_per_industry > 0 else {}
    # 用绝对资金
    cash = 1.0
    positions: List[Dict[str, Any]] = []
    nav_hist: List[Tuple[str, float]] = []
    trades: List[Dict[str, Any]] = []
    n_skip_ind = 0

    def mark_nav(di: int) -> float:
        d = cal[di]
        eq = cash
        for p in positions:
            sym = p["sym"]
            b = book.get(sym)
            if not b:
                continue
            ii = b["d2i"].get(d)
            if ii is None:
                # 停牌：用入场价
                eq += p["shares"] * p["entry_px"]
            else:
                eq += p["shares"] * float(b["c"][ii])
        return eq

    for di, d in enumerate(cal):
        # —— 出场检查 ——
        still = []
        for p in positions:
            sym = p["sym"]
            b = book.get(sym)
            if not b:
                still.append(p)
                continue
            ii = b["d2i"].get(d)
            if ii is None:
                still.append(p)
                continue
            # 仅在入场日之后检查
            if ii <= p["entry_i"]:
                still.append(p)
                continue
            held = ii - p["entry_i"]
            hi = float(b["h"][ii])
            lo = float(b["l"][ii])
            cl = float(b["c"][ii])
            entry = p["entry_px"]
            exit_px = None
            reason = None
            tp_hit = hi >= entry * (1 + tp / 100.0)
            sl_hit = lo <= entry * (1 - sl / 100.0)
            if tp_hit and sl_hit:
                exit_px = entry * (1 - sl / 100.0)
                reason = "sl_same"
            elif sl_hit:
                exit_px = entry * (1 - sl / 100.0)
                reason = "sl"
            elif tp_hit:
                exit_px = entry * (1 + tp / 100.0)
                reason = "tp"
            elif held >= period:
                exit_px = cl
                reason = "time"
            if exit_px is not None:
                # 平仓扣单边成本
                proceeds = p["shares"] * exit_px * (1.0 - half_cost)
                cash += proceeds
                ret_pct = (exit_px / entry - 1.0) * 100.0
                ret_pct = apply_roundtrip_cost(ret_pct, cost_bps)
                trades.append({
                    "sym": sym,
                    "entry_date": p["entry_date"],
                    "exit_date": d,
                    "ret": ret_pct,
                    "reason": reason,
                })
            else:
                still.append(p)
        positions = still

        # —— 新开 ——
        can_open = True
        if env_gate and di < len(iret20):
            im = iret20[di]
            if im == im and im <= 0:
                can_open = False
        if can_open and d in by_day:
            nav = mark_nav(di)
            if nav <= 0:
                nav = cash
            slots = max(0, max_hold - len(positions))
            lim = min(daily_cap, slots)
            if lim > 0:
                cands = sorted(by_day[d], key=lambda x: x["bias"])
                opened = 0
                held_syms = {p["sym"] for p in positions}
                # 当前持仓行业计数
                ind_counts: Dict[str, int] = {}
                if max_per_industry and max_per_industry > 0:
                    for p in positions:
                        ind = p.get("industry") or industry_of(p["sym"], ind_map)
                        if ind and ind != "未知":
                            ind_counts[ind] = ind_counts.get(ind, 0) + 1
                for cand in cands:
                    if opened >= lim:
                        break
                    if cand["sym"] in held_syms:
                        continue
                    if cand["px"] <= 0:
                        continue
                    ind = industry_of(cand["sym"], ind_map) if max_per_industry else "未知"
                    if max_per_industry and ind and ind != "未知":
                        if ind_counts.get(ind, 0) >= max_per_industry:
                            n_skip_ind += 1
                            continue
                    notional = nav * max_weight
                    if notional <= 0 or cash < notional * 0.5:
                        # 现金不足则按可用现金
                        notional = min(notional, cash * 0.95)
                    if notional < nav * 0.02:
                        break
                    # 开仓扣单边
                    spend = notional
                    if spend > cash:
                        spend = cash * 0.99
                    if spend <= 0:
                        break
                    shares = (spend * (1.0 - half_cost)) / cand["px"]
                    cash -= spend
                    positions.append({
                        "sym": cand["sym"],
                        "entry_date": d,
                        "entry_i": cand["i"],
                        "entry_px": cand["px"],
                        "shares": shares,
                        "industry": ind,
                    })
                    held_syms.add(cand["sym"])
                    if max_per_industry and ind and ind != "未知":
                        ind_counts[ind] = ind_counts.get(ind, 0) + 1
                    opened += 1

        nav_hist.append((d, mark_nav(di)))

    # 强制清仓期末
    if positions and nav_hist:
        last_d = nav_hist[-1][0]
        still_cash = cash
        for p in positions:
            b = book.get(p["sym"])
            px = p["entry_px"]
            if b:
                ii = b["d2i"].get(last_d)
                if ii is not None:
                    px = float(b["c"][ii])
            still_cash += p["shares"] * px * (1.0 - half_cost)
            trades.append({
                "sym": p["sym"],
                "entry_date": p["entry_date"],
                "exit_date": last_d,
                "ret": apply_roundtrip_cost((px / p["entry_px"] - 1.0) * 100.0, cost_bps),
                "reason": "eod",
            })
        cash = still_cash
        positions = []
        nav_hist[-1] = (last_d, cash)

    nav = np.array([x[1] for x in nav_hist], dtype=float)
    if len(nav) < 5:
        return {"ok": False, "error": "净值序列过短"}

    # 指标
    rets = np.diff(nav) / np.where(nav[:-1] == 0, np.nan, nav[:-1])
    rets = rets[np.isfinite(rets)]
    total = float(nav[-1] / nav[0] - 1.0) * 100.0
    days = max(1, len(nav))
    # 交易日年化
    ann = float((nav[-1] / nav[0]) ** (252.0 / days) - 1.0) * 100.0 if nav[0] > 0 else 0.0
    vol = float(np.std(rets, ddof=1) * np.sqrt(252) * 100) if len(rets) > 2 else 0.0
    sharpe = float((np.mean(rets) / np.std(rets, ddof=1)) * np.sqrt(252)) if len(rets) > 2 and np.std(rets, ddof=1) > 1e-12 else 0.0
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1.0
    mdd = float(dd.min() * 100)
    st = summarize_returns([t["ret"] for t in trades], "trades") if trades else {"n": 0, "winrate": 0, "mean": 0}

    # 指数同期对照
    i0, i1 = 0, len(cal) - 1
    bench = float(ic[i1] / ic[i0] - 1.0) * 100.0 if ic[i0] > 0 else 0.0

    os.makedirs(OUTPUT, exist_ok=True)
    eq_path = os.path.join(OUTPUT, "equity_curve.csv")
    pd.DataFrame({"date": [x[0] for x in nav_hist], "nav": nav}).to_csv(
        eq_path, index=False, encoding="utf-8-sig"
    )
    tr_path = os.path.join(OUTPUT, "equity_trades.csv")
    pd.DataFrame(trades).to_csv(tr_path, index=False, encoding="utf-8-sig")

    summary = {
        "policy": f"{'boost' if use_boost else 'robust'}_eq_h{max_hold}_c{daily_cap}"
        + ("_env" if env_gate else "")
        + (f"_ind{max_per_industry}" if max_per_industry else "")
        + f"_c{int(cost_bps)}",
        "n_trades": int(st.get("n") or 0),
        "trade_wr": float(st.get("winrate") or 0),
        "trade_mean": float(st.get("mean") or 0),
        "total_ret_pct": round(total, 2),
        "ann_ret_pct": round(ann, 2),
        "vol_ann_pct": round(vol, 2),
        "sharpe": round(sharpe, 2),
        "mdd_pct": round(mdd, 2),
        "bench_hs300_pct": round(bench, 2),
        "excess_pct": round(total - bench, 2),
        "final_nav": round(float(nav[-1]), 4),
        "days": days,
        "cost_bps": cost_bps,
        "max_per_industry": max_per_industry,
        "n_skip_ind": n_skip_ind,
    }

    md_path = os.path.join(OUTPUT, "equity_curve_summary.md")
    lines = [
        "# 组合资金曲线（PATH 出场）",
        "",
        f"- sample={len(pool)} seed={seed} exit={exit_mode} period≤{period}",
        f"- max_hold={max_hold} daily_cap={daily_cap} weight={max_weight} env_gate={env_gate} boost={use_boost}",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总收益 | {summary['total_ret_pct']}% |",
        f"| 年化 | {summary['ann_ret_pct']}% |",
        f"| 波动年化 | {summary['vol_ann_pct']}% |",
        f"| Sharpe | {summary['sharpe']} |",
        f"| 最大回撤 | {summary['mdd_pct']}% |",
        f"| 沪深300同期 | {summary['bench_hs300_pct']}% |",
        f"| 超额 | {summary['excess_pct']}% |",
        f"| 成交笔数 | {summary['n_trades']} |",
        f"| 单笔胜率 | {summary['trade_wr']:.1f}% |",
        f"| 单笔均值 | {summary['trade_mean']:+.2f}% |",
        "",
        "不构成投资建议。抽样池非全市场；停牌近似用前价。",
        "",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "ok": True,
        "summary": summary,
        "path": eq_path,
        "trades_path": tr_path,
        "md_path": md_path,
        "sample_n": len(pool),
        "disclaimer": "定额仓位日频净值；研究用。",
    }


def compare_equity_policies(
    sample_n: int = 350,
    seed: int = 42,
    period: int = 20,
    exit_mode: str = "tight",
    cost_bps: float = 15.0,
    include_stress: bool = True,
) -> Dict[str, Any]:
    """对照几套组合政策的资金曲线（含行业分散与成本压力）。"""
    specs = [
        {"name": "all_loose", "max_hold": 20, "daily_cap": 10, "env_gate": False, "use_boost": False, "max_weight": 0.05, "max_per_industry": 0, "cost_bps": cost_bps},
        {"name": "playbook", "max_hold": 6, "daily_cap": 3, "env_gate": False, "use_boost": False, "max_weight": 0.125, "max_per_industry": 0, "cost_bps": cost_bps},
        {"name": "playbook_env", "max_hold": 6, "daily_cap": 3, "env_gate": True, "use_boost": False, "max_weight": 0.125, "max_per_industry": 0, "cost_bps": cost_bps},
        {"name": "playbook_env_ind2", "max_hold": 6, "daily_cap": 3, "env_gate": True, "use_boost": False, "max_weight": 0.125, "max_per_industry": 2, "cost_bps": cost_bps},
        {"name": "boost_env_ind2", "max_hold": 6, "daily_cap": 3, "env_gate": True, "use_boost": True, "max_weight": 0.125, "max_per_industry": 2, "cost_bps": cost_bps},
    ]
    if include_stress:
        for c in (30.0, 50.0):
            specs.append({
                "name": f"playbook_env_ind2_c{int(c)}",
                "max_hold": 6, "daily_cap": 3, "env_gate": True, "use_boost": False,
                "max_weight": 0.125, "max_per_industry": 2, "cost_bps": c,
            })

    rows = []
    for sp in specs:
        r = run_equity_curve(
            sample_n=sample_n,
            seed=seed,
            period=period,
            exit_mode=exit_mode,
            cost_bps=float(sp.get("cost_bps", cost_bps)),
            max_hold=sp["max_hold"],
            daily_cap=sp["daily_cap"],
            max_weight=sp["max_weight"],
            env_gate=sp["env_gate"],
            use_boost=sp["use_boost"],
            max_per_industry=int(sp.get("max_per_industry") or 0),
        )
        if not r.get("ok"):
            rows.append({"policy": sp["name"], "error": r.get("error")})
            continue
        s = dict(r["summary"])
        s["policy"] = sp["name"]
        rows.append(s)

    os.makedirs(OUTPUT, exist_ok=True)
    path = os.path.join(OUTPUT, "equity_policies.csv")
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

    # 选最优：在 cost=默认 的非压力组里比总收益/sharpe
    base_rows = [r for r in rows if "total_ret_pct" in r and "c30" not in r["policy"] and "c50" not in r["policy"]]
    best = None
    if base_rows:
        best = max(base_rows, key=lambda r: (r.get("total_ret_pct", -999), r.get("sharpe", -999), r.get("excess_pct", -999)))

    md = os.path.join(OUTPUT, "equity_policies_summary.md")
    lines = [
        "# 组合资金曲线政策对照（含行业分散/成本）",
        "",
        f"sample={sample_n} seed={seed} exit={exit_mode} base_cost={cost_bps}bps",
        "",
        "| policy | 总收益% | 年化% | Sharpe | MDD% | 超额% | 笔数 | 胜率 | skip_ind | cost |",
        "|--------|---------|-------|--------|------|-------|------|------|----------|------|",
    ]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r.get('policy')} | ERR | | | | | | | | |")
            continue
        lines.append(
            f"| {r['policy']} | {r['total_ret_pct']} | {r['ann_ret_pct']} | {r['sharpe']} | "
            f"{r['mdd_pct']} | {r['excess_pct']} | {r['n_trades']} | {r['trade_wr']:.1f} | "
            f"{r.get('n_skip_ind', 0)} | {r.get('cost_bps', cost_bps)} |"
        )
    if best:
        lines += ["", f"**本样本最优(非常压):** {best['policy']} 总收益 {best.get('total_ret_pct')}% Sharpe {best.get('sharpe')}", ""]
    lines += [
        "",
        "行业缓存不全时「未知」不计入分散配额。成本 30/50bps 为压力组。",
        "不构成投资建议。",
        "",
    ]
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "ok": True,
        "rows": rows,
        "best": best,
        "path": path,
        "md_path": md,
        "disclaimer": "抽样净值对照；非实盘。",
    }


# ---------------------------------------------------------------------------
# 操作层 playbook（纪律边，不依赖新因子神话）
# ---------------------------------------------------------------------------

PLAYBOOK = {
    "version": "playbook_v3",
    "max_names": 6,
    "max_weight_pct": 15.0,
    "min_cash_pct": 25.0,
    "max_new_default": 3,
    "max_per_industry": 2,
    "exit": "tight",
    "tp_pct": 4.0,
    "sl_pct": 3.0,
    "sort": "qscore_desc",
    "rules": [
        "红灯/非 MA20+MA60：空仓，不选股",
        "指数20日≤0：空仓(环境)，不新开",
        "只做 action_today 候选（联合 > 完整通过），按质量分排序",
        "同行业最多 2 只（未知行业不互斥）",
        "单日新建仓 ≤3 只；总持仓 ≤6 只",
        "单只 ≤12.5%~15% 总权益；留 ≥25% 现金",
        "买入即设 tight：止盈+4% / 止损−3%（PATH 纪律）",
        "不补仓摊成本；破位走",
        "MENGα/缠论/形态/妖股：只观察，不单独开仓",
    ],
}


def enrich_action_playbook(
    action: Dict[str, Any],
    idx_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """给 action_today 结果挂上组合纪律 + 环境标签。"""
    from .factors import index_low_vol_today

    out = dict(action)
    light = out.get("light") or "?"
    market_ok = bool(out.get("market_ok"))
    buy = list(out.get("buy") or [])

    # 环境标签
    lv = index_low_vol_today()
    low_vol = bool(lv.get("low_vol")) if lv.get("ok") else None
    idx_mom = None
    green_days = None
    if idx_df is None:
        idx_df = load_kline_csv("sh000300", min_bars=80)
    if idx_df is not None and len(idx_df) >= 25:
        ic = idx_df["close"].astype(float)
        idx_mom = float((ic.iloc[-1] / ic.iloc[-21] - 1.0) * 100)
        ma20 = ic.rolling(20).mean()
        above = ic > ma20
        g = 0
        for v in above.iloc[::-1]:
            if bool(v):
                g += 1
            else:
                break
        green_days = g

    env = {
        "light": light,
        "market_ok": market_ok,
        "low_vol": low_vol,
        "idx_ret20": round(idx_mom, 2) if idx_mom is not None else None,
        "green_days": green_days,
    }
    # 仓位建议（环境闸优先于低波提示）
    if not market_ok:
        risk = "空仓"
        size_hint = "总仓位 0%"
        max_new = 0
    elif idx_mom is not None and idx_mom <= 0:
        # 硬闸：指数20日≤0 不新开（组合 PATH 两轮均略优于全信号）
        risk = "环境禁新开"
        size_hint = "总仓位只管理旧仓；指数20日≤0 → 不新开"
        max_new = 0
    elif low_vol is False:
        risk = "谨慎"
        size_hint = "总仓位 ≤40%（指数非低波，robust 信号本应偏少）"
        max_new = 1
    elif green_days is not None and green_days < 5:
        risk = "试探"
        size_hint = "总仓位 ≤50%（转绿未满5日）"
        max_new = 2
    elif idx_mom is not None and idx_mom > 0 and low_vol is True:
        risk = "标准+"
        size_hint = (
            f"总仓位 ≤{100 - PLAYBOOK['min_cash_pct']:.0f}%；"
            f"单只≤{PLAYBOOK['max_weight_pct']:.0f}%（低波+指数20日>0，与 boost 同向）"
        )
        max_new = 3
    else:
        risk = "标准"
        size_hint = f"总仓位 ≤{100 - PLAYBOOK['min_cash_pct']:.0f}%；单只≤{PLAYBOOK['max_weight_pct']:.0f}%"
        max_new = 2

    # 质量分排序（联合/完整通过名单统一）
    def _qkey(x: dict) -> float:
        try:
            v = x.get("qscore")
            return float(v) if v is not None and v == v else -1e9
        except Exception:
            return -1e9

    buy = sorted(buy, key=_qkey, reverse=True)

    # 行业分散（同行业最多 max_per_industry）
    observe = list(out.get("observe") or [])
    try:
        from .industry import diversify_by_industry, industry_of, load_industry_map

        imap = load_industry_map()
        for x in buy:
            if not x.get("industry"):
                x["industry"] = industry_of(str(x.get("sym") or ""), imap)
        buy, rej_ind = diversify_by_industry(
            buy,
            max_per_industry=int(PLAYBOOK.get("max_per_industry") or 2),
            cache=imap,
        )
        for y in rej_ind:
            y = dict(y)
            y["tier"] = (y.get("tier") or "") + "·行业满"
            y["reason"] = (y.get("reason") or "") + f" | {y.get('why_div') or '行业分散'}"
            observe.append(y)
    except Exception:
        pass

    # 截断候选到 max_names；再按 max_new 截断当日可新建
    buy_capped = buy[: PLAYBOOK["max_names"]]
    env_blocked = False

    # 硬环境闸门：max_new=0 时买入候选改观察（不新开）
    if max_new <= 0 and buy_capped and market_ok:
        env_blocked = True
        for x in buy_capped:
            y = dict(x)
            y["tier"] = (y.get("tier") or "") + "·仅观察"
            y["reason"] = (y.get("reason") or "") + " | 环境闸门：不新开"
            observe.append(y)
        buy_capped = []
        out["action"] = "空仓(环境)"
        out["can_buy"] = False
        out["why_empty"] = (
            out.get("why_empty")
            or f"环境闸门：idx_ret20={env.get('idx_ret20')} low_vol={env.get('low_vol')} → 不新开"
        )
    elif max_new > 0 and len(buy_capped) > max_new:
        # 超出今日新建上限的进观察
        for x in buy_capped[max_new:]:
            y = dict(x)
            y["tier"] = (y.get("tier") or "") + "·超额观察"
            observe.append(y)
        buy_capped = buy_capped[:max_new]

    for i, x in enumerate(buy_capped):
        x = dict(x)
        x["playbook_rank"] = i + 1
        x["suggest_weight_pct"] = PLAYBOOK["max_weight_pct"]
        x["exit_hint"] = (
            f"tight TP{PLAYBOOK['tp_pct']:.0f}/SL{PLAYBOOK['sl_pct']:.0f}；"
            f"建议权重≤{PLAYBOOK['max_weight_pct']:.0f}%；勿补仓。"
        )
        buy_capped[i] = x

    out["buy"] = buy_capped
    out["n"] = len(buy_capped)
    out["observe"] = observe[:40]
    out["can_buy"] = bool(buy_capped)
    if buy_capped:
        out["action"] = out.get("action") if out.get("action") not in (None, "空仓") else "可关注候选"
    elif market_ok and env_blocked:
        out["action"] = "空仓(环境)"
    out["playbook"] = {
        **PLAYBOOK,
        "risk_mode": risk,
        "size_hint": size_hint,
        "max_new_today": max_new,
        "env": env,
        "env_blocked": env_blocked,
    }
    # 强化 note
    base_note = out.get("note") or ""
    gate_txt = "【环境不新开】" if env_blocked else f"今日最多新建{max_new}只"
    out["note"] = (
        f"{base_note} | 组合纪律[{risk}]: {size_hint}；"
        f"{gate_txt}；持仓上限{PLAYBOOK['max_names']}只。"
    )
    return out
