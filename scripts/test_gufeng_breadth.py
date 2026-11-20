#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""估值谷峰 · 全市场一谷占比 + 深谷慎接（2026-11-17）：
回放结论：一谷胜率大半来自择时（占比<20% 时一谷票跑输同日），深谷≤10% 不如 10–30%。
1) breadth_info 分母=能算出分位的票数（含中间区），分档边界 20%/40%；
2) caution_of 只标买侧 ≤10%；
3) meta 文件随 gufeng_all_*.csv 落盘并能读回；
4) scan_universe(stats=…) 的 n_scored 包含中间区、不含历史不足的票。
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib import gu_feng as gf


def _df(closes):
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2023-01-02", periods=len(closes))]
    return pd.DataFrame({"date": dates, "close": np.asarray(closes, dtype=float)})


def test_breadth_tiers():
    assert gf.breadth_info(199, 1000)["tier"] == "low"
    assert gf.breadth_info(200, 1000)["tier"] == "mid"
    assert gf.breadth_info(399, 1000)["tier"] == "mid"
    b = gf.breadth_info(400, 1000)
    assert b["tier"] == "high" and b["share"] == 0.4 and "十年" in b["text"]
    assert gf.breadth_info(5, 0) is None and gf.breadth_info(None, 10) is None


def test_caution_only_deep_buy():
    assert gf.caution_of(True, 0.10) == "深谷·慎接"
    assert gf.caution_of(True, 0.1001) is None
    assert gf.caution_of(False, 0.05) is None
    assert gf.caution_of(True, None) is None


def test_meta_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "gufeng_all_20261117_150000.csv")
        open(p, "w").close()
        mp = gf.write_meta(p, {"n_scored": 3, "breadth": gf.breadth_info(2, 3)})
        assert os.path.basename(mp) == "gufeng_meta_20261117_150000.json"
        assert gf.read_meta(p)["breadth"]["tier"] == "high"
        assert gf.read_meta(os.path.join(d, "gufeng_all_x.csv")) is None


def test_scan_counts_mid_in_denominator():
    from lib import data as dmod

    up = list(np.linspace(10, 30, 799))
    frames = {
        "sh600001": _df(up + [11.0]),   # 一谷
        "sh600002": _df(up + [20.0]),   # 中间
        "sh600003": _df(up + [29.0]),   # 一峰
        "sh600004": _df(up[:300]),      # 历史不足，不计
    }
    orig = (dmod._read_fund_raw, dmod.load_kline_csv, dmod.resolve_name)
    try:
        dmod._read_fund_raw = lambda: {}
        dmod.load_kline_csv = lambda sym, min_bars=280: frames.get(sym)
        dmod.resolve_name = lambda sym, seed, fund: "测试股份"
        st = {}
        buys, sells = gf.scan_universe(list(frames), stats=st, progress_every=0)
    finally:
        dmod._read_fund_raw, dmod.load_kline_csv, dmod.resolve_name = orig
    assert st["n_scored"] == 3, st
    assert [s.sym for s in buys] == ["sh600001"] and [s.sym for s in sells] == ["sh600003"]
    rows = gf.signals_to_rows(buys)
    assert rows[0]["caution"] == "深谷·慎接", rows[0]


def test_deep_divergence_not_strengthened():
    # 回放：深谷∩底背离 60 日平均跑输同日约 2%，不应与「深谷·慎接」同时挂「强化买入」
    assert gf.buy_label(True, 0.05) == "买入观察"
    assert gf.buy_label(True, 0.20) == "强化买入"
    assert gf.buy_label(False, 0.20) == "买入观察"
    deep = gf.GuFengSignal("sh600001", "深", "buy", "2026-11-17", 1.0, 0.05, None, None, "m", divergence=True, div_ago=1)
    mid = gf.GuFengSignal("sh600002", "中", "buy", "2026-11-17", 1.0, 0.20, None, None, "m", divergence=True, div_ago=1)
    r_deep, r_mid = gf.signals_to_rows([deep, mid])
    assert r_deep["side"] == "买入观察" and r_deep["caution"] == "深谷·慎接" and r_deep["divergence"] is True
    assert r_mid["side"] == "强化买入" and r_mid["caution"] is None
    # CSV 回读行只有 val_pct_pct（×100）
    assert gf.row_val_pct({"val_pct_pct": "5.0"}) == 0.05
    assert gf.buy_label(True, gf.row_val_pct({"val_pct_pct": "25"})) == "强化买入"


def main() -> int:
    tests = [
        test_deep_divergence_not_strengthened,
        test_breadth_tiers,
        test_caution_only_deep_buy,
        test_meta_roundtrip,
        test_scan_counts_mid_in_denominator,
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
