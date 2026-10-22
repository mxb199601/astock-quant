#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""名称缺失回归：基本面缺条目时不得把代码当名称展示。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_attach_live_prices_fills_placeholder_name():
    """截图病例：sh600901 在基本面缺失时，实时行情名应写回 name。"""
    import lib.data as data

    orig = data.fetch_realtime_quotes

    def fake(syms):
        return {
            "sh600901": {
                "name": "江苏金租",
                "price": 6.67,
                "chg_pct": 0.91,
                "date": "2026-08-18",
                "time": "10:19:09",
            }
        }

    data.fetch_realtime_quotes = fake
    try:
        out = data.attach_live_prices(
            [{"sym": "sh600901", "name": "sh600901", "px": 6.51}]
        )
        assert out[0]["name"] == "江苏金租", out[0]
        # 已有中文名不得被空行情冲掉
        out2 = data.attach_live_prices(
            [{"sym": "sh603668", "name": "天马科技", "px": 10.92}]
        )
        assert out2[0]["name"] == "天马科技", out2[0]
    finally:
        data.fetch_realtime_quotes = orig


def test_force_refresh_merges_not_clobbers():
    """新浪分页只拉回一截时，不得覆盖已有名称表。"""
    import lib.data as data

    tmp = tempfile.mkdtemp(prefix="fund_")
    fund_path = os.path.join(tmp, "_fundamentals.json")
    existing = {
        "sh600901": {"name": "江苏金租", "pe": 1},
        "sh600295": {"name": "鄂尔多斯", "pe": 1},
    }
    json.dump(existing, open(fund_path, "w", encoding="utf-8"), ensure_ascii=False)

    orig_path = data.FUND_PATH
    orig_curl = data.curl
    orig_meta = data._write_meta

    def fake_curl(url, timeout=15, referer=""):
        if "page=1" in url:
            return json.dumps(
                [
                    {
                        "symbol": "sh603668",
                        "name": "天马科技",
                        "per": 1,
                        "pb": 1,
                        "turnoverratio": 1,
                        "nmc": 1,
                        "changepercent": 0,
                    }
                ]
            )
        return "[]"

    data.FUND_PATH = fund_path
    data.curl = fake_curl
    data._write_meta = lambda **kw: None
    try:
        got = data.load_fundamentals(force=True, maxpage=3)
        assert got.get("sh600901", {}).get("name") == "江苏金租", got
        assert got.get("sh600295", {}).get("name") == "鄂尔多斯", got
        assert got.get("sh603668", {}).get("name") == "天马科技", got
    finally:
        data.FUND_PATH = orig_path
        data.curl = orig_curl
        data._write_meta = orig_meta


def test_fill_row_names_replaces_code_placeholder():
    """复盘/冷静池 CSV 把代码当名称时，须用基本面中文名写回。"""
    from lib.data import fill_row_names

    out = fill_row_names(
        [{"sym": "sz001309", "name": "sz001309", "px": 441.86}],
        fund={"sz001309": {"name": "德明利"}},
    )
    assert out[0]["name"] == "德明利", out[0]
    assert out[0]["sym"] == "sz001309"
    # 已有中文名不得被对照表覆盖
    out2 = fill_row_names(
        [{"sym": "sh603683", "name": "晶华新材"}],
        fund={"sh603683": {"name": "别的名字"}},
    )
    assert out2[0]["name"] == "晶华新材", out2[0]
    # 中文列名 + 裸代码
    out3 = fill_row_names(
        [{"代码": "002080", "名称": "sz002080"}],
        fund={"sz002080": {"name": "中材科技"}},
    )
    assert out3[0]["sym"] == "sz002080", out3[0]
    assert out3[0]["name"] == "中材科技", out3[0]


def main() -> int:
    tests = [
        test_attach_live_prices_fills_placeholder_name,
        test_force_refresh_merges_not_clobbers,
        test_fill_row_names_replaces_code_placeholder,
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
