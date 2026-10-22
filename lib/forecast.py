# -*- coding: utf-8 -*-
"""历史相似情境 → 次日/短线涨跌条件统计 + 可选大模型解读。

算法 v3（研究降级版，不再作为买入闸门）：
  - 特征加权：超额收益、大盘环境权重大
  - 相似日距离加权统计（近邻权重大）
  - 非对称喊单：做多门槛更高；v3 进一步抬高多头门槛，减少假「偏多」
  - 大盘红灯抑制「偏多」标签
  - 置信度：样本数 + |上涨占比-50| + 均值幅度；默认多数为「低」

Walk-forward 参考：综合命中约 52–54%（接近随机）。
**联合信号 v2 已不再依赖本模块**；本页仅作历史相似描述统计 / 弱刹车。
输出必须带 disclaimer。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .data import fetch_kline, load_kline_csv, market_regime, to_sym
from .factors import rsi_series

ALGO_VERSION = "analog_v3_weak_brake"

# 特征权重（RSI, bias, excess, zf60, vr, idx_up, >ma20, >ma60, >ma250）
FEATURE_WEIGHTS = np.array([1.2, 1.0, 2.2, 0.8, 0.6, 1.8, 1.3, 1.1, 0.9], dtype=float)

# 喊单门槛 v3：多头更严、空头略严 → 更多「中性」，少喊方向
LONG_UP_TH = 66.0
LONG_MEAN_TH = 0.45
SHORT_UP_TH = 36.0
SHORT_MEAN_TH = -0.35
DEFAULT_TOP_K = 50
MIN_ANALOGS = 25


def _feat_row(
    c: pd.Series, v: pd.Series, i: int, idx_ret20: float, idx_up20: bool
) -> Optional[np.ndarray]:
    if i < 260 or i >= len(c):
        return None
    px = float(c.iloc[i])
    ma20 = float(c.iloc[i - 19 : i + 1].mean())
    ma60 = float(c.iloc[i - 59 : i + 1].mean())
    ma250 = float(c.iloc[i - 249 : i + 1].mean())
    if ma20 <= 0:
        return None
    ret20 = (px / float(c.iloc[i - 20]) - 1) * 100 if i >= 20 else 0.0
    zf60 = (px / float(c.iloc[i - 60]) - 1) * 100 if i >= 60 else 0.0
    vol5 = float(v.iloc[i - 4 : i + 1].mean())
    vol60 = float(v.iloc[i - 59 : i + 1].mean()) if i >= 59 else vol5
    vr = vol5 / vol60 if vol60 > 0 else 1.0
    window = c.iloc[max(0, i - 30) : i + 1]
    r = rsi_series(window)
    rsi_v = float(r.iloc[-1]) if len(r) and r.iloc[-1] == r.iloc[-1] else 50.0
    bias = (px / ma20 - 1) * 100
    excess = ret20 - idx_ret20
    return np.array(
        [
            rsi_v / 100.0,
            bias / 20.0,
            excess / 20.0,
            zf60 / 50.0,
            min(vr, 5.0) / 3.0,
            1.0 if idx_up20 else 0.0,
            1.0 if px > ma20 else 0.0,
            1.0 if px > ma60 else 0.0,
            1.0 if px > ma250 else 0.0,
        ],
        dtype=float,
    )


def _index_context_at(idx_df: pd.DataFrame, date: str) -> Tuple[float, bool]:
    d = idx_df.copy()
    d["date"] = d["date"].astype(str)
    sub = d[d["date"] <= date]
    if len(sub) < 25:
        return 0.0, False
    c = sub["close"]
    ret20 = float((c.iloc[-1] / c.iloc[-21] - 1) * 100)
    up = bool(c.iloc[-1] > c.rolling(20).mean().iloc[-1])
    return ret20, up


def _weighted_stats(rets: np.ndarray, weights: np.ndarray) -> Dict[str, float]:
    w = weights / (weights.sum() + 1e-12)
    mean = float(np.sum(rets * w))
    # weighted up rate
    up_rate = float(np.sum(w[rets > 0]) * 100)
    # approximate quantiles via sorted
    order = np.argsort(rets)
    cw = np.cumsum(w[order])
    def wq(q):
        j = int(np.searchsorted(cw, q))
        j = min(max(j, 0), len(rets) - 1)
        return float(rets[order[j]])
    return {
        "n": int(len(rets)),
        "up_rate": round(up_rate, 1),
        "mean": round(mean, 2),
        "median": round(wq(0.5), 2),
        "p25": round(wq(0.25), 2),
        "p75": round(wq(0.75), 2),
    }


def _confidence(up_rate: float, mean: float, n: int) -> Tuple[str, float]:
    """返回 (高/中/低, 0~100 分)。v3：门槛抬高，默认多数「低」。"""
    edge = abs(up_rate - 50.0)
    score = edge * 1.0 + min(abs(mean) * 6, 15) + min(n / 12.0, 8)
    # 真正「高」极少：需要明显偏离 + 足够样本
    if n >= 50 and edge >= 14 and abs(mean) >= 0.50:
        return "高", round(min(score, 88), 1)
    if n >= 35 and edge >= 10 and abs(mean) >= 0.35:
        return "中", round(min(score, 72), 1)
    return "低", round(min(score, 55), 1)


def _lean_from_stats(
    d1: Dict[str, float], market_light: str
) -> Tuple[str, str, Optional[int]]:
    """
    返回 (lean中文, reason, pred方向 1/-1/None)
    多头更严；红灯禁止偏多。
    """
    up, mu, n = d1["up_rate"], d1["mean"], d1["n"]
    if n < MIN_ANALOGS:
        return "样本不足", "相似日太少", None

    # 红灯：不允许偏多（与策略纪律一致）
    if market_light == "红灯" and up >= LONG_UP_TH and mu > LONG_MEAN_TH:
        return "中性(红灯抑制多头)", "历史相似偏多但大盘红灯，不喊多", None

    if up >= LONG_UP_TH and mu > LONG_MEAN_TH:
        # 黄灯降级
        if market_light == "黄灯":
            return "弱偏多", "相似偏多但大盘仅黄灯", 1
        return "历史相似偏多", f"次日上涨占比{up}% 均值{mu:+.2f}%", 1

    if up <= SHORT_UP_TH and mu < SHORT_MEAN_TH:
        return "历史相似偏空", f"次日上涨占比{up}% 均值{mu:+.2f}%", -1

    return "历史相似中性/纠缠", f"上涨占比{up}% 接近随机", None


def _collect_series_analogs(
    df: pd.DataFrame,
    idx: pd.DataFrame,
    cur: np.ndarray,
    horizons: Tuple[int, ...],
    source_weight: float,
    source_tag: str,
) -> Tuple[List[np.ndarray], List[dict]]:
    """收集 (feat, meta) meta 含后续收益所需 close 序列索引信息。"""
    c = df["close"]
    v = df["vol"] if "vol" in df.columns else pd.Series(np.ones(len(df)))
    dates = df["date"].astype(str).values
    n = len(df)
    feats, metas = [], []
    max_h = max(horizons)
    for i in range(260, n - max_h - 1):
        ir, iu = _index_context_at(idx, dates[i])
        f = _feat_row(c, v, i, ir, iu)
        if f is None or np.any(~np.isfinite(f)):
            continue
        feats.append(f)
        # 预存各 horizon 收益
        rets_h = {}
        for h in horizons:
            entry = float(c.iloc[i])
            rets_h[h] = (float(c.iloc[i + h]) / entry - 1) * 100
        metas.append(
            {
                "i": i,
                "date": dates[i],
                "rets": rets_h,
                "w_src": source_weight,
                "src": source_tag,
                "next1d": rets_h.get(1),
            }
        )
    return feats, metas


def historical_analog_forecast(
    code: str,
    top_k: int = DEFAULT_TOP_K,
    horizons: Tuple[int, ...] = (1, 5, 20),
    use_industry: bool = True,
    max_peers: int = 12,
) -> Dict[str, Any]:
    """A: 本股 + 同行业同伴历史相似日；B 的联合信号在 joint 模块。"""
    sym = to_sym(code)
    df = load_kline_csv(sym, min_bars=300)
    if df is None:
        df = fetch_kline(sym, force=False, min_bars=300)
    idx = load_kline_csv("sh000300", min_bars=100)
    if df is None or idx is None or len(df) < 320:
        return {"ok": False, "error": f"{sym} 数据不足", "sym": sym}

    regime = market_regime()
    light = regime.get("light") or "未知"
    c = df["close"]
    v = df["vol"] if "vol" in df.columns else pd.Series(np.ones(len(df)))
    dates = df["date"].astype(str).values
    n = len(df)
    last = n - 1

    idx_ret20, idx_up = _index_context_at(idx, dates[last])
    cur = _feat_row(c, v, last, idx_ret20, idx_up)
    if cur is None or np.any(~np.isfinite(cur)):
        return {"ok": False, "error": "特征提取失败", "sym": sym}

    # —— A: 本股 + 行业同伴 相似池 ——
    all_feats: List[np.ndarray] = []
    all_meta: List[dict] = []
    own_f, own_m = _collect_series_analogs(df, idx, cur, horizons, 1.5, "self")
    all_feats.extend(own_f)
    all_meta.extend(own_m)

    industry = "未知"
    peers_used: List[str] = []
    if use_industry:
        try:
            from .industry import get_industry, industry_peers

            industry = get_industry(sym)
            peers = industry_peers(sym, max_peers=max_peers, sleep=0.03)
            for p in peers:
                pdf = load_kline_csv(p, min_bars=300)
                if pdf is None or len(pdf) < 320:
                    continue
                pf, pm = _collect_series_analogs(pdf, idx, cur, horizons, 1.0, p)
                if len(pf) < 30:
                    continue
                all_feats.extend(pf)
                all_meta.extend(pm)
                peers_used.append(p)
        except Exception:
            pass

    if len(all_feats) < 40:
        return {"ok": False, "error": "历史相似样本过少", "sym": sym}

    F = np.vstack(all_feats)
    std = F.std(axis=0)
    std[std < 1e-6] = 1.0
    std_w = std / np.sqrt(FEATURE_WEIGHTS)
    Fn = F / std_w
    cn = cur / std_w
    dist = np.sqrt(((Fn - cn) ** 2).sum(axis=1))
    # 本股权重：距离等效缩小（更易入选）
    src_w = np.array([m["w_src"] for m in all_meta], dtype=float)
    dist_adj = dist / np.sqrt(src_w)

    order = np.argsort(dist_adj)
    k = min(top_k, len(order))
    pick = order[:k]
    d_pick = dist_adj[pick]
    d0 = float(d_pick.min()) + 1e-6
    w_pick = (1.0 / (d_pick + d0)) * src_w[pick]

    stats: List[Dict[str, Any]] = []
    analog_rows = []
    for h in horizons:
        rets, ws = [], []
        for j, wi in zip(pick, w_pick):
            meta = all_meta[j]
            r = meta["rets"].get(h)
            if r is None or not np.isfinite(r):
                continue
            rets.append(float(r))
            ws.append(float(wi))
            if h == 1 and len(analog_rows) < 12:
                analog_rows.append(
                    {
                        "date": meta["date"],
                        "src": meta["src"],
                        "dist": round(float(dist_adj[j]), 4),
                        "next1d": round(float(meta.get("next1d") or r), 2),
                    }
                )
        if len(rets) < MIN_ANALOGS:
            continue
        st = _weighted_stats(np.array(rets), np.array(ws))
        st["horizon"] = h
        stats.append(st)

    px_bar = float(c.iloc[-1])
    ma20 = float(c.rolling(20).mean().iloc[-1])
    bar_date = str(dates[-1])
    # 实时价（相似研究仍用日K截面；展示用现价）
    live_px = None
    live_chg = None
    live_time = None
    try:
        from .data import fetch_realtime_quotes
        q = fetch_realtime_quotes([sym]).get(sym) or {}
        if q.get("price") and q["price"] == q["price"] and q["price"] > 0:
            live_px = round(float(q["price"]), 2)
            live_chg = round(float(q["chg_pct"]), 2) if q.get("chg_pct") == q.get("chg_pct") else None
            live_time = f"{q.get('date') or ''} {q.get('time') or ''}".strip()
    except Exception:
        pass
    px = live_px if live_px is not None else px_bar
    snapshot = {
        "sym": sym,
        "date": live_time or bar_date,
        "bar_date": bar_date,
        "px": round(px, 2),
        "px_bar": round(px_bar, 2),
        "live_px": live_px,
        "live_chg_pct": live_chg,
        "live_time": live_time,
        "live": live_px is not None,
        "rsi": round(float(rsi_series(c).iloc[-1]), 1),
        "bias20": round((px_bar / ma20 - 1) * 100, 1),
        "rs20": round((px_bar / float(c.iloc[-21]) - 1) * 100, 1),
        "idx20": round(idx_ret20, 1),
        "excess20": round((px_bar / float(c.iloc[-21]) - 1) * 100 - idx_ret20, 1),
        "market_light": light,
        "analogs": k,
        "industry": industry,
        "peers": peers_used,
        "algo": ALGO_VERSION,
    }

    d1 = next((s for s in stats if s["horizon"] == 1), None)
    if d1:
        lean, lean_reason, pred = _lean_from_stats(d1, light)
        conf_level, conf_score = _confidence(d1["up_rate"], d1["mean"], d1["n"])
    else:
        lean, lean_reason, pred = "样本不足", "无次日统计", None
        conf_level, conf_score = "低", 0.0

    # 校准后的「可行动置信」：低置信一律当中性展示
    actionable = pred is not None and conf_level in ("中", "高")
    display_lean = lean if actionable or pred is None else f"{lean}（置信低，视为中性）"

    result = {
        "ok": True,
        "sym": sym,
        "snapshot": snapshot,
        "horizons": stats,
        "analog_samples": analog_rows,
        "lean": display_lean,
        "lean_raw": lean,
        "lean_reason": lean_reason,
        "pred": pred,
        "actionable": actionable,
        "confidence": conf_level,
        "confidence_score": conf_score,
        "method": (
            f"本股+同行业同伴历史特征加权距离最近 {k} 条"
            f"（行业={industry}，同伴{len(peers_used)}只）；距离加权统计；"
            f"多头门槛上涨≥{LONG_UP_TH}%且均值>{LONG_MEAN_TH}；"
            f"空头门槛上涨≤{SHORT_UP_TH}%且均值<{SHORT_MEAN_TH}；红灯抑制偏多"
        ),
        "backtest_note": (
            "Walk-forward 约52–54%，接近随机。"
            "本模块 v3 仅作弱刹车/描述统计；买入闸门已改用联合v2（结构∩MENGα）。"
        ),
        "algo": ALGO_VERSION,
        "research_value": "low",
        "industry": industry,
        "peers_used": peers_used,
        "disclaimer": (
            "涨跌研究边极弱（~52–54%），对「猜涨跌」基本无决策价值；"
            "仅作历史相似描述，不构成投资建议，不可作买入依据。"
        ),
    }
    return result


def llm_interpret(stats_payload: Dict[str, Any], model: str = "grok-4-1-fast-non-reasoning") -> Dict[str, Any]:
    """大模型只解读给定统计，禁止编造数字。"""
    api_key = os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")

    sys_prompt = (
        "你是A股量化研究助手。用户给你「历史相似日」统计与置信度。"
        "规则：1) 只能用给定数字 2) 禁止承诺必涨必跌 3) 置信低时明确说没有交易优势"
        "4) 红灯环境提醒空仓纪律 5) 命中率历史校准仅约五成多，要写进风险"
        "输出JSON：summary(2-4句中文), lean(偏多|偏空|中性), "
        "confidence(高|中|低), risks(2-4条), action(一句纪律建议)。"
    )

    # 模板兜底
    def template():
        d1 = next((h for h in stats_payload.get("horizons", []) if h["horizon"] == 1), None)
        lean = stats_payload.get("lean") or stats_payload.get("lean_raw") or "中性"
        conf = stats_payload.get("confidence", "低")
        if d1:
            summary = (
                f"在本股历史上最相似的 {d1['n']} 个交易日（距离加权）中，"
                f"次日上涨占比约 {d1['up_rate']}%，平均收益约 {d1['mean']:+.2f}% "
                f"（中位 {d1['median']:+.2f}%）。标签：{lean}；置信度：{conf}。"
                f"Walk-forward 校准综合命中仅约五成多，优势很薄。"
            )
        else:
            summary = "样本不足，无法形成次日统计。"
        return {
            "ok": True,
            "source": "template",
            "summary": summary,
            "lean": lean,
            "confidence": conf,
            "risks": [
                "历史相似≠未来重复",
                "次日涨跌噪声极大，校准命中约52–54%",
                "红灯环境下完整策略应空仓",
                "低置信信号应视为无观点",
            ],
            "action": (
                "仅研究参考；完整通过仍看大盘红绿灯；"
                "置信低或中性时不据此开仓。"
            ),
            "hint": "export XAI_API_KEY=... 后可启用大模型解读（api.x.ai / SpaceXAI）",
        }

    if not api_key:
        return template()

    try:
        from openai import OpenAI

        # 短超时：无 key 或 API 卡死时别拖垮整页「处理中」
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=8.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": json.dumps(stats_payload, ensure_ascii=False, indent=2),
                },
            ],
            temperature=0.15,
            timeout=8.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            data = json.loads(text)
        except Exception:
            data = {
                "summary": text[:500],
                "lean": stats_payload.get("lean"),
                "confidence": stats_payload.get("confidence"),
                "risks": ["模型未返回标准JSON，以统计表为准"],
                "action": "以统计表与置信度为准",
            }
        data["ok"] = True
        data["source"] = "llm"
        data["model"] = model
        return data
    except Exception as e:
        out = template()
        out["source"] = "llm_error_fallback"
        out["error"] = str(e)
        return out


def forecast_stock(
    code: str,
    use_llm: bool = True,
    top_k: int = DEFAULT_TOP_K,
    use_industry: bool = True,
) -> Dict[str, Any]:
    base = historical_analog_forecast(code, top_k=top_k, use_industry=use_industry)
    if not base.get("ok"):
        return base
    payload = {
        "snapshot": base["snapshot"],
        "horizons": base["horizons"],
        "lean": base["lean"],
        "lean_raw": base.get("lean_raw"),
        "confidence": base.get("confidence"),
        "confidence_score": base.get("confidence_score"),
        "actionable": base.get("actionable"),
        "method": base["method"],
        "backtest_note": base.get("backtest_note"),
        "disclaimer": base["disclaimer"],
    }
    if use_llm:
        base["llm"] = llm_interpret(payload)
    else:
        key = os.environ.pop("XAI_API_KEY", None)
        key2 = os.environ.pop("OPENAI_API_KEY", None)
        try:
            base["llm"] = llm_interpret(payload)
        finally:
            if key is not None:
                os.environ["XAI_API_KEY"] = key
            if key2 is not None:
                os.environ["OPENAI_API_KEY"] = key2
    return base
