#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载主板（非ST）+ 沪深300 自 2012 年起的日K，供谷峰长历史回放（研究用）。

- 来源：东财 push2his，后复权（fqt=2）。前复权在东财是减法复权，分红多的票早年会变负数，不能算涨跌幅
- 存放：cache/kline_long/{sym}.csv.gz，与日更用的 cache/kline_sina 分开，互不影响
- 节流：每次请求后停 0.25 秒；失败重试（2/5/10 秒退避）；连续 15 只失败就停（大概率被限流）
- 断点续传：已存在且非空的文件跳过
用法：$PY scripts/fetch_long_history.py [--beg 20120101]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd

from lib.data import _em_secid, filter_mainboard, list_cached_symbols, load_fundamentals
from lib.paths import ROOT as PROOT

OUT = os.path.join(PROOT, "cache", "kline_long")
LOG = os.path.join(PROOT, "logs", "fetch_long_history.log")


def fetch(secid: str, beg: str) -> pd.DataFrame | None:
    u = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56"
        f"&klt=101&fqt=2&beg={beg}&end=20500101&lmt=10000"
    )
    raw = subprocess.run(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0", u], capture_output=True).stdout
    data = json.loads(raw.decode("utf-8", "ignore") or "{}")
    kl = (data.get("data") or {}).get("klines") or []
    rows = []
    for line in kl:
        p = line.split(",")
        if len(p) >= 6:
            rows.append((p[0], float(p[1]), float(p[3]), float(p[4]), float(p[2]), float(p[5])))
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "vol"])


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beg", default="20120101")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    fund = load_fundamentals(force=False)
    syms = ["sh000300"] + filter_mainboard(list_cached_symbols(), fund=fund, drop_st=True)
    log(f"开始：{len(syms)} 只（含沪深300），起始 {args.beg} → {OUT}")
    done = skipped = failed = streak = 0
    for i, sym in enumerate(syms):
        path = os.path.join(OUT, f"{sym}.csv.gz")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            skipped += 1
            continue
        secid = "1.000300" if sym == "sh000300" else _em_secid(sym)
        df = None
        for wait in (0, 2, 5, 10):
            if wait:
                time.sleep(wait)
            try:
                df = fetch(secid, args.beg)
            except Exception:
                df = None
            if df is not None:
                break
        if df is None:
            failed += 1
            streak += 1
            log(f"失败 {sym}（连续失败 {streak}）")
            if streak >= 15:
                log("连续 15 只失败，疑似被限流，先停下；稍后重跑会从断点继续")
                return 2
        else:
            streak = 0
            df.to_csv(path, index=False, compression="gzip")
            done += 1
        if (i + 1) % 100 == 0:
            log(f"进度 {i+1}/{len(syms)}：新下 {done}，跳过 {skipped}，失败 {failed}")
        time.sleep(args.sleep)
    log(f"完成：新下 {done}，跳过 {skipped}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
