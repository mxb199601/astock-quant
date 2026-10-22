# -*- coding: utf-8 -*-
"""扫描本地缓存：高胜率公式 + 打分。

用法：
  python3 screen_market.py --theme
  python3 screen_market.py --theme --mode base|strict|elite
默认 mode=strict（回测验证：配 TP8/SL5 胜率约 70% 量级）
"""
import glob
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from lib.data import is_mainboard_a, is_st_name, load_fundamentals, market_regime
from lib.factors import formula3_today, rsi
from lib.paths import CACHE, OUTPUT
from lib.themes import THEME_POOL

os.makedirs(OUTPUT, exist_ok=True)
fund = load_fundamentals(force=False)

only_theme = "--theme" in sys.argv
# --mode X
entry_mode = "strict"
if "--mode" in sys.argv:
    i = sys.argv.index("--mode")
    if i + 1 < len(sys.argv):
        entry_mode = sys.argv[i + 1].lower()


def load(sym):
    fp = os.path.join(CACHE, sym + ".csv")
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, dtype={"date": str})
    return df if len(df) >= 320 else None


idx = load("sh000300")
if idx is None:
    print("缺少 sh000300 缓存，请先: python cli.py refresh --theme")
    sys.exit(1)

ic = idx["close"]
idx20 = (ic.iloc[-1] / ic.iloc[-21] - 1) * 100
regime = market_regime()
idx_up = regime.get("above_ma20", bool(ic.iloc[-1] > ic.rolling(20).mean().iloc[-1]))
idx_up60 = regime.get("above_ma60", bool(ic.iloc[-1] > ic.rolling(60).mean().iloc[-1]))
light = regime.get("light", "绿灯" if idx_up else "红灯")

print(f"扫盘范围: {'主题池' if only_theme else '主板缓存'} | 入场模式: {entry_mode}")
print("股票池规则: 仅沪深主板，剔除创业板(300)/科创板(688)/ST")
print(f"大盘: 沪深300近20日 {idx20:+.1f}%, {light} | MA20={idx_up} MA60={idx_up60}")
if light == "红灯":
    print("⚠ 红灯环境：按手册应空仓；下列仅为结构扫描，不代表可交易。")
print()

from lib.data import universe_mom60
umom = universe_mom60()


def fnum(x):
    try:
        return float(x)
    except Exception:
        return None


def get_theme(sym):
    return THEME_POOL[sym][1] if sym in THEME_POOL else ""


def analyze(sym):
    df = load(sym)
    if df is None:
        return None
    c = df["close"]
    v = df["vol"]
    ma5 = c.rolling(5).mean().iloc[-1]
    ma10 = c.rolling(10).mean().iloc[-1]
    ma20 = c.rolling(20).mean().iloc[-1]
    ma60 = c.rolling(60).mean().iloc[-1]
    ma250 = c.rolling(250).mean()
    px = float(c.iloc[-1])
    vr = float(v.rolling(5).mean().iloc[-1] / v.rolling(60).mean().iloc[-1])
    zf60 = (px / float(c.iloc[-61]) - 1) * 100
    rs20 = (px / float(c.iloc[-21]) - 1) * 100
    bias = (px / float(ma20) - 1) * 100
    has250 = ma250.iloc[-1] == ma250.iloc[-1]
    f = fund.get(sym, {})
    name = f.get("name", THEME_POOL.get(sym, (sym, ""))[0])
    pe, pb, turn = fnum(f.get("pe")), fnum(f.get("pb")), fnum(f.get("turn"))
    pctile = float(np.searchsorted(umom, zf60) / len(umom) * 100) if len(umom) else 50.0
    rsi_val = rsi(c, 14)
    vol_surge = float(v.iloc[-1]) > float(v.rolling(5).mean().iloc[-1] * 1.5)

    if not is_mainboard_a(sym) or is_st_name(name):
        return None
    veto = (
        (not is_st_name(name))
        and (has250 and px > ma250.iloc[-1] and ma250.iloc[-1] >= ma250.iloc[-21])
        and (px > df["low"].rolling(60).min().iloc[-1] * 1.05 and px > ma20)
        and ((pb is None or pb < 10) and (pe is None or pe > 0))
    )
    if entry_mode in ("strict", "elite"):
        veto = veto and idx_up and idx_up60 and rsi_val < 65 and 5 < zf60 < 35
    if entry_mode == "elite":
        veto = veto and abs(bias) < 8 and rs20 > idx20 + 3 and 1.2 < vr < 2.5

    f3, _ = formula3_today(df, idx20, idx_up, idx_up60, mode=entry_mode)
    plus = sum(
        [
            ma5 > ma10 > ma20 > ma60,
            rs20 > idx20,
            pctile >= 70,
            turn is not None and 2 <= turn <= 15,
            1 < vr < 3,
            0 < zf60 < 60,
            bias < 15,
            rsi_val < 70,
            vol_surge,
        ]
    )
    missing = []
    if not (ma5 > ma10 > ma20 > ma60):
        missing.append("均线多头")
    if not (px > ma20):
        missing.append("站上MA20")
    if not veto:
        missing.append("一票否决")
    if not f3:
        missing.append(f"公式({entry_mode})")
    if rsi_val >= 65:
        missing.append("RSI>=65")
    if not (5 < zf60 < 35):
        missing.append("涨幅带")
    return {
        "name": name,
        "sym": sym,
        "px": round(px, 2),
        "plus": plus,
        "veto": veto,
        "f3": f3,
        "pctile": round(pctile),
        "zf60": round(zf60, 1),
        "rsi": round(rsi_val, 1),
        "pe": pe,
        "pb": pb,
        "turn": round(turn, 1) if turn else None,
        "missing": missing,
        "theme": get_theme(sym),
    }


