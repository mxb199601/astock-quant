#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化研究系统 — 本地 Web 界面（标准库，零额外依赖）

启动:
  python app.py
  浏览器打开 http://127.0.0.1:8765

研究用，非交易终端，不荐股。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

HOST = "127.0.0.1"
PORT = 8765

# 扫盘缓存（避免每次点页面都重算 3000 只）
_SCAN_CACHE = {"data": None, "lock": threading.Lock()}


def _json(handler: BaseHTTPRequestHandler, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _html(handler: BaseHTTPRequestHandler, html: str, code=200):
    body = html.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def api_status():
    from lib.data import list_cached_symbols, load_fundamentals, market_regime
    from lib.factors import index_low_vol_today
    from lib.paths import CACHE

    r = market_regime(live=True)
    lv = index_low_vol_today()
    return {
        "ok": True,
        "regime": r,
        "mainboard": len(list_cached_symbols(mainboard_only=True)),
        "fund": len(load_fundamentals(False)),
        "cache": CACHE,
        "idx_low_vol": lv.get("low_vol"),
        "idx_vol": lv.get("vol"),
        "idx_vol_thr": lv.get("thr"),
        "live": r.get("live"),
        "disclaimer": "研究工具，不构成投资建议。红灯空仓。盘中用实时价；日K多收盘后更新。",
    }


def api_scan(force=False):
    with _SCAN_CACHE["lock"]:
        if _SCAN_CACHE["data"] is not None and not force:
            d = dict(_SCAN_CACHE["data"])
            d["cached"] = True
            return d
        from lib.scan import run_structure_scan

        data = run_structure_scan()
        data["cached"] = False
        _SCAN_CACHE["data"] = data
        return data


def api_score(codes, mode="strict", require_low_vol=False):
    from lib.scan import score_codes

    codes = [c.strip() for c in codes if c.strip()]
    if not codes:
        return {"ok": False, "error": "请提供代码，如 600519,002371"}
    # score_codes 目前只传 mode；低波经 factors.score_one 需 scan 支持时再透传
    return {
        "ok": True,
        "mode": mode,
        "require_low_vol": bool(require_low_vol),
        "results": score_codes(codes, mode=mode),
    }


def api_forecast(code: str, use_llm: bool = False):
    from lib.forecast import forecast_stock

    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "请提供代码"}
    # 默认：无 LLM + 不用行业拉网（快、稳）；需要行业可 ?industry=1
    return forecast_stock(
        code, use_llm=use_llm, top_k=40, use_industry=False,
    )


def api_joint():
    from lib.joint import run_joint_signals

    return run_joint_signals(use_llm=False)


def api_patterns(pattern=None):
    from lib.pattern_scan import run_pattern_scan

    return run_pattern_scan(pattern=pattern or None)


def api_github_formulas():
    """主题池上的三套 GitHub 观察公式。不进 action。"""
    from lib.data import load_kline_csv
    from lib.formulas import FORMULA_REGISTRY, evaluate_today
    from lib.themes import THEME_POOL

    keys = ("rsrs", "kdj_cross", "oversold_rebound")
    idx = load_kline_csv("sh000300", min_bars=100)
    if idx is None:
        return {"ok": False, "error": "缺少 sh000300"}
    ic = idx["close"].astype(float)
    above = bool(ic.iloc[-1] > ic.rolling(20).mean().iloc[-1]) if len(ic) >= 20 else False
    rows = []
    for sym, meta in THEME_POOL.items():
        name = meta[0]
        theme = meta[1] if len(meta) > 1 else ""
        df = load_kline_csv(sym, min_bars=30)
        if df is None or len(df) < 30:
            continue
        flags = evaluate_today(sym, df, idx)
        hits = [k for k in keys if flags.get(k)]
        if not hits:
            continue
        rows.append({
            "sym": sym,
            "name": name,
            "theme": theme,
            "px": round(float(df["close"].iloc[-1]), 2),
            "hits": hits,
        })
    catalog = [
        {"id": k, "name": FORMULA_REGISTRY[k]["name"], "desc": FORMULA_REGISTRY[k]["desc"]}
        for k in keys
    ]
    return {
        "ok": True,
        "index_above_ma20": above,
        "scanned": len(THEME_POOL),
        "catalog": catalog,
        "rows": rows,
        "note": "观察层，不进今日操作。这三套都要求大盘站上 MA20，红灯时名单为空是纪律，不是漏扫。",
        "disclaimer": "研究工具，不构成投资建议。",
    }


_YAOGU_CACHE = {"data": None, "ts": 0, "top": 50, "lock": threading.Lock(), "disk_used": False}


def _yaogu_from_last_csv(top=50):
    """进程刚起来时用上次扫描 CSV，并把代码占位名换成中文。"""
    import csv
    from lib.data import attach_live_prices, is_placeholder_name, load_fundamentals, to_sym
    from lib.paths import OUTPUT

    fp = os.path.join(OUTPUT, "yaogu_prelaunch.csv")
    if not os.path.exists(fp):
        return None
    fund = load_fundamentals(False)
    rows = []
    with open(fp, encoding="utf-8-sig") as f:
        for rec in csv.DictReader(f):
            sym = to_sym(str(rec.get("代码") or ""))
            if not sym:
                continue
            name = rec.get("名称") or ""
            fn = (fund.get(sym) or {}).get("name") or ""
            if fn and not is_placeholder_name(fn, sym):
                name = fn
            try:
                dist = float(rec.get("距20日低%") or 0) / 100.0
            except Exception:
                dist = 0.0
            pats = [x for x in str(rec.get("形态") or "").split("、") if x]
            rows.append(
                {
                    "sym": sym,
                    "name": name,
                    "yaogu_score": rec.get("妖股分"),
                    "px": rec.get("现价"),
                    "rsi": rec.get("RSI"),
                    "bias20": rec.get("距MA20%"),
                    "ret20": rec.get("20日%"),
                    "dist_low20": dist,
                    "patterns": pats,
                }
            )
            if len(rows) >= int(top or 50):
                break
    if not rows:
        return None
    try:
        rows = attach_live_prices(rows)
    except Exception:
        pass
    return {
        "ok": True,
        "rows": rows,
        "live_prices": True,
        "note": "沿用上次扫描，名称已按对照表补全。",
        "disclaimer": "无法预知谁会成为妖股；此为启动前状态相似度排序，风险极大。",
    }


def api_yaogu(top=50):
    import time as _time
    from lib.yaogu import scan_yaogu_prelaunch

    top = int(top or 50)
    now = _time.time()
    with _YAOGU_CACHE["lock"]:
        # 5 分钟内复用（现价仍会再套实时）
        if (
            _YAOGU_CACHE["data"] is not None
            and _YAOGU_CACHE["top"] >= top
            and now - _YAOGU_CACHE["ts"] < 300
        ):
            d = dict(_YAOGU_CACHE["data"])
            rows = list(d.get("rows") or [])[:top]
            try:
                from lib.data import attach_live_prices
                rows = attach_live_prices(rows)
            except Exception:
                pass
            d["rows"] = rows
            d["cached"] = True
            d["note"] = (d.get("note") or "") + " [缓存<5分钟，现价已刷新]"
            return d
        # 进程冷启动只读一次上次 CSV，避免为了补名称再空等 1～2 分钟
        if not _YAOGU_CACHE.get("disk_used"):
            disk = _yaogu_from_last_csv(top)
            if disk:
                _YAOGU_CACHE["data"] = disk
                _YAOGU_CACHE["ts"] = now
                _YAOGU_CACHE["top"] = top
                _YAOGU_CACHE["disk_used"] = True
                d = dict(disk)
                d["cached"] = True
                return d
            _YAOGU_CACHE["disk_used"] = True
        data = scan_yaogu_prelaunch(top_n=top)
        if data.get("ok"):
            _YAOGU_CACHE["data"] = data
            _YAOGU_CACHE["ts"] = now
            _YAOGU_CACHE["top"] = top
        data["cached"] = False
        return data


def api_alpha(top=40):
    from lib.own_alpha import scan_own_alpha

    return scan_own_alpha(top_n=int(top or 40))


_GUFENG_STATE = {
    "lock": threading.Lock(),
    "scanning": False,
    "error": None,
    "started_ts": 0,
    "finished_ts": 0,
    "source": None,
    "data": None,  # last payload dict
    "div_cache": {},  # source|div_days -> annotated rows
}


def _gufeng_latest_csv():
    """Newest output/gufeng_all_*.csv (prefer all; fallback buy+sell merge)."""
    import glob
    from lib.paths import OUTPUT

    paths = sorted(glob.glob(os.path.join(OUTPUT, "gufeng_all_*.csv")))
    if paths:
        return paths[-1]
    buys = sorted(glob.glob(os.path.join(OUTPUT, "gufeng_buy_*.csv")))
    sells = sorted(glob.glob(os.path.join(OUTPUT, "gufeng_sell_*.csv")))
    if buys or sells:
        return {"buy": buys[-1] if buys else None, "sell": sells[-1] if sells else None}
    return None


def _gufeng_read_csv(path_or_pair, side="all"):
    import csv
    from lib.data import drop_st_signals, is_st_name

    rows = []
    paths = []
    if isinstance(path_or_pair, dict):
        if path_or_pair.get("buy"):
            paths.append(path_or_pair["buy"])
        if path_or_pair.get("sell"):
            paths.append(path_or_pair["sell"])
        source = " + ".join(os.path.basename(p) for p in paths)
    else:
        paths = [path_or_pair]
        source = os.path.basename(path_or_pair)

    for fp in paths:
        with open(fp, encoding="utf-8-sig") as f:
            for rec in csv.DictReader(f):
                name = str(rec.get("name") or "")
                if is_st_name(name):
                    continue
                side_raw = str(rec.get("side") or "")
                if side == "buy" and "买" not in side_raw:
                    continue
                if side == "sell" and "卖" not in side_raw:
                    continue
                try:
                    val_pct_pct = float(rec.get("val_pct_pct"))
                except Exception:
                    val_pct_pct = None
                try:
                    ref_price = float(rec.get("ref_price"))
                except Exception:
                    ref_price = rec.get("ref_price")
                pe = rec.get("pe")
                pb = rec.get("pb")
                try:
                    pe = float(pe) if pe not in (None, "", "None") else None
                except Exception:
                    pe = None
                try:
                    pb = float(pb) if pb not in (None, "", "None") else None
                except Exception:
                    pb = None
                div_raw = rec.get("divergence")
                divergence = None
                if div_raw not in (None, ""):
                    divergence = str(div_raw).strip().lower() in ("1", "true", "yes", "y", "是")
                div_ago = rec.get("div_ago")
                try:
                    div_ago = int(div_ago) if div_ago not in (None, "", "None") else None
                except Exception:
                    div_ago = None
                side_out = side_raw or ("买入观察" if side == "buy" else "卖出观察")
                if divergence and "买" in side_out and "强化" not in side_out:
                    side_out = "强化买入"
                def _pf(key):
                    raw = rec.get(key)
                    if raw in (None, "", "None"):
                        return None
                    try:
                        v = float(raw)
                        return v if v == v else None
                    except Exception:
                        return None
                rows.append({
                    "side": side_out,
                    "code": rec.get("code") or "",
                    "sym": rec.get("sym") or "",
                    "name": name,
                    "ref_price": ref_price,
                    "buy_ceiling": _pf("buy_ceiling"),
                    "buy_prefer": _pf("buy_prefer"),
                    "target_buy": _pf("target_buy"),
                    "target_note": (rec.get("target_note") or "") or None,
                    "sell_floor": _pf("sell_floor"),
                    "val_pct_pct": val_pct_pct,
                    "pe": pe,
                    "pb": pb,
                    "turnover": _pf("turnover"),
                    "asof": rec.get("asof") or "",
                    "metric": rec.get("metric") or "price_3y_pct",
                    "divergence": divergence,
                    "div_ago": div_ago,
                })
    rows = drop_st_signals(rows)
    # stable order: buys by pct asc, sells by pct desc
    buys = sorted([r for r in rows if "买" in r["side"]], key=lambda r: (r["val_pct_pct"] is None, r["val_pct_pct"] or 0))
    sells = sorted([r for r in rows if "卖" in r["side"]], key=lambda r: (r["val_pct_pct"] is None, -(r["val_pct_pct"] or 0)))
    if side == "buy":
        out = buys
    elif side == "sell":
        out = sells
    else:
        out = buys + sells
    return out, source, len(buys), len(sells)


