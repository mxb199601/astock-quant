#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据新鲜度回归（2026-08-19 审计定案的四处修复）：
1) fetch_kline 全源失败回退旧缓存时：不得重写文件伪装 mtime，且须标记 stale_fallback；
2) refresh() 不得把「回退旧缓存」计成 kline_ok（假成功 ok=3017 fail=0 病例）；
3) ensure_kline_fresh 只对滞后标的补尾；
4) MENGα 扫描 excess20 用个股自身截止日的指数窗口，payload 报告截面日与滞后数。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd


def _bdates(n: int, start: str = "2025-01-06") -> list:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def _mk_kline(fp: str, dates: list, closes: list) -> None:
    n = len(dates)
    df = pd.DataFrame({
        "date": dates,
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "vol": [1_000_000.0] * n,
    })
    df.to_csv(fp, index=False)


def _rw_closes(n: int, seed: int, base: float = 20.0) -> list:
    rng = np.random.RandomState(seed)
    steps = rng.normal(0, 0.01, size=n)
    return list(base * np.exp(np.cumsum(steps)))


class _Env:
    """临时缓存目录 + lib.data 路径/网络补丁，测试后还原。"""

    def __init__(self):
        import lib.data as data
        self.data = data
        self.tmp = tempfile.mkdtemp(prefix="freshness_")
        self.saved = {
            "CACHE": data.CACHE, "FUND_PATH": data.FUND_PATH, "META_PATH": data.META_PATH,
            "curl": data.curl, "fetch_kline_eastmoney": data.fetch_kline_eastmoney,
        }
        data.CACHE = self.tmp
        data.FUND_PATH = os.path.join(self.tmp, "_fundamentals.json")
        data.META_PATH = os.path.join(self.tmp, "_meta.json")

    def offline(self):
        def _dead(*a, **k):
            raise RuntimeError("network down (test)")
        self.data.curl = _dead
        self.data.fetch_kline_eastmoney = lambda *a, **k: None

    def close(self):
        for k, v in self.saved.items():
            setattr(self.data, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_fetch_kline_offline_fallback_marked_and_no_rewrite():
    """全源失败强刷：返回旧缓存须带 stale_fallback 标记，且不重写 csv（内容与 mtime 均不变）。"""
    env = _Env()
    try:
        env.offline()
        fp = os.path.join(env.tmp, "sz002080.csv")
        _mk_kline(fp, _bdates(300), _rw_closes(300, seed=1))
        before_bytes = open(fp, "rb").read()
        before_mtime = os.stat(fp).st_mtime_ns
        time.sleep(0.02)

        df = env.data.fetch_kline("sz002080", force=True, min_bars=200)

        assert df is not None and len(df) == 300, "应回退旧缓存而非返回 None"
        assert df.attrs.get("stale_fallback") is True, "回退旧缓存必须标记 stale_fallback"
        assert open(fp, "rb").read() == before_bytes, "全源失败不得改写缓存内容"
        assert os.stat(fp).st_mtime_ns == before_mtime, "全源失败不得刷新 mtime 伪装新鲜"
    finally:
        env.close()


def test_refresh_counts_stale_fallback_not_ok():
    """refresh()：回退旧缓存的标的计入 kline_stale，不计 kline_ok；meta 如实落盘。"""
    env = _Env()
    try:
        env.offline()
        _mk_kline(os.path.join(env.tmp, "sz002080.csv"), _bdates(300), _rw_closes(300, seed=2))
        json.dump({"sz002080": {"name": "中材科技"}}, open(env.data.FUND_PATH, "w"))

        stats = env.data.refresh(symbols=["sz002080"], fund=False, force_kline=True, sleep=0)

        assert stats["kline_ok"] == 0, f"回退旧缓存不得计 ok，实得 {stats['kline_ok']}"
        assert stats.get("kline_stale", 0) == 1, f"应计 kline_stale=1，实得 {stats.get('kline_stale')}"
        meta = json.load(open(env.data.META_PATH, encoding="utf-8"))
        assert meta.get("last_refresh_ok") == 0, "meta 不得报假成功"
        assert meta.get("last_refresh_stale") == 1, "meta 须记录 stale 数"
    finally:
        env.close()


def test_ensure_kline_fresh_refreshes_only_stale():
    """ensure_kline_fresh：仅对最后 K 线早于 ref_date 的标的调 fetch_kline 补尾。"""
    env = _Env()
    try:
        dates = _bdates(300)
        ref = dates[-1]
        _mk_kline(os.path.join(env.tmp, "sz002001.csv"), dates, _rw_closes(300, seed=3))          # 新鲜
        _mk_kline(os.path.join(env.tmp, "sz002002.csv"), dates[:-10], _rw_closes(290, seed=4))    # 滞后10日
        calls = []
        orig = env.data.fetch_kline
        env.data.fetch_kline = lambda sym, *a, **k: calls.append(sym) or None
        try:
            stats = env.data.ensure_kline_fresh(["sz002001", "sz002002"], ref_date=ref, sleep=0)
        finally:
            env.data.fetch_kline = orig

        assert calls == ["sz002002"], f"只应补尾滞后标的，实调 {calls}"
        assert stats["checked"] == 2 and stats["stale"] == 1, stats
        assert stats["refreshed"] == 0 and stats["still_stale"] == 1, stats
    finally:
        env.close()


def test_idx20_lookup_uses_asof_window():
    """_idx20_lookup：按「≤个股截止日」的指数窗口算 idx20，不吃未来指数K线。"""
    from lib.own_alpha import _idx20_lookup

    dates = _bdates(40)
    closes = [100.0] * 30 + [100.0 + 2.0 * (i - 29) for i in range(30, 40)]  # 末10根爬到120
    idx_df = pd.DataFrame({"date": dates, "close": closes})
    get = _idx20_lookup(idx_df)

    assert abs(get(dates[-1]) - 20.0) < 1e-9, f"末日窗口应为 (120/100-1)*100=20，实得 {get(dates[-1])}"
    assert abs(get(dates[34]) - 10.0) < 1e-9, f"d34 窗口应为 (110/100-1)*100=10，实得 {get(dates[34])}"
    assert get(dates[10]) == 0.0, "不足 21 根时应返回 0"
    assert abs(get("2024-01-01")) < 1e-9, "早于全部指数日期时应返回 0"


def test_scan_reports_asof_and_aligns_excess20():
    """scan_own_alpha 集成：payload 带截面日/滞后数，滞后股 excess20 用其自身截止日的指数窗口。"""
    import lib.data as data
    import lib.own_alpha as oa

    env = _Env()
    out_tmp = tempfile.mkdtemp(prefix="menga_out_")
    dates = _bdates(320)
    d_full, d_stale = dates[-1], dates[-11]

    # 指数：前 310 根恒 100，末 10 根每根 +2 → 末日 120；使两个截止日的 idx20 相差 20pp
    idx_closes = [100.0] * 310 + [100.0 + 2.0 * (i - 309) for i in range(310, 320)]
    _mk_kline(os.path.join(env.tmp, "sh000300.csv"), dates, idx_closes)

    stale_sym = "sz002001"
    stale_closes = _rw_closes(310, seed=100)
    _mk_kline(os.path.join(env.tmp, f"{stale_sym}.csv"), dates[:-10], stale_closes)
    for i in range(59):
        sym = f"sz{2100 + i:06d}" if i % 2 else f"sh{600100 + i:06d}"
        _mk_kline(os.path.join(env.tmp, f"{sym}.csv"), dates[20:], _rw_closes(300, seed=200 + i))

    em_calls = []
    saved = {
        "fetch_kline_eastmoney": data.fetch_kline_eastmoney,
        "load_fundamentals": data.load_fundamentals,
        "oa.load_fundamentals": oa.load_fundamentals,
        "oa.market_regime": oa.market_regime,
        "oa.attach_live_prices": oa.attach_live_prices,
        "oa.OUTPUT": oa.OUTPUT,
    }
    data.fetch_kline_eastmoney = lambda sym, *a, **k: em_calls.append(sym) or None
    data.load_fundamentals = lambda *a, **k: {}
    oa.load_fundamentals = lambda *a, **k: {}
    oa.market_regime = lambda *a, **k: {"ok": True, "light": "红灯"}
    oa.attach_live_prices = lambda rows, **k: rows
    oa.OUTPUT = out_tmp
    try:
        res = oa.scan_own_alpha(top_n=40, refresh_stale=True)

        assert res.get("ok"), f"扫描失败: {res}"
        assert res.get("asof") == d_full, f"asof 应为 {d_full}，实得 {res.get('asof')}"
        assert res.get("stale_n") == 1, f"stale_n 应为 1，实得 {res.get('stale_n')}"
        assert "截面日" in (res.get("note") or ""), "note 须标注截面日"
        assert stale_sym in em_calls, "扫描前应对滞后标的尝试补尾"

        out = pd.read_csv(os.path.join(out_tmp, "menga_today.csv"))
        row = out[out["sym"] == stale_sym].iloc[0]
        px, p20 = stale_closes[-1], stale_closes[-21]
        ret20 = (px / p20 - 1.0) * 100.0
        idx20_asof_stale = (idx_closes[309] / idx_closes[289] - 1.0) * 100.0   # = 0.0
        idx20_latest = (idx_closes[319] / idx_closes[299] - 1.0) * 100.0       # = 20.0
        want = ret20 - idx20_asof_stale
        wrong = ret20 - idx20_latest
        assert abs(float(row["excess20"]) - want) < 5e-4, \
            f"滞后股 excess20 应按其截止日指数窗口={want:.4f}，实得 {row['excess20']}"
        assert abs(float(row["excess20"]) - wrong) > 1.0, "不得混用指数最新窗口"
    finally:
        for k, v in saved.items():
            if k.startswith("oa."):
                setattr(oa, k[3:], v)
            else:
                setattr(data, k, v)
        env.close()
        shutil.rmtree(out_tmp, ignore_errors=True)


def test_non_force_fallback_marks_stale_and_refresh_counts_it():
    """非 force 路径滞后 + 补尾源不可达：须标 stale_fallback，refresh 计 kline_stale。"""
    env = _Env()
    try:
        env.offline()
        _mk_kline(os.path.join(env.tmp, "sz002080.csv"), _bdates(300), _rw_closes(300, seed=5))

        df = env.data.fetch_kline("sz002080", force=False, min_bars=200)
        assert df is not None and df.attrs.get("stale_fallback") is True, \
            "非 force 补尾失败也必须标 stale_fallback"

        stats = env.data.refresh(symbols=["sz002080"], fund=False, force_kline=False, sleep=0)
        assert stats["kline_ok"] == 0 and stats["kline_stale"] == 1, \
            f"非 force 回退不得计 ok，实得 ok={stats['kline_ok']} stale={stats['kline_stale']}"
    finally:
        env.close()


def test_tail_patch_without_new_date_does_not_rewrite():
    """补尾未带来新日期（停牌股）：不得重写文件刷新 mtime。"""
    env = _Env()
    try:
        dates = _bdates(300)
        fp = os.path.join(env.tmp, "sz002080.csv")
        closes = _rw_closes(300, seed=6)
        _mk_kline(fp, dates, closes)
        tail = pd.DataFrame({
            "date": dates[-30:], "open": closes[-30:], "high": closes[-30:],
            "low": closes[-30:], "close": closes[-30:], "vol": [1_000_000.0] * 30,
        })
        env.data.fetch_kline_eastmoney = lambda *a, **k: tail
        before_mtime = os.stat(fp).st_mtime_ns
        time.sleep(0.02)

        df = env.data.fetch_kline("sz002080", force=False, min_bars=200)

        assert df is not None and len(df) == 300
        assert os.stat(fp).st_mtime_ns == before_mtime, "补尾没带来新日期就不该重写文件"
        assert df.attrs.get("stale_fallback") is True, "仍是滞后数据，须标记"
    finally:
        env.close()


def test_deep_stale_falls_back_to_full_refetch():
    """滞后超过补尾窗口（复牌股）：改走全量重拉，不拼出带空洞的K线。"""
    env = _Env()
    try:
        dates = _bdates(360)
        fp = os.path.join(env.tmp, "sz002080.csv")
        closes = _rw_closes(360, seed=7)
        _mk_kline(fp, dates[:300], closes[:300])          # 缓存停在第300根
        seen = {"lmt": []}

        def _em(sym, lmt=30, **k):
            seen["lmt"].append(lmt)
            d, c = dates[-lmt:], closes[-lmt:]
            return pd.DataFrame({
                "date": d, "open": c, "high": c, "low": c, "close": c,
                "vol": [1e6] * len(d),
            })

        env.data.fetch_kline_eastmoney = _em
        env.data.curl = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no sina"))

        df = env.data.fetch_kline("sz002080", force=False, min_bars=200)

        assert df is not None, "应返回数据"
        assert max(seen["lmt"]) >= 120, f"深度滞后须用大窗口重拉，实用 lmt={seen['lmt']}"
        d = [str(x) for x in df["date"].tolist()]
        gap = set(dates[:len(dates)]) - set(d)
        assert not any(dates[300] <= g <= dates[-1] for g in gap), f"拼接后不得留空洞: {sorted(gap)[:3]}"
    finally:
        env.close()


def test_list_cached_symbols_actually_drops_st():
    """ST 剔除不得依赖已剔过 ST 的 fund——否则 ST 股因取不到名称而漏网。"""
    env = _Env()
    try:
        dates, closes = _bdates(300), _rw_closes(300, seed=11)
        _mk_kline(os.path.join(env.tmp, "sh600530.csv"), dates, closes)   # ST交昂
        _mk_kline(os.path.join(env.tmp, "sh600000.csv"), dates, closes)   # 正常股
        json.dump(
            {"sh600530": {"name": "ST交昂"}, "sh600000": {"name": "浦发银行"}},
            open(env.data.FUND_PATH, "w", encoding="utf-8"), ensure_ascii=False,
        )

        got = env.data.list_cached_symbols(mainboard_only=True, drop_st=True)

        assert "sh600530" not in got, f"ST 股必须被剔除，实得 {got}"
        assert "sh600000" in got, f"正常股不该被误剔，实得 {got}"
    finally:
        env.close()


def test_scan_excludes_st_even_when_fund_lacks_name():
    """MENGα 榜单不得出现 ST 股（截图病例：ST交昂 曾排到第2）。"""
    import lib.data as data
    import lib.own_alpha as oa

    env = _Env()
    out_tmp = tempfile.mkdtemp(prefix="menga_st_")
    dates = _bdates(320)
    _mk_kline(os.path.join(env.tmp, "sh000300.csv"), dates, [100.0] * 320)
    # ST 股走势最"冷"（一路跌），必然排进 MENGα 榜首——若过滤失效就会被抓到
    _mk_kline(os.path.join(env.tmp, "sh600530.csv"), dates,
              list(np.linspace(30.0, 8.0, 320)))
    for i in range(60):
        sym = f"sz{2100 + i:06d}" if i % 2 else f"sh{600100 + i:06d}"
        _mk_kline(os.path.join(env.tmp, f"{sym}.csv"), dates, _rw_closes(320, seed=500 + i))
    json.dump({"sh600530": {"name": "ST交昂"}},
              open(env.data.FUND_PATH, "w", encoding="utf-8"), ensure_ascii=False)

    saved = {
        "oa.market_regime": oa.market_regime,
        "oa.attach_live_prices": oa.attach_live_prices,
        "oa.OUTPUT": oa.OUTPUT,
    }
    oa.market_regime = lambda *a, **k: {"ok": True, "light": "红灯"}
    oa.attach_live_prices = lambda rows, **k: rows
    oa.OUTPUT = out_tmp
    try:
        res = oa.scan_own_alpha(top_n=60)
        assert res.get("ok"), f"扫描应成功: {res}"
        syms = [r["sym"] for r in res.get("rows", [])]
        assert "sh600530" not in syms, f"ST 股混进榜单: {syms[:5]}"
        out = pd.read_csv(os.path.join(out_tmp, "menga_today.csv"))
        assert "sh600530" not in set(out["sym"]), "ST 股混进导出 CSV"
    finally:
        for k, v in saved.items():
            setattr(oa, k[3:], v)
        env.close()
        shutil.rmtree(out_tmp, ignore_errors=True)


def test_kline_last_date_never_returns_partial_fragment():
    """尾读窗口落在超长行中间时，宁可返回 None，绝不返回会被误判为新鲜的数字片段。"""
    env = _Env()
    try:
        fp = os.path.join(env.tmp, "sz002080.csv")
        with open(fp, "w") as f:
            f.write("date,open,high,low,close,vol\n")
            f.write("2026-01-05,1,1,1,1,1\n")
            f.write("2026-08-05,1,1,1,1," + "9" * 900 + "\n")  # 末行远超尾读窗口

        got = env.data.kline_last_date("sz002080")
        assert got is None or (len(got) == 10 and got[4] == "-"), \
            f"不得返回片段 {got!r}——'9999' 之类字典序会大于日期而误判新鲜"

        # 正常文件仍须准确
        _mk_kline(os.path.join(env.tmp, "sz002081.csv"), _bdates(10), _rw_closes(10, seed=9))
        assert env.data.kline_last_date("sz002081") == _bdates(10)[-1]
        assert env.data.kline_last_date("sz009999") is None, "缺文件应返回 None"
    finally:
        env.close()


def test_scan_is_read_only_by_default():
    """默认扫描纯本地只读：不打网络；滞后时 note 须给出刷新提示。"""
    import lib.data as data
    import lib.own_alpha as oa

    env = _Env()
    out_tmp = tempfile.mkdtemp(prefix="menga_out2_")
    dates = _bdates(320)
    _mk_kline(os.path.join(env.tmp, "sh000300.csv"), dates, [100.0] * 320)
    _mk_kline(os.path.join(env.tmp, "sz002001.csv"), dates[:-10], _rw_closes(310, seed=300))
    for i in range(59):
        sym = f"sz{2100 + i:06d}" if i % 2 else f"sh{600100 + i:06d}"
        _mk_kline(os.path.join(env.tmp, f"{sym}.csv"), dates[20:], _rw_closes(300, seed=400 + i))

    net = []
    saved = {
        "fetch_kline_eastmoney": data.fetch_kline_eastmoney, "curl": data.curl,
        "load_fundamentals": data.load_fundamentals,
        "oa.load_fundamentals": oa.load_fundamentals, "oa.market_regime": oa.market_regime,
        "oa.attach_live_prices": oa.attach_live_prices, "oa.OUTPUT": oa.OUTPUT,
    }
    data.fetch_kline_eastmoney = lambda sym, *a, **k: net.append(sym) or None
    data.curl = lambda *a, **k: net.append("curl") or ""
    data.load_fundamentals = lambda *a, **k: {}
    oa.load_fundamentals = lambda *a, **k: {}
    oa.market_regime = lambda *a, **k: {"ok": True, "light": "红灯"}
    oa.attach_live_prices = lambda rows, **k: rows
    oa.OUTPUT = out_tmp
    try:
        res = oa.scan_own_alpha(top_n=40)

        assert res.get("ok"), f"扫描应成功: {res}"
        assert net == [], f"默认扫描不得打网络，实调 {net[:5]}"
        assert res.get("stale_n") == 1, f"仍须如实报告滞后数，实得 {res.get('stale_n')}"
        assert "refresh" in (res.get("note") or ""), "滞后时 note 须提示跑 refresh"
    finally:
        for k, v in saved.items():
            setattr(oa, k[3:], v) if k.startswith("oa.") else setattr(data, k, v)
        env.close()
        shutil.rmtree(out_tmp, ignore_errors=True)


def main() -> int:
    tests = [
        test_fetch_kline_offline_fallback_marked_and_no_rewrite,
        test_refresh_counts_stale_fallback_not_ok,
        test_non_force_fallback_marks_stale_and_refresh_counts_it,
        test_tail_patch_without_new_date_does_not_rewrite,
        test_deep_stale_falls_back_to_full_refetch,
        test_list_cached_symbols_actually_drops_st,
        test_scan_excludes_st_even_when_fund_lacks_name,
        test_kline_last_date_never_returns_partial_fragment,
        test_ensure_kline_fresh_refreshes_only_stale,
        test_idx20_lookup_uses_asof_window,
        test_scan_reports_asof_and_aligns_excess20,
        test_scan_is_read_only_by_default,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
