# -*- coding: utf-8 -*-
"""持仓助手：生成/更新 output/holdings.csv，供破戒检查使用。

用法见 cli.py holdings
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .paths import OUTPUT, ROOT

DEFAULT_HOLDINGS = os.path.join(OUTPUT, "holdings.csv")
COLS = ["sym", "name", "weight_pct", "entry_price", "entry_date", "new_today", "px", "industry"]


def default_path(path: Optional[str] = None) -> str:
    return path or DEFAULT_HOLDINGS


def _to_sym(code: str) -> str:
    from .data import to_sym
    return to_sym(str(code).strip())


def load_rows(path: Optional[str] = None) -> List[Dict[str, Any]]:
    from .discipline import load_holdings

    p = default_path(path)
    if not os.path.exists(p):
        return []
    rows, _ = load_holdings(p)
    return rows


def save_rows(rows: List[Dict[str, Any]], path: Optional[str] = None) -> str:
    p = default_path(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    out = []
    for r in rows:
        out.append({
            "sym": r.get("sym"),
            "name": r.get("name") or "",
            "weight_pct": r.get("weight_pct") if r.get("weight_pct") is not None else "",
            "entry_price": r.get("entry_price") if r.get("entry_price") is not None else "",
            "entry_date": r.get("entry_date") or "",
            "new_today": 1 if r.get("new_today") else 0,
            "px": r.get("px") if r.get("px") is not None else "",
            "industry": r.get("industry") or "",
        })
    df = pd.DataFrame(out, columns=COLS)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return p


def init_holdings(path: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """创建空持仓表（含表头）。"""
    p = default_path(path)
    if os.path.exists(p) and not force:
        return {"ok": True, "path": p, "created": False, "note": "已存在，未覆盖（--force 可重建）"}
    save_rows([], p)
    return {"ok": True, "path": p, "created": True, "n": 0}


def from_action(
    path: Optional[str] = None,
    equal_weight: bool = True,
    max_weight: float = 12.5,
    mark_new: bool = True,
    merge: bool = False,
    use_joint: bool = True,
) -> Dict[str, Any]:
    """用今日 action 买入候选生成/合并持仓草稿。

    equal_weight: 在 max_weight 与 75/n 之间取小，均分。
    merge: True 时保留旧仓，新候选追加（不覆盖旧 entry）。
    """
    from .action_today import get_action_today
    from .edge_boost import PLAYBOOK
    from .industry import industry_of, load_industry_map

    act = get_action_today(use_joint=use_joint)
    buy = list(act.get("buy") or [])
    today = datetime.now().strftime("%Y-%m-%d")
    p = default_path(path)
    imap = load_industry_map()

    if not buy:
        return {
            "ok": True,
            "path": p,
            "n": 0,
            "action": act.get("action"),
            "light": act.get("light"),
            "note": f"今日无买入候选（action={act.get('action')}），未改持仓文件",
            "action_result": act,
        }

    n = len(buy)
    # 均分且单只不超过 max_weight，总和尽量 ≤ 75
    if equal_weight:
        w = min(max_weight, 75.0 / max(n, 1))
        w = round(w, 2)
    else:
        w = None

    new_rows: List[Dict[str, Any]] = []
    for x in buy:
        sym = _to_sym(str(x.get("sym") or ""))
        px = x.get("px")
        try:
            px = float(px) if px is not None else None
        except Exception:
            px = None
        new_rows.append({
            "sym": sym,
            "name": x.get("name") or "",
            "weight_pct": w,
            "entry_price": px,  # 草稿：以现价为成本占位，请自行改成真实成本
            "entry_date": today if mark_new else "",
            "new_today": bool(mark_new),
            "px": px,
            "industry": x.get("industry") or industry_of(sym, imap),
        })

    if merge and os.path.exists(p):
        old = load_rows(p)
        old_map = {r["sym"]: r for r in old}
        for nr in new_rows:
            if nr["sym"] in old_map:
                # 保留旧成本与入场日
                o = old_map[nr["sym"]]
                nr["entry_price"] = o.get("entry_price") if o.get("entry_price") is not None else nr["entry_price"]
                nr["entry_date"] = o.get("entry_date") or nr["entry_date"]
                nr["new_today"] = False
                nr["weight_pct"] = o.get("weight_pct") if o.get("weight_pct") is not None else nr["weight_pct"]
            old_map[nr["sym"]] = nr
        rows = list(old_map.values())
    else:
        rows = new_rows

    # 行业补全
    for r in rows:
        if not r.get("industry"):
            r["industry"] = industry_of(r["sym"], imap)

    path_out = save_rows(rows, p)
    return {
        "ok": True,
        "path": path_out,
        "n": len(rows),
        "action": act.get("action"),
        "light": act.get("light"),
        "weight_each": w,
        "max_hold": PLAYBOOK.get("max_names"),
        "note": (
            f"已从 action={act.get('action')} 写入 {len(new_rows)} 只候选"
            + ("（合并旧仓）" if merge else "（覆盖）")
            + "；entry_price 默认用现价占位，请改成真实成本后再 discipline"
        ),
        "rows": rows,
    }


def sync_prices(path: Optional[str] = None, live: bool = True) -> Dict[str, Any]:
    """刷新持仓现价（实时优先，失败用日K）。"""
    p = default_path(path)
    if not os.path.exists(p):
        return {"ok": False, "error": f"无持仓文件: {p}，先 holdings init 或 from-action"}

    rows = load_rows(p)
    if not rows:
        return {"ok": True, "path": p, "n": 0, "note": "持仓为空"}

    from .data import fetch_realtime_quotes, load_kline_csv

    updated = 0
    if live:
        try:
            qmap = fetch_realtime_quotes([r["sym"] for r in rows])
            for r in rows:
                q = qmap.get(r["sym"]) or {}
                px = q.get("price")
                if px is not None and float(px) > 0:
                    r["px"] = float(px)
                    if q.get("name"):
                        r["name"] = q.get("name")
                    updated += 1
        except Exception:
            pass

    for r in rows:
        if r.get("px") is not None:
            continue
        df = load_kline_csv(r["sym"], min_bars=5)
        if df is not None and len(df):
            r["px"] = float(df["close"].iloc[-1])
            updated += 1

    # 浮盈（不写文件，仅返回）
    for r in rows:
        ep, px = r.get("entry_price"), r.get("px")
        if ep and px and float(ep) > 0:
            r["pnl_pct"] = round((float(px) / float(ep) - 1.0) * 100.0, 2)

    save_rows(rows, p)
    return {"ok": True, "path": p, "n": len(rows), "updated": updated, "rows": rows}


def fill_industry_batch(
    symbols: Optional[List[str]] = None,
    max_n: int = 200,
    sleep: float = 0.12,
    only_missing: bool = True,
) -> Dict[str, Any]:
    """批量补行业缓存（打东财，限速）。"""
    import time

    from .data import list_cached_symbols
    from .industry import fetch_industry, load_industry_map, save_industry_map

    m = load_industry_map()
    if symbols:
        pool = [_to_sym(s) for s in symbols]
    else:
        pool = list_cached_symbols(mainboard_only=True, drop_st=True)

    todo = []
    for s in pool:
        if only_missing and m.get(s) and m[s] != "未知":
            continue
        todo.append(s)
    todo = todo[: max(0, int(max_n))]

    ok = fail = 0
    for i, s in enumerate(todo):
        try:
            ind = fetch_industry(s)
            m[s] = ind
            if ind and ind != "未知":
                ok += 1
            else:
                fail += 1
        except Exception:
            m[s] = "未知"
            fail += 1
        if (i + 1) % 20 == 0:
            save_industry_map(m)
        time.sleep(max(0.0, sleep))

    save_industry_map(m)
    known = sum(1 for v in m.values() if v and v != "未知")
    return {
        "ok": True,
        "requested": len(todo),
        "filled_ok": ok,
        "filled_unknown": fail,
        "map_total": len(m),
        "map_known": known,
        "note": f"行业缓存 known={known}/{len(m)}；本次处理 {len(todo)} 只",
    }


def show_holdings(path: Optional[str] = None) -> Dict[str, Any]:
    p = default_path(path)
    rows = load_rows(p) if os.path.exists(p) else []
    total_w = sum(float(r["weight_pct"]) for r in rows if r.get("weight_pct") is not None)
    return {
        "ok": True,
        "path": p if os.path.exists(p) else None,
        "n": len(rows),
        "total_weight": round(total_w, 2),
        "cash_implied": round(100.0 - total_w, 2) if rows else None,
        "rows": rows,
    }


def set_equal_weights(path: Optional[str] = None, max_weight: float = 12.5) -> Dict[str, Any]:
    p = default_path(path)
    if not os.path.exists(p):
        return {"ok": False, "error": f"无文件 {p}"}
    rows = load_rows(p)
    if not rows:
        return {"ok": True, "path": p, "n": 0}
    w = min(max_weight, 75.0 / len(rows))
    w = round(w, 2)
    for r in rows:
        r["weight_pct"] = w
    save_rows(rows, p)
    return {"ok": True, "path": p, "n": len(rows), "weight_each": w}