def _gufeng_run_scan():
    """Full scan (same ST rules as cli.py gufeng); writes CSVs and updates cache."""
    import csv
    import time as _time
    from datetime import datetime

    from lib.data import (
        drop_st_signals,
        fetch_realtime_quotes,
        filter_mainboard,
        is_st_name,
        list_cached_symbols,
        load_fundamentals,
        refresh_fund_names,
    )
    from lib.gu_feng import scan_universe, signals_to_rows, refresh_target_buy
    from lib.paths import OUTPUT

    with _GUFENG_STATE["lock"]:
        _GUFENG_STATE["scanning"] = True
        _GUFENG_STATE["error"] = None
        _GUFENG_STATE["started_ts"] = _time.time()
    try:
        try:
            refresh_fund_names()
        except Exception:
            pass
        fund = load_fundamentals(force=False)
        syms = filter_mainboard(list_cached_symbols(), fund=fund, drop_st=True)
        buys, sells = scan_universe(syms, fund=fund, live={})
        all_sigs = buys + sells
        if all_sigs:
            try:
                live = fetch_realtime_quotes([s.sym for s in all_sigs])
                kept_b, kept_s = [], []
                for s in all_sigs:
                    q = live.get(s.sym) or {}
                    qn = str(q.get("name") or "").strip()
                    if qn:
                        s.name = qn
                    if is_st_name(s.name):
                        continue
                    try:
                        px = float(q.get("price") or q.get("px"))
                        if px == px:
                            s.price = round(px, 4)
                            refresh_target_buy(s)
                    except Exception:
                        pass
                    refresh_target_buy(s)
                    (kept_b if s.side == "buy" else kept_s).append(s)
                buys, sells = kept_b, kept_s
            except Exception:
                pass
        from lib.gu_feng import annotate_buy_turnover
        annotate_buy_turnover(buys, fund=fund)
        annotate_buy_turnover(sells, fund=fund)
        buy_rows = drop_st_signals(signals_to_rows(buys))
        sell_rows = drop_st_signals(signals_to_rows(sells))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fields = ["side", "code", "sym", "name", "ref_price", "buy_ceiling", "buy_prefer", "target_buy", "target_note", "sell_floor", "val_pct_pct", "pe", "pb", "turnover", "asof", "metric"]

        def _w(path, rows):
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    w.writerow(r)

        buy_path = os.path.join(OUTPUT, f"gufeng_buy_{ts}.csv")
        sell_path = os.path.join(OUTPUT, f"gufeng_sell_{ts}.csv")
        all_path = os.path.join(OUTPUT, f"gufeng_all_{ts}.csv")
        _w(buy_path, buy_rows)
        _w(sell_path, sell_rows)
        _w(all_path, buy_rows + sell_rows)
        payload = {
            "ok": True,
            "rows": buy_rows + sell_rows,
            "n_buy": len(buy_rows),
            "n_sell": len(sell_rows),
            "source": os.path.basename(all_path),
            "source_path": all_path,
            "cached": False,
            "scanning": False,
            "note": (
                "观察层（一谷≤30%买观察 / 一峰≥70%卖观察）；主板含002，剔ST/创业/科创。"
                "目标买入上限=近3年30%分位价，建议分批=20%分位价；现价≤上限则目标买=现价（现价已在一谷）。"
                "目标价为历史分位映射，非预测必成交价。与大盘红绿灯独立；研究用，非荐股。"
            ),
            "disclaimer": "研究工具，不构成投资建议。中间区观望。目标价非委托/预测成交价。",
            "rules": {
                "universe": "主板（含002），排除 ST/*ST、创业板300/301、科创688",
                "valley": "≤30% → 买入观察",
                "peak": "≥70% → 卖出观察",
                "target_buy": "30%分位价=买入上限；20%分位=建议分批；在谷内目标买=现价",
                "metric": "近3年自身收盘价分位（price_3y_pct）",
            },
        }
        with _GUFENG_STATE["lock"]:
            _GUFENG_STATE["data"] = payload
            _GUFENG_STATE["source"] = all_path
            _GUFENG_STATE["finished_ts"] = _time.time()
            _GUFENG_STATE["scanning"] = False
            _GUFENG_STATE["div_cache"] = {}  # 新扫描后作废底背离缓存
    except Exception as e:
        with _GUFENG_STATE["lock"]:
            _GUFENG_STATE["error"] = str(e)
            _GUFENG_STATE["scanning"] = False
            _GUFENG_STATE["finished_ts"] = _time.time()


def api_gufeng(side="all", rescan=False, divergence=False, div_days=5, low_turnover=False, turnover_max=None):
    """估值谷峰：默认读最新 CSV；rescan=1 后台全量扫描。
    divergence=1：买侧只留一谷∩近 div_days(3～5)日底背离；卖侧不变。
    """
    import time as _time

    from lib.data import load_fundamentals
    from lib.gu_feng import (
        annotate_rows_divergence,
        annotate_rows_turnover,
        clamp_div_lookback,
        enrich_rows_target_prices,
        filter_rows_by_turnover,
        resolve_turnover_max,
    )

    side = (side or "all").strip().lower()
    if side in ("买", "买入", "buy", "valley"):
        side = "buy"
    elif side in ("卖", "卖出", "sell", "peak"):
        side = "sell"
    else:
        side = "all"

    want_div = bool(divergence)
    div_days = clamp_div_lookback(div_days)
    to_max = resolve_turnover_max(low_turnover=bool(low_turnover), turnover_max=turnover_max)
    want_lt = to_max is not None

    with _GUFENG_STATE["lock"]:
        scanning = bool(_GUFENG_STATE["scanning"])
        err = _GUFENG_STATE["error"]
        cached_data = _GUFENG_STATE["data"]
        div_cache = _GUFENG_STATE.get("div_cache") or {}

    if rescan:
        if scanning:
            return {
                "ok": True,
                "scanning": True,
                "rows": [],
                "note": "扫描进行中，请稍候刷新…",
                "disclaimer": "研究工具，不构成投资建议。",
            }
        t = threading.Thread(target=_gufeng_run_scan, daemon=True)
        t.start()
        return {
            "ok": True,
            "scanning": True,
            "rows": [],
            "note": "已启动后台扫描（主板全量，约需数分钟）。可先看最新 CSV，完成后自动可刷新。",
            "disclaimer": "研究工具，不构成投资建议。",
        }

    def _apply_divergence(rows, source_key):
        """Annotate buy rows with 底背离; cache by source+lookback."""
        if not want_div:
            return rows, 0, 0
        cache_key = f"{source_key}|{div_days}"
        with _GUFENG_STATE["lock"]:
            cached = (_GUFENG_STATE.get("div_cache") or {}).get(cache_key)
        if cached is not None:
            annotated = cached
        else:
            # 已有 CSV 列则不再算；否则只对买侧二次过滤算形态
            need = [
                r for r in rows
                if "买" in str(r.get("side") or "") and r.get("divergence") is None
            ]
            if need:
                annotated = annotate_rows_divergence(rows, lookback=div_days, buy_only=True)
            else:
                annotated = list(rows)
                for r in annotated:
                    if r.get("divergence") and "买" in str(r.get("side") or "") and "强化" not in str(r.get("side") or ""):
                        r["side"] = "强化买入"
            with _GUFENG_STATE["lock"]:
                dc = dict(_GUFENG_STATE.get("div_cache") or {})
                dc[cache_key] = annotated
                # 只保留最近 3 个缓存
                if len(dc) > 3:
                    for k in list(dc.keys())[:-3]:
                        dc.pop(k, None)
                _GUFENG_STATE["div_cache"] = dc
        n_buy_all = sum(1 for r in annotated if "买" in str(r.get("side") or ""))
        n_buy_div = sum(1 for r in annotated if r.get("divergence") and "买" in str(r.get("side") or ""))
        # 过滤：买侧只要强化；卖侧全留
        filtered = []
        for r in annotated:
            s = str(r.get("side") or "")
            if "买" in s:
                if r.get("divergence"):
                    filtered.append(r)
            else:
                filtered.append(r)
        return filtered, n_buy_all, n_buy_div

    def _apply_turnover(rows):
        """Annotate + filter buy side by turnover_max; return (rows, n_before, n_after)."""
        if not want_lt:
            return rows, 0, 0
        try:
            fund = load_fundamentals(force=False)
        except Exception:
            fund = {}
        annotated = annotate_rows_turnover(rows, fund=fund, buy_only=False)
        # count buy before filter among current rows
        n_before = sum(1 for r in annotated if "买" in str(r.get("side") or ""))
        filtered = filter_rows_by_turnover(annotated, to_max, buy_only=True)
        n_after = sum(1 for r in filtered if "买" in str(r.get("side") or ""))
        return filtered, n_before, n_after

    # Prefer in-memory result right after a scan
    if cached_data and cached_data.get("ok") and not scanning:
        rows = list(cached_data.get("rows") or [])
        if rows and all(r.get("buy_ceiling") is None for r in rows[: min(20, len(rows))]):
            rows = enrich_rows_target_prices(rows)
        source = cached_data.get("source") or "memory"
        rows_div, n_buy_all, n_buy_div = _apply_divergence(rows, f"mem:{source}")
        base_rows = rows_div if want_div else rows
        rows_lt, n_lt_before, n_lt_after = _apply_turnover(base_rows)
        use_rows = rows_lt if want_lt else base_rows
        if side == "buy":
            use_rows = [r for r in use_rows if "买" in str(r.get("side") or "")]
        elif side == "sell":
            use_rows = [r for r in use_rows if "卖" in str(r.get("side") or "")]
        n_buy = sum(1 for r in use_rows if "买" in str(r.get("side") or "")) if want_lt else sum(1 for r in base_rows if "买" in str(r.get("side") or ""))
        n_sell = sum(1 for r in (cached_data.get("rows") or []) if "卖" in str(r.get("side") or ""))
        out = dict(cached_data)
        out["rows"] = use_rows
        out["n_buy"] = n_buy
        out["n_sell"] = n_sell
        out["n_buy_all"] = n_buy_all if want_div else n_buy
        out["n_buy_div"] = n_buy_div if want_div else None
        out["n_buy_lt"] = n_lt_after if want_lt else None
        out["n_buy_lt_before"] = n_lt_before if want_lt else None
        out["divergence"] = want_div
        out["div_days"] = div_days if want_div else None
        out["low_turnover"] = want_lt
        out["turnover_max"] = to_max if want_lt else None
        out["side"] = side
        out["cached"] = True
        out["scanning"] = False
        notes = []
        if want_div:
            notes.append(f"买侧已叠加近{div_days}日底背离：强化买入 {n_buy_div}/{n_buy_all}")
        if want_lt:
            notes.append(f"缩量换手<{to_max:g}%：{n_lt_after}/{n_lt_before}")
        if notes:
            out["note"] = "；".join(notes) + "。卖出一峰逻辑不变。观察层，非荐股。"
        return out

    latest = _gufeng_latest_csv()
    if not latest:
        return {
            "ok": False,
            "error": "尚无 gufeng CSV，请点「重新扫描」或先跑 cli.py gufeng",
            "scanning": scanning,
            "rows": [],
        }
    try:
        # 读全量再按 side / divergence 过滤，便于统计
        all_rows, source, n_buy_file, n_sell_file = _gufeng_read_csv(latest, side="all")
        # 旧 CSV 无目标价列时，按缓存 K 线补算
        if all_rows and all(
            r.get("buy_ceiling") is None for r in all_rows[: min(20, len(all_rows))]
        ):
            all_rows = enrich_rows_target_prices(all_rows)
    except Exception as e:
        return {"ok": False, "error": str(e), "scanning": scanning, "rows": []}

    src_key = source if isinstance(source, str) else str(latest)
    rows_div, n_buy_all, n_buy_div = _apply_divergence(all_rows, src_key)
    base_rows = rows_div if want_div else all_rows
    rows_lt, n_lt_before, n_lt_after = _apply_turnover(base_rows)
    use_rows = rows_lt if want_lt else base_rows
    if side == "buy":
        use_rows = [r for r in use_rows if "买" in str(r.get("side") or "")]
    elif side == "sell":
        use_rows = [r for r in use_rows if "卖" in str(r.get("side") or "")]

    n_buy = sum(1 for r in use_rows if "买" in str(r.get("side") or "")) if want_lt else sum(1 for r in base_rows if "买" in str(r.get("side") or ""))
    n_sell = n_sell_file

    mtime = None
    if isinstance(latest, str) and os.path.exists(latest):
        mtime = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(os.path.getmtime(latest)))

    note = (
        "观察层：一谷（分位≤30%）买入观察 · 一峰（≥70%）卖出观察 · 中间区观望。"
        "目标买入上限=近3年30%分位价，建议分批价=20%分位价；现价≤上限则目标买=现价（现价已在一谷）。"
        "目标价来自历史分位映射，不是预测必成交价。"
        "主板含002，已剔 ST/创业板/科创。与大盘红绿灯独立（红灯仍可看名单）。"
        "默认读取最新扫描结果；点「重新扫描」后台重跑。"
    )
    bits = []
    if want_div:
        bits.append(f"买侧叠加近{div_days}日底背离（一谷∩底背离→强化买入 {n_buy_div}/{n_buy_all}）")
    if want_lt:
        bits.append(f"缩量换手<{to_max:g}%（{n_lt_after}/{n_lt_before}）")
    if bits:
        note = "；".join(bits) + "。卖出一峰不变。研究观察层，非荐股；勿与放量/金叉等做 AND。"

    return {
        "ok": True,
        "rows": use_rows,
        "n_buy": n_buy,
        "n_sell": n_sell,
        "n_buy_all": n_buy_all if want_div else n_buy_file,
        "n_buy_div": n_buy_div if want_div else None,
        "n_buy_lt": n_lt_after if want_lt else None,
        "n_buy_lt_before": n_lt_before if want_lt else None,
        "n": len(use_rows),
        "side": side,
        "divergence": want_div,
        "div_days": div_days if want_div else None,
        "low_turnover": want_lt,
        "turnover_max": to_max if want_lt else None,
        "source": source,
        "mtime": mtime,
        "cached": True,
        "scanning": scanning,
        "scan_error": err,
        "note": note,
        "disclaimer": "研究工具，不构成投资建议，非荐股。目标买入价来自历史分位映射，非预测必成交价。",
        "rules": {
            "universe": "主板（含002），排除 ST/*ST、创业板300/301、科创688",
            "valley": "≤30% → 买入观察",
            "peak": "≥70% → 卖出观察",
            "target_buy": "买入上限=30%分位价；建议分批=20%分位价；在谷内目标买=现价",
            "sell_floor": "卖出参考=70%分位价（一峰下沿）",
            "divergence": f"可选：近{div_days}日底背离叠加买侧（回测约+3pp）",
            "low_turnover": "可选：买侧换手率%<阈值（默认1%，回测一谷∧换手<1% win20≈71%）",
            "metric": "近3年自身收盘价分位（price_3y_pct）；目标价=历史分位映射非预测",
        },
    }




