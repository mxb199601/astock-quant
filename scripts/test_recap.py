#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘报告：网上骨架 × 本仓纪律。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.recap import build_recap  # noqa: E402


def _must(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)


def _red_payload():
    return {
        "date": "2026-09-04",
        "light": "红灯",
        "action": "空仓",
        "market_ok": False,
        "ret20": -3.12,
        "close": 4548.05,
        "above_ma20": False,
        "above_ma60": False,
        "tier": None,
        "buy": [],
        "observe": [
            {"sym": "sh600363", "name": "联创光电", "industry": "电子", "menga": 100, "px": 10.0},
            {"sym": "sh603226", "name": "菲林格尔", "industry": "电子", "menga": 99, "px": 8.0},
            {"sym": "sh603211", "name": "晋拓股份", "industry": "电子", "menga": 98, "px": 7.0},
            {"sym": "sh603256", "name": "宏和科技", "industry": "电子", "menga": 97, "px": 6.0},
        ],
    }


def _green_payload():
    buy = []
    for i, (sym, name, ind, q) in enumerate(
        [
            ("sh600519", "贵州茅台", "白酒", 90),
            ("sz000858", "五粮液", "白酒", 80),
            ("sz000568", "泸州老窖", "白酒", 70),
            ("sh601318", "中国平安", "保险", 85),
            ("sh601601", "中国太保", "保险", 60),
        ]
    ):
        buy.append(
            {
                "sym": sym,
                "name": name,
                "industry": ind,
                "qscore": q,
                "px": 100 + i,
                "tier": "联合信号★★",
                "reason": "完整通过∩MENGα冷静",
                "rsi": 50,
                "zf60": 10,
            }
        )
    return {
        "date": "2026-08-01",
        "light": "绿灯",
        "action": "可关注候选",
        "market_ok": True,
        "ret20": 2.1,
        "close": 4000.0,
        "above_ma20": True,
        "above_ma60": True,
        "tier": "联合信号★★",
        "buy": buy,
        "observe": [],
    }


def test_red_light_no_pad_and_no_recommend():
    md = build_recap(_red_payload())
    _must("空仓" in md, "红灯须写空仓")
    _must("无买入向候选" in md or "买入向" in md and "0" in md, md[:400])
    _must("建议买入" not in md, "禁止建议买入")
    _must("年化" not in md, "禁止年化目标")
    _must("约50%" not in md, "禁止50%年化")
    _must("建仓价格" not in md, "禁止推理建仓价")
    _must("推荐正好" not in md, "禁止凑满3只")
    _must("观察，非买入" in md or "观察非买入" in md, "观察层须标明非买")
    # 观察层电子最多 3 只，且不能写成推荐
    _must(md.count("联创光电") == 1, "观察应列出")
    _must("宏和科技" not in md, "观察同行业超过3只应截断")


def test_green_industry_cap_two():
    md = build_recap(_green_payload())
    _must("可关注候选" in md, md[:200])
    _must("贵州茅台" in md and "五粮液" in md, "白酒前两只应按质量分留下")
    _must("泸州老窖" not in md, "同行业第三只须被 playbook 截掉（≤2）")
    _must("中国平安" in md and "中国太保" in md, "保险两只都可留")
    _must("TP4" in md and "SL3" in md, "出场须是 tight")
    _must("PATH" in md, "须标明 PATH")
    _must("反方" in md, "名单非空须有反方")
    _must("失效" in md, "须有失效条件")


def test_youzi_and_position_slots():
    md = build_recap(_red_payload())
    _must("市场情绪" in md and "红灯" in md, "游资槽位→红绿灯")
    _must("阶段" in md, "游资槽位→操作口径")
    _must("仓位" in md or "现金" in md, "少妇仓位优先→playbook")
    _must("同行业" in md, "同行业上限")
    _must("不构成投资建议" in md, "免责")


