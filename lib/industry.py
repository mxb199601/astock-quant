# -*- coding: utf-8 -*-
"""行业信息（东方财富 F10），缓存到本地。用于行业内相似日。"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

from .data import curl, load_fundamentals, to_sym
from .paths import CACHE

INDUSTRY_PATH = os.path.join(CACHE, "_industry.json")


def _em_code(sym: str) -> str:
    s = to_sym(sym)
    code = s[2:]
    return ("SH" if s.startswith("sh") else "SZ") + code


def fetch_industry(sym: str) -> str:
    """拉取单票行业，失败返回 未知。"""
    s = to_sym(sym)
    url = (
        "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/"
        f"CompanySurveyAjax?code={_em_code(s)}"
    )
    try:
        # eastmoney 不一定要 sina referer
        import subprocess
        r = subprocess.run(
            ["curl", "-s", "-m", "12", "-A", "Mozilla/5.0", url],
            capture_output=True,
        )
        text = r.stdout.decode("utf-8", "ignore")
        data = json.loads(text)
        jb = data.get("jbzl") or {}
        ind = jb.get("sshy") or jb.get("sszjhhy") or "未知"
        return str(ind).strip() or "未知"
    except Exception:
        return "未知"


def load_industry_map() -> Dict[str, str]:
    if os.path.exists(INDUSTRY_PATH):
        try:
            return json.load(open(INDUSTRY_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def industry_of(sym: str, cache: Optional[Dict[str, str]] = None) -> str:
    """只读缓存，不打网。未知则返回「未知」。"""
    s = to_sym(sym)
    m = cache if cache is not None else load_industry_map()
    v = (m.get(s) or "").strip()
    return v if v else "未知"


def diversify_by_industry(
    items: List[dict],
    *,
    max_per_industry: int = 2,
    industry_key: str = "industry",
    sym_key: str = "sym",
    already: Optional[List[str]] = None,
    cache: Optional[Dict[str, str]] = None,
) -> Tuple[List[dict], List[dict]]:
    """按行业分散截断。

    items 已按优先级排好（质量分高在前）。
    already: 已持仓行业名列表（可重复计数）。
    返回 (kept, rejected)；未知行业不互相顶替（各自独立桶）。
    """
    m = cache if cache is not None else load_industry_map()
    counts: Dict[str, int] = {}
    if already:
        for ind in already:
            if not ind or ind == "未知":
                continue
            counts[ind] = counts.get(ind, 0) + 1

    kept: List[dict] = []
    rejected: List[dict] = []
    for it in items:
        row = dict(it)
        sym = str(row.get(sym_key) or "")
        ind = row.get(industry_key)
        if not ind:
            ind = industry_of(sym, m)
            row[industry_key] = ind
        # 未知：不占用分散配额（避免缓存不全时误杀）
        if ind and ind != "未知":
            if counts.get(ind, 0) >= max_per_industry:
                row["why_div"] = f"同行业已满({ind}≤{max_per_industry})"
                rejected.append(row)
                continue
            counts[ind] = counts.get(ind, 0) + 1
        kept.append(row)
    return kept, rejected


def save_industry_map(m: Dict[str, str]) -> None:
    os.makedirs(CACHE, exist_ok=True)
    json.dump(m, open(INDUSTRY_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def get_industry(sym: str, cache: Optional[Dict[str, str]] = None) -> str:
    s = to_sym(sym)
    m = cache if cache is not None else load_industry_map()
    if s in m and m[s] and m[s] != "未知":
        return m[s]
    ind = fetch_industry(s)
    m[s] = ind
    if cache is None:
        save_industry_map(m)
    else:
        cache[s] = ind
    return ind


def industry_peers(sym: str, max_peers: int = 20, sleep: float = 0.05) -> List[str]:
    """同行业、流通市值靠前的主板同伴（不含自己）。"""
    s = to_sym(sym)
    fund = load_fundamentals(False)
    m = load_industry_map()
    ind = get_industry(s, cache=m)
    if ind == "未知":
        save_industry_map(m)
        return []

    # 候选：按市值排序
    cands = []
    for k, v in fund.items():
        if k == s:
            continue
        try:
            nmc = float(v.get("nmc") or 0)
        except Exception:
            nmc = 0
        cands.append((nmc, k))
    cands.sort(reverse=True)

    peers = []
    checked = 0
    for nmc, k in cands:
        if len(peers) >= max_peers:
            break
        # 已有缓存行业则快
        if k in m and m[k] not in ("", None):
            if m[k] == ind:
                peers.append(k)
            continue
        # 限制现场拉数次数，避免太慢
        if checked >= max_peers * 4:
            break
        ki = get_industry(k, cache=m)
        checked += 1
        if ki == ind:
            peers.append(k)
        if sleep and sleep > 0:
            time.sleep(min(sleep, 0.02))
    save_industry_map(m)
    return peers