def api_cool(top=40):
    from lib.cool_pool import build_cool_pool

    return build_cool_pool(top_n=int(top or 40), rescan=True)


def api_action():
    from lib.action_today import get_action_today

    return get_action_today(use_joint=True)


def api_discipline(cached=False, week=False, days=14):
    from lib.discipline import run_discipline_check, run_discipline_week

    if week:
        return run_discipline_week(days=int(days or 14))
    return run_discipline_check(refresh_action=not cached)


def api_recap(cached=True, no_observe=False):
    from lib.recap import run_recap

    try:
        return run_recap(
            cached=bool(cached),
            with_observe=not bool(no_observe),
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


def api_live(codes=None):
    """轻量实时：大盘 + 可选个股现价（不扫全市场）。"""
    from lib.data import fetch_realtime_quotes, market_regime, to_sym

    reg = market_regime(live=True)
    syms = ["sh000300"]
    if codes:
        for c in codes:
            c = (c or "").strip()
            if c:
                syms.append(to_sym(c))
    quotes = fetch_realtime_quotes(syms)
    rows = []
    for s, q in quotes.items():
        rows.append({
            "sym": s,
            "name": q.get("name"),
            "price": q.get("price"),
            "chg_pct": q.get("chg_pct"),
            "prev_close": q.get("prev_close"),
            "high": q.get("high"),
            "low": q.get("low"),
            "date": q.get("date"),
            "time": q.get("time"),
        })
    return {
        "ok": True,
        "regime": reg,
        "quotes": rows,
        "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "note": "新浪hq快照，延迟约数十秒~数分钟；日K仍可能停在上一交易日。",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[web] " + (fmt % args) + "\n")

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path in ("/", "/index.html"):
            return _html(self, INDEX_HTML)
        if path == "/api/status":
            try:
                return _json(self, api_status())
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/scan":
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            try:
                return _json(self, api_scan(force=force))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/score":
            codes = qs.get("codes", [""])[0].replace("，", ",").replace(" ", ",").split(",")
            mode = qs.get("mode", ["robust"])[0]
            low_vol = qs.get("low_vol", ["0"])[0] in ("1", "true", "yes")
            try:
                return _json(self, api_score(codes, mode=mode, require_low_vol=low_vol))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/forecast":
            code = qs.get("code", [""])[0]
            # 默认 llm=0，避免无 key/API 慢时页面一直转圈
            use_llm = qs.get("llm", ["0"])[0] in ("1", "true", "yes")
            try:
                return _json(self, api_forecast(code, use_llm=use_llm))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/joint":
            try:
                return _json(self, api_joint())
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/patterns":
            pattern = qs.get("pattern", [None])[0] or None
            try:
                return _json(self, api_patterns(pattern=pattern))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/github-formulas":
            try:
                return _json(self, api_github_formulas())
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/yaogu":
            try:
                return _json(self, api_yaogu(top=qs.get("top", ["50"])[0]))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/alpha":
            try:
                return _json(self, api_alpha(top=qs.get("top", ["40"])[0]))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/cool":
            try:
                return _json(self, api_cool(top=qs.get("top", ["40"])[0]))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/action":
            try:
                return _json(self, api_action())
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/discipline":
            cached = qs.get("cached", ["0"])[0] in ("1", "true", "yes")
            week = qs.get("week", ["0"])[0] in ("1", "true", "yes")
            days = qs.get("days", ["14"])[0]
            try:
                return _json(self, api_discipline(cached=cached, week=week, days=days))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/recap":
            cached = qs.get("cached", ["1"])[0] in ("1", "true", "yes")
            no_obs = qs.get("no_observe", ["0"])[0] in ("1", "true", "yes")
            try:
                return _json(self, api_recap(cached=cached, no_observe=no_obs))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/live":
            codes = qs.get("codes", [""])[0].replace("，", ",").replace(" ", ",").split(",")
            codes = [c for c in codes if c.strip()]
            try:
                return _json(self, api_live(codes=codes or None))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/gufeng":
            side = qs.get("side", ["all"])[0]
            rescan = qs.get("rescan", ["0"])[0] in ("1", "true", "yes")
            divergence = qs.get("divergence", ["0"])[0] in ("1", "true", "yes")
            try:
                div_days = int(qs.get("div_days", ["5"])[0])
            except Exception:
                div_days = 5
            low_turnover = qs.get("low_turnover", ["0"])[0] in ("1", "true", "yes")
            turnover_max = None
            if "turnover_max" in qs:
                try:
                    turnover_max = float(qs.get("turnover_max", [None])[0])
                except Exception:
                    turnover_max = None
            try:
                return _json(self, api_gufeng(side=side, rescan=rescan, divergence=divergence, div_days=div_days, low_turnover=low_turnover, turnover_max=turnover_max))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if path == "/api/health":
            return _json(self, {"ok": True})
        return _json(self, {"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {}

        if u.path == "/api/scan":
            try:
                return _json(self, api_scan(force=True))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if u.path == "/api/gufeng":
            side = body.get("side") or "all"
            rescan = bool(body.get("rescan"))
            divergence = bool(body.get("divergence"))
            try:
                div_days = int(body.get("div_days") or 5)
            except Exception:
                div_days = 5
            low_turnover = bool(body.get("low_turnover"))
            turnover_max = body.get("turnover_max")
            try:
                turnover_max = float(turnover_max) if turnover_max is not None and turnover_max != "" else None
            except Exception:
                turnover_max = None
            try:
                return _json(self, api_gufeng(side=side, rescan=rescan, divergence=divergence, div_days=div_days, low_turnover=low_turnover, turnover_max=turnover_max))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        if u.path == "/api/score":
            codes = body.get("codes") or []
            if isinstance(codes, str):
                codes = codes.replace("，", ",").split(",")
            mode = body.get("mode") or "strict"
            try:
                return _json(self, api_score(list(codes), mode=mode))
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)}, 500)
        return _json(self, {"ok": False, "error": "not found"}, 404)


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>量化研究系统</title>
<style>
  :root {
    --bg: #0b0f14; --panel: #121820; --border: #243044; --text: #e8eef7;
    --muted: #8b9bb4; --accent: #5b9dff; --green: #3dd68c; --red: #ff6b6b;
    --yellow: #f0c14b; --row: #0e141c;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, "PingFang SC", "Segoe UI", sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.5;
  }
  header {
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 16px 22px; border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #141b24, #0b0f14);
    position: sticky; top: 0; z-index: 10;
  }
  h1 { margin: 0; font-size: 1.15rem; letter-spacing: .02em; }
  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 12px; padding: 3px 10px; border-radius: 999px;
    background: #1a2330; color: var(--muted); border: 1px solid var(--border);
  }
  main { max-width: 1200px; margin: 0 auto; padding: 18px 20px 48px; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr 1fr; } }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px;
  }
  .card .label { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
  .card .value { font-size: 1.35rem; font-weight: 650; }
  .card .sub { color: var(--muted); font-size: 12px; margin-top: 4px; }
  .light-green { color: var(--green); }
  .light-red { color: var(--red); }
  .light-yellow { color: var(--yellow); }
  .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 8px 0 14px; }
  button, .btn {
    appearance: none; border: 1px solid var(--border); background: #1a2433; color: var(--text);
    border-radius: 9px; padding: 8px 14px; font-size: 13px; cursor: pointer;
  }
  button.primary { background: #1d3f73; border-color: #2d5f9e; }
  button:hover { filter: brightness(1.08); }
  button:disabled { opacity: .5; cursor: not-allowed; }
  input, select {
    background: #0e141c; border: 1px solid var(--border); color: var(--text);
    border-radius: 9px; padding: 8px 12px; font-size: 13px; min-width: 220px;
  }
  section.card { margin-bottom: 14px; }
  h2 { margin: 0 0 10px; font-size: 1rem; color: #9ecbff; }
  .note { color: var(--muted); font-size: 13px; margin: 0 0 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }
  th { color: var(--muted); font-weight: 600; position: sticky; top: 0; background: var(--panel); }
  tr:hover td { background: var(--row); }
  .num { font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; }
  .pill {
    display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px;
    background: #1d2a22; color: var(--green); border: 1px solid #2a4a38;
  }
  .pill.warn { background: #2a2418; color: var(--yellow); border-color: #4a3c1a; }
  .pill.bad { background: #2a1818; color: var(--red); border-color: #4a1a1a; }
  .tabs { display: flex; gap: 6px; margin-bottom: 12px; }
  .tabs button.active { background: #1d3f73; border-color: #2d5f9e; }
  .hidden { display: none; }
  .score-block { border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 10px; background: #0e141c; }
  .checks { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; font-size: 12.5px; }
  footer { color: var(--muted); font-size: 12px; margin-top: 18px; text-align: center; }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #345; border-top-color: #8cf; border-radius: 50%; animation: spin .7s linear infinite; vertical-align: -2px; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .scroll { max-height: 520px; overflow: auto; border-radius: 8px; }
</style>
</head>
<body>
<header>
  <div>
    <h1>量化研究系统 <span class="badge">算法v4 · MENGα · robust · 主板剔创业/科创/ST · 研究用</span></h1>
  </div>
  <div class="badge" id="clock">—</div>
</header>
<main>
  <div class="grid" id="stats">
    <div class="card"><div class="label">大盘红绿灯 · 实时</div><div class="value" id="light">—</div><div class="sub" id="lightSub">加载中</div></div>
    <div class="card"><div class="label">沪深300 现价</div><div class="value num" id="ret20">—</div><div class="sub" id="idxDate">—</div></div>
    <div class="card"><div class="label">联合信号 ★★</div><div class="value num" id="jointN">—</div><div class="sub" id="jointNote">完整通过∩MENGα冷静</div></div>
    <div class="card"><div class="label">完整通过 ★</div><div class="value num" id="fullN">—</div><div class="sub" id="fullNote">大盘多头+结构+严过滤</div></div>
  </div>

  <div class="toolbar">
    <button class="primary" id="btnScan" onclick="doScan(false)">加载/刷新名单</button>
    <button id="btnForce" onclick="doScan(true)">强制重扫全市场</button>
    <button onclick="loadStatus()">刷新实时大盘</button>
    <button class="primary" onclick="doAction(); tab('action')">今日买什么？</button>
    <label class="badge" style="cursor:pointer;user-select:none">
      <input type="checkbox" id="autoLive" checked style="vertical-align:middle;margin-right:4px">
      实时自动刷新 <span id="liveSec">30</span>s
    </label>
    <span class="badge" id="liveBadge">实时: —</span>
    <span class="badge">主板 <span id="pool">—</span> · 基本面 <span id="fundN">—</span></span>
    <span id="busy" class="badge hidden"><span class="spinner"></span>处理中…</span>
  </div>

  <section class="card" id="actionBanner" style="border-color:#3a5080; margin-bottom:14px">
    <div class="label">今日操作口径（唯一买入向出口）</div>
    <div class="value" id="actionTitle" style="font-size:1.6rem">—</div>
    <div class="sub" id="actionSub">点「今日买什么？」生成。红灯=空仓；绿灯才出候选。MENGα不是买入。</div>
  </section>

  <section class="card">
    <div class="tabs">
      <button class="active" data-tab="action" onclick="tab('action')">今日操作</button>
      <button data-tab="recap" onclick="tab('recap')">复盘</button>
      <button data-tab="joint" onclick="tab('joint')">联合信号 ★★</button>
      <button data-tab="full" onclick="tab('full')">完整通过 ★</button>
      <button data-tab="pass" onclick="tab('pass')">结构观察</button>
      <button data-tab="strict" onclick="tab('strict')">严结构</button>
      <button data-tab="rs" onclick="tab('rs')">相对强</button>
      <button data-tab="near" onclick="tab('near')">接近名单</button>
      <button data-tab="score" onclick="tab('score')">个股打分</button>
      <button data-tab="forecast" onclick="tab('forecast')">涨跌研究</button>
      <button data-tab="patterns" onclick="tab('patterns')">形态指标</button>
      <button data-tab="gh" onclick="tab('gh')">GitHub策略</button>
      <button data-tab="yaogu" onclick="tab('yaogu')">妖股启动前</button>
      <button data-tab="alpha" onclick="tab('alpha')">MENGα 自研</button>
      <button data-tab="gufeng" onclick="tab('gufeng')">估值谷峰</button>
      <button data-tab="cool" onclick="tab('cool')">冷静观察池</button>
      <button data-tab="discipline" onclick="tab('discipline')">破戒检查</button>
    </div>

    <div id="tab-action">
      <p class="note"><strong>唯一「买不买」出口</strong>：红灯/未站MA20+MA60 → <strong>空仓</strong>；绿灯 → 联合信号优先，否则完整通过。PATH 历史胜率约四成~五成，<strong>不是稳赚荐股</strong>。MENGα/妖股/形态永不单独当买入。</p>
      <div class="toolbar">
        <button class="primary" onclick="doAction()">计算今日操作</button>
      </div>
      <div id="actionNote" class="note"></div>
      <div class="scroll"><table><thead><tr>
        <th>档位</th><th>名称</th><th>代码</th><th>现价</th><th>质量分</th><th>RSI</th><th>60日%</th><th>理由</th>
      </tr></thead><tbody id="tbody-action"><tr><td colspan="8" class="note">点「计算今日操作」</td></tr></tbody></table></div>
    </div>

    <div id="tab-recap" class="hidden">
      <p class="note"><strong>复盘</strong>：网上报告骨架 × 本仓纪律。买入向只来自 action；观察层不是买点，且只要现价&lt;30元（优先实时，失败回退日K）。红灯不凑 3×3。名称从基本面补全，不用代码顶名。</p>
      <div class="toolbar">
        <button class="primary" onclick="doRecap(true)">用已有日报</button>
        <button onclick="doRecap(false)">重新生成</button>
      </div>
      <div id="recapNote" class="note"></div>
      <div id="recapMap"></div>
      <h3 style="font-size:14px;margin:14px 0 8px">买入向（唯一出口）</h3>
      <div class="scroll"><table><thead><tr>
        <th>行业</th><th>名称</th><th>代码</th><th>现价</th><th>质量分</th><th>档位</th><th>出场</th>
      </tr></thead><tbody id="tbody-recap-buy"><tr><td colspan="7" class="note">点「用已有日报」</td></tr></tbody></table></div>
      <h3 style="font-size:14px;margin:14px 0 8px">观察层（观察，非买入）</h3>
      <div id="recapObs"></div>
    </div>

    <div id="tab-cool" class="hidden">
      <p class="note"><strong>冷静观察池</strong> = MENGα 高分 + 低热 + 深洗 + 不过分拥挤 + 过滤极端深跌/仙股。<strong>不是建议买入</strong>；红灯时完整通过仍为 0，本池仅研究。</p>
      <div class="toolbar">
        <button class="primary" onclick="doCool()">生成观察池</button>
      </div>
      <div id="coolNote" class="note"></div>
      <div class="scroll"><table><thead><tr>
        <th>α</th><th>热</th><th>洗</th><th>拥</th><th>名称</th><th>代码</th><th>现价</th><th>60日%</th><th>风险提示</th>
      </tr></thead><tbody id="tbody-cool"><tr><td colspan="9" class="note">点「生成观察池」</td></tr></tbody></table></div>
    </div>

    <div id="tab-discipline" class="hidden">
      <p class="note"><strong>破戒检查</strong>：对照 playbook（环境闸/仓位/行业/止损）。持仓填 <code>output/holdings.csv</code>。纪律审计，非荐股。</p>
      <div class="toolbar">
        <button class="primary" onclick="doDiscipline(false)">今日破戒检查</button>
        <button onclick="doDiscipline(true)">用缓存 action</button>
        <button onclick="doDisciplineWeek()">近14日汇总</button>
      </div>
      <div id="disciplineNote" class="note"></div>
      <div class="scroll"><table><thead><tr>
        <th>状态</th><th>级别</th><th>规则</th><th>说明</th>
      </tr></thead><tbody id="tbody-discipline"><tr><td colspan="4" class="note">点「今日破戒检查」</td></tr></tbody></table></div>
      <div id="disciplineHold" class="note" style="margin-top:10px"></div>
    </div>

    <div id="tab-alpha" class="hidden">
      <p class="note">数据驱动自研体系：全主板历史截面约 8.8 万条，<strong>均值回归主轴</strong>（反过热 IC20≈+0.07）。子分：冷热度 Heat / 洗盘 Wash / 萌芽 Spark / 拥挤 Crowd。MENGα 百分位越高越偏「冷+蓄势」。<strong>非荐股，高α≠必涨</strong>。上表冷、下表烫，是对照不是买卖。</p>
      <div class="toolbar">
        <button class="primary" onclick="doAlpha()">扫描 MENGα</button>
      </div>
      <div id="alphaNote" class="note"></div>
      <div class="scroll"><table><thead><tr>
        <th>α分</th><th>热</th><th>洗</th><th>芽</th><th>拥</th><th>名称</th><th>代码</th><th>现价</th><th>60日%</th><th>超额20</th><th>标签</th>
      </tr></thead><tbody id="tbody-alpha"><tr><td colspan="11" class="note">点扫描</td></tr></tbody></table></div>
      <p class="note" style="margin-top:12px">过热对照（Heat 最高，历史偏弱，勿追）：</p>
      <div class="scroll"><table><thead><tr>
        <th>热</th><th>α</th><th>名称</th><th>代码</th><th>现价</th><th>60日%</th><th>RSI</th>
      </tr></thead><tbody id="tbody-alpha-hot"><tr><td colspan="7" class="note">—</td></tr></tbody></table></div>
    </div>

    <div id="tab-gufeng" class="hidden">
      <p class="note"><strong>估值谷峰 · 观察层</strong>：主板（含002）近3年自身价格分位 — <strong>一谷≤30% 买入观察</strong>、<strong>一峰≥70% 卖出观察</strong>、中间区观望。<strong>目标买入上限</strong>=30%分位价（一谷上沿），<strong>建议分批价</strong>=20%分位价；现价≤上限则目标买=现价（标注「现价已在一谷」）。目标价来自历史分位映射，<strong>不是预测必成交价</strong>。可选<strong>叠加底背离（买侧）</strong>：一谷∩近3～5日底背离→强化买入；可选<strong>缩量</strong>（换手&lt;1% 或 &lt;0.5%，基本面 turn）；二者可叠加；卖出一峰不变。已剔 ST/*ST、创业板300/301、科创688。<strong>非荐股</strong>。默认读最新 CSV；旧结果缺列时按缓存K线补算。</p>
      <div class="toolbar">
        <select id="gufengSide">
          <option value="all" selected>全部</option>
          <option value="buy">只看买入观察</option>
          <option value="sell">只看卖出观察</option>
        </select>
        <label style="margin-left:8px;user-select:none;cursor:pointer">
          <input type="checkbox" id="gufengDiv" /> 叠加底背离（买侧）
        </label>
        <select id="gufengDivDays" title="底背离回看天数">
          <option value="3">近3日</option>
          <option value="5" selected>近5日</option>
        </select>
        <label style="margin-left:8px;user-select:none;cursor:pointer">
          <input type="checkbox" id="gufengLt" /> 缩量（换手&lt;1%）
        </label>
        <select id="gufengLtMax" title="换手率上限%">
          <option value="1" selected>&lt;1%</option>
          <option value="0.5">&lt;0.5%</option>
        </select>
        <button class="primary" onclick="doGufeng(false)">加载最新结果</button>
        <button onclick="doGufeng(true)">重新扫描（后台）</button>
        <span class="badge" id="gufengBadge">—</span>
      </div>
      <div id="gufengNote" class="note"></div>
      <div class="scroll"><table><thead><tr>
        <th>信号</th><th>代码</th><th>名称</th><th>参考价</th><th>目标买入</th><th>买入上限(30%)</th><th>分批价(20%)</th><th>卖参(70%)</th><th>分位%</th><th>换手%</th><th>PE</th><th>PB</th><th>底背离</th><th>截面日</th>
      </tr></thead><tbody id="tbody-gufeng"><tr><td colspan="14" class="note">点「加载最新结果」</td></tr></tbody></table></div>
    </div>

    <div id="tab-yaogu" class="hidden">
      <p class="note">根据历史「20日浮盈≥50%」启动日倒推画像：低位/均线附近、RSI中偏低、尚未完全多头。扫描今日相似股。<strong>绝大多数不会成妖</strong>，仅观察。</p>
      <div class="toolbar">
        <button class="primary" onclick="doYaogu()">扫描启动前画像</button>
      </div>
      <div id="yaoguNote" class="note"></div>
      <div class="scroll"><table><thead><tr>
        <th>妖股分</th><th>名称</th><th>代码</th><th>现价(实时)</th><th>涨跌%</th><th>RSI</th><th>bias%</th><th>20日%</th><th>距低%</th><th>形态</th>
      </tr></thead><tbody id="tbody-yaogu"><tr><td colspan="10" class="note">点扫描</td></tr></tbody></table></div>
    </div>

    <div id="tab-patterns" class="hidden">
      <p class="note">常见形态：黄金坑、一阳穿三线、金叉、放量突破等（可复现定义，与通达信可能略有差异）。<strong>命中≠推荐买入</strong>；红灯仍建议空仓。</p>
      <div class="toolbar">
        <select id="patFilter">
          <option value="">全部形态</option>
          <option>黄金坑</option>
          <option>一阳穿三线</option>
          <option>金叉</option>
          <option>底背离</option>
          <option>放量突破</option>
          <option>回踩年线</option>
          <option>均线多头</option>
          <option>缩量回踩</option>
          <option>启明星</option>
        </select>
        <button class="primary" onclick="doPatterns()">扫描形态</button>
      </div>
      <div id="patCounts" class="note"></div>
      <div class="scroll"><table><thead><tr>
        <th>名称</th><th>代码</th><th>现价</th><th>命中数</th><th>形态</th>
      </tr></thead><tbody id="tbody-pat"><tr><td colspan="5" class="note">点「扫描形态」</td></tr></tbody></table></div>
    </div>

    <div id="tab-gh" class="hidden">
      <p class="note">三套来自 GitHub 公开规则的本地重写：RSRS、KDJ 超卖金叉、超跌反弹评分。只扫主题池，<strong>观察层，不进今日操作</strong>。大盘未站上 MA20 时不触发。</p>
      <div class="toolbar">
        <button class="primary" onclick="doGh()">扫描主题池</button>
      </div>
      <div id="ghCatalog" class="note"></div>
      <div id="ghNote" class="note"></div>
      <div class="scroll"><table><thead><tr>
        <th>名称</th><th>代码</th><th>主题</th><th>现价</th><th>触发</th>
      </tr></thead><tbody id="tbody-gh"><tr><td colspan="5" class="note">点「扫描主题池」</td></tr></tbody></table></div>
    </div>

    <div id="tab-joint" class="hidden">
      <p class="note" id="jointHint"><strong>最严名单 B（联合 v2）</strong>：完整通过 ∩ MENGα 冷静过滤（α/热/芽/拥）。已弃用涨跌研究闸门（其边≈随机）。红灯时必为 0。非荐股。</p>
      <div class="toolbar">
        <button class="primary" id="btnJoint" onclick="doJoint()">计算联合信号</button>
      </div>
      <div class="scroll"><table><thead><tr>
        <th>名称</th><th>代码</th><th>质量分</th><th>MENGα</th><th>热</th><th>芽</th><th>拥</th><th>过滤</th>
      </tr></thead><tbody id="tbody-joint"><tr><td colspan="8" class="note">点「计算联合信号」</td></tr></tbody></table></div>
    </div>

    <div id="tab-full">
      <p class="note" id="fullHint"><strong>系统「完整通过」名单</strong>（算法v4 robust）：大盘 MA20+MA60 + 结构反热 + 质量分排序。红灯=0。联合=完整通过∩MENGα。</p>
      <div class="scroll"><table><thead><tr>
        <th>名称</th><th>代码</th><th>质量分</th><th>加分</th><th>现价</th><th>RSI</th><th>60日%</th><th>超额20日</th><th>PE</th>
      </tr></thead><tbody id="tbody-full"><tr><td colspan="9" class="note">尚未扫盘</td></tr></tbody></table></div>
    </div>

    <div id="tab-pass" class="hidden">
      <p class="note" id="passHint">忽略大盘的个股结构，按质量分排序。<strong>不是推荐买入</strong>。</p>
      <div class="scroll"><table><thead><tr>
        <th>名称</th><th>代码</th><th>质量分</th><th>加分</th><th>现价</th><th>RSI</th><th>60日%</th><th>超额20日</th><th>PE</th>
      </tr></thead><tbody id="tbody-pass"><tr><td colspan="9" class="note">尚未扫盘</td></tr></tbody></table></div>
    </div>

    <div id="tab-strict" class="hidden">
      <p class="note">严结构 v2：RSI&lt;65、涨幅5~40%、个股MA20上行、距MA20&lt;12%（仍忽略大盘）。</p>
      <div class="scroll"><table><thead><tr>
        <th>名称</th><th>代码</th><th>质量分</th><th>加分</th><th>现价</th><th>RSI</th><th>60日%</th><th>超额20日</th><th>PE</th>
      </tr></thead><tbody id="tbody-strict"><tr><td colspan="9" class="note">尚未扫盘</td></tr></tbody></table></div>
    </div>

    <div id="tab-rs" class="hidden">
      <p class="note">红灯也看的<strong>相对强</strong>Top40：20日跑赢大盘、站上自身MA20、不过热。<strong>仅观察，非荐股。</strong></p>
      <div class="scroll"><table><thead><tr>
        <th>名称</th><th>代码</th><th>质量分</th><th>超额20日</th><th>现价</th><th>RSI</th><th>60日%</th><th>加分</th><th>PE</th>
      </tr></thead><tbody id="tbody-rs"><tr><td colspan="9" class="note">尚未扫盘</td></tr></tbody></table></div>
    </div>

    <div id="tab-near" class="hidden">
      <p class="note">接近通过（加分够但未全满）。</p>
      <div class="scroll"><table><thead><tr>
        <th>名称</th><th>代码</th><th>质量分</th><th>加分</th><th>现价</th><th>RSI</th><th>60日%</th>
      </tr></thead><tbody id="tbody-near"><tr><td colspan="7" class="note">尚未扫盘</td></tr></tbody></table></div>
    </div>

    <div id="tab-score" class="hidden">
      <div class="toolbar">
        <input id="codes" placeholder="代码，逗号分隔 如 600519,002371" value="600519,002371,600428">
        <select id="mode">
          <option value="robust" selected>robust v4（PATH优化）</option>
          <option value="elite">elite</option>
          <option value="winrate">winrate</option>
          <option value="strict">strict v2</option>
          <option value="base">base 原公式三</option>
        </select>
        <button class="primary" onclick="doScore()">打分</button>
      </div>
      <div id="scoreOut"></div>
    </div>

    <div id="tab-forecast" class="hidden">
      <p class="note"><strong>研究价值低</strong>：历史相似描述统计，walk-forward 约 <strong>52–54%</strong>（接近随机）。<strong>不做买入依据</strong>；联合信号已改用结构∩MENGα。门槛 v3 更严，多数结果为中性。</p>
      <div class="toolbar">
        <input id="fcode" placeholder="单个代码 如 600610" value="">
        <button class="primary" onclick="doForecast()">研究涨跌</button>
      </div>
      <div id="forecastOut"><p class="note">可选参考；默认不做大模型。若想省时间，直接看「今日操作 / 联合 / MENGα」。</p></div>
    </div>
  </section>

  <footer>
    本地系统 http://127.0.0.1:8765 · 研究用非荐股 · 红灯空仓 · 回测请看 PATH 非 OPT · 出场参考 tight TP4/SL3
  </footer>
</main>
<script>
const $ = (id) => document.getElementById(id);
const setText = (id, v) => { const el = $(id); if (el) el.textContent = v; };
const setHtml = (id, v) => { const el = $(id); if (el) el.innerHTML = v; };
function setBusy(on) {
  $('busy').classList.toggle('hidden', !on);
  $('btnScan').disabled = on;
  $('btnForce').disabled = on;
}
function lightClass(l) {
  if (l === '绿灯') return 'light-green';
  if (l === '红灯') return 'light-red';
  return 'light-yellow';
}
async function loadStatus(silent) {
  try {
    const r = await fetch('/api/live').then(x => x.json());
    if (!r.ok && r.error) { if (!silent) alert(r.error); return; }
    const g = r.regime || {};
    const light = g.light || '未知';
    const le = $('light');
    if (le) { le.textContent = light; le.className = 'value ' + lightClass(light); }
    const chg = g.live_chg_pct != null ? g.live_chg_pct : g.ret20;
    // 主数字：现价；副行：涨跌% · 时间 · 日K日期
    setText('ret20', g.close != null ? Number(g.close).toFixed(2) : '—');
    const chgS = (chg != null && !isNaN(chg)) ? ((chg >= 0 ? '+' : '') + Number(chg).toFixed(2) + '%') : '';
    const liveTag = g.live ? '实时' : '日线';
    setText('lightSub', `MA20=${g.above_ma20} · MA60=${g.above_ma60}` + (g.ma20 != null ? ` · MA20=${g.ma20}` : ''));
    setText('idxDate', `${g.last_date || '—'} · ${chgS} · ${liveTag}` + (g.bar_date ? ` · 日K=${g.bar_date}` : ''));
    setText('liveBadge', g.live
      ? `实时 ${g.last_date || ''}  ${g.close != null ? Number(g.close).toFixed(2) : ''}`
      : '实时未取到，用日线');
    setText('clock', new Date().toLocaleString());
    // 补池子规模（可选）
    fetch('/api/status').then(x => x.json()).then(s => {
      if (!s.ok) return;
      setText('pool', s.mainboard);
      setText('fundN', s.fund);
    }).catch(() => {});
  } catch (e) {
    if (!silent) alert('实时失败: ' + e);
  }
}
let _liveTimer = null;
function setupAutoLive() {
  if (_liveTimer) { clearInterval(_liveTimer); _liveTimer = null; }
  const box = $('autoLive');
  if (!box || !box.checked) return;
  _liveTimer = setInterval(() => {
    // 仅轻量实时，禁止每 30s 全市场扫盘（会卡死「处理中」）
    loadStatus(true);
    fetch('/api/action').then(x => x.json()).then(r => {
      if (!r.ok) return;
      const act = r.action || '—';
      const n = r.n ?? 0;
      const titleEl = $('actionTitle');
      if (titleEl) {
        titleEl.textContent = act === '空仓' ? '【空仓】今日无买入候选' : `【可关注】${n} 只候选`;
        titleEl.className = 'value ' + (act === '空仓' ? 'light-red' : 'light-green');
      }
      setText('actionSub', r.note || '');
      if (r.joint_count != null) setText('jointN', r.joint_count);
      if (r.full_pass_count != null) setText('fullN', r.full_pass_count);
    }).catch(() => {});
  }, 30000);
}
function emptyMsg(tbodyId, msg) {
  $(tbodyId).innerHTML = `<tr><td colspan="9" class="note">${esc(msg)}</td></tr>`;
}
function renderRows(tbodyId, rows, kind='main', emptyText='无数据') {
  const tb = $(tbodyId);
  if (!rows || !rows.length) {
    tb.innerHTML = `<tr><td colspan="9" class="note">${esc(emptyText)}</td></tr>`;
    return;
  }
  if (kind === 'near') {
    tb.innerHTML = rows.map(r => `
    <tr>
      <td>${esc(r.name)}</td><td class="num">${esc(r.sym)}</td>
      <td class="num">${r.qscore ?? '—'}</td>
      <td class="num">${r.plus}/9</td>
      <td class="num">${r.px}</td><td class="num">${r.rsi}</td>
      <td class="num">${fmtPct(r.zf60)}</td>
    </tr>`).join('');
    return;
  }
  if (kind === 'rs') {
    tb.innerHTML = rows.map(r => `
    <tr>
      <td>${esc(r.name)}</td><td class="num">${esc(r.sym)}</td>
      <td class="num"><span class="pill">${r.qscore ?? '—'}</span></td>
      <td class="num">${fmtPct(r.excess20)}</td>
      <td class="num">${r.px}</td><td class="num">${r.rsi}</td>
      <td class="num">${fmtPct(r.zf60)}</td>
      <td class="num">${r.plus}/9</td>
      <td class="num">${r.pe ?? '—'}</td>
    </tr>`).join('');
    return;
  }
  tb.innerHTML = rows.map(r => `
    <tr>
      <td>${esc(r.name)}</td>
      <td class="num">${esc(r.sym)}</td>
      <td class="num"><span class="pill">${r.qscore ?? '—'}</span></td>
      <td class="num">${r.plus}/9</td>
      <td class="num">${r.px}</td>
      <td class="num">${r.rsi}</td>
      <td class="num">${fmtPct(r.zf60)}</td>
      <td class="num">${fmtPct(r.excess20 != null ? r.excess20 : r.rs20)}</td>
      <td class="num">${r.pe ?? '—'}</td>
    </tr>`).join('');
}
function fmtPct(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
async function doScan(force) {
  setBusy(true);
  try {
    const url = force ? '/api/scan?force=1' : '/api/scan';
    const method = force ? 'POST' : 'GET';
    const r = await fetch(url, { method }).then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); return; }
    const full = r.full_pass || [];
    const tech = r.tech_pass || [];
    const strict = r.strict_struct || [];
    setText('fullN', full.length);
    setText('fullNote', r.market_ok
      ? (r.cached ? '缓存 · 可作候选观察' : '刚扫完 · 可作候选观察')
      : '红灯/未站MA20+MA60 → 0');
    // 顶部无 passN 卡片；用 fullNote 旁逻辑即可，勿写空节点
    setText('passHint', `结构观察 ${tech.length} · 严结构 ${strict.length} · 非荐股`);
    setHtml('fullHint', r.note
      ? esc(r.note)
      : '<strong>完整通过</strong> = 大盘多头 + 结构 + 严过滤。红灯时必为 0。');
    renderRows('tbody-full', full, 'main',
      r.market_ok ? '暂无完整通过标的' : '大盘未满足（需 MA20+MA60）→ 完整通过为空。请看「结构观察/严结构/相对强」。');
    renderRows('tbody-pass', tech, 'main', '无结构通过');
    renderRows('tbody-strict', strict, 'main', '无严结构标的');
    renderRows('tbody-rs', r.rs_leaders || [], 'rs', '无相对强标的');
    renderRows('tbody-near', r.near || [], 'near', '无接近名单');
    if (r.regime) {
      const g = r.regime;
      const light = g.light || '未知';
      const le = $('light');
      if (le) { le.textContent = light; le.className = 'value ' + lightClass(light); }
      setText('lightSub', `MA20=${g.above_ma20} · MA60=${g.above_ma60}`);
    }
    // 默认停在联合信号；并异步算联合（红灯会很快返回0）
    tab('joint');
    doJoint();
  } catch (e) {
    alert('扫盘失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doScore() {
  const codes = $('codes').value;
  const mode = $('mode').value;
  setBusy(true);
  try {
    const r = await fetch('/api/score?mode=' + encodeURIComponent(mode) + '&codes=' + encodeURIComponent(codes)).then(x => x.json());
    if (!r.ok) { $('scoreOut').innerHTML = '<p class="note">' + esc(r.error || '失败') + '</p>'; return; }
    $('scoreOut').innerHTML = (r.results || []).map(x => {
      if (x.error) return `<div class="score-block"><strong>${esc(x.name)} (${esc(x.sym)})</strong> — ${esc(x.error)}</div>`;
      const cls = x.veto_pass ? 'pill' : 'pill bad';
      return `<div class="score-block">
        <div><strong>${esc(x.name)}</strong> <span class="num">${esc(x.sym)}</span>
          <span class="${cls}">${esc(x.verdict)}</span>
          <span class="badge">大盘 ${esc(x.light)}</span>
        </div>
        <div class="sub num">现价 ${x.px} · 加分 ${x.plus} · RSI ${x.rsi} · 60日 ${fmtPct(x.zf60)} · 公式三 ${x.f3 ? '✓' : '✗'}</div>
        <div class="checks" style="margin-top:8px">
          ${(x.veto||[]).map(v => `<div>${v.ok ? '✓' : '✗'} ${esc(v.name)}</div>`).join('')}
        </div>
        <div class="note" style="margin-top:8px">${esc(x.exit_hint||'')}</div>
      </div>`;
    }).join('');
  } finally {
    setBusy(false);
  }
}
async function doJoint() {
  setBusy(true);
  try {
    const r = await fetch('/api/joint').then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); return; }
    setText('jointN', r.joint_count ?? 0);
    setText('jointNote', r.light ? (`大盘${r.light}`) : '—');
    setText('jointHint', r.note || '');
    const rows = r.joint || [];
    const tj = $('tbody-joint');
    if (!tj) return;
    if (!rows.length) {
      tj.innerHTML = `<tr><td colspan="8" class="note">${esc(r.note || '联合信号为空')}</td></tr>`;
    } else {
      tj.innerHTML = rows.map(x => `
        <tr>
          <td>${esc(x.name)}</td>
          <td class="num">${esc(x.sym)}</td>
          <td class="num">${x.qscore ?? '—'}</td>
          <td class="num"><span class="pill">${x.menga ?? '—'}</span></td>
          <td class="num">${x.heat ?? '—'}</td>
          <td class="num">${x.spark ?? '—'}</td>
          <td class="num">${x.crowd ?? '—'}</td>
          <td>${esc(x.forecast_lean || x.why || '')}</td>
        </tr>`).join('');
    }
  } catch (e) {
    alert('联合信号失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doAlpha() {
  setBusy(true);
  try {
    const r = await fetch('/api/alpha?top=40').then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); return; }
    const f = r.formula || {};
    const ev = f.evidence || {};
    const txt = (r.note || '') +
      (ev.ic20_anti_heat != null ? ` 证据IC20(反热)=${ev.ic20_anti_heat}` : '') +
      ' ' + (r.disclaimer || '');
    if (r.stale_n) {
      $('alphaNote').innerHTML =
        `<span class="pill bad">数据滞后</span> <strong>指标截面日 ${r.asof}</strong>，` +
        `${r.stale_n} 只个股K线更旧——榜单排序基于旧截面，` +
        `请先跑 <code>cli.py refresh --all</code>。 ` + txt.replace(/</g, '&lt;');
    } else {
      $('alphaNote').textContent = txt;
    }
    const rows = r.rows || [];
    if (!rows.length) {
      $('tbody-alpha').innerHTML = '<tr><td colspan="11" class="note">无结果</td></tr>';
    } else {
      $('tbody-alpha').innerHTML = rows.map(x => `
        <tr>
          <td class="num"><span class="pill">${x.menga}</span></td>
          <td class="num">${x.heat}</td>
          <td class="num">${x.wash}</td>
          <td class="num">${x.spark}</td>
          <td class="num">${x.crowd}</td>
          <td>${esc(x.name)}</td>
          <td class="num">${esc(x.sym)}</td>
          <td class="num">${x.px}${x.live_chg_pct != null ? ' <span class="note">(' + fmtPct(x.live_chg_pct) + ')</span>' : ''}</td>
          <td class="num">${fmtPct(x.ret60)}</td>
          <td class="num">${fmtPct(x.excess20)}</td>
          <td>${esc(x.tag)}</td>
        </tr>`).join('');
    }
    const hot = r.hot_rows || [];
    if (!hot.length) {
      $('tbody-alpha-hot').innerHTML = '<tr><td colspan="7" class="note">—</td></tr>';
    } else {
      $('tbody-alpha-hot').innerHTML = hot.map(x => `
        <tr>
          <td class="num">${x.heat}</td>
          <td class="num">${x.menga}</td>
          <td>${esc(x.name)}</td>
          <td class="num">${esc(x.sym)}</td>
          <td class="num">${x.px}${x.live_chg_pct != null ? ' (' + fmtPct(x.live_chg_pct) + ')' : ''}</td>
          <td class="num">${fmtPct(x.ret60)}</td>
          <td class="num">${x.rsi}</td>
        </tr>`).join('');
    }
  } catch (e) {
    alert('MENGα 失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doDiscipline(cached) {
  setBusy(true);
  try {
    const url = '/api/discipline' + (cached ? '?cached=1' : '');
    const r = await fetch(url).then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); return; }
    const env = r.env || {};
    setText('disciplineNote',
      '总评【' + (r.grade || '—') + '】 通过' + (r.n_pass || 0) +
      ' 失败' + (r.n_fail || 0) + ' 严重' + (r.n_crit || 0) +
      ' · action=' + (r.action || '—') + ' · 大盘' + (r.light || '—') +
      ' · 指数20日=' + (env.idx_ret20 != null ? env.idx_ret20 + '%' : '—') +
      ' · 持仓' + (r.holdings_n || 0) + '只 · ' + (r.disclaimer || '')
    );
    const checks = r.checks || [];
    const tb = $('tbody-discipline');
    if (!tb) return;
    if (!checks.length) {
      tb.innerHTML = '<tr><td colspan="4" class="note">无检查项</td></tr>';
    } else {
      tb.innerHTML = checks.map(c => {
        const ok = !!c.ok;
        const st = ok ? '✅ PASS' : '❌ FAIL';
        const sev = ok ? '' : (c.severity || '');
        return '<tr style="' + (ok ? '' : 'background:rgba(180,40,40,0.15)') + '">' +
          '<td>' + st + '</td><td>' + esc(sev) + '</td><td>' + esc(c.title) +
          '</td><td class="note">' + esc(c.detail) + '</td></tr>';
      }).join('');
    }
    const hs = r.holdings || [];
    if (hs.length) {
      setText('disciplineHold',
        '持仓: ' + hs.map(h =>
          (h.sym || '') +
          (h.weight_pct != null ? ' ' + h.weight_pct + '%' : '') +
          (h.pnl_pct != null ? ' 浮盈' + h.pnl_pct + '%' : '') +
          (h.new_today ? ' 新建' : '')
        ).join(' · ')
      );
    } else if (r.holdings_path) {
      setText('disciplineHold',
        '持仓文件为空（' + r.holdings_path + '）。可 cli.py holdings from-action 生成草稿，或按 example 填权重/成本。'
      );
    } else {
      setText('disciplineHold', '无持仓文件。模板 output/holdings.example.csv → holdings.csv');
    }
  } catch (e) {
    alert('破戒检查失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doDisciplineWeek() {
  setBusy(true);
  try {
    const r = await fetch('/api/discipline?week=1&days=14').then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); return; }
    setText('disciplineNote',
      '近' + ((r.rows || []).length) + '份 · 守戒' + (r.n_ok || 0) +
      ' · 破戒' + (r.n_bad || 0) + ' · 严重日' + (r.n_crit_days || 0) +
      ' · ' + (r.md_path || '')
    );
    const rows = r.rows || [];
    const tb = $('tbody-discipline');
    if (!tb) return;
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="4" class="note">尚无历史 discipline 日报，先跑今日检查</td></tr>';
    } else {
      tb.innerHTML = rows.map(x =>
        '<tr><td>' + esc(x.asof) + '</td><td>' + esc(x.grade) +
        '</td><td>通过' + (x.n_pass != null ? x.n_pass : '—') +
        ' 失败' + (x.n_fail != null ? x.n_fail : '—') +
        '</td><td>严重' + (x.n_crit != null ? x.n_crit : '—') + '</td></tr>'
      ).join('');
    }
    setText('disciplineHold', '');
  } catch (e) {
    alert('周汇总失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doCool() {
  setBusy(true);
  try {
    const r = await fetch('/api/cool?top=40').then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); return; }
    setText('coolNote', (r.note || '') + ' ' + (r.disclaimer || ''));
    const rows = r.rows || [];
    const tb = $('tbody-cool');
    if (!tb) return;
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="9" class="note">无入池标的（过滤后为空）</td></tr>';
    } else {
      tb.innerHTML = rows.map(x => `
        <tr>
          <td class="num"><span class="pill">${x.menga}</span></td>
          <td class="num">${x.heat}</td>
          <td class="num">${x.wash}</td>
          <td class="num">${x.crowd}</td>
          <td>${esc(x.name)}</td>
          <td class="num">${esc(x.sym)}</td>
          <td class="num">${x.px}</td>
          <td class="num">${fmtPct(x.ret60)}</td>
          <td>${esc(x.risk || '—')}</td>
        </tr>`).join('');
    }
  } catch (e) {
    alert('冷静观察池失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doAction() {
  setBusy(true);
  try {
    const r = await fetch('/api/action').then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); return; }
    const act = r.action || '—';
    const n = r.n ?? 0;
    const titleEl = $('actionTitle');
    if (titleEl) {
      titleEl.textContent = act === '空仓' ? '【空仓】今日无买入候选' : `【可关注】${n} 只候选`;
      titleEl.className = 'value ' + (act === '空仓' ? 'light-red' : 'light-green');
    }
    setText('actionSub', r.note || '');
    setText('actionNote', (r.note || '') + (r.why_empty ? ' | ' + r.why_empty : '') + ' ' + (r.disclaimer || ''));
    setText('jointN', r.joint_count ?? 0);
    setText('fullN', r.full_pass_count ?? 0);
    const tb = $('tbody-action');
    const rows = r.buy || [];
    if (tb) {
      if (!rows.length) {
        tb.innerHTML = `<tr><td colspan="8" class="note">${esc(r.note || '无候选 — 空仓')}</td></tr>`;
      } else {
        tb.innerHTML = rows.map(x => `
          <tr>
            <td>${esc(x.tier || '')}</td>
            <td>${esc(x.name)}</td>
            <td class="num">${esc(x.sym)}</td>
            <td class="num">${x.px ?? '—'}</td>
            <td class="num">${x.qscore ?? '—'}</td>
            <td class="num">${x.rsi ?? '—'}</td>
            <td class="num">${fmtPct(x.zf60)}</td>
            <td>${esc(x.reason || '')}</td>
          </tr>`).join('');
      }
    }
  } catch (e) {
    alert('今日操作失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doYaogu() {
  setBusy(true);
  setText('yaoguNote', '扫描中… 全市场约需 1～2 分钟（有缓存则秒开），请稍候');
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 180000);
    const r = await fetch('/api/yaogu?top=50', { signal: ctrl.signal }).then(x => x.json());
    clearTimeout(timer);
    if (!r.ok && r.error) { alert(r.error); setText('yaoguNote', r.error); return; }
    $('yaoguNote').textContent = (r.note || '') + ' ' + (r.disclaimer || '');
    const rows = r.rows || [];
    if (!rows.length) {
      $('tbody-yaogu').innerHTML = '<tr><td colspan="10" class="note">无匹配</td></tr>';
    } else {
      $('tbody-yaogu').innerHTML = rows.map(x => `
        <tr>
          <td class="num"><span class="pill">${x.yaogu_score}</span></td>
          <td>${esc(x.name)}</td>
          <td class="num">${esc(x.sym)}</td>
          <td class="num">${x.px}${x.live ? '' : '·日K'}</td>
          <td class="num">${x.live_chg_pct != null ? fmtPct(x.live_chg_pct) : '—'}</td>
          <td class="num">${x.rsi}</td>
          <td class="num">${fmtPct(x.bias20)}</td>
          <td class="num">${fmtPct(x.ret20)}</td>
          <td class="num">${(x.dist_low20*100).toFixed(1)}%</td>
          <td>${esc((x.patterns||[]).join('、'))}</td>
        </tr>`).join('');
    }
  } catch (e) {
    const msg = (e && e.name === 'AbortError') ? '扫描超时，请再点一次（服务可能正忙）' : ('失败: ' + e);
    alert(msg);
    setText('yaoguNote', msg);
  } finally {
    setBusy(false);
  }
}
async function doGh() {
  setBusy(true);
  try {
    const r = await fetch('/api/github-formulas').then(x => x.json());
    if (!r.ok) { $('ghNote').textContent = r.error || '失败'; return; }
    $('ghCatalog').innerHTML = (r.catalog || []).map(x =>
      '<div>' + esc(x.name) + ' <span class="num">' + esc(x.id) + '</span> — ' + esc(x.desc) + '</div>'
    ).join('');
    $('ghNote').textContent = (r.note || '') + ' 大盘站上MA20=' + (r.index_above_ma20 ? '是' : '否')
      + '，扫描 ' + (r.scanned || 0) + ' 只。' + (r.disclaimer || '');
    const rows = r.rows || [];
    if (!rows.length) {
      $('tbody-gh').innerHTML = '<tr><td colspan="5" class="note">今日主题池没有触发。</td></tr>';
    } else {
      $('tbody-gh').innerHTML = rows.map(x => `
        <tr>
          <td>${esc(x.name)}</td>
          <td class="num">${esc(x.sym)}</td>
          <td>${esc(x.theme || '')}</td>
          <td class="num">${x.px}</td>
          <td>${esc((x.hits || []).join('、'))}</td>
        </tr>`).join('');
    }
  } catch (e) {
    alert('GitHub策略失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doPatterns() {
  setBusy(true);
  try {
    const p = $('patFilter').value;
    const url = '/api/patterns' + (p ? ('?pattern=' + encodeURIComponent(p)) : '');
    const r = await fetch(url).then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); return; }
    const counts = r.counts || {};
    $('patCounts').textContent = r.note + ' | ' + Object.entries(counts).filter(([,v])=>v>0).map(([k,v])=>k+':'+v).join(' · ');
    const rows = r.rows || [];
    if (!rows.length) {
      $('tbody-pat').innerHTML = '<tr><td colspan="5" class="note">今日无命中（或过滤后为空）</td></tr>';
    } else {
      $('tbody-pat').innerHTML = rows.slice(0,150).map(x => `
        <tr>
          <td>${esc(x.name)}</td>
          <td class="num">${esc(x.sym)}</td>
          <td class="num">${x.px}</td>
          <td class="num"><span class="pill">${x.n_hits}</span></td>
          <td>${esc((x.hits||[]).join('、'))}</td>
        </tr>`).join('');
    }
  } catch (e) {
    alert('形态扫描失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doForecast() {
  const code = $('fcode').value.trim();
  if (!code) { alert('请输入代码'); return; }
  // 立刻清掉上一只（如 600428），避免「改了代码还显示旧结果」
  $('forecastOut').innerHTML = `<p class="note">正在研究 <strong>${esc(code)}</strong>… 约 10～20 秒，请勿连点</p>`;
  setBusy(true);
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60000);
    // llm=0：统计先出，避免大模型卡住整页
    const r = await fetch('/api/forecast?llm=0&code=' + encodeURIComponent(code), { signal: ctrl.signal }).then(x => x.json());
    clearTimeout(timer);
    if (!r.ok) { $('forecastOut').innerHTML = '<p class="note">' + esc(r.error || '失败') + '</p>'; return; }
    const s = r.snapshot || {};
    const conf = r.confidence || '低';
    const confCls = conf === '高' ? 'pill' : (conf === '中' ? 'pill warn' : 'pill bad');
    const rows = (r.horizons || []).map(h => `
      <tr>
        <td class="num">${h.horizon}日</td>
        <td class="num">${h.n}</td>
        <td class="num">${h.up_rate}%</td>
        <td class="num">${fmtPct(h.mean)}</td>
        <td class="num">${fmtPct(h.median)}</td>
        <td class="num">${fmtPct(h.p25)} ~ ${fmtPct(h.p75)}</td>
      </tr>`).join('');
    const llm = r.llm || {};
    const titleSym = s.sym || r.sym || code;
    const livePx = s.live_px != null ? s.live_px : s.px;
    const barPx = s.px_bar != null ? s.px_bar : s.px;
    const liveLine = s.live
      ? `现价 <strong>${livePx}</strong>${s.live_chg_pct != null ? ' (' + fmtPct(s.live_chg_pct) + ')' : ''} · 实时 ${esc(s.live_time || s.date || '')}`
      : `现价 ${livePx ?? '—'}（无实时，日K）`;
    const barLine = `相似研究截面日K: ${esc(s.bar_date || s.date || '')} 收盘 ${barPx ?? '—'}（日K收盘后才更新，故会停在上一交易日）`;
    $('forecastOut').innerHTML = `
      <div class="score-block">
        <div><strong>${esc(titleSym)}</strong> · ${liveLine}
          <span class="pill">${esc(r.lean || '')}</span>
          <span class="${confCls}">置信 ${esc(conf)} ${r.confidence_score ?? ''}</span>
          <span class="badge">可行动 ${r.actionable ? '是' : '否'}</span>
          <span class="badge">大盘 ${esc(s.market_light || '')}</span>
        </div>
        <div class="note">${barLine}</div>
        <div class="sub num">RSI ${s.rsi ?? '—'} · bias ${s.bias20 ?? '—'}% · 超额20日 ${fmtPct(s.excess20)} · 相似日 ${s.analogs ?? '—'}（均按日K截面）</div>
        <div class="note" style="margin-top:8px">${esc(r.lean_reason || '')}</div>
        <div class="note">${esc(r.method || '')}</div>
        <div class="note">${esc(r.backtest_note || '')}</div>
      </div>
      <div class="scroll"><table>
        <thead><tr><th>持有</th><th>n</th><th>历史上涨占比</th><th>均值</th><th>中位</th><th>P25~P75</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="score-block" style="margin-top:12px">
        <div><strong>解读</strong> <span class="badge">${esc(llm.source || 'template')}</span></div>
        <p>${esc(llm.summary || '以统计表为准；默认未调用大模型。')}</p>
        <p class="note">纪律：${esc(llm.action || '研究参考，红灯空仓。')}</p>
        ${(llm.risks || []).map(x => `<div class="note">· ${esc(x)}</div>`).join('')}
        ${llm.hint ? `<div class="note">${esc(llm.hint)}</div>` : ''}
      </div>
      <p class="note">${esc(r.disclaimer || '')}</p>`;
  } catch (e) {
    const msg = (e && e.name === 'AbortError') ? '研究超时，请再试一次（或检查代码是否正确）' : ('失败: ' + e);
    alert(msg);
    $('forecastOut').innerHTML = `<p class="note">${esc(msg)}</p>`;
  } finally {
    setBusy(false);
  }
}
async function doRecap(cached) {
  setBusy(true);
  const useCached = cached !== false;
  setText('recapNote', useCached ? '读取已有日报…' : '重新生成复盘…');
  try {
    const url = '/api/recap?cached=' + (useCached ? '1' : '0');
    const r = await fetch(url).then(x => x.json());
    if (!r.ok && r.error) { alert(r.error); setText('recapNote', r.error); return; }
    const act = r.action || '—';
    const light = r.light || '—';
    setText('recapNote', (r.note || '') + ' ' + (r.disclaimer || '') + (r.file ? ' · ' + r.file : ''));
    const map = $('recapMap');
    const ret20 = r.ret20 != null ? ((Number(r.ret20) >= 0 ? '+' : '') + Number(r.ret20).toFixed(2) + '%') : '—';
    const close = r.close != null ? Number(r.close).toFixed(2) : '—';
    if (map) {
      map.innerHTML = `
        <div class="score-block">
          <div><strong class="${lightClass(light)}">${esc(light)}</strong> · 操作口径 <strong class="${act === '空仓' ? 'light-red' : ''}">${esc(act)}</strong>
            <span class="badge">${esc(r.date || '')}</span>
            ${r.cached ? '<span class="badge">cached</span>' : ''}
          </div>
          <div class="note">沪深300 ${close} · 近20日 ${ret20} · MA20=${esc(String(r.above_ma20))} · MA60=${esc(String(r.above_ma60))}</div>
          <div class="note">出场 PATH tight TP4/SL3 · 买入向同行业≤2 · 观察每行业≤3 · 观察现价&lt;30元（优先实时） · 现金≥25% 单只≤15% 持仓≤6 今日新建≤3</div>
        </div>`;
    }
    const tb = $('tbody-recap-buy');
    const buy = r.buy || [];
    if (tb) {
      if (!buy.length) {
        tb.innerHTML = '<tr><td colspan="7" class="note">今日无买入向候选。禁止从观察层抽票凑 3×3。</td></tr>';
      } else {
        tb.innerHTML = buy.map(x => `
          <tr>
            <td>${esc(x.industry || '未知')}</td>
            <td>${esc(x.name)}</td>
            <td class="num">${esc(x.sym)}</td>
            <td class="num">${x.px ?? '—'}</td>
            <td class="num">${x.qscore ?? '—'}</td>
            <td>${esc(x.tier || r.tier || '—')}</td>
            <td class="note">PATH tight TP4/SL3</td>
          </tr>`).join('');
      }
    }
    const obsBox = $('recapObs');
    const groups = r.observe_groups || [];
    if (obsBox) {
      if (!groups.length) {
        obsBox.innerHTML = '<p class="note">无观察名单。</p>';
      } else {
        obsBox.innerHTML = groups.map(g => {
          const rows = g.rows || [];
          const body = rows.map(x => `
            <tr>
              <td>${esc(x.name)}</td>
              <td class="num">${esc(x.sym)}</td>
              <td class="num">${x.menga ?? '—'}</td>
              <td class="num">${x.px ?? '—'}</td>
            </tr>`).join('');
          return `
            <p class="note" style="margin:10px 0 4px">${esc(g.industry || '未知')} · 观察（${rows.length} 只，上限 3）</p>
            <div class="scroll"><table>
              <thead><tr><th>名称</th><th>代码</th><th>α</th><th>现价</th></tr></thead>
              <tbody>${body}</tbody>
            </table></div>`;
        }).join('');
      }
    }
  } catch (e) {
    alert('复盘失败: ' + e);
    setText('recapNote', '失败: ' + e);
  } finally {
    setBusy(false);
  }
}
async function doGufeng(rescan) {
  const side = ($('gufengSide') && $('gufengSide').value) || 'all';
  const wantDiv = !!( $('gufengDiv') && $('gufengDiv').checked );
  const divDays = ($('gufengDivDays') && $('gufengDivDays').value) || '5';
  const wantLt = !!( $('gufengLt') && $('gufengLt').checked );
  const ltMax = ($('gufengLtMax') && $('gufengLtMax').value) || '1';
  setBusy(true);
  if (rescan) {
    setText('gufengNote', '已请求后台扫描… 全主板较慢，请稍后点「加载最新结果」');
  } else {
    let tip = '加载中…';
    if (wantDiv && wantLt) tip = '加载中（底背离+缩量）…';
    else if (wantDiv) tip = '加载中（买侧叠加底背离，约数秒）…';
    else if (wantLt) tip = '加载中（买侧缩量）…';
    setText('gufengNote', tip);
  }
  try {
    let url = '/api/gufeng?side=' + encodeURIComponent(side) + (rescan ? '&rescan=1' : '');
    if (wantDiv) url += '&divergence=1&div_days=' + encodeURIComponent(divDays);
    if (wantLt) url += '&low_turnover=1&turnover_max=' + encodeURIComponent(ltMax);
    const r = await fetch(url).then(x => x.json());
    if (!r.ok && r.error) {
      setText('gufengNote', r.error);
      $('tbody-gufeng').innerHTML = '<tr><td colspan="14" class="note">' + esc(r.error) + '</td></tr>';
      return;
    }
    if (r.scanning) {
      setText('gufengBadge', '扫描中…');
      setText('gufengNote', (r.note || '扫描进行中') + ' ' + (r.disclaimer || ''));
      if (!window._gufengPoll) {
        window._gufengPoll = setInterval(async () => {
          try {
            let pu = '/api/gufeng?side=' + encodeURIComponent(($('gufengSide')||{}).value || 'all');
            if ($('gufengDiv') && $('gufengDiv').checked) {
              pu += '&divergence=1&div_days=' + encodeURIComponent(($('gufengDivDays')||{}).value || '5');
            }
            if ($('gufengLt') && $('gufengLt').checked) {
              pu += '&low_turnover=1&turnover_max=' + encodeURIComponent(($('gufengLtMax')||{}).value || '1');
            }
            const s = await fetch(pu).then(x => x.json());
            if (s && !s.scanning && s.ok) {
              clearInterval(window._gufengPoll);
              window._gufengPoll = null;
              doGufeng(false);
            } else if (s && s.scanning) {
              setText('gufengBadge', '扫描中…');
            }
          } catch (e) {}
        }, 8000);
      }
      if (!(r.rows || []).length) {
        setBusy(false);
        return;
      }
    }
    const nb = r.n_buy ?? 0, ns = r.n_sell ?? 0;
    let badge = '买' + nb + ' · 卖' + ns;
    if (r.divergence && r.n_buy_div != null) {
      badge = '强化买' + r.n_buy_div + '/' + (r.n_buy_all ?? nb) + ' · 卖' + ns;
    }
    if (r.low_turnover && r.n_buy_lt != null) {
      badge = '缩量买' + r.n_buy_lt + '/' + (r.n_buy_lt_before ?? nb) + ' · 卖' + ns;
    }
    setText('gufengBadge',
      badge +
      (r.source ? ' · ' + r.source : '') +
      (r.mtime ? ' · ' + r.mtime : '') +
      (r.scanning ? ' · 扫描中' : '')
    );
    setText('gufengNote', (r.note || '') + ' ' + (r.disclaimer || ''));
    const rows = r.rows || [];
    const tb = $('tbody-gufeng');
    if (!tb) return;
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="14" class="note">无匹配（或中间区观望）。可切换筛选/取消底背离/缩量或重新扫描。</td></tr>';
    } else {
      tb.innerHTML = rows.map(x => {
        const isBuy = String(x.side || '').indexOf('买') >= 0;
        const pill = isBuy ? 'pill' : 'pill bad';
        let divCell = '—';
        if (x.divergence === true) {
          divCell = '是' + (x.div_ago != null ? '(T-' + x.div_ago + ')' : '');
        } else if (x.divergence === false) {
          divCell = '否';
        }
        const fmtPx = (v) => (v != null && v !== '' && !Number.isNaN(Number(v)) ? Number(v).toFixed(2) : '—');
        let tbCell = fmtPx(x.target_buy);
        if (x.target_note) tbCell += '<div style="font-size:11px;opacity:.75">' + esc(x.target_note) + '</div>';
        return '<tr>' +
          '<td><span class="' + pill + '">' + esc(x.side) + '</span></td>' +
          '<td class="num">' + esc(x.code || x.sym) + '</td>' +
          '<td>' + esc(x.name) + '</td>' +
          '<td class="num">' + fmtPx(x.ref_price) + '</td>' +
          '<td class="num">' + tbCell + '</td>' +
          '<td class="num">' + fmtPx(x.buy_ceiling) + '</td>' +
          '<td class="num">' + fmtPx(x.buy_prefer) + '</td>' +
          '<td class="num">' + fmtPx(x.sell_floor) + '</td>' +
          '<td class="num">' + (x.val_pct_pct != null ? Number(x.val_pct_pct).toFixed(1) : '—') + '</td>' +
          '<td class="num">' + (x.turnover != null && x.turnover !== '' && !Number.isNaN(Number(x.turnover)) ? Number(x.turnover).toFixed(2) : '—') + '</td>' +
          '<td class="num">' + (x.pe != null ? x.pe : '—') + '</td>' +
          '<td class="num">' + (x.pb != null ? x.pb : '—') + '</td>' +
          '<td class="num">' + esc(divCell) + '</td>' +
          '<td class="num">' + esc(x.asof || '') + '</td>' +
          '</tr>';
      }).join('');
    }
  } catch (e) {
    alert('估值谷峰失败: ' + e);
    setText('gufengNote', '失败: ' + e);
  } finally {
    setBusy(false);
  }
}
function tab(name) {
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  ['action','recap','joint','full','pass','strict','rs','near','score','forecast','patterns','yaogu','alpha','gufeng','cool','discipline'].forEach(t => {
    const el = $(`tab-${t}`);
    if (el) el.classList.toggle('hidden', t !== name);
  });
  if (name === 'recap' && !window._recapLoaded) {
    window._recapLoaded = true;
    doRecap(true);
  }
  if (name === 'gufeng' && !window._gufengLoaded) {
    window._gufengLoaded = true;
    doGufeng(false);
  }
}
loadStatus().then(() => doScan(false)).then(() => doAction()).then(() => setupAutoLive());
const _autoEl = document.getElementById('autoLive');
if (_autoEl) _autoEl.addEventListener('change', setupAutoLive);
</script>
</body>
</html>
"""


def main():
    # 预热：不强制扫盘，打开页面再扫
    url = f"http://{HOST}:{PORT}/"
    print("=" * 50)
    print("  量化研究系统")
    print(f"  打开: {url}")
    print("  池子: 主板 A 股（剔创业板/科创板/ST）")
    print("  停止: Ctrl+C")
    print("=" * 50)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.server_close()


if __name__ == "__main__":
    main()
