# -*- coding: utf-8 -*-
"""今日操作口径：唯一「候选」出口。

硬规则（研究纪律，不是稳赚）：
  1. 大盘未站 MA20+MA60（红灯/黄灯不够）→ 操作=空仓，买入候选=[]
  2. 绿灯 → 优先「联合信号」；若无联合则用「完整通过(v4 robust 优先)」
  3. MENGα / 妖股 / 形态 / 冷静池 永不单独作为买入候选

返回字段 action: 空仓 | 可关注候选
disclaimer 始终保留。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import pandas as pd

from .paths import OUTPUT


def get_action_today(use_joint: bool = True, light_only: bool = False) -> Dict[str, Any]:
    from .data import market_regime
    from .joint import run_joint_signals
    from .scan import run_structure_scan

    # 先实时大盘；红灯可秒回，避免全市场扫盘堵死页面
    regime = market_regime(live=True)
    light = regime.get("light") or "未知"
    market_ok = bool(regime.get("above_ma20") and regime.get("above_ma60"))

    from .up_prob import format_up_prob_hint, load_up_prob_ref

    up_hint = format_up_prob_hint("robust")
    up_ref = load_up_prob_ref()

    # —— 红灯：唯一正确「推荐」是空仓（不扫 3000 只）——
    if not market_ok:
        note = (
            f"大盘{light}：系统操作口径 = 【空仓】。"
            f"完整通过=0、买入候选=0。"
            f"MENGα/冷静池/妖股前只供研究，不是买入推荐。"
            f"请等沪深300站上 MA20+MA60 后再看本页候选。"
            f" {up_hint}"
        )
        out = {
            "ok": True,
            "action": "空仓",
            "can_buy": False,
            "light": light,
            "market_ok": False,
            "buy": [],
            "n": 0,
            "tier": None,
            "full_pass_count": 0,
            "joint_count": 0,
            "up_prob_hint": up_hint,
            "up_prob_ref": up_ref,
            "observe_strict": 0,
            "observe_robust": 0,
            "regime": regime,
            "note": note,
            "why_empty": "大盘未满足 MA20+MA60（红灯纪律）",
            "disclaimer": "研究工具，不构成投资建议；空仓亦为纪律输出，非收益承诺。",
            "fast": True,
        }
        try:
            from .edge_boost import enrich_action_playbook

            out = enrich_action_playbook(out)
        except Exception:
            pass
        _save(out)
        return out

    if light_only:
        return {
            "ok": True,
            "action": "可关注候选",
            "can_buy": False,
            "light": light,
            "market_ok": True,
            "buy": [],
            "n": 0,
            "note": "light_only 模式未扫名单",
            "regime": regime,
        }

    scan = run_structure_scan()
    if not scan.get("ok"):
        return {
            "ok": False,
            "error": scan.get("error") or "扫盘失败",
            "action": "未知",
            "buy": [],
            "n": 0,
        }

    regime = scan.get("regime") or regime
    light = regime.get("light") or light
    market_ok = bool(scan.get("market_ok"))
    full = list(scan.get("full_pass") or [])
    robust = list(scan.get("robust_struct") or [])
    strict = list(scan.get("strict_struct") or [])

    # —— 绿灯：联合 > 完整通过 ——
    joint: List[dict] = []
    joint_note = ""
    if use_joint:
        jr = run_joint_signals(use_llm=False)
        joint = list(jr.get("joint") or [])
        joint_note = jr.get("note") or ""

    buy: List[dict] = []
    tier = None
    if joint:
        tier = "联合信号★★"
        for x in joint:
            buy.append({
                "sym": x.get("sym"),
                "name": x.get("name"),
                "px": x.get("px"),
                "qscore": x.get("qscore"),
                "rsi": x.get("rsi"),
                "zf60": x.get("zf60"),
                "tier": tier,
                "reason": (
                    f"完整通过∩MENGα冷静 "
                    f"α{x.get('menga') or '—'} 热{x.get('heat') or '—'} "
                    f"芽{x.get('spark') or '—'}（{x.get('forecast_lean') or '联合v2'}）"
                ),
                "exit_hint": (
                    f"纪律: tight TP4/SL3（PATH盈利概率约五成出头）。{up_hint}"
                ),
            })
    elif full:
        tier = "完整通过★"
        for x in full:
            buy.append({
                "sym": x.get("sym"),
                "name": x.get("name"),
                "px": x.get("px"),
                "qscore": x.get("qscore"),
                "rsi": x.get("rsi"),
                "zf60": x.get("zf60"),
                "tier": x.get("tier") or tier,
                "reason": "大盘多头+结构(v4 robust优先)+质量分排序",
                "exit_hint": f"纪律: tight TP4/SL3。{up_hint}",
            })

    # 质量分优先（与 playbook topN / 组合模拟选股一致）
    def _q(x: dict) -> float:
        try:
            return float(x.get("qscore") if x.get("qscore") is not None else -1e9)
        except Exception:
            return -1e9

    buy.sort(key=_q, reverse=True)

    if buy:
        action = "可关注候选"
        note = (
            f"大盘{light}：操作口径 = 【{action}】共 {len(buy)} 只（档位={tier}，已按质量分排序）。"
            f"这是系统最严结构名单，仍不是稳赚/荐股；单笔请自控仓位。"
            f" {up_hint}"
        )
    else:
        action = "空仓"
        note = (
            f"大盘{light}但今日无联合信号/完整通过 → 操作口径仍 = 【空仓】。"
            f"{joint_note} 结构观察仍可看，勿用 MENGα 当买入。"
            f" {up_hint}"
        )

    out = {
        "ok": True,
        "action": action,
        "can_buy": bool(buy),
        "light": light,
        "market_ok": True,
        "buy": buy,
        "n": len(buy),
        "tier": tier,
        "full_pass_count": len(full),
        "joint_count": len(joint),
        "observe_strict": len(strict),
        "observe_robust": len(robust),
        "note": note,
        "up_prob_hint": up_hint,
        "up_prob_ref": up_ref,
        "why_empty": None if buy else "绿灯但无票通过完整/联合过滤",
        "disclaimer": "研究工具，不构成投资建议。候选≠必须买；有本金亏损可能。历史上涨概率≠下一笔会涨。",
    }
    # 组合纪律 + 环境仓位提示（边在分散/止损，不在新画线）
    try:
        from .edge_boost import enrich_action_playbook

        out = enrich_action_playbook(out)
    except Exception:
        pass
    _save(out)
    return out


def _save(out: Dict[str, Any]) -> None:
    os.makedirs(OUTPUT, exist_ok=True)
    rows = out.get("buy") or []
    pd.DataFrame(rows).to_csv(
        os.path.join(OUTPUT, "action_today.csv"), index=False, encoding="utf-8-sig"
    )
    obs = out.get("observe") or []
    if obs:
        pd.DataFrame(obs).to_csv(
            os.path.join(OUTPUT, "action_observe.csv"), index=False, encoding="utf-8-sig"
        )
    pb = out.get("playbook") or {}
    with open(os.path.join(OUTPUT, "action_today.txt"), "w", encoding="utf-8") as f:
        f.write(f"action={out.get('action')}\n")
        f.write(f"light={out.get('light')}\n")
        f.write(f"n={out.get('n')}\n")
        f.write(f"risk_mode={pb.get('risk_mode')}\n")
        f.write(f"max_new={pb.get('max_new_today')}\n")
        f.write(f"env_blocked={pb.get('env_blocked')}\n")
        f.write(f"note={out.get('note')}\n")
        for x in rows:
            f.write(f"BUY\t{x.get('sym')}\t{x.get('name')}\t{x.get('tier')}\n")
        for x in obs[:20]:
            f.write(f"OBS\t{x.get('sym')}\t{x.get('name')}\t{x.get('tier')}\n")