def test_rows_from_csv_fills_placeholder_name():
    """cached 复盘读 cool_pool.csv 时，代码占位名须换成中文。"""
    import tempfile

    from lib import data as data_mod
    from lib.recap import _rows_from_csv

    tmp = tempfile.mkdtemp(prefix="recap_names_")
    path = os.path.join(tmp, "cool.csv")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("sym,name,menga,px\nsz001309,sz001309,100.0,16.55\n")
    orig = data_mod.load_fundamentals
    data_mod.load_fundamentals = lambda force=False, **kw: {"sz001309": {"name": "德明利"}}
    try:
        rows = _rows_from_csv(path)
        _must(rows, "应读到一行")
        _must(rows[0].get("sym") == "sz001309", str(rows[0]))
        _must(rows[0].get("name") == "德明利", "占位名应换成德明利，实际=%r" % (rows[0].get("name"),))
        md = build_recap(
            {
                "date": "2026-09-04",
                "light": "红灯",
                "action": "空仓",
                "market_ok": False,
                "buy": [],
                "observe": rows,
            }
        )
        _must("德明利" in md, md)
        _must("| sz001309 | sz001309 |" not in md, "名称列不得再写代码")
        _must("| sz001309 | 德明利 |" in md, md)
    finally:
        data_mod.load_fundamentals = orig


def test_observe_keeps_only_px_under_30():
    """观察层只要现价 < 30；≥30 / 缺价剔除。买入向名单本身不改。"""
    md = build_recap(
        {
            "date": "2026-09-04",
            "light": "红灯",
            "action": "空仓",
            "market_ok": False,
            "buy": [],
            "observe": [
                {"sym": "sz001309", "name": "德明利", "industry": "半导体", "menga": 100, "px": 441.86},
                {"sym": "sh603683", "name": "晶华新材", "industry": "未知", "menga": 100, "px": 16.55},
                {"sym": "sh603698", "name": "航天工程", "industry": "未知", "menga": 99.9, "px": 24.13},
                {"sym": "sz002993", "name": "奥海科技", "industry": "未知", "menga": 99.9, "px": 40.92},
                {"sym": "sz002080", "name": "中材科技", "industry": "玻璃陶瓷", "menga": 99.6, "px": 60.24},
                {"sym": "sz000066", "name": "中国长城", "industry": "计算机设备", "menga": 98.5, "px": 14.8},
                {"sym": "sh600000", "name": "浦发银行", "industry": "银行", "menga": 90, "px": 30.0},
                {"sym": "sh600001", "name": "缺价票", "industry": "未知", "menga": 89, "px": None},
            ],
        }
    )
    _must("晶华新材" in md and "航天工程" in md and "中国长城" in md, md)
    _must("德明利" not in md, "441 元须剔除")
    _must("奥海科技" not in md, "40.92 须剔除")
    _must("中材科技" not in md, "60.24 须剔除")
    _must("浦发银行" not in md, "等于 30 不是小于 30")
    _must("缺价票" not in md, "缺现价不能当 <30")
    _must("30" in md and ("现价" in md or "元" in md), "报告须写明现价<30 条件")


def test_observe_prefers_live_px_over_bar():
    """日K 16 但实时 35 须剔除；日K 40 但实时 18 须留下。失败回退日K。"""
    md = build_recap(
        {
            "date": "2026-09-04",
            "light": "红灯",
            "action": "空仓",
            "market_ok": False,
            "buy": [],
            "observe": [
                {
                    "sym": "sh603683",
                    "name": "晶华新材",
                    "industry": "未知",
                    "menga": 100,
                    "px": 16.55,
                    "live": True,
                    "live_px": 35.0,
                },
                {
                    "sym": "sz000066",
                    "name": "中国长城",
                    "industry": "计算机设备",
                    "menga": 98.5,
                    "px": 40.0,
                    "live": True,
                    "live_px": 14.8,
                },
                {
                    "sym": "sh603698",
                    "name": "航天工程",
                    "industry": "未知",
                    "menga": 99.9,
                    "px": 24.13,
                    "live": False,
                    "live_px": None,
                },
            ],
        }
    )
    _must("晶华新材" not in md, "实时 35 须剔除，即使日K<30")
    _must("中国长城" in md, "实时 14.8 须留下，即使日K≥30")
    _must("航天工程" in md, "无实时时回退日K 24.13")


def test_fill_unknown_industry_for_cheap_rows():
    """过 <30 的未知行业须被对照表/拉取结果替换。"""
    from lib.recap import _fill_unknown_industry

    rows = [
        {"sym": "sh603683", "name": "晶华新材", "industry": "未知", "px": 16.55},
        {"sym": "sz000066", "name": "中国长城", "industry": "计算机设备", "px": 14.8},
    ]
    fake = {"sh603683": "涂料油墨"}

    def getter(sym):
        return fake.get(sym, "未知")

    out = _fill_unknown_industry(rows, get_ind=getter)
    _must(out[0]["industry"] == "涂料油墨", out[0])
    _must(out[1]["industry"] == "计算机设备", "已有行业不得覆盖")