if only_theme:
    syms = [s for s in THEME_POOL.keys() if is_mainboard_a(s)]
else:
    syms = []
    for fp in glob.glob(os.path.join(CACHE, "sz*.csv")) + glob.glob(os.path.join(CACHE, "sh6*.csv")):
        sym = os.path.basename(fp)[:-4]
        if sym == "sh000300":
            continue
        if not is_mainboard_a(sym):
            continue
        name = (fund.get(sym) or {}).get("name", "")
        if name and is_st_name(name):
            continue
        syms.append(sym)

rows = []
close_list = []
for sym in syms:
    st = analyze(sym)
    if st is None:
        continue
    if st["veto"] and st["f3"] and st["plus"] >= 5:
        rows.append(st)
    if only_theme and (st["veto"] or st["plus"] >= 4 or st["f3"]):
        close_list.append(st)

rows.sort(key=lambda x: (x["plus"], x["pctile"]), reverse=True)
close_list.sort(key=lambda x: (x["f3"], x["plus"], x["pctile"]), reverse=True)

print(f"今日通过(模式={entry_mode}, 公式+否决+加分≥5) 共 {len(rows)} 只 | 池子 {len(umom)} 只\n")
print("%-8s %-10s %-6s %7s %6s %5s %6s %7s %5s %s" % ("名称", "代码", "加分", "现价", "RSI", "分位", "60日%", "换手", "公式", "主题"))
for st in rows:
    print(
        "%-8s %-10s %d/9  %7.2f %6.1f %4d  %+6.1f %6s %4s %s"
        % (
            st["name"][:8],
            st["sym"],
            st["plus"],
            st["px"],
            st["rsi"],
            st["pctile"],
            st["zf60"],
            st["turn"] if st["turn"] is not None else "-",
            "✓" if st["f3"] else "✗",
            st["theme"],
        )
    )
print("\nCODES:", " ".join(r["sym"] for r in rows))
print("出场建议: 止盈+8% / 止损-5%（本地回测高胜率配置）")

if only_theme:
    print("\n【主题池接近/跟踪】（主板）")
    mainboard = [st for st in close_list if is_mainboard_a(st["sym"])]
    for st in mainboard[:12]:
        miss = ", ".join(st["missing"]) if st["missing"] else "结构较好"
        tag = "PASS" if st["f3"] and st["veto"] else "watch"
        print(
            f"  [{tag}] {st['name']} ({st['sym']}) 加分{st['plus']}/9 RSI{st['rsi']} "
            f"60日{st['zf60']:+.1f}%  缺: {miss}"
        )
    if mainboard:
        pd.DataFrame(
            [
                {
                    "代码": st["sym"],
                    "名称": st["name"],
                    "加分": st["plus"],
                    "RSI": st["rsi"],
                    "主题": st["theme"],
                    "60日涨幅%": st["zf60"],
                    "公式通过": st["f3"],
                    "现价": st["px"],
                }
                for st in mainboard
            ]
        ).to_csv(os.path.join(OUTPUT, "mainboard_theme_close.csv"), index=False, encoding="utf-8-sig")
        print("已输出 output/mainboard_theme_close.csv")
