# -*- coding: utf-8 -*-
"""网上复盘骨架 × 本仓纪律。不搜网页、不荐股、不编年化。"""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from .industry import diversify_by_industry, industry_of, load_industry_map
from .paths import OUTPUT

TP, SL = 4.0, 3.0
MAX_BUY_PER_IND = 2
MAX_OBS_PER_IND = 3
MAX_OBS_PX = 30.0
MAX_HOLD, MAX_WEIGHT, CASH_MIN, MAX_NEW = 6, 15.0, 25.0, 3


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _attach_industry(rows: List[dict]) -> List[dict]:
    m = load_industry_map()
    out = []
    for r in rows:
        row = dict(r)
        if not row.get("industry"):
            row["industry"] = industry_of(str(row.get("sym") or ""), m)
        out.append(row)
    return out


def _cap_unknown(rows: List[dict], max_per: int) -> List[dict]:
    """未知行业在 diversify 里不占配额；复盘仍截断，避免铺满。"""
    counts: Dict[str, int] = {}
    out: List[dict] = []
    for r in rows:
        ind = str(r.get("industry") or "未知")
        n = counts.get(ind, 0)
        if n >= max_per:
            continue
        counts[ind] = n + 1
        out.append(r)
    return out


def _group(rows: List[dict]) -> "OrderedDict[str, List[dict]]":
    g: "OrderedDict[str, List[dict]]" = OrderedDict()
    for r in rows:
        ind = str(r.get("industry") or "未知")
        g.setdefault(ind, []).append(r)
    return g