def test_industry_fetch_fail_stays_unknown():
    from lib.recap import _fill_unknown_industry

    def boom(_sym):
        raise RuntimeError("eastmoney down")

    out = _fill_unknown_industry(
        [{"sym": "sh603683", "name": "晶华新材", "industry": "未知", "px": 16.55}],
        get_ind=boom,
    )
    _must(out[0]["industry"] == "未知", out[0])


def test_overlay_live_exception_keeps_bar_rows():
    import lib.data as data_mod
    from lib.recap import _overlay_live_prices

    orig = data_mod.attach_live_prices

    def boom(rows, **_kw):
        raise RuntimeError("sina down")

    data_mod.attach_live_prices = boom
    try:
        rows = [{"sym": "sh603698", "name": "航天工程", "px": 24.13}]
        out = _overlay_live_prices(rows)
        _must(len(out) == 1 and out[0]["name"] == "航天工程", out)
        _must(out[0]["px"] == 24.13, out[0])
        _must(not out[0].get("live"), out[0])
    finally:
        data_mod.attach_live_prices = orig


def test_invalid_live_px_falls_back_to_bar():
    md = build_recap(
        {
            "date": "2026-09-04",
            "light": "红灯",
            "action": "空仓",
            "market_ok": False,
            "buy": [],
            "observe": [
                {
                    "sym": "sh603698",
                    "name": "航天工程",
                    "industry": "专业服务",
                    "menga": 99,
                    "px": 24.13,
                    "live": True,
                    "live_px": "bad",
                }
            ],
        }
    )
    _must("航天工程" in md, md)


def test_run_recap_falls_back_to_menga_when_cool_missing():
    import shutil
    import tempfile

    import lib.data as data_mod
    from lib import recap as recap_mod

    tmp = tempfile.mkdtemp(prefix="recap_fb_")
    orig_out = recap_mod.OUTPUT
    orig_attach = data_mod.attach_live_prices
    orig_reg = data_mod.market_regime
    orig_fill = recap_mod._fill_unknown_industry
    recap_mod.OUTPUT = tmp
    data_mod.attach_live_prices = lambda rows, **_k: list(rows)
    data_mod.market_regime = lambda live=False: {
        "last_date": "2026-09-04",
        "bar_date": "2026-09-04",
        "ret20": -3.12,
        "close": 4548.05,
        "above_ma20": False,
        "above_ma60": False,
        "light": "红灯",
    }
    recap_mod._fill_unknown_industry = lambda rows, **_k: list(rows)
    try:
        with open(os.path.join(tmp, "action_today.txt"), "w", encoding="utf-8") as f:
            f.write("action=空仓\nlight=红灯\nn=0\n")
        with open(os.path.join(tmp, "menga_today.csv"), "w", encoding="utf-8-sig") as f:
            f.write("sym,name,menga_pct,px\nsh603683,晶华新材,100,16.55\n")
        d = recap_mod.run_recap(cached=True)
        names = [x.get("name") for x in (d.get("observe") or [])]
        _must(d.get("ok") and d.get("action") == "空仓", d)
        _must("晶华新材" in names, "无冷静池时应回退 menga_today，实际=%r" % names)
    finally:
        recap_mod.OUTPUT = orig_out
        data_mod.attach_live_prices = orig_attach
        data_mod.market_regime = orig_reg
        recap_mod._fill_unknown_industry = orig_fill
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_recap_prefers_cool_pool_over_menga():
    """cached 复盘有冷静池时用它，即使 menga 更新——这是当前行为，不是新鲜截面。"""
    import shutil
    import tempfile

    import lib.data as data_mod
    from lib import recap as recap_mod

    tmp = tempfile.mkdtemp(prefix="recap_pref_")
    orig_out = recap_mod.OUTPUT
    orig_attach = data_mod.attach_live_prices
    orig_reg = data_mod.market_regime
    orig_fill = recap_mod._fill_unknown_industry
    recap_mod.OUTPUT = tmp
    data_mod.attach_live_prices = lambda rows, **_k: list(rows)
    data_mod.market_regime = lambda live=False: {
        "last_date": "2026-09-04",
        "bar_date": "2026-09-04",
        "ret20": -1,
        "close": 4500,
        "above_ma20": False,
        "above_ma60": False,
        "light": "红灯",
    }
    recap_mod._fill_unknown_industry = lambda rows, **_k: list(rows)
    try:
        with open(os.path.join(tmp, "action_today.txt"), "w", encoding="utf-8") as f:
            f.write("action=空仓\nlight=红灯\nn=0\n")
        with open(os.path.join(tmp, "cool_pool.csv"), "w", encoding="utf-8-sig") as f:
            f.write("sym,name,menga,px\nsh603698,航天工程,99.9,24.13\n")
        with open(os.path.join(tmp, "menga_today.csv"), "w", encoding="utf-8-sig") as f:
            f.write("sym,name,menga_pct,px\nsh603683,晶华新材,100,16.55\n")
        d = recap_mod.run_recap(cached=True)
        names = [x.get("name") for x in (d.get("observe") or [])]
        _must("航天工程" in names, names)
        _must("晶华新材" not in names, "有冷静池时不得改走 menga：%r" % names)
    finally:
        recap_mod.OUTPUT = orig_out
        data_mod.attach_live_prices = orig_attach
        data_mod.market_regime = orig_reg
        recap_mod._fill_unknown_industry = orig_fill
        shutil.rmtree(tmp, ignore_errors=True)


