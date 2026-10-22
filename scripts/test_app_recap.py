#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""软件复盘页：标签存在，cached 名单名称不得是代码。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _must(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)


def test_index_has_recap_tab():
    from app import INDEX_HTML

    _must("复盘" in INDEX_HTML, "首页须有复盘标签")
    _must("/api/recap" in INDEX_HTML, "须请求 /api/recap")
    _must("doRecap" in INDEX_HTML, "须有 doRecap")
    _must("tab-recap" in INDEX_HTML, "须有 tab-recap")
    _must("30" in INDEX_HTML and "现价" in INDEX_HTML, "复盘页须写明现价<30")
    _must("实时" in INDEX_HTML, "须写明现价优先实时")


def test_api_recap_cached_chinese_names():
    from lib.data import is_placeholder_name

    from app import api_recap

    d = api_recap(cached=True)
    _must(d.get("ok"), d.get("error") or str(d)[:200])
    _must(d.get("action") in ("空仓", "可关注候选", "仅观察", "破戒") or d.get("action"), "须有操作口径")
    rows = list(d.get("observe") or [])
    for g in d.get("observe_groups") or []:
        rows.extend(g.get("rows") or [])
    _must(rows, "cached 复盘应有观察层（来自 cool_pool/menga）")
    ph = []
    seen = set()
    for r in rows:
        sym = str(r.get("sym") or "")
        name = str(r.get("name") or "")
        key = (sym, name)
        if key in seen:
            continue
        seen.add(key)
        if is_placeholder_name(name, sym):
            ph.append("%s/%s" % (sym, name))
    _must(not ph, "软件复盘名称不得是代码: " + ", ".join(ph[:8]))
    by_sym = {str(r.get("sym")): r for r in rows}
    _must("sz001309" not in by_sym, "德明利 441 元须被现价<30 剔除")
    _must("sz002993" not in by_sym, "奥海科技 40.92 须剔除")
    _must("sz002080" not in by_sym, "中材科技 60.24 须剔除")
    if "sh603683" in by_sym:
        _must(by_sym["sh603683"].get("name") == "晶华新材", by_sym["sh603683"])
    for r in rows:
        px = r.get("px")
        _must(px is not None and float(px) < 30, "观察层现价须<30: %s %s" % (r.get("sym"), px))


def test_api_action_red_empty():
    from app import api_action

    d = api_action()
    _must(d.get("ok") is not False, d)
    _must(d.get("action") == "空仓" or d.get("light") == "红灯" or not (d.get("buy") or []), d)
    _must(len(d.get("buy") or []) == 0, "红灯买入向须空: %s" % d.get("n"))


def main():
    test_index_has_recap_tab()
    test_api_recap_cached_chinese_names()
    test_api_action_red_empty()
    print("ok")


if __name__ == "__main__":
    raise SystemExit(main())
