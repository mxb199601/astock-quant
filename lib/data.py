# -*- coding: utf-8 -*-
"""数据层：拉数 / 缓存 / 前复权近似。对齐个人量化工具常见做法：本地 CSV 缓存 + 强制刷新。"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .paths import CACHE, FUND_PATH, META_PATH


def curl(url: str, timeout: int = 15, referer: str = "https://finance.sina.com.cn") -> str:
    r = subprocess.run(
        ["curl", "-s", "-m", str(timeout), "-A",
         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
         "-e", referer, url],
        capture_output=True,
    )
    return r.stdout.decode("utf-8", "ignore")


def to_sym(code: str) -> str:
    code = code.strip().lower()
    if code.startswith(("sh", "sz")):
        return code
    if code.startswith("6") or code.startswith("5") or code.startswith("9"):
        return "sh" + code
    return "sz" + code


def is_st_name(name: str) -> bool:
    """名称含 ST / *ST / SST / S*ST / 退市 等则视为风险警示或退市整理。"""
    raw = (name or "").strip().replace(" ", "").replace("　", "").replace("＊", "*")
    if not raw:
        return False
    if "退市" in raw:
        return True
    n = raw.upper()
    # ST、*ST、SST、S*ST、ST* 等任意含 ST 的简称
    return "ST" in n


def is_placeholder_name(name: str, sym: str) -> bool:
    """名称空、或就是代码本身（sh600901 / 600901）。"""
    n = (name or "").strip()
    if not n:
        return True
    s = to_sym(str(sym or ""))
    nl, sl = n.lower(), s.lower()
    return nl == sl or nl == sl[2:]


def resolve_name(sym: str, name: str = "", fund: Optional[Dict] = None) -> str:
    """占位名（空/代码本身）用基本面中文名替换；已有中文名不覆盖。"""
    s = to_sym(str(sym or "")) if sym else ""
    n = (name or "").strip()
    if s and not is_placeholder_name(n, s):
        return n
    if fund is None:
        fund = load_fundamentals(False)
    fn = str(((fund.get(s) or {}).get("name") or "")).strip()
    if fn and s and not is_placeholder_name(fn, s):
        return fn
    return n or s


def fill_row_names(rows: List[dict], fund: Optional[Dict] = None) -> List[dict]:
    """给结果行补中文名，并统一 sym。兼容 代码/名称 列。"""
    if not rows:
        return rows
    if fund is None:
        fund = load_fundamentals(False)
    out: List[dict] = []
    for r in rows:
        rr = dict(r)
        raw_sym = rr.get("sym") or rr.get("代码") or rr.get("code") or ""
        s = to_sym(str(raw_sym)) if str(raw_sym).strip() else ""
        if s:
            rr["sym"] = s
        raw_name = rr.get("name")
        if raw_name in (None, ""):
            raw_name = rr.get("名称") or ""
        rr["name"] = resolve_name(s, str(raw_name or ""), fund)
        out.append(rr)
    return out


def is_mainboard_a(sym: str) -> bool:
    """沪深主板 A 股（含原中小板 002/003），排除创业板/科创板/北交所/指数。

    保留:
      上证: 600/601/603/605
      深证: 000/001/002/003
    排除:
      创业板 sz300*
      科创板 sh688*
      北交所 bj* / 8* / 4*
      指数 sh000* sz399*
    """
    s = to_sym(sym)
    if s in ("sh000300", "sh000001", "sz399006", "sz399001"):
        return False
    if s.startswith(("bj", "sh8", "sz8", "sh4", "sz4")):
        return False
    if s.startswith("sh688") or s.startswith("sz30"):
        return False
    if s.startswith("sh"):
        code = s[2:]
        return code.startswith(("600", "601", "603", "605"))
    if s.startswith("sz"):
        code = s[2:]
        return code.startswith(("000", "001", "002", "003"))
    return False


def filter_mainboard(
    symbols: Iterable[str],
    fund: Optional[Dict] = None,
    drop_st: bool = True,
) -> List[str]:
    """过滤为主板非 ST。

    drop_st 时优先读原始基本面缓存（_read_fund_raw）：load_fundamentals
    已把 ST 条目剔掉，用过滤后的 fund 判 ST 会因取不到名称而漏网。
    """
    out = []
    raw_fund = _read_fund_raw() if drop_st else {}
    for raw in symbols:
        sym = to_sym(raw)
        if not is_mainboard_a(sym):
            continue
        if drop_st:
            name = (raw_fund.get(sym) or {}).get("name", "")
            if not name and fund is not None:
                name = (fund.get(sym) or {}).get("name", "")
            if is_st_name(name):
                continue
        out.append(sym)
    return out


def lim_of(sym: str) -> float:
    return 0.205 if (sym.startswith("sz30") or sym.startswith("sh688")) else 0.105


def qfq(df: pd.DataFrame, sym: str) -> pd.DataFrame:
    """按跳空近似前复权（忽略现金分红细节）。"""
    o = df["open"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(c)
    adj = np.ones(n)
    cum = 1.0
    lim = lim_of(sym)
    for t in range(n - 1, 0, -1):
        if c[t - 1] > 0 and abs(o[t] / c[t - 1] - 1) > lim + 0.02:
            cum *= o[t] / c[t - 1]
        adj[t - 1] = cum
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float) * adj
    return df


def load_kline_csv(sym: str, min_bars: int = 280) -> Optional[pd.DataFrame]:
    fp = os.path.join(CACHE, f"{sym}.csv")
    if not os.path.exists(fp):
        return None
    try:
        df = pd.read_csv(fp, dtype={"date": str})
        for c in ["open", "high", "low", "close", "vol"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        return df if len(df) >= min_bars else None
    except Exception:
        return None


def _em_secid(sym: str) -> str:
    s = to_sym(sym)
    code = s[2:]
    # 东财: 沪市1. 深市0.
    return f"1.{code}" if s.startswith("sh") else f"0.{code}"


def fetch_kline_eastmoney(sym: str, lmt: int = 120) -> Optional[pd.DataFrame]:
    """东财/腾讯日K 补最新交易日（新浪 scale=240 常滞后）。"""
    sym = to_sym(sym)
    # 1) 腾讯前复权日K（较稳）
    try:
        u = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
            f"param={sym},day,,,{int(lmt)},qfq"
        )
        raw = subprocess.run(
            ["curl", "-s", "-m", "12", "-A", "Mozilla/5.0", u],
            capture_output=True,
        ).stdout.decode("utf-8", "ignore")
        data = json.loads(raw) if raw else {}
        node = (data.get("data") or {}).get(sym) or {}
        days = node.get("qfqday") or node.get("day") or []
        if days:
            rows = []
            for p in days:
                if len(p) < 5:
                    continue
                rows.append({
                    "date": str(p[0]),
                    "open": float(p[1]),
                    "close": float(p[2]),
                    "high": float(p[3]),
                    "low": float(p[4]),
                    "vol": float(p[5]) if len(p) > 5 else 0.0,
                })
            if rows:
                return pd.DataFrame(rows).dropna().reset_index(drop=True)
    except Exception:
        pass

    # 2) 东财备用
    secid = _em_secid(sym)
    u = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&end=20500101&lmt={int(lmt)}"
    )
    try:
        raw = subprocess.run(
            ["curl", "-s", "-m", "15", "-A", "Mozilla/5.0", u],
            capture_output=True,
        ).stdout.decode("utf-8", "ignore")
        if not raw or "klines" not in raw:
            return None
        data = json.loads(raw)
        klines = (data.get("data") or {}).get("klines") or []
        if not klines:
            return None
        rows = []
        for line in klines:
            p = line.split(",")
            if len(p) < 6:
                continue
            rows.append({
                "date": p[0],
                "open": float(p[1]),
                "close": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "vol": float(p[5]),
            })
        if not rows:
            return None
        return pd.DataFrame(rows).dropna().reset_index(drop=True)
    except Exception:
        return None


def _merge_kline_prefer_new(base: pd.DataFrame, newer: pd.DataFrame) -> pd.DataFrame:
    """按 date 合并，newer 覆盖同日。"""
    if base is None or base.empty:
        return newer
    if newer is None or newer.empty:
        return base
    b = base.copy()
    n = newer.copy()
    b["date"] = b["date"].astype(str)
    n["date"] = n["date"].astype(str)
    b = b.set_index("date")
    n = n.set_index("date")
    for col in ["open", "high", "low", "close", "vol"]:
        if col not in b.columns and col in n.columns:
            b[col] = np.nan
        if col in n.columns:
            b.loc[n.index.intersection(b.index), col] = n.loc[n.index.intersection(b.index), col]
            add = n.index.difference(b.index)
            if len(add):
                b = pd.concat([b, n.loc[add, [c for c in ["open", "high", "low", "close", "vol"] if c in n.columns]]])
    b = b.reset_index().sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    for c in ["open", "high", "low", "close", "vol"]:
        b[c] = pd.to_numeric(b[c], errors="coerce")
    return b.dropna(subset=["close"]).reset_index(drop=True)


def _sessions_behind(last: str, today: str) -> int:
    """缓存末日距今的工作日数（节假日不扣，偏高估——高估只会更早走全量重拉）。"""
    try:
        return max(0, len(pd.bdate_range(last, today)) - 1)
    except Exception:
        return 0


def fetch_kline(sym: str, force: bool = False, min_bars: int = 280) -> Optional[pd.DataFrame]:
    """拉日K：新浪长历史 + 东财补最新交易日（解决新浪盘中/当日滞后）。"""
    fp = os.path.join(CACHE, f"{to_sym(sym)}.csv")
    sym = to_sym(sym)
    today = datetime.now().strftime("%Y-%m-%d")

    def _load_any() -> Optional[pd.DataFrame]:
        if not os.path.exists(fp):
            return None
        try:
            df0 = pd.read_csv(fp, dtype={"date": str})
            for c in ["open", "high", "low", "close", "vol"]:
                if c in df0.columns:
                    df0[c] = pd.to_numeric(df0[c], errors="coerce")
            return df0.dropna(subset=["close"]).reset_index(drop=True)
        except Exception:
            return None

    cached = _load_any()
    if not force and cached is not None and len(cached) >= min_bars:
        last = str(cached["date"].iloc[-1])
        # 本地已是今天或更新：直接用
        if last >= today:
            return cached if len(cached) >= min_bars else None
        # 本地滞后：东财补尾。滞后超过补尾窗口（长期停牌复牌）会拼出空洞，改用大窗口
        lmt = 30 if _sessions_behind(last, today) <= 25 else 400
        em = fetch_kline_eastmoney(sym, lmt=lmt)
        if em is not None and len(em):
            merged = _merge_kline_prefer_new(cached, em)
            if len(merged) >= min_bars:
                if str(merged["date"].iloc[-1]) > last:
                    # 不重做全量 qfq，仅写入（东财已是前复权 fqt=1）
                    merged.to_csv(fp, index=False)
                    return merged
                # 补尾没带来新日期（停牌）：不重写文件伪装 mtime
                merged.attrs["stale_fallback"] = True
                return merged
        # 补尾源不可达：返回的仍是滞后数据，如实标记
        cached.attrs["stale_fallback"] = True
        return cached if len(cached) >= min_bars else None

    # 强制/无缓存：新浪全量 + 东财补尾
    u = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=1023"
    )
    df = None
    try:
        arr = json.loads(curl(u))
        if arr and len(arr) >= max(50, min_bars // 2):
            df = pd.DataFrame(arr).rename(columns={"day": "date", "volume": "vol"})
            for c in ["open", "high", "low", "close", "vol"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna().reset_index(drop=True)
            df = qfq(df, sym)
    except Exception:
        df = None

    got_sina = df is not None
    if df is None:
        df = cached

    em = fetch_kline_eastmoney(sym, lmt=120)
    got_em = em is not None and len(em) > 0
    if got_em:
        if df is not None:
            df = _merge_kline_prefer_new(df, em)
        else:
            df = em

    if df is None or len(df) < min_bars:
        fb = load_kline_csv(sym, min_bars=min_bars)
        if fb is not None and not (got_sina or got_em):
            fb.attrs["stale_fallback"] = True
        return fb

    if not (got_sina or got_em):
        # 全源失败、df 即旧缓存：不重写文件伪装 mtime，如实标记回退
        df.attrs["stale_fallback"] = True
        return df

    df.to_csv(fp, index=False)
    return df


def _read_fund_raw() -> Dict:
    if not os.path.exists(FUND_PATH):
        return {}
    try:
        raw = json.load(open(FUND_PATH, encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _filter_fund(raw: Dict) -> Dict:
    return {
        s: v
        for s, v in (raw or {}).items()
        if is_mainboard_a(s) and not is_st_name((v or {}).get("name", ""))
    }


def _save_fund(fund: Dict) -> None:
    json.dump(fund, open(FUND_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        _write_meta(fund_count=len(_filter_fund(fund)), fund_updated=True)
    except Exception:
        pass


def _fetch_sina_fundamentals(maxpage: int = 60) -> Dict:
    fund: Dict = {}
    for p in range(1, maxpage + 1):
        u = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={p}&num=100&sort=symbol&asc=1"
            "&node=hs_a&symbol=&_s_r_a=page"
        )
        try:
            arr = json.loads(curl(u))
        except Exception:
            continue
        if not arr:
            break
        for d in arr:
            sym = d.get("symbol", "")
            name = d.get("name", "")
            if not is_mainboard_a(sym):
                continue
            # 必须写入 ST 简称：若跳过，戴帽后旧名会永远留在缓存里导致过滤漏网
            fund[sym] = {
                "name": name,
                "pe": d.get("per"),
                "pb": d.get("pb"),
                "turn": d.get("turnoverratio"),
                "nmc": d.get("nmc"),
                "chg": d.get("changepercent"),
                "st": bool(is_st_name(name)),
            }
        time.sleep(0.03)
    return fund


def load_fundamentals(force: bool = False, maxpage: int = 60) -> Dict:
    existing = _read_fund_raw()
    if not force and existing:
        return _filter_fund(existing)
    fetched = _fetch_sina_fundamentals(maxpage)
    if not fetched:
        return _filter_fund(existing)
    # 分页中断时只合并、不整表覆盖（曾经把 3000+ 写成 653）
    merged = dict(existing)
    merged.update(fetched)
    _save_fund(merged)
    return _filter_fund(merged)


def backfill_missing_names(symbols: Optional[Iterable[str]] = None) -> Dict:
    """用实时行情补基本面里缺的中文名，写入缓存。"""
    fund = _read_fund_raw()
    if symbols is None:
        symbols = list_cached_symbols(mainboard_only=True, drop_st=False)
    missing = []
    for raw in symbols:
        s = to_sym(str(raw))
        if not is_mainboard_a(s):
            continue
        if is_placeholder_name((fund.get(s) or {}).get("name", ""), s):
            missing.append(s)
    if missing:
        quotes = fetch_realtime_quotes(missing)
        for s, q in quotes.items():
            qn = ((q or {}).get("name") or "").strip()
            if qn and not is_placeholder_name(qn, s):
                rec = dict(fund.get(s) or {})
                rec["name"] = qn
                fund[s] = rec
        _save_fund(fund)
    return _filter_fund(fund)


def refresh_fund_names(symbols: Optional[Iterable[str]] = None) -> Dict:
    """用新浪实时行情刷新简称（含刚戴帽的 ST），写入原始基本面缓存。

    比整表 force 分页更快：按 60 只一批拉 hq。返回过滤后的非 ST fund（与 load_fundamentals 一致）。
    """
    fund = _read_fund_raw()
    if symbols is None:
        symbols = []
        for fp in sorted(glob.glob(os.path.join(CACHE, "s*.csv"))):
            sym = os.path.basename(fp)[:-4]
            if is_mainboard_a(sym):
                symbols.append(sym)
        # 也刷新缓存里已有、但本地可能无 K 线的条目
        for sym in fund:
            if is_mainboard_a(sym):
                symbols.append(sym)
        symbols = list(dict.fromkeys(symbols))
    else:
        symbols = [to_sym(str(s)) for s in symbols if s]
    if not symbols:
        return _filter_fund(fund)
    quotes = fetch_realtime_quotes(symbols)
    changed = 0
    for s, q in quotes.items():
        qn = ((q or {}).get("name") or "").strip()
        if not qn or is_placeholder_name(qn, s):
            continue
        rec = dict(fund.get(s) or {})
        old = str(rec.get("name") or "")
        if old != qn or bool(rec.get("st")) != is_st_name(qn):
            changed += 1
        rec["name"] = qn
        rec["st"] = bool(is_st_name(qn))
        fund[s] = rec
    if changed or quotes:
        _save_fund(fund)
    return _filter_fund(fund)


def drop_st_signals(rows: List[dict], name_key: str = "name", sym_key: str = "sym") -> List[dict]:
    """对候选买卖行二次剔 ST（按名称；空名时查原始基本面）。"""
    if not rows:
        return rows
    raw = _read_fund_raw()
    out: List[dict] = []
    for r in rows:
        name = str(r.get(name_key) or r.get("名称") or "")
        sym = str(r.get(sym_key) or r.get("代码") or r.get("code") or "")
        if sym:
            sym = to_sym(sym)
        if not name or is_placeholder_name(name, sym):
            name = str((raw.get(sym) or {}).get("name") or name)
        if is_st_name(name):
            continue
        rr = dict(r)
        if name:
            rr[name_key] = name
        out.append(rr)
    return out


def fetch_mainboard_universe(maxpage: int = 60) -> List[str]:
    """从新浪拉主板非 ST 代码列表（不依赖本地缓存）。"""
    codes: List[str] = []
    for p in range(1, maxpage + 1):
        u = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={p}&num=100&sort=symbol&asc=1"
            "&node=hs_a&symbol=&_s_r_a=page"
        )
        try:
            arr = json.loads(curl(u))
        except Exception:
            continue
        if not arr:
            break
        for d in arr:
            sym, name = d.get("symbol", ""), d.get("name", "")
            if is_mainboard_a(sym) and not is_st_name(name):
                codes.append(sym)
        time.sleep(0.03)
    # 去重保序
    return list(dict.fromkeys(codes))


def list_cached_symbols(
    include_index: bool = False,
    mainboard_only: bool = True,
    drop_st: bool = True,
) -> List[str]:
    """本地 K 线代码。默认：仅主板、剔 ST（若有基本面名称）。"""
    fund = None
    if drop_st and mainboard_only:
        try:
            # 必须用未过滤的原始基本面：load_fundamentals 已把 ST 剔掉，
            # 拿它判 ST 会让 ST 股因取不到名称而漏网
            fund = _read_fund_raw()
        except Exception:
            fund = None
    out = []
    for fp in sorted(glob.glob(os.path.join(CACHE, "s*.csv"))):
        sym = os.path.basename(fp)[:-4]
        if sym in ("sh000300", "sh000001", "sz399006", "sz399001"):
            if include_index:
                out.append(sym)
            continue
        if mainboard_only and not is_mainboard_a(sym):
            continue
        if drop_st and fund is not None:
            rec = fund.get(sym) or {}
            name = (rec.get("name") or "")
            if rec.get("st") or (name and is_st_name(name)):
                continue
        out.append(sym)
    return out


def universe_mom60() -> np.ndarray:
    rets = []
    for sym in list_cached_symbols(mainboard_only=True, drop_st=True):
        df = load_kline_csv(sym, min_bars=62)
        if df is None:
            continue
        rets.append((df["close"].iloc[-1] / df["close"].iloc[-61] - 1) * 100)
    return np.sort(np.array(rets)) if rets else np.array([])


def index_ret20(sym: str = "sh000300") -> float:
    df = fetch_kline(sym, force=False, min_bars=30)
    if df is None or len(df) < 21:
        return float("nan")
    c = df["close"].values
    return float((c[-1] / c[-21] - 1) * 100)


def fetch_realtime_quotes(symbols: Iterable[str]) -> Dict[str, dict]:
    """新浪 hq 实时快照（盘中有延迟，非 Level2）。

    返回 {sym: {name, open, prev_close, price, high, low, vol, date, time, chg_pct, live}}
    """
    syms = [to_sym(s) for s in symbols if s]
    out: Dict[str, dict] = {}

    def _f(x, default=float("nan")):
        try:
            if x is None or x == "":
                return default
            return float(x)
        except Exception:
            return default

    for i in range(0, len(syms), 60):
        batch = syms[i : i + 60]
        url = "https://hq.sinajs.cn/list=" + ",".join(batch)
        raw = subprocess.run(
            ["curl", "-s", "-m", "12", "-A", "Mozilla/5.0",
             "-e", "https://finance.sina.com.cn", url],
            capture_output=True,
        ).stdout
        # 新浪 hq 多为 GBK
        text = raw.decode("gbk", "ignore")
        if "hq_str_" not in text:
            text = raw.decode("utf-8", "ignore")
        for line in text.splitlines():
            if "hq_str_" not in line or '="' not in line:
                continue
            try:
                left, right = line.split("=", 1)
                sym = left.split("hq_str_")[-1].strip()
                body = right.strip().rstrip(";").strip().strip('"')
                if not body:
                    continue
                p = body.split(",")
                if len(p) < 4:
                    continue
                name = p[0]
                # 个股标准 32+ 字段
                if len(p) >= 32 and not sym.startswith(("sh000", "sz399")):
                    prev = _f(p[2])
                    price = _f(p[3])
                    chg = (price / prev - 1) * 100 if prev and prev > 0 else float("nan")
                    out[sym] = {
                        "name": name,
                        "open": _f(p[1]),
                        "prev_close": prev,
                        "price": price,
                        "high": _f(p[4]),
                        "low": _f(p[5]),
                        "vol": _f(p[8]),
                        "amount": _f(p[9]),
                        "date": p[30],
                        "time": p[31],
                        "chg_pct": chg,
                        "live": True,
                    }
                    continue
                # 指数完整串 / 其它
                prev = _f(p[2])
                price = _f(p[3])
                # 简版 s_sh000300: name,price,chg,chg%,vol,amount
                if len(p) <= 8 and abs(_f(p[1], 0)) > 10:
                    price = _f(p[1])
                    chg = _f(p[3])
                    prev = price - _f(p[2], 0) if _f(p[2]) == _f(p[2]) else float("nan")
                    out[sym] = {
                        "name": name,
                        "open": float("nan"),
                        "prev_close": prev,
                        "price": price,
                        "high": float("nan"),
                        "low": float("nan"),
                        "vol": _f(p[4]) if len(p) > 4 else float("nan"),
                        "amount": _f(p[5]) if len(p) > 5 else float("nan"),
                        "date": "",
                        "time": "",
                        "chg_pct": chg,
                        "live": True,
                    }
                    continue
                chg = (price / prev - 1) * 100 if prev and prev > 0 else float("nan")
                date = p[30] if len(p) > 30 else (p[-3] if len(p) >= 3 else "")
                tms = p[31] if len(p) > 31 else (p[-2] if len(p) >= 2 else "")
                out[sym] = {
                    "name": name,
                    "open": _f(p[1]),
                    "prev_close": prev,
                    "price": price,
                    "high": _f(p[4]) if len(p) > 4 else float("nan"),
                    "low": _f(p[5]) if len(p) > 5 else float("nan"),
                    "vol": _f(p[8]) if len(p) > 8 else float("nan"),
                    "amount": _f(p[9]) if len(p) > 9 else float("nan"),
                    "date": str(date),
                    "time": str(tms),
                    "chg_pct": chg,
                    "live": True,
                }
            except Exception:
                continue
        time.sleep(0.03)
    return out


def attach_live_prices(
    rows: List[dict],
    sym_key: str = "sym",
    px_key: str = "px",
) -> List[dict]:
    """给结果列表叠加实时现价（字段 live_px / live_chg_pct / live_time；px 同步为实时价）。"""
    if not rows:
        return rows
    syms = []
    for r in rows:
        s = r.get(sym_key)
        if s:
            syms.append(to_sym(str(s)))
    quotes = fetch_realtime_quotes(syms)
    out = []
    for r in rows:
        rr = dict(r)
        s = to_sym(str(rr.get(sym_key) or ""))
        q = quotes.get(s) or {}
        qname = (q.get("name") or "").strip()
        if qname and is_placeholder_name(rr.get("name"), s) and not is_placeholder_name(qname, s):
            rr["name"] = qname
        price = q.get("price")
        if price is not None and price == price and price > 0:
            bar = rr.get("px_bar")
            try:
                bar_ok = bar not in (None, "") and float(bar) > 0
            except (TypeError, ValueError):
                bar_ok = False
            if not bar_ok and not rr.get("live"):
                rr["px_bar"] = rr.get(px_key)
            rr[px_key] = round(float(price), 2)
            rr["live_px"] = round(float(price), 2)
            chg = q.get("chg_pct")
            rr["live_chg_pct"] = round(float(chg), 2) if chg == chg else None
            rr["live_time"] = f"{q.get('date') or ''} {q.get('time') or ''}".strip()
            rr["live"] = True
        else:
            rr["live"] = False
            rr["live_px"] = None
            try:
                bar = rr.get("px_bar")
                if bar not in (None, "") and float(bar) > 0:
                    rr[px_key] = round(float(bar), 2)
            except (TypeError, ValueError):
                pass
        out.append(rr)
    return out


def market_regime(sym: str = "sh000300", live: bool = True) -> dict:
    """大盘红绿灯：优先实时价 + 日线均线；无实时则退回日线收盘。

    live=True（默认）：用新浪 hq 最新价相对日线 MA20/MA60（把实时价视作当日最新点）。
    日 K 往往收盘后才更新，盘中必须靠实时。
    """
    df = fetch_kline(sym, force=False, min_bars=80)
    if df is None or len(df) < 60:
        return {"ok": False, "light": "未知", "ret20": float("nan")}
    closes = df["close"].astype(float)
    bar_date = str(df["date"].iloc[-1])
    px = float(closes.iloc[-1])
    live_meta = {"live": False, "live_time": None, "bar_date": bar_date}

    if live:
        q = fetch_realtime_quotes([sym]).get(sym) or {}
        if q.get("price") and q["price"] == q["price"] and q["price"] > 0:
            px = float(q["price"])
            live_meta = {
                "live": True,
                "live_time": f"{q.get('date') or ''} {q.get('time') or ''}".strip(),
                "live_chg_pct": q.get("chg_pct"),
                "bar_date": bar_date,
            }

    # 用「历史收盘 + 当前价」近似当日序列尾部，算 MA
    hist = closes.iloc[:-1] if live_meta.get("live") else closes
    if live_meta.get("live"):
        series = pd.concat([hist, pd.Series([px])], ignore_index=True)
    else:
        series = closes
    ma20 = float(series.iloc[-20:].mean())
    ma60 = float(series.iloc[-60:].mean())
    ma20_prev = float(series.iloc[-25:-5].mean()) if len(series) >= 25 else ma20
    # ret20：相对 20 根前收盘（不含仅实时点时用 hist）
    base_px = float(closes.iloc[-21]) if len(closes) > 21 else float(closes.iloc[0])
    ret20 = (px / base_px - 1) * 100 if base_px > 0 else float("nan")
    up20 = px > ma20
    up60 = px > ma60
    slope20 = ma20 >= ma20_prev
    if up20 and up60 and slope20:
        light = "绿灯"
    elif not up20:
        light = "红灯"
    else:
        light = "黄灯"
    last_date = live_meta.get("live_time") or bar_date
    return {
        "ok": True,
        "light": light,
        "ret20": float(ret20),
        "above_ma20": bool(up20),
        "above_ma60": bool(up60),
        "ma20_up": bool(slope20),
        "last_date": str(last_date),
        "close": float(px),
        "bar_date": bar_date,
        "live": bool(live_meta.get("live")),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
    }


def _write_meta(**kwargs):
    meta = {}
    if os.path.exists(META_PATH):
        try:
            meta = json.load(open(META_PATH, encoding="utf-8"))
        except Exception:
            meta = {}
    meta.update(kwargs)
    meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
    meta["cache_count"] = len(list_cached_symbols(include_index=True))
    json.dump(meta, open(META_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def refresh(
    symbols: Optional[Iterable[str]] = None,
    fund: bool = True,
    force_kline: bool = True,
    sleep: float = 0.05,
    mainboard_only: bool = True,
    universe: bool = False,
    max_symbols: Optional[int] = None,
    skip_if_fresh: bool = False,
    asof: Optional[str] = None,
) -> dict:
    """刷新基本面 + 指定标的 K 线。

    默认只刷主板非 ST。
    universe=True 时从新浪拉全主板列表再刷（可配合 max_symbols 限流）。
    force_kline=True 默认强制重拉；skip_if_fresh=True 时若本地最后日期>=asof 则跳过。
    """
    stats = {
        "fund": 0, "kline_ok": 0, "kline_fail": 0, "kline_skip": 0, "kline_stale": 0,
        "symbols": [], "skipped_non_mainboard": 0,
    }
    if fund:
        f = load_fundamentals(force=True)
        try:
            f = backfill_missing_names()
        except Exception:
            pass
        stats["fund"] = len(f)
    else:
        f = load_fundamentals(force=False)

    if universe and symbols is None:
        print("拉取主板非ST全市场列表…")
        symbols = fetch_mainboard_universe()
        print(f"主板非ST 共 {len(symbols)} 只")
    elif symbols is None:
        from .themes import THEME_POOL
        symbols = (
            ["sh000300"]
            + list_cached_symbols(mainboard_only=mainboard_only, drop_st=True)
            + list(THEME_POOL.keys())
        )
        symbols = list(dict.fromkeys(symbols))
    else:
        symbols = [to_sym(s) for s in symbols]

    # 过滤
    kept = []
    for sym in symbols:
        if sym in ("sh000300", "sh000001"):
            kept.append(sym)
            continue
        if mainboard_only and not is_mainboard_a(sym):
            stats["skipped_non_mainboard"] += 1
            continue
        if mainboard_only and f:
            name = (f.get(sym) or {}).get("name", "")
            if name and is_st_name(name):
                stats["skipped_non_mainboard"] += 1
                continue
        kept.append(sym)
    symbols = list(dict.fromkeys(kept))
    if max_symbols is not None and len(symbols) > max_symbols + 1:
        # 保留指数 + 前 N
        head = [s for s in symbols if s.startswith("sh000")]
        rest = [s for s in symbols if not s.startswith("sh000")][:max_symbols]
        symbols = head + rest

    total = len(symbols)
    t0 = time.time()
    for i, sym in enumerate(symbols):
        # skip_if_fresh：本地最后交易日已达到 asof 则跳过（日更加速）
        if skip_if_fresh and asof:
            old = load_kline_csv(sym, min_bars=50)
            if old is not None and str(old["date"].iloc[-1]) >= str(asof):
                stats["kline_skip"] += 1
                stats["kline_ok"] += 1
                stats["symbols"].append(sym)
                continue

        df = fetch_kline(sym, force=force_kline, min_bars=200)
        if df is not None and len(df) >= 200:
            if bool(df.attrs.get("stale_fallback")):
                # 全源失败回退旧缓存：数据没更新，不得计成功
                stats["kline_stale"] += 1
            else:
                stats["kline_ok"] += 1
                stats["symbols"].append(sym)
        else:
            stats["kline_fail"] += 1
        if sleep:
            time.sleep(sleep)
        if (i + 1) % 50 == 0 or i + 1 == total:
            elapsed = time.time() - t0
            rate = (i + 1) / max(elapsed, 1)
            eta = (total - i - 1) / max(rate, 0.01)
            print(
                f"  进度 {i+1}/{total} ok={stats['kline_ok']} fail={stats['kline_fail']} "
                f"stale={stats['kline_stale']} skip={stats['kline_skip']} "
                f"{elapsed:.0f}s ETA~{eta/60:.1f}min",
                flush=True,
            )
    if stats["kline_stale"]:
        print(f"  警告：{stats['kline_stale']} 只全源失败、仍是旧数据（未计入 ok）", flush=True)
    _write_meta(
        last_refresh=datetime.now().isoformat(timespec="seconds"),
        last_refresh_ok=stats["kline_ok"],
        last_refresh_fail=stats["kline_fail"],
        last_refresh_stale=stats["kline_stale"],
        fund_count=stats["fund"] or len(f),
        universe="mainboard_no_st" if mainboard_only else "all",
    )
    return stats


def kline_last_date(sym: str) -> Optional[str]:
    """轻读缓存最后一根K线日期（seek 文件尾，不整表解析）。

    只认严格的 YYYY-MM-DD：读到行中片段时返回 None（判为滞后去重拉），
    绝不让 '9999…' 这类片段在字典序比较里冒充新日期。"""
    fp = os.path.join(CACHE, f"{to_sym(sym)}.csv")
    try:
        with open(fp, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 512))
            lines = f.read().decode("utf-8", "ignore").strip().splitlines()
        for line in reversed(lines):
            head = line.split(",", 1)[0].strip()
            if len(head) == 10 and head[4] == "-" and head[7] == "-" and head[:4].isdigit():
                return head
    except OSError:
        return None
    return None


def ensure_kline_fresh(
    symbols: Iterable[str],
    ref_date: str,
    min_bars: int = 80,
    sleep: float = 0.02,
) -> dict:
    """扫描前的新鲜度门：最后K线早于 ref_date 的标的走 fetch_kline 补尾。

    只补滞后者，网络不可用时如实返回 still_stale，不抛异常。"""
    stats = {"checked": 0, "stale": 0, "refreshed": 0, "still_stale": 0}
    ref = str(ref_date)
    t0 = time.time()
    for raw in symbols:
        sym = to_sym(str(raw))
        stats["checked"] += 1
        last = kline_last_date(sym)
        if last is not None and last >= ref:
            continue
        stats["stale"] += 1
        try:
            df = fetch_kline(sym, force=False, min_bars=min_bars)
        except Exception:
            df = None
        if df is not None and len(df) and str(df["date"].iloc[-1]) >= ref:
            stats["refreshed"] += 1
        else:
            stats["still_stale"] += 1
        if sleep:
            time.sleep(sleep)
        if stats["stale"] % 200 == 0:
            print(
                f"  补尾 {stats['stale']} 只滞后（已刷 {stats['refreshed']}）"
                f" {time.time()-t0:.0f}s",
                flush=True,
            )
    return stats