JINGQUAN = {
    "sym": "sz002885",
    "name": "京泉华",
    "industry": "消费电子",
    "menga": 99.0,
    "px": 31.54,
    "px_bar": 26.19,
    "live_px": 31.54,
    "live": True,
}


def test_cool_row_overlay_exception_uses_px_bar():
    """生产冷静池行：live 失败须用日K 26.19，不得用旧 live 31.54。"""
    import lib.data as data_mod
    from lib.recap import _overlay_live_prices, _px_under, _row_px

    orig = data_mod.attach_live_prices

    def boom(rows, **_k):
        raise RuntimeError("sina down")

    data_mod.attach_live_prices = boom
    try:
        out = _overlay_live_prices([dict(JINGQUAN)])
        _must(abs(float(_row_px(out[0])) - 26.19) < 1e-6, repr(out[0]))
        _must(_px_under(out[0]), "日K<30 须留下，实际=%r" % (out[0],))
        _must(not out[0].get("live"), repr(out[0]))
    finally:
        data_mod.attach_live_prices = orig


def test_attach_live_miss_uses_px_bar_not_stale_px():
    import lib.data as data_mod
    from lib.recap import _px_under, _row_px

    orig = data_mod.fetch_realtime_quotes
    data_mod.fetch_realtime_quotes = lambda _syms: {}
    try:
        out = data_mod.attach_live_prices([dict(JINGQUAN)])
        _must(abs(float(out[0].get("px_bar")) - 26.19) < 1e-6, out[0])
        _must(not out[0].get("live"), out[0])
        _must(abs(float(_row_px(out[0])) - 26.19) < 1e-6, out[0])
        _must(_px_under(out[0]), out[0])
    finally:
        data_mod.fetch_realtime_quotes = orig


def test_attach_live_second_pass_keeps_px_bar():
    import lib.data as data_mod

    orig = data_mod.fetch_realtime_quotes

    def fake(_syms):
        return {
            "sz002885": {
                "name": "京泉华",
                "price": 27.73,
                "chg_pct": 0.0,
                "date": "2026-09-04",
                "time": "15:00:00",
            }
        }

    data_mod.fetch_realtime_quotes = fake
    try:
        out = data_mod.attach_live_prices([dict(JINGQUAN)])
        _must(abs(float(out[0]["px_bar"]) - 26.19) < 1e-6, "日K不得被 live px 覆盖: %r" % out[0])
        _must(abs(float(out[0]["live_px"]) - 27.73) < 1e-6, out[0])
        _must(out[0].get("live") is True, out[0])
    finally:
        data_mod.fetch_realtime_quotes = orig


