#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主题潜力股增强筛子（人形机器人 + 低空经济）
基于你的《股票操作手册》 + screen_market.py 严格逻辑。

- 只扫描主题池（准确代码）
- 应用完整 一票否决 + 公式三 + 加分
- 输出通过的、接近的、和详细打分
- 附带简单主题催化剂提示
- 严格遵守你回测验证的纪律

用法：
  python3 theme_screen.py              # 默认：显示所有主题股状态 + 推荐
  python3 theme_screen.py --close      # 只显示接近通过或已通过的
  python3 theme_screen.py --full       # 类似全市场格式，只列通过的

与 theme_watch.py 配合使用：
  先 theme_watch.py --score 看个体
  再 theme_screen.py 看批量是否触发你的公式
"""

import glob, os, json, warnings
import numpy as np, pandas as pd
warnings.simplefilter("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache", "kline_sina")
fund = json.load(open(os.path.join(CACHE, "_fundamentals.json")))

# === 准确主题池（已验证代码 + 2026催化剂） ===
THEME_STOCKS = {
    # 人形机器人核心
    "sh688017": ("绿的谐波", "人形机器人-谐波减速器龙头，特斯拉/宇树链"),
    "sh688028": ("沃尔德", "人形机器人-精密工具，高调研，当前最接近你标准"),
    "sz300607": ("拓斯达", "人形机器人-本体+产线，加分满分待技术"),
    "sz002050": ("三花智控", "人形机器人-执行器+热管理，与绿的组合"),
    "sz300718": ("长盛轴承", "人形机器人-轴承+丝杠"),
    "sz300124": ("汇川技术", "人形机器人-伺服平台，确定性较高"),
    "sz002747": ("埃斯顿", "人形机器人-工业+人形双场景"),
    "sh603728": ("鸣志电器", "人形机器人-灵巧手电机"),
    "sh688322": ("奥比中光", "人形机器人-3D视觉感知"),
    "sz300953": ("震裕科技", "人形机器人-丝杠执行器"),
    "sz300100": ("双林股份", "人形机器人-丝杠"),
    "sz002031": ("巨轮智能", "人形机器人-RV减速器国产突破"),
    # 低空经济
    "sz002085": ("万丰奥威", "低空经济-整机+通航+eVTOL，估值相对安全"),
    "sz300681": ("英搏尔", "低空经济-电推进系统"),
    "sh688070": ("纵横股份", "低空经济-无人机+低空治理系统"),
}

def load(sym):
    fp = os.path.join(CACHE, sym + ".csv")
    if not os.path.exists(fp): return None
    df = pd.read_csv(fp, dtype={"date": str})
    return df if len(df) >= 320 else None

def fnum(x):
    try: return float(x)
    except: return None

def get_stock_status(sym):
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
    px = c.iloc[-1]

    vr = v.rolling(5).mean().iloc[-1] / v.rolling(60).mean().iloc[-1]
    zf60 = (px / c.iloc[-61] - 1) * 100
    rs20 = (px / c.iloc[-21] - 1) * 100
    bias = (px / ma20 - 1) * 100

    has250 = ma250.iloc[-1] == ma250.iloc[-1]
    f = fund.get(sym, {})
    name = f.get("name", sym)
    pe = fnum(f.get("pe"))
    pb = fnum(f.get("pb"))
    turn = fnum(f.get("turn"))

    # 构建全市场动量分布（轻量版，复用逻辑）
    umom = []
    for fp in glob.glob(os.path.join(CACHE, "sz*.csv")) + glob.glob(os.path.join(CACHE, "sh6*.csv")) + glob.glob(os.path.join(CACHE, "sh0*.csv")):
        try:
            d = pd.read_csv(fp)
            if len(d) > 61:
                umom.append((d["close"].iloc[-1] / d["close"].iloc[-61] - 1) * 100)
        except:
            pass
    umom = np.sort(np.array(umom))
    pctile = np.searchsorted(umom, zf60) / len(umom) * 100 if len(umom) > 0 else 50

    # 大盘状态
    idx = load("sh000300")
    ic = idx["close"]
    idx20 = (ic.iloc[-1] / ic.iloc[-21] - 1) * 100
    idx_up = ic.iloc[-1] > ic.rolling(20).mean().iloc[-1]

    # 一票否决 (完全复制你的逻辑)
    veto = ("ST" not in name.upper()) and \
           (has250 and px > ma250.iloc[-1] and ma250.iloc[-1] >= ma250.iloc[-21]) and \
           (px > df["low"].rolling(60).min().iloc[-1] * 1.05 and px > ma20) and \
           ((pb is None or pb < 10) and (pe is None or pe > 0))

    # 公式三
    f3 = idx_up and (rs20 > idx20) and \
         (has250 and px > ma250.iloc[-1] and ma250.iloc[-1] >= ma250.iloc[-21]) and \
         (ma5 > ma10 > ma20 > ma60) and (1 < vr < 3) and (0 < zf60 < 50)

    # 加分
    plus = sum([
        ma5 > ma10 > ma20 > ma60,
        rs20 > idx20,
        pctile >= 70,
        (turn is not None and 2 <= turn <= 15),
        1 < vr < 3,
        0 < zf60 < 60,
        bias < 15
    ])

    return {
        "sym": sym,
        "name": name,
        "px": round(px, 2),
        "pe": pe,
        "pb": pb,
        "turn": round(turn, 1) if turn else None,
        "zf60": round(zf60, 1),
        "rs20": round(rs20, 1),
        "pctile": round(pctile),
        "veto": veto,
        "f3": f3,
        "plus": plus,
        "ma5_gt_10_20_60": ma5 > ma10 > ma20 > ma60,
        "above_ma20": px > ma20,
        "above_ma250": px > ma250.iloc[-1],
        "vr": round(vr, 2),
        "idx_up": idx_up,
        "idx20": round(idx20, 1),
        "theme_note": THEME_STOCKS.get(sym, ("", ""))[1]
    }

def main():
    import sys
    only_close = "--close" in sys.argv
    full_mode = "--full" in sys.argv

    idx = load("sh000300")
    ic = idx["close"]
    idx20 = (ic.iloc[-1] / ic.iloc[-21] - 1) * 100
    idx_up = ic.iloc[-1] > ic.rolling(20).mean().iloc[-1]
    print("大盘: 沪深300近20日 %+.1f%%, %s\n" % (idx20, "绿灯" if idx_up else "红灯"))

    results = []
    for sym in THEME_STOCKS:
        st = get_stock_status(sym)
        if st:
            results.append(st)

    # 排序：先通过的，再接近的
    results.sort(key=lambda x: (x["veto"] and x["f3"] and x["plus"] >= 5, x["plus"], x["pctile"]), reverse=True)

    passed = []
    close_ones = []
    for r in results:
        is_pass = r["veto"] and r["f3"] and r["plus"] >= 5
        is_close = r["veto"] and (r["plus"] >= 5 or (r["plus"] >= 4 and not r["f3"]))

        if is_pass:
            passed.append(r)
        if is_close:
            close_ones.append(r)

    if full_mode:
        print("主题池中通过(公式三+打分≥5) 的：")
        if not passed:
            print("  （无）")
        for r in passed:
            print("%-7s %-9s %d/7   %6.2f %7s %6s %6s%% %+6.1f %4d  %s" % (
                r["name"], r["sym"], r["plus"], r["px"],
                round(r["pe"],1) if r["pe"] else "-", round(r["pb"],1) if r["pb"] else "-",
                r["turn"], r["zf60"], r["pctile"], r["theme_note"][:20]))
        print("\nCODES: " + " ".join([r["sym"] for r in passed]))
        return

    print("=== 主题潜力股筛查（严格按你的手册） ===\n")

    if passed:
        print("✅ 已完全通过你的公式三 + 加分≥5：")
        for r in passed:
            print(f"  {r['name']} ({r['sym']})  加分{r['plus']}/7  现价{r['px']}")
        print()
    else:
        print("✅ 已完全通过：无（符合当前市场，主题股大多在回调/等待确认）\n")

    if close_ones:
        print("⚠️  接近通过（通过否决 或 高加分，值得重点观察）：")
        for r in close_ones:
            status = []
            if r["veto"]: status.append("否决通过")
            if r["f3"]: status.append("公式三通过")
            if r["plus"] >= 5: status.append(f"加分{r['plus']}/7")
            elif r["plus"] >= 4: status.append(f"加分{r['plus']}/7(接近)")
            print(f"  {r['name']} ({r['sym']})  {' + '.join(status)}  现价{r['px']}")
            print(f"      {r['theme_note']}")
            if not r["ma5_gt_10_20_60"]:
                print("      缺：均线多头 (MA5>MA10>MA20>MA60)")
            if not r["above_ma20"]:
                print("      缺：站上MA20")
            print()
    else:
        print("接近的：暂无明显\n")

    print("=== 手册纪律提醒 ===")
    print("• 只有同时满足 一票否决 + 公式三 + 加分≥5 才算触发")
    print("• 即使接近，也要等“放量突破 + 缩量回踩不破”确认买点")
    print("• 大盘绿灯时参与，主题只是加分项，不是万能")
    print("• 建议每周运行一次，结合半年报/订单更新")
    print("\n配合使用：")
    print("  python3 theme_watch.py --checklist     # 看沃尔德详细自检卡")
    print("  python3 score_stocks.py 688028 ...     # 单个详细打分")

if __name__ == "__main__":
    main()