def _num_px(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def _row_px(r: dict) -> Optional[float]:
    """观察层计价：本次 live 成功用 live_px；否则日K 走 px_bar，再才是 px。"""
    if r.get("live"):
        lp = _num_px(r.get("live_px"))
        if lp is not None:
            return lp
    bar = _num_px(r.get("px_bar"))
    if bar is not None:
        return bar
    return _num_px(r.get("px"))


def _px_under(r: dict, max_px: float = MAX_OBS_PX) -> bool:
    """观察层：现价须 >0 且 < max_px。缺价/非数字不算。"""
    v = _row_px(r)
    return v is not None and v < max_px


def _drop_stale_live(rows: List[dict]) -> List[dict]:
    """无本次行情时不要把 CSV 里旧 live 当现价；日K 用 px_bar。"""
    out = []
    for r in rows:
        rr = dict(r)
        bar = _num_px(rr.get("px_bar"))
        rr["live"] = False
        rr["live_px"] = None
        if bar is not None:
            rr["px"] = bar
        out.append(rr)
    return out


def _overlay_live_prices(rows: List[dict]) -> List[dict]:
    if not rows:
        return rows
    try:
        from .data import attach_live_prices

        return attach_live_prices(rows)
    except Exception:
        return _drop_stale_live(rows)


def _fill_unknown_industry(
    rows: List[dict],
    get_ind=None,
    max_fetch: int = 20,
) -> List[dict]:
    """只补「未知」；已有行业不覆盖。get_ind 可注入，默认东财 F10。"""
    fn = get_ind
    if fn is None:
        from .industry import get_industry, load_industry_map

        cache = dict(load_industry_map())
        fn = lambda s, _c=cache: get_industry(s, cache=_c)
    n = 0
    out: List[dict] = []
    for r in rows:
        rr = dict(r)
        ind = str(rr.get("industry") or "").strip() or "未知"
        if ind == "未知" and n < max_fetch:
            n += 1
            try:
                got = str(fn(str(rr.get("sym") or "")) or "").strip()
            except Exception:
                got = ""
            rr["industry"] = got if got and got != "未知" else "未知"
        else:
            rr["industry"] = ind
        out.append(rr)
    return out


def _prepare_observe(rows: List[dict]) -> List[dict]:
    """实时价叠加 + 仅对过 <30 的票补行业。"""
    obs = _overlay_live_prices(_attach_industry(rows))
    filled = _fill_unknown_industry([r for r in obs if _px_under(r)])
    imap = {str(x.get("sym") or ""): x.get("industry") for x in filled}
    out = []
    for r in obs:
        rr = dict(r)
        s = str(rr.get("sym") or "")
        if s in imap and imap[s]:
            rr["industry"] = imap[s]
        out.append(rr)
    return out


def _select_lists(
    payload: Dict[str, Any],
) -> Tuple[List[dict], List[dict], List[dict], "OrderedDict[str, List[dict]]", "OrderedDict[str, List[dict]]"]:
    buy_raw = list(payload.get("buy") or [])
    if not payload.get("market_ok"):
        buy_raw = []
    obs_raw = [r for r in (payload.get("observe") or []) if _px_under(r)]
    buy_raw.sort(key=lambda x: _f(x.get("qscore"), -1e9), reverse=True)
    obs_raw.sort(key=lambda x: _f(x.get("menga"), -1e9), reverse=True)
    buy, buy_rej = diversify_by_industry(buy_raw, max_per_industry=MAX_BUY_PER_IND)
    obs, _obs_rej = diversify_by_industry(obs_raw, max_per_industry=MAX_OBS_PER_IND)
    buy = _cap_unknown(buy, MAX_BUY_PER_IND)
    obs = _cap_unknown(obs, MAX_OBS_PER_IND)
    return buy, obs, buy_rej, _group(buy), _group(obs)


def _public_row(r: dict, *, buy: bool = False) -> dict:
    def num(v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    out = {
        "sym": r.get("sym") or "",
        "name": r.get("name") or "",
        "industry": r.get("industry") or "未知",
        "px": _row_px(r),
        "menga": num(r.get("menga")),
        "live": bool(r.get("live")),
    }
    if buy:
        out["qscore"] = num(r.get("qscore"))
        out["tier"] = r.get("tier") or ""
        out["reason"] = r.get("reason") or ""
        out["rsi"] = num(r.get("rsi"))
        out["zf60"] = num(r.get("zf60"))
    return out


def build_recap(payload: Dict[str, Any]) -> str:
    light = str(payload.get("light") or "未知")
    action = str(payload.get("action") or "未知")
    date = str(payload.get("date") or "")
    market_ok = bool(payload.get("market_ok"))
    buy, obs, buy_rej, buy_g, obs_g = _select_lists(payload)

    ret20 = payload.get("ret20")
    close = payload.get("close")
    tier = payload.get("tier") or "—"
    ma20 = payload.get("above_ma20")
    ma60 = payload.get("above_ma60")

    lines: List[str] = []
    lines.append("# 本仓复盘 %s" % (date or ""))
    lines.append("")
    lines.append("> 骨架借自公开 Grok 复盘模板 / 少妇仓位优先 / 游资槽位；**票和纪律只来自本仓 CLI**。")
    lines.append("> 研究工具，不构成投资建议。")
    lines.append("")
    lines.append("## 结构映射")
    lines.append("")
    lines.append("| 外来槽位 | 本仓落点 |")
    lines.append("|----------|----------|")
    lines.append("| 市场情绪（游资） | 红绿灯 **%s** | " % light)
    lines.append("| 阶段（游资） | 操作口径 **%s** | " % action)
    lines.append("| 量价/结构（游资） | 档位 %s | " % tier)
    lines.append("| 买卖点 | 不推理价位；出场 **PATH tight TP%.0f/SL%.0f** |" % (TP, SL))
    lines.append("| 每板块凑满 3 只 | 买入向同行业≤**%d**；观察向每行业≤**%d**，不足不补 |" % (MAX_BUY_PER_IND, MAX_OBS_PER_IND))
    lines.append("| 网上收益目标 | **不写**。固定持有上涨概率走 `up-prob`，与 PATH 不是同一命题 |")
    lines.append("| 仓位优先（少妇） | 现金≥%.0f%% 单只≤%.0f%% 持仓≤%d 今日新建≤%d |" % (CASH_MIN, MAX_WEIGHT, MAX_HOLD, MAX_NEW))
    lines.append("")
    lines.append("## 整体市场")
    lines.append("")
    lines.append(
        "沪深300 **%s**，近20日 %s，收盘 %s。站上MA20=%s MA60=%s。"
        % (
            light,
            ("%+.2f%%" % float(ret20)) if ret20 is not None else "—",
            ("%.2f" % float(close)) if close is not None else "—",
            ma20,
            ma60,
        )
    )
    if not market_ok:
        lines.append("未同时站上 MA20+MA60 → 纪律 **空仓**。不做未来 3–10 天板块切换预测（那是网上模板让模型编的）。")
    else:
        lines.append("绿灯只看 `action` 候选。MENGα / 冷静池仍是观察层。")
    lines.append("")
    lines.append("## 买入向候选（唯一出口）")
    lines.append("")
    if not buy:
        lines.append("**今日无买入向候选。** 禁止为了凑满板块×3 只从观察层抽票。")
        if buy_rej:
            lines.append("行业截断 %d 只（同行业已满）。" % len(buy_rej))
        lines.append("")
    else:
        lines.append("共 %d 只（已按质量分排序，同行业≤%d）。档位=%s。" % (len(buy), MAX_BUY_PER_IND, tier))
        lines.append("")
        for ind, rows in buy_g.items():
            lines.append("### %s（%d 只）" % (ind, len(rows)))
            lines.append("")
            lines.append("| 代码 | 名称 | 现价 | 质量分 | 档位 | 出场 |")
            lines.append("|------|------|------|--------|------|------|")
            for r in rows:
                lines.append(
                    "| %s | %s | %s | %s | %s | PATH tight TP%.0f/SL%.0f |"
                    % (
                        r.get("sym") or "",
                        r.get("name") or "",
                        r.get("px") if r.get("px") is not None else "—",
                        r.get("qscore") if r.get("qscore") is not None else "—",
                        r.get("tier") or tier,
                        TP,
                        SL,
                    )
                )
            lines.append("")
            lines.append("失效条件：大盘转红、跌破结构、触及 SL%.0f。" % SL)
            lines.append("")
        lines.append("## 反方")
        lines.append("")
        lines.append("- 红灯切换后本名单应作废（买入向出口被 regime 切断）。")
        lines.append("- PATH 同日双触先止损；OPT 七成胜率禁止当实盘。")
        lines.append("- 成本抬到 50bps 或熊市窗，压力测试里期望易转负。")
        lines.append("- 同行业拥挤：本报告已截到每行业 %d 只，仍可能相关齐跌。" % MAX_BUY_PER_IND)
        lines.append("")

    lines.append("## 观察层（观察，非买入）")
    lines.append("")
    if not obs:
        lines.append("无观察名单（未提供冷静池/α，过滤现价<%.0f元后为空，或为空）。" % MAX_OBS_PX)
        lines.append("")
    else:
        lines.append(
            "来自冷静池/α 截面，**不是** action 候选。只列现价<%.0f元（优先实时，失败回退日K）。红灯时尤其不能当买点。"
            % MAX_OBS_PX
        )
        lines.append("")
        for ind, rows in obs_g.items():
            lines.append("### %s · 观察（%d 只，上限 %d）" % (ind, len(rows), MAX_OBS_PER_IND))
            lines.append("")
            lines.append("| 代码 | 名称 | α | 现价 |")
            lines.append("|------|------|---|------|")
            for r in rows:
                pxv = _row_px(r)
                lines.append(
                    "| %s | %s | %s | %s |"
                    % (
                        r.get("sym") or "",
                        r.get("name") or "",
                        r.get("menga") if r.get("menga") is not None else "—",
                        pxv if pxv is not None else "—",
                    )
                )
            lines.append("")
    lines.append("研究工具，不构成投资建议。")
    return "\n".join(lines) + "\n"


def _rows_from_csv(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    import pandas as pd

    from .data import fill_row_names

    df = pd.read_csv(path)
    if df is None or df.empty:
        return []
    rows = fill_row_names(df.to_dict(orient="records"))
    for x in rows:
        if x.get("menga") in (None, "") and x.get("menga_pct") not in (None, ""):
            x["menga"] = x.get("menga_pct")
    return rows


def _light_from_txt() -> Tuple[str, str, int]:
    p = os.path.join(OUTPUT, "action_today.txt")
    action, light, n = "未知", "未知", 0
    if not os.path.exists(p):
        return action, light, n
    for line in open(p, encoding="utf-8"):
        if line.startswith("action="):
            action = line.split("=", 1)[1].strip()
        elif line.startswith("light="):
            light = line.split("=", 1)[1].strip()
        elif line.startswith("n="):
            try:
                n = int(line.split("=", 1)[1].strip())
            except ValueError:
                n = 0
    return action, light, n


def write_recap(*, cached: bool = False, with_observe: bool = True) -> str:
    return str(run_recap(cached=cached, with_observe=with_observe).get("file") or "")


def run_recap(*, cached: bool = False, with_observe: bool = True) -> Dict[str, Any]:
    os.makedirs(OUTPUT, exist_ok=True)
    if cached:
        action, light, n = _light_from_txt()
        buy = _rows_from_csv(os.path.join(OUTPUT, "action_today.csv")) if n else []
        market_ok = light == "绿灯"
        payload: Dict[str, Any] = {
            "date": "",
            "light": light,
            "action": action,
            "market_ok": market_ok,
            "buy": _attach_industry(buy),
            "observe": [],
            "tier": None,
        }
        try:
            from .data import market_regime

            rg = market_regime(live=True)
            payload["date"] = str(rg.get("last_date") or rg.get("bar_date") or "")
            payload["ret20"] = rg.get("ret20")
            payload["close"] = rg.get("close")
            payload["above_ma20"] = rg.get("above_ma20")
            payload["above_ma60"] = rg.get("above_ma60")
            payload["market_ok"] = bool(rg.get("above_ma20") and rg.get("above_ma60"))
            if rg.get("light"):
                payload["light"] = rg.get("light")
        except Exception:
            pass
        if not payload.get("market_ok"):
            payload["buy"] = []
            payload["action"] = "空仓"
            payload["light"] = "红灯"
    else:
        from .action_today import get_action_today

        r = get_action_today(use_joint=True)
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "action 失败")
        rg = r.get("regime") or {}
        payload = {
            "date": str(rg.get("last_date") or rg.get("bar_date") or ""),
            "light": r.get("light"),
            "action": r.get("action"),
            "market_ok": bool(r.get("market_ok")),
            "ret20": rg.get("ret20"),
            "close": rg.get("close"),
            "above_ma20": rg.get("above_ma20"),
            "above_ma60": rg.get("above_ma60"),
            "tier": r.get("tier"),
            "buy": _attach_industry(list(r.get("buy") or [])),
            "observe": [],
        }
    if with_observe:
        cool = _rows_from_csv(os.path.join(OUTPUT, "cool_pool.csv"))
        if not cool:
            cool = _rows_from_csv(os.path.join(OUTPUT, "menga_today.csv"))
            for x in cool:
                if "menga" not in x and "menga_pct" in x:
                    x["menga"] = x.get("menga_pct")
        payload["observe"] = _prepare_observe(cool[:80])
    md = build_recap(payload)
    path = os.path.join(OUTPUT, "recap_latest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    buy, obs, buy_rej, buy_g, obs_g = _select_lists(payload)
    light = str(payload.get("light") or "未知")
    action = str(payload.get("action") or "未知")
    return {
        "ok": True,
        "cached": bool(cached),
        "file": path,
        "date": payload.get("date") or "",
        "light": light,
        "action": action,
        "market_ok": bool(payload.get("market_ok")),
        "ret20": payload.get("ret20"),
        "close": payload.get("close"),
        "above_ma20": payload.get("above_ma20"),
        "above_ma60": payload.get("above_ma60"),
        "tier": payload.get("tier") or "—",
        "buy": [_public_row(r, buy=True) for r in buy],
        "observe": [_public_row(r) for r in obs],
        "buy_groups": [
            {"industry": k, "rows": [_public_row(r, buy=True) for r in v]}
            for k, v in buy_g.items()
        ],
        "observe_groups": [
            {"industry": k, "rows": [_public_row(r) for r in v]}
            for k, v in obs_g.items()
        ],
        "buy_rejected": len(buy_rej),
        "markdown": md,
        "disclaimer": "研究工具，不构成投资建议。",
        "note": _recap_note(cached=bool(cached)),
    }


def _recap_note(*, cached: bool) -> str:
    parts = [
        "网上骨架 × 本仓纪律。买入向只来自 action；观察层不是买点，现价<30元（优先实时，失败回退日K）。红灯空仓。",
    ]
    cool = os.path.join(OUTPUT, "cool_pool.csv")
    menga = os.path.join(OUTPUT, "menga_today.csv")
    try:
        if cached and os.path.exists(cool) and os.path.exists(menga):
            if os.path.getmtime(cool) + 86400 < os.path.getmtime(menga):
                parts.append("成员截面过期（冷静池早于 menga，名单不是今日 α 截面）。")
    except OSError:
        pass
    return " ".join(p.strip() for p in parts)
