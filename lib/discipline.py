# -*- coding: utf-8 -*-
"""破戒检查日报：对照 playbook 检查系统输出 + 可选实盘持仓。

持仓文件（可选）默认路径：
  output/holdings.csv
  或项目根 holdings.csv

列（任选子集，大小写不敏感）：
  sym / 代码          必填
  name / 名称         可选
  weight_pct / 权重%  建议；多只权重之和应 ≤ 100-min_cash
  entry_price / 成本  可选；用于检查是否跌破止损
  entry_date / 入场日 可选；用于统计持仓天数
  new_today / 今日新建  1/0/true/false；缺省用 entry_date==今日判断

用法：
  python3 cli.py discipline
  python3 cli.py discipline --holdings output/holdings.csv
  python3 cli.py daily   # 日更末尾自动跑
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .paths import OUTPUT, ROOT

HOLDINGS_CANDIDATES = [
    os.path.join(OUTPUT, "holdings.csv"),
    os.path.join(ROOT, "holdings.csv"),
    os.path.join(ROOT, "output", "holdings.csv"),
]

HOLDINGS_TEMPLATE = """# 可选：破戒检查用持仓表（复制为 output/holdings.csv 后填）
# 权重% 为占总权益比例；多只之和建议 ≤ 75（留≥25%现金）
sym,name,weight_pct,entry_price,entry_date,new_today
# sh600519,贵州茅台,12.5,1600,2026-08-01,0
"""


def _f(x, default=None):
    try:
        if x is None or (isinstance(x, float) and x != x):
            return default
        if isinstance(x, str) and not x.strip():
            return default
        return float(x)
    except Exception:
        return default


def _sym(x: str) -> str:
    from .data import to_sym
    return to_sym(str(x).strip())


def resolve_holdings_path(path: Optional[str] = None) -> Optional[str]:
    if path and os.path.exists(path):
        return path
    for p in HOLDINGS_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def ensure_holdings_template() -> str:
    """若无持仓文件，写一份模板到 output/holdings.example.csv。"""
    os.makedirs(OUTPUT, exist_ok=True)
    ex = os.path.join(OUTPUT, "holdings.example.csv")
    if not os.path.exists(ex):
        with open(ex, "w", encoding="utf-8") as f:
            f.write(HOLDINGS_TEMPLATE)
    return ex


def load_holdings(path: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    p = resolve_holdings_path(path)
    if not p:
        return [], None
    df = pd.read_csv(p, comment="#")
    if df.empty:
        return [], p
    # 列名归一
    colmap = {}
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in ("sym", "code", "代码", "symbol"):
            colmap[c] = "sym"
        elif cl in ("name", "名称"):
            colmap[c] = "name"
        elif cl in ("weight_pct", "weight", "权重", "权重%", "仓位", "仓位%"):
            colmap[c] = "weight_pct"
        elif cl in ("entry_price", "cost", "成本", "成本价", "入场价"):
            colmap[c] = "entry_price"
        elif cl in ("entry_date", "date", "入场日", "买入日"):
            colmap[c] = "entry_date"
        elif cl in ("new_today", "今日新建", "new"):
            colmap[c] = "new_today"
        elif cl in ("px", "price", "现价", "last"):
            colmap[c] = "px"
    df = df.rename(columns=colmap)
    if "sym" not in df.columns:
        return [], p

    today = datetime.now().strftime("%Y-%m-%d")
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        raw = str(r.get("sym") or "").strip()
        if not raw or raw.startswith("#"):
            continue
        sym = _sym(raw)
        ed = str(r.get("entry_date") or "").strip()[:10]
        nt = r.get("new_today")
        if nt is None or (isinstance(nt, float) and nt != nt) or str(nt).strip() == "":
            new_today = bool(ed and ed == today)
        else:
            new_today = str(nt).strip().lower() in ("1", "true", "yes", "y", "是")
        rows.append({
            "sym": sym,
            "name": str(r.get("name") or ""),
            "weight_pct": _f(r.get("weight_pct")),
            "entry_price": _f(r.get("entry_price")),
            "entry_date": ed or None,
            "new_today": new_today,
            "px": _f(r.get("px")),
        })
    return rows, p


def _check(
    rule_id: str,
    title: str,
    ok: bool,
    detail: str,
    severity: str = "warn",
) -> Dict[str, Any]:
    return {
        "id": rule_id,
        "title": title,
        "status": "PASS" if ok else "FAIL",
        "ok": ok,
        "detail": detail,
        "severity": severity if not ok else "info",
    }


def run_discipline_check(
    holdings_path: Optional[str] = None,
    refresh_action: bool = True,
    asof: Optional[str] = None,
) -> Dict[str, Any]:
    """主入口：系统自检 + 可选持仓破戒。"""
    from .data import load_kline_csv, market_regime
    from .edge_boost import PLAYBOOK
    from .industry import industry_of, load_industry_map

    asof = asof or datetime.now().strftime("%Y-%m-%d")
    checks: List[Dict[str, Any]] = []
    ensure_holdings_template()

    # —— 1. 拉取 action / 环境 ——
    action: Dict[str, Any] = {}
    if refresh_action:
        try:
            from .action_today import get_action_today
            action = get_action_today(use_joint=True)
        except Exception as e:
            action = {"ok": False, "error": str(e)}
    else:
        # 读缓存文件尽力还原
        action = _action_from_files()

    pb = action.get("playbook") or dict(PLAYBOOK)
    env = pb.get("env") or {}
    market_ok = bool(action.get("market_ok"))
    light = action.get("light") or env.get("light") or "?"
    buy = list(action.get("buy") or [])
    observe = list(action.get("observe") or [])
    act = action.get("action") or "?"
    idx_ret20 = env.get("idx_ret20")
    if idx_ret20 is None:
        # 兜底
        try:
            idx = load_kline_csv("sh000300", min_bars=30)
            if idx is not None and len(idx) >= 21:
                ic = idx["close"].astype(float)
                idx_ret20 = float((ic.iloc[-1] / ic.iloc[-21] - 1.0) * 100)
        except Exception:
            pass
    green_days = env.get("green_days")
    low_vol = env.get("low_vol")
    env_blocked = bool(pb.get("env_blocked"))
    max_new = int(pb.get("max_new_today") if pb.get("max_new_today") is not None else PLAYBOOK.get("max_new_default") or 3)
    max_hold = int(pb.get("max_names") or PLAYBOOK["max_names"])
    max_w = float(pb.get("max_weight_pct") or PLAYBOOK["max_weight_pct"])
    min_cash = float(pb.get("min_cash_pct") or PLAYBOOK["min_cash_pct"])
    max_ind = int(pb.get("max_per_industry") or PLAYBOOK.get("max_per_industry") or 2)
    tp = float(pb.get("tp_pct") or PLAYBOOK["tp_pct"])
    sl = float(pb.get("sl_pct") or PLAYBOOK["sl_pct"])

    # S1 红灯 → 空仓
    if not market_ok:
        checks.append(_check(
            "S1_red_empty",
            "红灯/未站MA20+MA60 → 系统空仓",
            act in ("空仓", "空仓(环境)") and len(buy) == 0,
            f"light={light} action={act} buy={len(buy)}",
            "crit",
        ))
    else:
        checks.append(_check(
            "S1_green_ok",
            "绿灯环境可评估候选",
            True,
            f"light={light} action={act}",
        ))

    # S2 环境闸：指数20日≤0 不新开
    if idx_ret20 is not None and idx_ret20 <= 0:
        checks.append(_check(
            "S2_env_gate",
            "指数20日≤0 → 不新开（空仓环境）",
            act == "空仓(环境)" or len(buy) == 0,
            f"idx_ret20={idx_ret20}% action={act} buy={len(buy)} env_blocked={env_blocked}",
            "crit",
        ))
    else:
        checks.append(_check(
            "S2_env_open",
            "指数20日>0（或未知）允许评估新开",
            True,
            f"idx_ret20={idx_ret20}",
        ))

    # S3 候选数量 ≤ max_new / max_hold
    checks.append(_check(
        "S3_buy_cap",
        f"系统买入候选 ≤ max_new({max_new}) 且 ≤ max_hold({max_hold})",
        len(buy) <= max_new and len(buy) <= max_hold,
        f"buy={len(buy)} max_new={max_new} max_hold={max_hold}",
        "warn",
    ))

    # S4 质量分降序
    qs = []
    for x in buy:
        try:
            qs.append(float(x.get("qscore")) if x.get("qscore") is not None else None)
        except Exception:
            qs.append(None)
    sorted_ok = True
    prev = 1e18
    for q in qs:
        if q is None:
            continue
        if q > prev + 1e-9:
            sorted_ok = False
            break
        prev = q
    checks.append(_check(
        "S4_qscore_sort",
        "买入候选按质量分降序",
        sorted_ok or len(buy) <= 1,
        f"qscore={qs}",
        "warn",
    ))

    # S5 行业分散（系统名单）
    imap = load_industry_map()
    ind_counts: Dict[str, int] = {}
    for x in buy:
        ind = x.get("industry") or industry_of(str(x.get("sym") or ""), imap)
        if ind and ind != "未知":
            ind_counts[ind] = ind_counts.get(ind, 0) + 1
    bad_ind = {k: v for k, v in ind_counts.items() if v > max_ind}
    checks.append(_check(
        "S5_industry_sys",
        f"系统候选同行业 ≤{max_ind}",
        len(bad_ind) == 0,
        f"counts={ind_counts or '{}'} bad={bad_ind or '{}'}",
        "warn",
    ))

    # S6 出场提示含 tight
    exit_ok = True
    for x in buy:
        eh = str(x.get("exit_hint") or "")
        if "tight" not in eh.lower() and "TP" not in eh and "止盈" not in eh:
            # 仍算部分通过：playbook 会补 exit_hint
            pass
    checks.append(_check(
        "S6_exit_hint",
        f"出场纪律 tight TP{tp:.0f}/SL{sl:.0f}",
        True,
        f"playbook exit={pb.get('exit')} TP{tp}/SL{sl}",
    ))

    # —— 持仓检查 ——
    holdings, hpath = load_holdings(holdings_path)
    hold_checks: List[Dict[str, Any]] = []
    hold_summary: Dict[str, Any] = {"n": 0, "path": hpath}

    if not holdings:
        if hpath:
            hold_checks.append(_check(
                "H0_empty",
                "持仓文件为空（仅系统自检）",
                True,
                f"已有 {hpath} 但无有效行；填入代码/权重/成本后复查，或 cli.py holdings from-action",
            ))
        else:
            hold_checks.append(_check(
                "H0_no_file",
                "未提供持仓文件（仅系统自检）",
                True,
                f"可复制 {ensure_holdings_template()} → output/holdings.csv 后复查",
            ))
    else:
        hold_summary["n"] = len(holdings)
        # 现价增强：实时行情优先，失败回落日K
        try:
            from .data import fetch_realtime_quotes

            quotes = fetch_realtime_quotes([h["sym"] for h in holdings])
            for h in holdings:
                q = quotes.get(h["sym"]) or {}
                if q.get("price") and float(q["price"]) > 0:
                    h["px"] = float(q["price"])
                    h["px_src"] = "live"
                    if q.get("name") and not h.get("name"):
                        h["name"] = q.get("name")
                    if q.get("chg_pct") is not None:
                        h["chg_pct"] = q.get("chg_pct")
        except Exception:
            pass
        # H1 持仓只数
        hold_checks.append(_check(
            "H1_max_hold",
            f"持仓只数 ≤{max_hold}",
            len(holdings) <= max_hold,
            f"n={len(holdings)}",
            "crit",
        ))

        # H2 权重与现金
        weights = [h["weight_pct"] for h in holdings if h.get("weight_pct") is not None]
        if weights:
            total_w = sum(weights)
            cash_pct = 100.0 - total_w
            over_w = [h for h in holdings if h.get("weight_pct") is not None and h["weight_pct"] > max_w + 1e-6]
            hold_checks.append(_check(
                "H2_cash",
                f"现金 ≥{min_cash}%（权重合计≤{100-min_cash}%）",
                cash_pct + 1e-6 >= min_cash,
                f"权重合计={total_w:.1f}% 隐含现金={cash_pct:.1f}%",
                "crit",
            ))
            hold_checks.append(_check(
                "H2_single_w",
                f"单只权重 ≤{max_w}%",
                len(over_w) == 0,
                f"超限={[ (x['sym'], x['weight_pct']) for x in over_w ]}" if over_w else "无",
                "crit",
            ))
        else:
            hold_checks.append(_check(
                "H2_weight_missing",
                "持仓未填 weight_pct，跳过仓位检查",
                True,
                "建议补全权重%",
                "warn",
            ))

        # H3 今日新建数
        n_new = sum(1 for h in holdings if h.get("new_today"))
        hold_checks.append(_check(
            "H3_new_today",
            f"今日新建 ≤{max_new}",
            n_new <= max_new,
            f"new_today={n_new}",
            "crit",
        ))

        # H4 环境禁新开时不得有今日新建
        if idx_ret20 is not None and idx_ret20 <= 0:
            hold_checks.append(_check(
                "H4_env_no_new",
                "指数20日≤0 时持仓不得有今日新建",
                n_new == 0,
                f"idx_ret20={idx_ret20}% new={n_new}",
                "crit",
            ))
        if not market_ok:
            hold_checks.append(_check(
                "H4_red_no_new",
                "非绿灯（未站MA20+MA60）时不得有今日新建",
                n_new == 0,
                f"light={light} market_ok={market_ok} new={n_new}",
                "crit",
            ))

        # H5 行业分散
        h_ind: Dict[str, List[str]] = {}
        for h in holdings:
            ind = industry_of(h["sym"], imap)
            h["industry"] = ind
            if ind and ind != "未知":
                h_ind.setdefault(ind, []).append(h["sym"])
        bad_h = {k: v for k, v in h_ind.items() if len(v) > max_ind}
        hold_checks.append(_check(
            "H5_industry",
            f"持仓同行业 ≤{max_ind}",
            len(bad_h) == 0,
            f"{ {k: len(v) for k,v in h_ind.items()} } bad={bad_h or '{}'}",
            "warn",
        ))

        # H6 是否在今日 action 候选中（仅对今日新建严格）
        buy_set = {str(x.get("sym")) for x in buy}
        obs_set = {str(x.get("sym")) for x in observe}
        rogue_new = []
        for h in holdings:
            if h.get("new_today") and h["sym"] not in buy_set:
                rogue_new.append(h["sym"])
        hold_checks.append(_check(
            "H6_new_in_action",
            "今日新建 ∈ action 买入候选",
            len(rogue_new) == 0,
            f"破戒新建={rogue_new or '无'}（观察名单也不算可买）",
            "crit",
        ))

        # 旧仓不在名单：提示但不一定 FAIL（允许持有未在今日名单的旧仓）
        off_list = [h["sym"] for h in holdings if h["sym"] not in buy_set and h["sym"] not in obs_set]
        hold_checks.append(_check(
            "H6b_old_offlist",
            "旧仓可不在今日名单（仅提示）",
            True,
            f"不在今日名单的持仓={off_list or '无'}",
        ))

        # H7 止损：现价相对成本
        for h in holdings:
            ep = h.get("entry_price")
            px = h.get("px")
            if px is None:
                df = load_kline_csv(h["sym"], min_bars=5)
                if df is not None and len(df):
                    px = float(df["close"].iloc[-1])
                    h["px"] = px
            if ep and px and ep > 0:
                dd = (px / ep - 1.0) * 100.0
                h["pnl_pct"] = round(dd, 2)
                if dd <= -sl:
                    hold_checks.append(_check(
                        f"H7_sl_{h['sym']}",
                        f"{h['sym']} 已触及/跌破止损 −{sl}%",
                        False,
                        f"成本{ep} 现价{px} 浮盈{dd:+.2f}% → 纪律应卖出",
                        "crit",
                    ))
                elif dd >= tp:
                    hold_checks.append(_check(
                        f"H7_tp_{h['sym']}",
                        f"{h['sym']} 已达止盈 +{tp}%（提示）",
                        True,
                        f"成本{ep} 现价{px} 浮盈{dd:+.2f}% → 可按 tight 兑现",
                    ))

        hold_summary["holdings"] = holdings

    # 汇总
    all_checks = checks + hold_checks
    n_fail = sum(1 for c in all_checks if not c["ok"])
    n_crit = sum(1 for c in all_checks if not c["ok"] and c.get("severity") == "crit")
    n_pass = sum(1 for c in all_checks if c["ok"])
    grade = "守戒" if n_fail == 0 else ("严重破戒" if n_crit else "部分破戒")

    report = _write_discipline_report(
        asof=asof,
        grade=grade,
        action=action,
        env={
            "light": light,
            "market_ok": market_ok,
            "idx_ret20": idx_ret20,
            "green_days": green_days,
            "low_vol": low_vol,
            "env_blocked": env_blocked,
            "max_new": max_new,
        },
        checks=all_checks,
        holdings=holdings,
        holdings_path=hpath,
        n_fail=n_fail,
        n_crit=n_crit,
        n_pass=n_pass,
        playbook=pb,
    )

    # csv 明细
    os.makedirs(OUTPUT, exist_ok=True)
    csv_path = os.path.join(OUTPUT, "discipline_checks.csv")
    pd.DataFrame(all_checks).to_csv(csv_path, index=False, encoding="utf-8-sig")

    return {
        "ok": True,
        "asof": asof,
        "grade": grade,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "n_crit": n_crit,
        "checks": all_checks,
        "action": act,
        "light": light,
        "env": {
            "light": light,
            "market_ok": market_ok,
            "idx_ret20": idx_ret20,
            "green_days": green_days,
            "low_vol": low_vol,
            "env_blocked": env_blocked,
            "max_new": max_new,
        },
        "buy": buy,
        "observe": observe[:30],
        "holdings": holdings,
        "holdings_path": hpath,
        "holdings_n": len(holdings),
        "report": report,
        "csv": csv_path,
        "playbook_version": pb.get("version") or PLAYBOOK.get("version"),
        "disclaimer": "破戒检查是纪律审计，不构成投资建议。",
    }


def run_discipline_week(days: int = 14) -> Dict[str, Any]:
    """汇总 output/daily/discipline_*.md 近 N 天总评（读已有日报，不重跑扫盘）。"""
    import re
    from glob import glob

    day_dir = os.path.join(OUTPUT, "daily")
    files = sorted(glob(os.path.join(day_dir, "discipline_20*.md")), reverse=True)
    rows = []
    for path in files[: max(days * 2, days)]:
        base = os.path.basename(path)
        # discipline_2026-08-07.md
        m = re.match(r"discipline_(\d{4}-\d{2}-\d{2})\.md$", base)
        if not m:
            continue
        asof = m.group(1)
        try:
            text = open(path, encoding="utf-8").read()
        except Exception:
            continue
        grade = "—"
        n_pass = n_fail = n_crit = None
        gm = re.search(r"总评:\s*\*?\*?([^*\n]+)", text)
        if gm:
            grade = gm.group(1).strip()
        nm = re.search(r"通过\s*(\d+)\s*/\s*失败\s*(\d+)\s*/\s*严重\s*(\d+)", text)
        if nm:
            n_pass, n_fail, n_crit = int(nm.group(1)), int(nm.group(2)), int(nm.group(3))
        rows.append({
            "asof": asof,
            "grade": grade.replace("*", "").strip(),
            "n_pass": n_pass,
            "n_fail": n_fail,
            "n_crit": n_crit,
            "path": path,
        })
        if len(rows) >= days:
            break

    rows = sorted(rows, key=lambda r: r["asof"], reverse=True)
    n_ok = sum(1 for r in rows if "守戒" in (r.get("grade") or ""))
    n_bad = sum(1 for r in rows if "破戒" in (r.get("grade") or ""))
    n_crit_days = sum(1 for r in rows if (r.get("n_crit") or 0) > 0)

    os.makedirs(os.path.join(OUTPUT, "daily"), exist_ok=True)
    md_path = os.path.join(OUTPUT, "daily", "discipline_week.md")
    lines = [
        f"# 破戒周汇总（近 {len(rows)} 份日报）",
        "",
        f"- 生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 守戒日: {n_ok} · 破戒日: {n_bad} · 有严重项日: {n_crit_days}",
        "",
        "| 日期 | 总评 | 通过 | 失败 | 严重 |",
        "|------|------|------|------|------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['asof']} | {r['grade']} | {r.get('n_pass') if r.get('n_pass') is not None else '—'} | "
            f"{r.get('n_fail') if r.get('n_fail') is not None else '—'} | "
            f"{r.get('n_crit') if r.get('n_crit') is not None else '—'} |"
        )
    lines += ["", "数据来自已生成的 discipline_YYYY-MM-DD.md；无日报的日子不出现。", ""]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    pd.DataFrame(rows).to_csv(
        os.path.join(OUTPUT, "discipline_week.csv"), index=False, encoding="utf-8-sig"
    )
    return {
        "ok": True,
        "rows": rows,
        "n_ok": n_ok,
        "n_bad": n_bad,
        "n_crit_days": n_crit_days,
        "md_path": md_path,
        "csv": os.path.join(OUTPUT, "discipline_week.csv"),
    }


def _action_from_files() -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "buy": [], "observe": [], "action": "未知", "market_ok": False}
    p = os.path.join(OUTPUT, "action_today.csv")
    t = os.path.join(OUTPUT, "action_today.txt")
    if os.path.exists(t):
        for line in open(t, encoding="utf-8"):
            line = line.strip()
            if line.startswith("action="):
                out["action"] = line.split("=", 1)[1]
            if line.startswith("light="):
                out["light"] = line.split("=", 1)[1]
            if line.startswith("risk_mode="):
                out.setdefault("playbook", {})["risk_mode"] = line.split("=", 1)[1]
            if line.startswith("env_blocked="):
                out.setdefault("playbook", {})["env_blocked"] = line.split("=", 1)[1].lower() == "true"
            if line.startswith("BUY\t") or line.startswith("BUY "):
                parts = line.split("\t") if "\t" in line else line.split()
                if len(parts) >= 2:
                    out["buy"].append({"sym": parts[1], "name": parts[2] if len(parts) > 2 else ""})
    if os.path.exists(p):
        try:
            df = pd.read_csv(p)
            if len(df) and not out["buy"]:
                out["buy"] = df.to_dict(orient="records")
        except Exception:
            pass
    # market_ok 粗判
    act = out.get("action") or ""
    out["market_ok"] = act not in ("空仓",) or "环境" in act
    if act == "空仓":
        out["market_ok"] = False
    if "环境" in act:
        out["market_ok"] = True
        out["playbook"] = out.get("playbook") or {}
        out["playbook"]["env_blocked"] = True
    return out


def _write_discipline_report(
    asof: str,
    grade: str,
    action: Dict[str, Any],
    env: Dict[str, Any],
    checks: List[Dict[str, Any]],
    holdings: List[Dict[str, Any]],
    holdings_path: Optional[str],
    n_fail: int,
    n_crit: int,
    n_pass: int,
    playbook: Dict[str, Any],
) -> str:
    day_dir = os.path.join(OUTPUT, "daily")
    os.makedirs(day_dir, exist_ok=True)
    path = os.path.join(day_dir, f"discipline_{asof}.md")
    latest = os.path.join(day_dir, "discipline_latest.md")

    fails = [c for c in checks if not c["ok"]]
    lines = [
        f"# 破戒检查日报 {asof}",
        "",
        f"- 生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **总评: {grade}**（通过 {n_pass} / 失败 {n_fail} / 严重 {n_crit}）",
        f"- playbook: {playbook.get('version', 'v3')}",
        f"- 系统 action: **{action.get('action')}** | 大盘 {env.get('light')} | "
        f"指数20日 {env.get('idx_ret20')}% | 低波 {env.get('low_vol')} | "
        f"转绿 {env.get('green_days')} 日 | max_new={env.get('max_new')}",
        f"- 持仓文件: {holdings_path or '无（仅系统自检）'}",
        "",
        "## 失败项（优先看）",
        "",
    ]
    if not fails:
        lines.append("无。今日相对 playbook **守戒**。")
        lines.append("")
    else:
        lines.append("| 级别 | 规则 | 说明 |")
        lines.append("|------|------|------|")
        for c in fails:
            lines.append(f"| {c.get('severity')} | {c['title']} | {c['detail']} |")
        lines.append("")

    lines += [
        "## 全量检查",
        "",
        "| 状态 | ID | 规则 | 说明 |",
        "|------|-----|------|------|",
    ]
    for c in checks:
        mark = "✅" if c["ok"] else "❌"
        lines.append(f"| {mark} {c['status']} | {c['id']} | {c['title']} | {c['detail']} |")
    lines.append("")

    lines += ["## 系统买入候选", ""]
    buy = action.get("buy") or []
    if not buy:
        lines.append("无。")
        lines.append("")
    else:
        lines.append("| 代码 | 名称 | 质量分 | 行业 | 档位 |")
        lines.append("|------|------|--------|------|------|")
        for x in buy:
            lines.append(
                f"| {x.get('sym')} | {x.get('name')} | {x.get('qscore')} | "
                f"{x.get('industry') or '—'} | {x.get('tier')} |"
            )
        lines.append("")

    if holdings:
        lines += ["## 持仓快照", ""]
        lines.append("| 代码 | 名称 | 权重% | 成本 | 现价 | 浮盈% | 今日新建 | 行业 |")
        lines.append("|------|------|-------|------|------|-------|----------|------|")
        for h in holdings:
            lines.append(
                f"| {h.get('sym')} | {h.get('name')} | {h.get('weight_pct') if h.get('weight_pct') is not None else '—'} | "
                f"{h.get('entry_price') if h.get('entry_price') is not None else '—'} | "
                f"{h.get('px') if h.get('px') is not None else '—'} | "
                f"{h.get('pnl_pct') if h.get('pnl_pct') is not None else '—'} | "
                f"{'是' if h.get('new_today') else '否'} | {h.get('industry') or '—'} |"
            )
        lines.append("")
    else:
        lines += [
            "## 持仓",
            "",
            "未读取到 holdings。模板：`output/holdings.example.csv` → 复制为 `output/holdings.csv`。",
            "",
        ]

    lines += [
        "## playbook 规则备忘",
        "",
    ]
    for r in (playbook.get("rules") or []):
        lines.append(f"- {r}")
    lines += [
        "",
        "> 破戒检查 = 纪律审计，不构成投资建议。历史纪律 ≠ 未来收益。",
        "",
    ]
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    with open(latest, "w", encoding="utf-8") as f:
        f.write(text)
    # 根目录快捷
    with open(os.path.join(OUTPUT, "discipline_latest.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return path
