#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""估值谷峰 · 实时价与分位同口径回归（2026-11-17）：
病例：参考价换成盘中实时价，分位%/买入上限/买卖分档却仍按昨收，大跌大涨日两列对不上、分档错。
1) 快照日期 = 缓存末根 → 替换末根；更新 → 追加一根且截面日=快照日；更旧/停牌价0 → 不用；
2) 昨收在中间区、盘中跌破一谷上沿 → 分档改为买入观察，分位按实时价算；
3) 参考价 ≤ 买入上限 ⇔ 分档为一谷（同一个价，不再自相矛盾）；
4) scan_universe 用实时简称做 ST 校验（缓存旧名漏网）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from lib import gu_feng as gf


def _df(closes, start="2023-01-02"):
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=len(closes))]
    return pd.DataFrame({"date": dates, "close": np.asarray(closes, dtype=float)})


def _ramp(n=800):
    # 1..n 单调上涨后回落到中间：末根分位≈50%
    c = list(np.linspace(10, 30, n - 1)) + [20.0]
    return _df(c)


def test_close_with_live_rules():
    d = _ramp()
    dates, close = d["date"].tolist(), d["close"].to_numpy()
    last = dates[-1]
    c, asof = gf.close_with_live(dates, close, 15.0, last)
    assert len(c) == len(close) and c[-1] == 15.0 and asof == last
    c, asof = gf.close_with_live(dates, close, 15.0, "2099-01-01")
    assert len(c) == len(close) + 1 and c[-1] == 15.0 and asof == "2099-01-01"
    c, asof = gf.close_with_live(dates, close, 15.0, "2000-01-01")
    assert c is close and asof == last
    assert gf.live_quote({"price": 0, "date": last}) == (None, "")
    assert gf.live_quote({"price": "12.5", "date": last + " x"})[0] == 12.5


def test_live_drop_flips_zone():
    d = _ramp()
    base = gf.assess(d)
    assert base["zone"] == "mid", base
    a = gf.assess(d, live_px=11.0, live_date="2099-01-01")
    assert a["zone"] == "buy" and a["price"] == 11.0 and a["asof"] == "2099-01-01"
    assert a["val_pct"] < 0.1, a["val_pct"]
    sig = gf.score_symbol(d, "sh600000", live_px=11.0, live_date="2099-01-01")
    assert sig is not None and sig.side == "buy" and sig.price == 11.0
    assert sig.target_note == "现价已在一谷" and sig.target_buy == 11.0


def test_ref_price_consistent_with_zone():
    d = _ramp()
    for px in np.linspace(9, 32, 47):
        a = gf.assess(d, live_px=float(px), live_date=d["date"].iloc[-1])
        in_valley = a["levels"]["target_note"] == "现价已在一谷"
        assert in_valley == (a["zone"] == "buy"), (px, a["val_pct"], a["levels"])


def test_tie_at_ceiling_follows_zone():
    # 历史大量并列在 10.0：现价=上限=10.0，但分位(≤计数)>30% → 观望，备注不得说已在一谷
    closes = [10.0] * 300 + list(np.linspace(10.5, 20, 500)) + [10.0]
    a = gf.assess(_df(closes))
    assert a["levels"]["buy_ceiling"] == 10.0 and a["val_pct"] > 0.3, a
    assert a["zone"] == "mid" and a["levels"]["target_note"] != "现价已在一谷", a


def test_scan_uses_live_name_for_st():
    from lib import data as dmod

    d = _ramp()
    orig = (dmod._read_fund_raw, dmod.load_kline_csv, dmod.resolve_name)
    try:
        dmod._read_fund_raw = lambda: {}
        dmod.load_kline_csv = lambda sym, min_bars=280: d
        dmod.resolve_name = lambda sym, seed, fund: "旧名股份"
        live = {
            "sh600001": {"name": "ST旧名", "price": 11.0, "date": "2099-01-01"},
            "sh600002": {"name": "好名字", "price": 11.0, "date": "2099-01-01"},
        }
        buys, sells = gf.scan_universe(["sh600001", "sh600002"], live=live, progress_every=0)
    finally:
        dmod._read_fund_raw, dmod.load_kline_csv, dmod.resolve_name = orig
    assert [s.sym for s in buys] == ["sh600002"] and not sells, [s.sym for s in buys]
    assert buys[0].name == "好名字" and buys[0].date == "2099-01-01"


def main() -> int:
    tests = [
        test_close_with_live_rules,
        test_live_drop_flips_zone,
        test_ref_price_consistent_with_zone,
        test_tie_at_ceiling_follows_zone,
        test_scan_uses_live_name_for_st,
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