def test_cached_red_clears_stale_green_buy():
    import shutil
    import tempfile

    import lib.data as data_mod
    from lib import recap as recap_mod

    tmp = tempfile.mkdtemp(prefix="recap_red_")
    orig_out = recap_mod.OUTPUT
    orig_attach = data_mod.attach_live_prices
    orig_reg = data_mod.market_regime
    orig_fill = recap_mod._fill_unknown_industry
    recap_mod.OUTPUT = tmp
    data_mod.attach_live_prices = lambda rows, **_k: list(rows)
    data_mod.market_regime = lambda live=False: {
        "last_date": "2026-09-04",
        "bar_date": "2026-09-04",
        "ret20": -3,
        "close": 4548,
        "above_ma20": False,
        "above_ma60": False,
        "light": "红灯",
    }
    recap_mod._fill_unknown_industry = lambda rows, **_k: list(rows)
    try:
        with open(os.path.join(tmp, "action_today.txt"), "w", encoding="utf-8") as f:
            f.write("action=可关注候选\nlight=绿灯\nn=1\n")
        with open(os.path.join(tmp, "action_today.csv"), "w", encoding="utf-8-sig") as f:
            f.write("sym,name,qscore,px\nsh600519,贵州茅台,90,1680\n")
        d = recap_mod.run_recap(cached=True, with_observe=False)
        _must(d.get("action") == "空仓", d)
        _must(d.get("light") == "红灯", d)
        _must(not (d.get("buy") or []), "红灯须空买，实际=%r" % d.get("buy"))
    finally:
        recap_mod.OUTPUT = orig_out
        data_mod.attach_live_prices = orig_attach
        data_mod.market_regime = orig_reg
        recap_mod._fill_unknown_industry = orig_fill
        shutil.rmtree(tmp, ignore_errors=True)


def test_stale_cool_note_mentions_expired_members():
    import shutil
    import tempfile
    import time

    import lib.data as data_mod
    from lib import recap as recap_mod

    tmp = tempfile.mkdtemp(prefix="recap_stale_")
    orig_out = recap_mod.OUTPUT
    orig_attach = data_mod.attach_live_prices
    orig_reg = data_mod.market_regime
    orig_fill = recap_mod._fill_unknown_industry
    recap_mod.OUTPUT = tmp
    data_mod.attach_live_prices = lambda rows, **_k: list(rows)
    data_mod.market_regime = lambda live=False: {
        "last_date": "2026-09-04",
        "bar_date": "2026-09-04",
        "ret20": -1,
        "close": 4500,
        "above_ma20": False,
        "above_ma60": False,
        "light": "红灯",
    }
    recap_mod._fill_unknown_industry = lambda rows, **_k: list(rows)
    try:
        with open(os.path.join(tmp, "action_today.txt"), "w", encoding="utf-8") as f:
            f.write("action=空仓\nlight=红灯\nn=0\n")
        cool = os.path.join(tmp, "cool_pool.csv")
        menga = os.path.join(tmp, "menga_today.csv")
        with open(cool, "w", encoding="utf-8-sig") as f:
            f.write("sym,name,menga,px,px_bar,live,live_px\nsh603698,航天工程,99,24.13,24.13,False,\n")
        with open(menga, "w", encoding="utf-8-sig") as f:
            f.write("sym,name,menga_pct,px\nsh603683,晶华新材,100,16.55\n")
        old = time.time() - 86400 * 19
        os.utime(cool, (old, old))
        d = recap_mod.run_recap(cached=True)
        note = str(d.get("note") or "")
        _must("过期" in note or "成员截面" in note, note)
    finally:
        recap_mod.OUTPUT = orig_out
        data_mod.attach_live_prices = orig_attach
        data_mod.market_regime = orig_reg
        recap_mod._fill_unknown_industry = orig_fill
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_red_light_no_pad_and_no_recommend()
    test_green_industry_cap_two()
    test_youzi_and_position_slots()
    test_rows_from_csv_fills_placeholder_name()
    test_observe_keeps_only_px_under_30()
    test_observe_prefers_live_px_over_bar()
    test_fill_unknown_industry_for_cheap_rows()
    test_industry_fetch_fail_stays_unknown()
    test_overlay_live_exception_keeps_bar_rows()
    test_invalid_live_px_falls_back_to_bar()
    test_run_recap_falls_back_to_menga_when_cool_missing()
    test_run_recap_prefers_cool_pool_over_menga()
    test_cool_row_overlay_exception_uses_px_bar()
    test_attach_live_miss_uses_px_bar_not_stale_px()
    test_attach_live_second_pass_keeps_px_bar()
    test_cached_red_clears_stale_green_buy()
    test_stale_cool_note_mentions_expired_members()
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
