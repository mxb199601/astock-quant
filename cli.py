#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化工具统一入口（个人研究向，非交易终端 / 非荐股）。

用法:
  python cli.py status
  python cli.py refresh [--theme|--all] [--no-fund]
  python cli.py score 600519 002371 ...
  python cli.py screen [--theme]
  python cli.py backtest [--codes CODE...] [--cost-bps 15]
  python cli.py formulas list|compare|scan
  python cli.py report
  python cli.py serve          # 启动 Web 系统
  python cli.py forecast 600428    # A:本股+行业相似→统计(+大模型)
  python cli.py joint              # B:完整通过∩MENGα冷静(v2)
  python cli.py patterns           # 黄金坑/一阳穿三线等形态
  python cli.py patterns --pattern 黄金坑
  python cli.py alpha [--top 40]   # 自研 MENGα 指标（数据驱动）
  python cli.py cool [--top 40]    # 冷静观察池（MENGα精炼，非荐股）
  python cli.py action             # 今日操作口径：空仓或可关注候选
  python cli.py recap              # 复盘报告（网上骨架×本仓纪律）
  python cli.py live [代码...]     # 实时行情快照（默认沪深300）
  python cli.py stress             # 压力测试（乐观窗 vs 路径出场）
  python cli.py hist-validate      # 多窗口历史验证（MENGα IC + PATH + 红绿灯）
  python cli.py up-prob            # 固定持有1/3/5/10/20日上涨概率
  python cli.py chan               # 缠论v1观察扫描（否决/一买二买，非买入）
  python cli.py chan --compare     # PATH对照: robust vs robust∩结构OK vs 买卖点
  python cli.py feng3             # 逢3必变高低点研究回测（观察层）
  python cli.py gufeng            # 估值一谷买/一峰卖扫描（观察层）
  python cli.py gufeng --divergence  # 买侧叠加近3～5日底背离 → 强化买入 CSV
  python cli.py gufeng --divergence --turnover-max 1  # 一谷+底背离+缩量(换手<1%)
  python cli.py edge               # 边沿阶梯：robust 之上还能靠什么（PATH）
  python cli.py edge --portfolio   # 组合级 PATH：同日cap/持仓上限/环境闸
  python cli.py edge --equity      # 日频资金曲线政策对照（NAV）
  python cli.py discipline         # 破戒检查日报（系统+可选持仓）
  python cli.py holdings init|from-action|sync-px|show|fill-industry|equal-weight|check
  python cli.py daily          # 收盘后日更（刷新+扫盘+通知；含破戒检查）
  python cli.py schedule install   # 安装工作日 15:35 自动日更
  # 或: python app.py
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

warnings.simplefilter("ignore")

# 保证可从任意 cwd 导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def cmd_status(_args):
    from lib.data import list_cached_symbols, load_fundamentals, market_regime
    from lib.factors import index_low_vol_today
    from lib.paths import CACHE, META_PATH
    import json
    import os

    r = market_regime()
    print("=== 大盘状态（沪深300）===")
    if not r.get("ok"):
        print("数据不足，请先: python cli.py refresh --theme")
        return 1
    print(f"  红绿灯: {r['light']}")
    print(f"  近20日: {r['ret20']:+.2f}%")
    print(f"  站上MA20: {r['above_ma20']} | 站上MA60: {r['above_ma60']} | MA20上行: {r['ma20_up']}")
    live_tag = "实时" if r.get("live") else "日线缓存"
    print(f"  最新: {r['last_date']}  现价: {r['close']:.2f}  ({live_tag})")
    if r.get("bar_date"):
        print(f"  日K最后交易日: {r.get('bar_date')}  MA20={r.get('ma20')} MA60={r.get('ma60')}")
    if r.get("live_chg_pct") is not None or r.get("live"):
        pass
    lv = index_low_vol_today()
    if lv.get("ok"):
        print(
            f"  指数低波: {'是' if lv.get('low_vol') else '否'} "
            f"(20日vol={lv.get('vol')} ≤ 近1年中位thr={lv.get('thr')}) "
            f"[robust v5 默认要求低波；--no-low-vol 关闭]"
        )
    n_mb = len(list_cached_symbols(mainboard_only=True, drop_st=True))
    n_all = len(list_cached_symbols(mainboard_only=False, drop_st=False, include_index=True))
    fund = load_fundamentals(force=False)
    print(f"\n=== 缓存（默认股票池=主板，剔创业板/科创板/ST）===")
    print(f"  主板K线: {n_mb}  磁盘CSV(含其它): {n_all}  基本面(主板非ST): {len(fund)}")
    print(f"  目录: {CACHE}")
    if os.path.exists(META_PATH):
        meta = json.load(open(META_PATH, encoding="utf-8"))
        print(f"  上次刷新: {meta.get('last_refresh') or meta.get('updated_at')}")
    print("\n注: 红灯环境按手册应降仓/空仓；工具只评估结构，不构成投资建议。")
    return 0


def cmd_refresh(args):
    from lib.data import refresh, to_sym
    from lib.themes import THEME_POOL

    universe = bool(getattr(args, "universe", False))
    if args.codes:
        symbols = [to_sym(c) for c in args.codes]
        universe = False
    elif args.all or universe:
        symbols = None  # universe 模式拉全主板；--all 刷缓存内主板
    else:
        # 默认主题池（已是主板）+ 指数
        symbols = list(THEME_POOL.keys()) + ["sh000300"]

    print("刷新中…（默认仅主板，剔除创业板/科创板/ST）")
    stats = refresh(
        symbols=symbols,
        fund=not args.no_fund,
        force_kline=True,
        mainboard_only=not getattr(args, "include_all_boards", False),
        universe=universe,
        max_symbols=getattr(args, "max", None),
    )
    print(f"基本面(主板非ST): {stats['fund']} 条")
    print(
        f"K线成功: {stats['kline_ok']}  失败: {stats['kline_fail']}  "
        f"仍是旧数据: {stats.get('kline_stale', 0)}  "
        f"跳过非主板/ST: {stats.get('skipped_non_mainboard', 0)}"
    )
    print("完成。可运行: python cli.py status | score | screen")
    return 0 if stats["kline_ok"] > 0 else 1


def cmd_score(args):
    from lib.data import index_ret20, load_fundamentals, universe_mom60
    from lib.factors import score_one, verdict
    from lib.data import to_sym

    codes = args.codes
    if not codes:
        print("用法: python cli.py score 600519 002371 ...")
        return 2
    mode = args.mode
    low_vol = not bool(getattr(args, "no_low_vol", False))
    print("构建/载入全市场基本面快照... 模式=%s 低波=%s" % (mode, "开" if low_vol else "关"))
    fund = load_fundamentals(force=False)
    umom = universe_mom60()
    idx20 = index_ret20()
    print("沪深300 近20日 %+.1f%% | 动量样本 %d 只\n" % (idx20, len(umom)))
    summary = []
    for code in codes:
        sym = to_sym(code)
        name, sym, r = score_one(
            sym, fund, idx20, umom, force_fetch=args.fetch, mode=mode,
            require_low_vol=low_vol,
        )
        if r is None:
            print("== %s(%s) 数据不足/取数失败 ==\n" % (name, sym))
            continue
        vd = verdict(r)
        summary.append((name, sym, r["veto_pass"], r["psum"], vd, r.get("f3")))
        print("== %s(%s) 现价%.2f | %s | 大盘%s | mode=%s ==" % (
            name, sym, r["px"], vd, r["light"], mode))
        print("  PE=%s PB=%s 换手=%s%% | 60日%+.1f%% (分位%s) | 距20日%+.1f%% | RSI=%s" % (
            r["pe"], r["pb"], r["turn"], r["zf60"], r["pctile"], r["bias"], r["rsi"]))
        print("  高胜率公式: %s  |  %s" % ("✓" if r["f3"] else "✗", r.get("exit_hint", "")))
        # 经典公式命中
        try:
            from lib.data import load_kline_csv
            from lib.formulas import evaluate_today, build_cross_sectional_rps
            dfk = load_kline_csv(sym)
            idxdf = load_kline_csv("sh000300")
            if dfk is not None and idxdf is not None:
                flags = evaluate_today(sym, dfk, idxdf, rps_map=build_cross_sectional_rps(120))
                hits = [k for k, v in flags.items() if v]
                print("  经典公式命中: %s" % (", ".join(hits) if hits else "无"))
        except Exception:
            pass
        print("  [一票否决]")
        for n, v in r["veto"]:
            print("    [%s] %s" % ("✓" if v else "✗", n))
        print("  [加分项 %d/%d]" % (r["psum"], len(r["plus"])))
        for n, v in r["plus"]:
            print("    [%s] %s" % ("✓" if v else "✗", n))
        print()
    if len(summary) > 1:
        print("=== 汇总排名 ===")
        for name, sym, vp, ps, vd, f3 in sorted(summary, key=lambda x: (x[2], x[5] or False, x[3]), reverse=True):
            print("  %-8s %-9s %s" % (name, sym, vd))
    print("\n注: 高胜率模式更严，信号更少；得分高≠会涨。不构成投资建议。")
    return 0


def cmd_screen(args):
    # 结构扫盘（含 robust / 可选低波）
    from lib.scan import run_structure_scan

    low_vol = not bool(getattr(args, "no_low_vol", False))
    r = run_structure_scan(require_low_vol=low_vol)
    if not r.get("ok"):
        print(r.get("error") or "扫盘失败")
        return 1
    print(r.get("note"))
    print(f"低波开关: {'开(v5默认)' if r.get('require_low_vol') else '关'} | 今日低波: {r.get('idx_low_vol')}")
    print(f"完整通过 {r.get('full_pass_count')} | 严结构 {len(r.get('strict_struct') or [])} | "
          f"robust {len(r.get('robust_struct') or [])} | 结构 {len(r.get('tech_pass') or [])}")
    rows = r.get("full_pass") or r.get("robust_struct") or r.get("strict_struct") or []
    for x in rows[:20]:
        print(f"  {x.get('name')} {x.get('sym')} q={x.get('qscore')} RSI={x.get('rsi')} zf60={x.get('zf60')}")
    if not rows:
        print("  （名单空；红灯完整通过为0属正常）")
    print("详见 output/*.csv；非荐股。")
    return 0


def cmd_backtest(args):
    from lib.backtest import run_theme_backtest
    codes = args.codes or None
    run_theme_backtest(
        codes=codes,
        cost_bps=args.cost_bps,
        make_plots=not args.no_plot,
        mode=args.mode,
        exit_mode=args.exit,
        compare=not args.no_compare,
        require_low_vol=not bool(getattr(args, "no_low_vol", False)),
    )
    return 0


def cmd_report(args):
    from lib.report import build_dashboard
    path = build_dashboard(run_live=not args.offline)
    print("报告:", path)
    if args.open:
        import subprocess
        subprocess.call(["open", path])
    return 0


def cmd_serve(args):
    """启动本地 Web 系统。"""
    import app as webapp
    if getattr(args, "port", None):
        webapp.PORT = int(args.port)
    webapp.main()
    return 0


def cmd_stress(args):
    """压力测试：乐观窗出场 vs 逐日路径。"""
    from lib.stress import run_stress

    print("=== 压力测试（主板抽样）===")
    print(f"样本≈{args.sample}  seed={args.seed}")
    print("OPT=原回测口径(窗内极值+先止盈)  PATH=逐日路径+同日先损\n")
    r = run_stress(sample_n=args.sample, seed=args.seed)
    if not r.get("ok"):
        print(r.get("error") or "失败")
        return 1
    print(f"{'scenario':48s} {'n':>5s} {'WR':>6s} {'mean':>7s} {'sharpe':>7s}")
    print("-" * 80)
    for row in r.get("rows") or []:
        if row.get("n"):
            print(
                f"{row['scenario'][:48]:48s} {row['n']:5d} "
                f"{row['winrate']:5.1f}% {row['mean']:+6.2f}% {row['sharpe']:6.2f}"
            )
    s = r.get("summary") or {}
    print()
    print(s.get("note") or "")
    print(f"elite事件数≈{r.get('n_events_elite')}  文件: {r.get('file')}")
    print("不构成投资建议。MDD 为事件串接近似，非组合回撤。")
    return 0


def cmd_hist_validate(args):
    """多窗口历史验证：MENGα IC + 结构 PATH + 红绿灯。"""
    from lib.hist_validate import run_all

    print("=== 历史验证（真实本地 K 线）===")
    print(f"样本股≈{args.sample}  seed={args.seed}  MENGα截面 step={args.step}")
    print("请稍候（多截面 + 全序列信号，约数分钟）…\n")
    r = run_all(sample_n=args.sample, seed=args.seed, menga_step=args.step)
    if not r.get("ok"):
        print("失败")
        return 1
    print(f"股票 {r.get('n_stocks')} | 指数 {r.get('idx_span')} | MENGα截面 {r.get('menga_sections')}")
    print(f"mean IC20(MENGα) = {r.get('menga_mean_ic')}")
    print(f"mean Q5-Q1 f20   = {r.get('menga_mean_q_spread')}")
    print()
    files = r.get("files") or {}
    for k, p in files.items():
        print(f"  {k}: {p}")
    summary = files.get("summary")
    if summary and os.path.exists(summary):
        print("\n----- 报告摘要 -----")
        with open(summary, encoding="utf-8") as f:
            print(f.read())
    print("不构成投资建议。")
    return 0


def cmd_up_prob(args):
    """固定持有期上涨概率（收盘对收盘）。"""
    from lib.up_prob import compute_up_prob

    print("=== 固定持有期上涨概率 ===")
    print(f"样本≈{args.sample} seed={args.seed}  horizons=1/3/5/10/20")
    print("上涨 = 持有 h 日后收盘价 > 入场日收盘；非 OPT、非必涨\n")
    r = compute_up_prob(sample_n=args.sample, seed=args.seed)
    if not r.get("ok"):
        print("失败")
        return 1
    print(f"股票 {r.get('n_stocks')}")
    print(f"{'mode':10s} {'h':>3s} {'n':>6s} {'上涨%':>7s} {'毛均':>7s} {'净均':>7s}")
    print("-" * 50)
    for row in r.get("rows") or []:
        if not row.get("n"):
            continue
        print(
            f"{row['mode']:10s} {row['horizon_d']:3d} {row['n']:6d} "
            f"{row['up_prob_pct']:6.1f}% {row['mean_gross']:+6.2f}% {row['mean_net']:+6.2f}%"
        )
    print()
    print("报告:", r.get("summary"))
    print("CSV: ", r.get("file"))
    print("不构成投资建议。历史概率 ≠ 下一笔会涨。")
    return 0


def cmd_live(args):
    """实时行情快照。"""
    from lib.data import fetch_realtime_quotes, market_regime, to_sym

    codes = list(args.codes or [])
    if not codes:
        codes = ["sh000300"]
    syms = [to_sym(c) for c in codes]
    if "sh000300" not in syms:
        syms = ["sh000300"] + syms
    q = fetch_realtime_quotes(syms)
    r = market_regime(live=True)
    print("=== 实时大盘 ===")
    print(f"  红绿灯: {r.get('light')}  live={r.get('live')}")
    print(f"  现价: {r.get('close')}  时间: {r.get('last_date')}")
    print(f"  日K最后: {r.get('bar_date')}  MA20={r.get('ma20')} MA60={r.get('ma60')}")
    print(f"  近20日约: {r.get('ret20'):+.2f}%" if r.get("ret20") == r.get("ret20") else "")
    print("\n=== 报价 ===")
    print(f"{'代码':10s} {'名称':8s} {'现价':>10s} {'涨跌%':>8s} {'时间':16s}")
    for s in syms:
        x = q.get(s) or {}
        print(
            f"{s:10s} {str(x.get('name') or '')[:8]:8s} "
            f"{float(x.get('price') or 0):10.2f} "
            f"{float(x.get('chg_pct') or 0):+7.2f}%  "
            f"{str(x.get('date') or '')} {str(x.get('time') or '')}"
        )
    print("\n注: 新浪hq快照，有延迟；非Level2。日K名单请收盘后 refresh。")
    return 0 if q else 1


def cmd_action(_args):
    """今日操作口径：红灯空仓；绿灯才出候选。"""
    from lib.action_today import get_action_today

    r = get_action_today(use_joint=True)
    if not r.get("ok"):
        print(r.get("error") or "失败")
        return 1
    print("=" * 48)
    print(f"  操作口径: 【{r.get('action')}】")
    print(f"  大盘: {r.get('light')}  候选数: {r.get('n')}")
    print("=" * 48)
    print(r.get("note") or "")
    if r.get("why_empty"):
        print(f"原因: {r['why_empty']}")
    pb = r.get("playbook") or {}
    if pb:
        env = pb.get("env") or {}
        print(
            f"\n【组合纪律】模式={pb.get('risk_mode')} | {pb.get('size_hint')} | "
            f"今日最多新建{pb.get('max_new_today')}只"
        )
        print(
            f"  环境: 低波={env.get('low_vol')} 指数20日={env.get('idx_ret20')}% "
            f"转绿天数={env.get('green_days')}"
        )
        print(
            f"  上限: 持仓≤{pb.get('max_names')}只 单只≤{pb.get('max_weight_pct')}% "
            f"现金≥{pb.get('min_cash_pct')}% 同行业≤{pb.get('max_per_industry', 2)} "
            f"出场={pb.get('exit')} TP{pb.get('tp_pct')}/SL{pb.get('sl_pct')}"
        )
    buy = r.get("buy") or []
    if buy:
        print(f"\n{'档位':12s} {'名称':8s} {'代码':10s} {'现价':>8s} {'质量分':>6s} 理由")
        for x in buy:
            print(
                f"{str(x.get('tier') or '')[:12]:12s} {str(x.get('name') or '')[:8]:8s} "
                f"{x.get('sym') or '':10s} {float(x.get('px') or 0):8.2f} "
                f"{str(x.get('qscore') or '—'):>6s}  {x.get('reason') or ''}"
            )
            print(f"           {x.get('exit_hint') or ''}")
    else:
        print("\n【买入候选】无。不要用 MENGα/妖股榜当买入。")
    obs = r.get("observe") or []
    if obs:
        print(f"\n【仅观察·不新开】{len(obs)} 只（环境闸门或超额截断）")
        for x in obs[:10]:
            print(
                f"  {str(x.get('name') or '')[:8]:8s} {x.get('sym') or '':10s} "
                f"{str(x.get('tier') or '')[:16]}"
            )
    print(f"\n文件: output/action_today.csv / action_today.txt")
    print(r.get("disclaimer") or "")
    return 0


def cmd_recap(args):
    """网上复盘骨架 × 本仓纪律（红灯空仓，同行业≤2，PATH tight）。"""
    from lib.recap import write_recap

    path = write_recap(
        cached=bool(getattr(args, "cached", False)),
        with_observe=not bool(getattr(args, "no_observe", False)),
    )
    print(open(path, encoding="utf-8").read())
    print("文件:", path)
    return 0


def cmd_cool(args):
    """冷静观察池：MENGα 精炼过滤（研究用）。"""
    from lib.cool_pool import build_cool_pool

    r = build_cool_pool(top_n=args.top, rescan=not getattr(args, "cached", False))
    if not r.get("ok"):
        print(r.get("error") or "失败")
        return 1
    print(r.get("note"))
    print(f"\n{'α':>5s} {'热':>4s} {'洗':>4s} {'拥':>4s} {'名称':8s} {'代码':10s} {'现价':>7s} {'60日%':>7s} 风险")
    for x in r.get("rows") or []:
        print(
            f"{x['menga']:5.1f} {x['heat']:4.0f} {x['wash']:4.0f} {x['crowd']:4.0f} "
            f"{str(x['name'])[:8]:8s} {x['sym']:10s} {x['px']:7.2f} "
            f"{x['ret60']:+6.1f}%  {x.get('risk') or '—'}"
        )
    print(f"\n文件: {r.get('file')}")
    print(r.get("disclaimer"))
    return 0


def cmd_alpha(args):
    """自研 MENGα 指标体系扫描。"""
    from lib.own_alpha import scan_own_alpha, score_symbols

    if args.codes:
        r = score_symbols(args.codes)
        if not r.get("ok"):
            print(r.get("error") or "失败")
            return 1
        print(r.get("note") or "")
        print(f"\n{'α':>5s} {'热':>4s} {'洗':>4s} {'芽':>4s} {'拥':>4s} 名称       代码         现价  标签")
        for x in r.get("rows") or []:
            print(
                f"{x['menga']:5.1f} {x['heat']:4.0f} {x['wash']:4.0f} {x['spark']:4.0f} {x['crowd']:4.0f} "
                f"{str(x['name'])[:8]:8s} {x['sym']:10s} {x['px']:7.2f}  {x['tag']}"
            )
        if r.get("missing"):
            print("未找到:", ", ".join(r["missing"]))
        print("\n" + (r.get("disclaimer") or ""))
        return 0

    r = scan_own_alpha(top_n=args.top, refresh_stale=getattr(args, "refresh_stale", False))
    if not r.get("ok"):
        print(r.get("error") or "失败")
        return 1
    print(r.get("note"))
    fml = r.get("formula") or {}
    print(f"公式: {fml.get('menga')}")
    ev = fml.get("evidence") or {}
    if ev:
        print(
            f"证据: IC20(反热)≈{ev.get('ic20_anti_heat')}  "
            f"Q5-Q1 20日≈+{ev.get('q5_q1_f20_pct')}%  "
            f"(经典高RS未来20日均值仅 {ev.get('classic_high_rs_f20_mean')})"
        )
    print()
    print(f"{'α':>5s} {'热':>4s} {'洗':>4s} {'芽':>4s} {'拥':>4s} {'名称':8s} {'代码':10s} {'现价':>7s} {'60日%':>7s} {'超额20':>7s} 标签")
    for x in r.get("rows") or []:
        print(
            f"{x['menga']:5.1f} {x['heat']:4.0f} {x['wash']:4.0f} {x['spark']:4.0f} {x['crowd']:4.0f} "
            f"{str(x['name'])[:8]:8s} {x['sym']:10s} {x['px']:7.2f} "
            f"{x['ret60']:+6.1f}% {x['excess20']:+6.1f}%  {x['tag']}"
        )
    print()
    print("--- 过热对照（Heat 最高，历史偏弱）---")
    for x in (r.get("hot_rows") or [])[:10]:
        print(
            f"热{x['heat']:5.1f} α{x['menga']:5.1f} {str(x['name'])[:8]:8s} {x['sym']:10s} "
            f"60日{x['ret60']:+.1f}% RSI{x['rsi']:.0f}"
        )
    print()
    print(f"文件: {r.get('file')}")
    print(r.get("disclaimer"))
    return 0


def cmd_yaogu(args):
    """妖股启动前画像扫描。"""
    from lib.yaogu import scan_yaogu_prelaunch

    r = scan_yaogu_prelaunch(top_n=args.top)
    if not r.get("ok"):
        print("失败")
        return 1
    print(r.get("note"))
    p = r.get("profile") or {}
    print(
        f"历史画像(n≈{p.get('events_studied')}): "
        f"RSI中位{p.get('rsi_med')} bias中位{p.get('bias_med')}% "
        f"ret20中位{p.get('ret20_med')}% 启动时多头仅{p.get('ma_bull_rate')}"
    )
    print()
    print(f"{'分':>5s} {'名称':8s} {'代码':10s} {'现价':>8s} {'RSI':>5s} {'bias':>6s} {'20日%':>7s} {'距低%':>6s} 形态")
    for x in r.get("rows") or []:
        print(
            f"{x['yaogu_score']:5.1f} {str(x['name'])[:8]:8s} {x['sym']:10s} {x['px']:8.2f} "
            f"{x['rsi']:5.1f} {x['bias20']:+5.1f}% {x['ret20']:+6.1f}% "
            f"{x['dist_low20']*100:5.1f}%  {'、'.join(x['patterns'][:3])}"
        )
    print()
    print("文件: output/yaogu_prelaunch.csv")
    print(r.get("disclaimer"))
    return 0


def cmd_patterns(args):
    """扫描黄金坑/一阳穿三线等常见形态。"""
    from lib.pattern_scan import run_pattern_scan
    from lib.patterns import list_patterns

    if args.list:
        print("已支持形态：")
        for n, d in list_patterns():
            print(f"  {n:8s}  {d}")
        return 0

    r = run_pattern_scan(pattern=args.pattern, min_hits=args.min_hits)
    if not r.get("ok"):
        print("失败")
        return 1
    print(r.get("note"))
    print()
    print("各形态命中数：")
    for k, v in sorted((r.get("counts") or {}).items(), key=lambda x: -x[1]):
        if v:
            print(f"  {k:8s}  {v:4d}")
    print()
    rows = r.get("rows") or []
    print(f"{'名称':8s} {'代码':10s} {'现价':>8s} {'命中':>4s}  形态")
    for x in rows[:40]:
        print(
            f"{str(x.get('name',''))[:8]:8s} {x['sym']:10s} {x['px']:8.2f} "
            f"{x['n_hits']:4d}  {'、'.join(x['hits'])}"
        )
    if len(rows) > 40:
        print(f"  … 共 {len(rows)} 只，见 output/patterns_today.csv")
    else:
        print(f"\n共 {len(rows)} 只 → output/patterns_today.csv")
    print(r.get("disclaimer", ""))
    return 0


def cmd_joint(args):
    """B: 完整通过 ∩ MENGα 冷静过滤（联合 v2）。"""
    from lib.joint import run_joint_signals

    r = run_joint_signals(use_llm=False, rescan_menga=True)
    if not r.get("ok"):
        print("失败:", r.get("error"))
        return 1
    print(r.get("note"))
    print(
        f"算法 {r.get('algo')} | 大盘 {r.get('light')} | "
        f"完整通过 {r.get('full_pass_count')} | 联合 {r.get('joint_count')}"
    )
    print()
    if not r.get("joint"):
        print("联合信号为空（红灯 / 完整通过=0 / 或未过 MENGα 冷静过滤）。")
        print("详见 output/joint_signals.csv")
        print(r.get("disclaimer", ""))
        return 0
    print(f"{'名称':8s} {'代码':10s} {'质量':>6s} {'α':>5s} {'热':>4s} {'芽':>4s} {'拥':>4s} {'过滤'}")
    for x in r["joint"]:
        print(
            f"{str(x.get('name',''))[:8]:8s} {x.get('sym'):10s} "
            f"{x.get('qscore') or 0:6.1f} {x.get('menga') or 0:5.1f} "
            f"{x.get('heat') or 0:4.0f} {x.get('spark') or 0:4.0f} "
            f"{x.get('crowd') or 0:4.0f} {x.get('forecast_lean') or ''}"
        )
    print()
    print("CODES:", " ".join(x["sym"] for x in r["joint"]))
    print("文件: output/joint_signals.csv")
    print(r.get("disclaimer", ""))
    return 0


def cmd_forecast(args):
    """历史相似情境 → 次日/短线条件统计 + 可选大模型解读。"""
    from lib.forecast import forecast_stock
    import json

    r = forecast_stock(
        args.code,
        use_llm=not args.no_llm,
        top_k=args.top_k,
        use_industry=not args.no_industry,
    )
    if not r.get("ok"):
        print("失败:", r.get("error"))
        return 1
    snap = r["snapshot"]
    print(f"=== {snap['sym']}  数据日 {snap['date']}  现价 {snap['px']}  算法={snap.get('algo')} ===")
    print(f"大盘 {snap['market_light']} | RSI {snap['rsi']} | 超额20日 {snap['excess20']:+.1f}%")
    print(f"标签: {r['lean']} | 置信: {r.get('confidence')}({r.get('confidence_score')}) | 可行动: {r.get('actionable')}")
    print(f"原因: {r.get('lean_reason')}")
    print(f"方法: {r['method']}")
    if r.get("backtest_note"):
        print(f"校准: {r['backtest_note']}")
    print()
    print(f"{'持有':>4s} {'n':>5s} {'上涨%':>7s} {'均值':>8s} {'中位':>8s} {'P25':>7s} {'P75':>7s}")
    for h in r["horizons"]:
        print(
            f"{h['horizon']:>3d}日 {h['n']:5d} {h['up_rate']:6.1f}% "
            f"{h['mean']:+7.2f}% {h['median']:+7.2f}% {h['p25']:+6.2f}% {h['p75']:+6.2f}%"
        )
    llm = r.get("llm") or {}
    print()
    print("--- 解读 ---")
    print(llm.get("summary") or "")
    if llm.get("action"):
        print("纪律:", llm.get("action"))
    if llm.get("hint"):
        print("提示:", llm.get("hint"))
    print()
    print(r["disclaimer"])
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def cmd_daily(args):
    """收盘后日更：刷新数据 → 扫盘 → 报告 → 通知。"""
    from lib.daily_job import run_daily

    r = run_daily(
        skip_weekend=not args.force_weekend,
        refresh_data=not args.no_refresh,
        notify=not args.no_notify,
        open_report=args.open,
    )
    if not r.get("ok"):
        return 1
    if r.get("skipped"):
        return 0
    print(
        f"摘要: {r.get('asof')} {r.get('light')} "
        f"完整通过={r.get('full_pass_count')} 报告={r.get('report')}"
    )
    return 0


def cmd_schedule(args):
    from lib.daily_job import install_schedule, schedule_status, uninstall_schedule

    if args.action == "install":
        path = install_schedule(hour=args.hour, minute=args.minute)
        print(f"已安装: {path}")
        print(f"时间: 工作日 {args.hour:02d}:{args.minute:02d}")
        print("手动试跑: python cli.py daily")
        print(schedule_status())
        return 0
    if args.action == "uninstall":
        ok = uninstall_schedule()
        print("已卸载" if ok else "未找到定时任务")
        return 0
    if args.action == "status":
        print(schedule_status())
        return 0
    print("用法: schedule install|uninstall|status")
    return 2


def cmd_holdings(args):
    """持仓助手：生成/更新 holdings.csv，供 discipline 使用。"""
    from lib.holdings_mgr import (
        DEFAULT_HOLDINGS,
        fill_industry_batch,
        from_action,
        init_holdings,
        set_equal_weights,
        show_holdings,
        sync_prices,
    )

    action = args.action
    path = args.path or DEFAULT_HOLDINGS

    if action == "init":
        r = init_holdings(path=path, force=args.force)
        print(f"持仓文件: {r.get('path')}  created={r.get('created')}  {r.get('note') or ''}")
        return 0

    if action == "from-action":
        print("=== 从今日 action 生成持仓草稿 ===")
        r = from_action(
            path=path,
            equal_weight=not args.no_equal,
            max_weight=args.max_weight,
            mark_new=not args.no_new,
            merge=args.merge,
        )
        if not r.get("ok"):
            print("失败:", r.get("error"))
            return 1
        print(f"action={r.get('action')} 大盘={r.get('light')} n={r.get('n')}")
        if r.get("weight_each") is not None:
            print(f"均权: 每只 {r.get('weight_each')}%（上限 {args.max_weight}%）")
        print(r.get("note") or "")
        print(f"文件: {r.get('path')}")
        for x in (r.get("rows") or [])[:20]:
            print(
                f"  {x.get('sym')} {str(x.get('name') or '')[:8]:8s} "
                f"w={x.get('weight_pct')} px={x.get('px')} ind={x.get('industry')}"
            )
        print("下一步: 改真实成本 → python3 cli.py holdings sync-px → python3 cli.py discipline")
        return 0

    if action == "sync-px":
        r = sync_prices(path=path, live=not args.no_live)
        if not r.get("ok"):
            print("失败:", r.get("error"))
            return 1
        print(f"已刷新现价 n={r.get('n')} updated={r.get('updated')} → {r.get('path')}")
        for x in (r.get("rows") or []):
            pnl = x.get("pnl_pct")
            pnl_s = f"{pnl:+.2f}%" if pnl is not None else "—"
            print(f"  {x.get('sym')} px={x.get('px')} 成本={x.get('entry_price')} 浮盈={pnl_s}")
        return 0

    if action == "show":
        r = show_holdings(path=path)
        print(f"文件: {r.get('path') or path}")
        print(f"只数: {r.get('n')}  权重合计: {r.get('total_weight')}%  隐含现金: {r.get('cash_implied')}%")
        for x in r.get("rows") or []:
            print(
                f"  {x.get('sym')} {str(x.get('name') or '')[:8]:8s} "
                f"w={x.get('weight_pct')} cost={x.get('entry_price')} "
                f"px={x.get('px')} new={int(bool(x.get('new_today')))} ind={x.get('industry')}"
            )
        if not r.get("n"):
            print("空。可用: python3 cli.py holdings from-action")
        return 0

    if action == "equal-weight":
        r = set_equal_weights(path=path, max_weight=args.max_weight)
        if not r.get("ok"):
            print("失败:", r.get("error"))
            return 1
        print(f"均权每只 {r.get('weight_each')}%  n={r.get('n')} → {r.get('path')}")
        return 0

    if action == "fill-industry":
        print(f"=== 批量补行业（最多 {args.max} 只，sleep={args.sleep}s）===")
        r = fill_industry_batch(max_n=args.max, sleep=args.sleep, only_missing=not args.all)
        print(r.get("note"))
        print(f"本次 ok={r.get('filled_ok')} unknown={r.get('filled_unknown')} map={r.get('map_known')}/{r.get('map_total')}")
        return 0

    if action == "check":
        # 同步现价后跑破戒
        if os.path.exists(path):
            sync_prices(path=path, live=not args.no_live)
        from lib.discipline import run_discipline_check

        r = run_discipline_check(holdings_path=path if os.path.exists(path) else None)
        print(f"总评: 【{r.get('grade')}】 pass={r.get('n_pass')} fail={r.get('n_fail')} crit={r.get('n_crit')}")
        fails = [c for c in (r.get("checks") or []) if not c.get("ok")]
        for c in fails:
            print(f"  ❌ [{c.get('severity')}] {c.get('title')}: {c.get('detail')}")
        if not fails:
            print("无失败项 — 守戒。")
        print(f"报告: {r.get('report')}")
        return 1 if (r.get("n_crit") or 0) > 0 else 0

    print("用法: holdings init|from-action|sync-px|show|equal-weight|fill-industry|check")
    return 2


def cmd_discipline(args):
    """破戒检查日报：对照 playbook 审计系统输出 + 可选持仓。"""
    from lib.discipline import (
        ensure_holdings_template,
        run_discipline_check,
        run_discipline_week,
    )

    print("=== 破戒检查日报 ===")
    if args.template:
        p = ensure_holdings_template()
        print(f"持仓模板: {p}")
        print("复制为 output/holdings.csv 后填写权重/成本，再跑: python3 cli.py discipline")
        return 0

    if args.week:
        w = run_discipline_week(days=args.days)
        print(f"近 {len(w.get('rows') or [])} 份日报")
        print(f"守戒日 {w.get('n_ok')} · 破戒日 {w.get('n_bad')} · 有严重项日 {w.get('n_crit_days')}")
        print()
        print(f"{'日期':12s} {'总评':12s} {'通过':>6s} {'失败':>6s} {'严重':>6s}")
        for row in w.get("rows") or []:
            print(
                f"{row['asof']:12s} {str(row.get('grade') or '—')[:12]:12s} "
                f"{str(row.get('n_pass') if row.get('n_pass') is not None else '—'):>6s} "
                f"{str(row.get('n_fail') if row.get('n_fail') is not None else '—'):>6s} "
                f"{str(row.get('n_crit') if row.get('n_crit') is not None else '—'):>6s}"
            )
        print()
        print(f"报告: {w.get('md_path')}")
        print(f"明细: {w.get('csv')}")
        return 0

    r = run_discipline_check(
        holdings_path=args.holdings,
        refresh_action=not args.cached,
    )
    if not r.get("ok"):
        print("失败")
        return 1
    print(f"日期: {r.get('asof')}")
    print(f"总评: 【{r.get('grade')}】  通过{r.get('n_pass')} 失败{r.get('n_fail')} 严重{r.get('n_crit')}")
    print(f"系统: action={r.get('action')} 大盘={r.get('light')}")
    env = r.get("env") or {}
    print(
        f"环境: 指数20日={env.get('idx_ret20')}% 低波={env.get('low_vol')} "
        f"转绿={env.get('green_days')} max_new={env.get('max_new')}"
    )
    print(f"持仓: {r.get('holdings_n')} 只  文件={r.get('holdings_path') or '无'}")
    if r.get("holdings"):
        print(f"{'代码':10s} {'权重%':>7s} {'成本':>8s} {'现价':>8s} {'浮盈%':>8s} {'新建':>4s}")
        for h in r.get("holdings") or []:
            print(
                f"{h.get('sym',''):10s} "
                f"{(h.get('weight_pct') if h.get('weight_pct') is not None else float('nan')):7.1f} "
                f"{(h.get('entry_price') if h.get('entry_price') is not None else float('nan')):8.2f} "
                f"{(h.get('px') if h.get('px') is not None else float('nan')):8.2f} "
                f"{(h.get('pnl_pct') if h.get('pnl_pct') is not None else float('nan')):8.2f} "
                f"{'Y' if h.get('new_today') else 'N':>4s}"
            )
    print()
    fails = [c for c in (r.get("checks") or []) if not c.get("ok")]
    if fails:
        print("失败项:")
        for c in fails:
            print(f"  ❌ [{c.get('severity')}] {c.get('title')}: {c.get('detail')}")
    else:
        print("无失败项 — 守戒。")
    print()
    print(f"报告: {r.get('report')}")
    print(f"明细: {r.get('csv')}")
    print("周汇总: python3 cli.py discipline --week")
    print(r.get("disclaimer") or "")
    return 1 if (r.get("n_crit") or 0) > 0 else 0


def cmd_edge(args):
    """边沿增强阶梯 / 组合 PATH / 资金曲线。"""
    from lib.edge_boost import (
        PLAYBOOK,
        compare_equity_policies,
        run_edge_ladder,
        run_equity_curve,
        run_portfolio_path,
    )

    if getattr(args, "equity", False):
        print("=== 组合资金曲线政策对照（日频 NAV + PATH）===")
        print(
            f"sample={args.sample} seed={args.seed} hold={args.period} exit={args.exit}"
        )
        r = compare_equity_policies(
            sample_n=args.sample,
            seed=args.seed,
            period=args.period,
            exit_mode=args.exit,
            cost_bps=args.cost_bps,
        )
        if not r.get("ok"):
            print("失败")
            return 1
        print()
        print(
            f"{'policy':22s} {'总收益%':>8s} {'年化%':>8s} {'Sharpe':>8s} "
            f"{'MDD%':>8s} {'超额%':>8s} {'笔数':>6s} {'胜率':>6s} {'cBps':>5s}"
        )
        for row in r.get("rows") or []:
            if "error" in row:
                print(f"{row.get('policy','?'):22s} ERR {row.get('error')}")
                continue
            print(
                f"{row['policy']:22s} {row['total_ret_pct']:8.2f} {row['ann_ret_pct']:8.2f} "
                f"{row['sharpe']:8.2f} {row['mdd_pct']:8.2f} {row['excess_pct']:8.2f} "
                f"{row['n_trades']:6d} {row['trade_wr']:6.1f} {float(row.get('cost_bps') or 15):5.0f}"
            )
        best = r.get("best") or {}
        if best:
            print()
            print(
                f"本样本最优: {best.get('policy')} 超额{best.get('excess_pct')}% "
                f"Sharpe{best.get('sharpe')} MDD{best.get('mdd_pct')}%"
            )
        # 额外导出最优政策完整曲线
        if best and best.get("policy") == "playbook_env":
            run_equity_curve(
                sample_n=args.sample,
                seed=args.seed,
                period=args.period,
                exit_mode=args.exit,
                cost_bps=args.cost_bps,
                env_gate=True,
                use_boost=False,
            )
        elif best and best.get("policy") == "boost_env":
            run_equity_curve(
                sample_n=args.sample,
                seed=args.seed,
                period=args.period,
                exit_mode=args.exit,
                cost_bps=args.cost_bps,
                env_gate=True,
                use_boost=True,
            )
        print(f"明细: {r.get('path')}")
        print(f"报告: {r.get('md_path')}")
        print(r.get("disclaimer", ""))
        return 0

    if getattr(args, "portfolio", False):
        print("=== 组合级 PATH（同日cap / 持仓上限 / 环境闸）===")
        print(
            f"sample={args.sample} seed={args.seed} hold={args.period} "
            f"exit={args.exit} 低波={'关' if args.no_low_vol else '开'}"
        )
        r = run_portfolio_path(
            sample_n=args.sample,
            seed=args.seed,
            period=args.period,
            exit_mode=args.exit,
            cost_bps=args.cost_bps,
            require_low_vol=not args.no_low_vol,
        )
        if not r.get("ok"):
            print("失败:", r.get("error"))
            return 1
        print(
            f"事件池 robust={r.get('n_events_robust')} boost={r.get('n_events_boost')}"
        )
        print()
        print(
            f"{'policy':16s} {'n':>6s} {'胜率%':>8s} {'均值%':>8s} "
            f"{'Sharpe':>8s} {'MDD%':>8s} {'skipEnv':>8s} {'skipCap':>8s}"
        )
        for row in r.get("policies") or []:
            print(
                f"{row['policy']:16s} {row['n']:6d} {row['winrate']:8.1f} "
                f"{row['mean']:+8.2f} {row['sharpe']:8.2f} {row['mdd']:8.1f} "
                f"{row['n_skip_env']:8d} {row['n_skip_cap']:8d}"
            )
        print()
        print("结论:", r.get("verdict"))
        print(f"明细: {r.get('path')}")
        print(f"报告: {r.get('md_path')}")
        print("资金曲线: python3 cli.py edge --equity")
        print(r.get("disclaimer", ""))
        return 0

    print("=== 边沿增强阶梯（PATH）===")
    print(
        f"sample={args.sample} seed={args.seed} hold={args.period} "
        f"exit={args.exit} daily_cap={args.daily_cap or '无'} "
        f"低波={'关' if args.no_low_vol else '开'}"
    )
    r = run_edge_ladder(
        sample_n=args.sample,
        seed=args.seed,
        period=args.period,
        exit_mode=args.exit,
        cost_bps=args.cost_bps,
        require_low_vol=not args.no_low_vol,
        daily_cap=args.daily_cap,
    )
    if not r.get("ok"):
        print("失败:", r.get("error"))
        return 1
    print()
    print(f"{'variant':18s} {'n':>6s} {'胜率%':>8s} {'均值%':>8s} {'Sharpe':>8s} {'MDD%':>8s}")
    rows = sorted(r.get("summary") or [], key=lambda x: -x["mean"])
    for row in rows:
        print(
            f"{row['variant']:18s} {row['n']:6d} {row['winrate']:8.1f} "
            f"{row['mean']:+8.2f} {row['sharpe']:8.2f} {row['mdd']:8.1f}"
        )
    print()
    picks = r.get("picks") or []
    if picks:
        print("相对 robust 候选（环境类优先）:")
        for p in picks[:6]:
            print(
                f"  · {p['variant']}: n={p['n']} 胜率{p['winrate']:.1f}% "
                f"均值{p['mean']:+.2f}% (Δmean {p.get('delta_mean', 0):+.2f}) [{p.get('tag','')}]"
            )
    else:
        print("相对 robust：本样本无稳定增量过滤器")
    rec = r.get("recommend") or {}
    print()
    print("建议入场:", rec.get("entry"))
    if rec.get("boost_if_any"):
        b = rec["boost_if_any"]
        print(f"增强规则: {b.get('variant')} — {b.get('how')}")
        if b.get("optional_mode"):
            print(f"  可选 mode: --mode {b['optional_mode']}（score/backtest）")
    if rec.get("sample_best"):
        sb = rec["sample_best"]
        print(
            f"本样本第一: {sb.get('variant')} mean={sb.get('path_mean'):+.2f}% "
            f"（仅供参考，结构再收窄勿默认）"
        )
    pb = PLAYBOOK
    print(
        f"组合纪律: 最多{pb['max_names']}只 / 单只≤{pb['max_weight_pct']}% / "
        f"现金≥{pb['min_cash_pct']}% / 出场 {pb['exit']} TP{pb['tp_pct']}/SL{pb['sl_pct']}"
    )
    print(f"明细: {r.get('path')}")
    print(f"报告: {r.get('md_path')}")
    print("组合级模拟: python3 cli.py edge --portfolio")
    print(r.get("disclaimer", ""))
    return 0


def cmd_chan(args):
    """缠论 v1：观察扫描或 PATH 对照回测（不进 action）。"""
    from lib.chan import compare_chan_vs_robust, compute_chan, run_chan_scan
    from lib.data import load_kline_csv, to_sym

    if args.compare:
        print("=== 缠论 v1 × robust PATH 对照 ===")
        print(
            f"sample={args.sample} seed={args.seed} hold={args.period} "
            f"exit={args.exit} 低波={'关' if args.no_low_vol else '开'}"
        )
        r = compare_chan_vs_robust(
            sample_n=args.sample,
            seed=args.seed,
            period=args.period,
            exit_mode=args.exit,
            cost_bps=args.cost_bps,
            require_low_vol=not args.no_low_vol,
        )
        if not r.get("ok"):
            print("失败:", r.get("error"))
            return 1
        print()
        print(f"{'variant':18s} {'n':>6s} {'胜率%':>8s} {'均值%':>8s} {'中位%':>8s} {'Sharpe':>8s}")
        for row in r.get("summary") or []:
            wr = row["winrate"]
            wr_s = f"{wr:.1f}" if wr == wr else "nan"
            print(
                f"{row['variant']:18s} {row['n']:6d} {wr_s:>8s} "
                f"{row['mean']:+8.2f} {row['median']:+8.2f} {row['sharpe']:8.2f}"
            )
        print()
        print("结论:", r.get("verdict"))
        print(f"明细: {r.get('path')}")
        print(r.get("disclaimer", ""))
        return 0

    if args.codes:
        # 个股结构摘要
        print("=== 缠论 v1 个股 ===")
        for code in args.codes:
            sym = to_sym(code)
            df = load_kline_csv(sym, min_bars=80)
            if df is None:
                print(f"{sym}: 无K线，请 refresh")
                continue
            ch = compute_chan(df)
            if not ch.get("ok"):
                print(f"{sym}: 数据不足")
                continue
            last = ch["last"]
            print(f"\n{sym}  笔={ch['n_bi']} 中枢={ch['n_zs']}")
            if last.get("zd") is not None:
                print(f"  最近中枢 ZD={last.get('zd')} ZG={last.get('zg')}")
            print(f"  末笔={last.get('last_bi')}  structure_ok={last.get('structure_ok')}")
            print(
                f"  今日: 一买={last.get('buy1_today')} 二买={last.get('buy2_today')} "
                f"三卖={last.get('sell3_today')}"
            )
        print("\n观察用，≠买入；规格见 docs/缠论最小规格.md")
        return 0

    r = run_chan_scan(limit=args.limit)
    if not r.get("ok"):
        print("失败")
        return 1
    print(r.get("note"))
    print()
    print(f"{'名称':8s} {'代码':10s} {'现价':>8s} {'笔':>4s} {'枢':>4s} {'OK':>4s}  标记")
    for x in (r.get("rows") or [])[:40]:
        ok = "Y" if x.get("structure_ok") else "N"
        print(
            f"{str(x.get('name',''))[:8]:8s} {x['sym']:10s} {x['px']:8.2f} "
            f"{x['n_bi']:4d} {x['n_zs']:4d} {ok:>4s}  {x.get('flags','')}"
        )
    print(f"\n→ {r.get('path')}")
    print(r.get("disclaimer", ""))
    return 0




def cmd_gufeng(args):
    """估值一谷买 / 一峰卖：主板扫描，输出参考买卖价（研究用）。"""
    import csv
    import os
    from datetime import datetime

    from lib.data import (
        attach_live_prices,
        drop_st_signals,
        fetch_realtime_quotes,
        filter_mainboard,
        is_st_name,
        list_cached_symbols,
        load_fundamentals,
        load_kline_csv,
        refresh_fund_names,
        resolve_name,
        to_sym,
    )
    from lib.gu_feng import scan_universe, score_symbol, signals_to_rows, refresh_target_buy, compute_target_levels, TARGET_CSV_FIELDS
    from lib.paths import OUTPUT

    valley = float(args.valley)
    peak = float(args.peak)
    # 先用实时简称刷新缓存（含刚戴帽 ST），再过滤；避免旧名「联创光电」漏网
    try:
        print("刷新股票简称（含 ST 检测）…", flush=True)
        refresh_fund_names()
    except Exception as e:
        print(f"(简称刷新跳过: {e})", flush=True)
    fund = load_fundamentals(force=False)

    if args.codes:
        print("=== 估值一谷一峰 · 个股 ===")
        live = {}
        try:
            live = fetch_realtime_quotes([to_sym(c) for c in args.codes])
        except Exception:
            live = {}
        for code in args.codes:
            sym = to_sym(code)
            f = fund.get(sym) or {}
            name = resolve_name(sym, str(f.get("name") or ""), fund)
            df = load_kline_csv(sym, min_bars=280)
            if df is None:
                print(f"{sym}: 无足够K线")
                continue
            pe = f.get("pe"); pb = f.get("pb")
            try:
                pe = float(pe) if pe is not None else None
            except Exception:
                pe = None
            try:
                pb = float(pb) if pb is not None else None
            except Exception:
                pb = None
            live_px = None
            if sym in live:
                try:
                    live_px = float(live[sym].get("price") or live[sym].get("px"))
                except Exception:
                    live_px = None
            # force classify even mid zone for display
            from lib.gu_feng import latest_past_percentile
            import numpy as np
            d = df.sort_values("date")
            close = d["close"].astype(float).to_numpy()
            vp = latest_past_percentile(close)
            px = live_px if live_px is not None else float(close[-1])
            if vp <= valley:
                zone = "一谷·买入观察"
            elif vp >= peak:
                zone = "一峰·卖出观察"
            else:
                zone = "中间区·观望"
            levels = compute_target_levels(close, px, valley=valley, peak=peak)
            tb = levels.get("target_buy")
            bc = levels.get("buy_ceiling")
            bp = levels.get("buy_prefer")
            sf = levels.get("sell_floor")
            tn = levels.get("target_note") or ""
            print(
                f"{sym} {name}  {zone}  分位={vp*100:.1f}%  参考价={px:.4f}  "
                f"目标买入={tb}({tn}) 上限(30%)={bc} 分批(20%)={bp} 卖参(70%)={sf}  "
                f"PE={pe} PB={pb}  asof={str(d['date'].iloc[-1])[:10]}"
            )
        print("研究用，不构成投资建议。")
        return 0

    syms = list_cached_symbols()
    syms = filter_mainboard(syms, fund=fund, drop_st=True)
    if args.limit and args.limit > 0:
        syms = syms[: int(args.limit)]
    print(f"=== 估值一谷一峰扫描 === 池={len(syms)} valley≤{valley:.0%} peak≥{peak:.0%}")
    live = {}
    if not args.no_live:
        try:
            # batch live for speed: only after scan on signal names; skip pre-fetch all
            pass
        except Exception:
            pass
    buys, sells = scan_universe(syms, valley=valley, peak=peak, fund=fund, live={})
    # attach live prices + 候选名单二次校验最新简称（防缓存仍滞后）
    all_sigs = buys + sells
    if all_sigs:
        try:
            live = fetch_realtime_quotes([s.sym for s in all_sigs])
            kept_buys, kept_sells = [], []
            dropped_st = []
            for s in all_sigs:
                q = live.get(s.sym) or {}
                qn = str(q.get("name") or "").strip()
                if qn:
                    s.name = qn
                if is_st_name(s.name):
                    dropped_st.append(f"{s.sym}:{s.name}")
                    continue
                if not args.no_live:
                    try:
                        px = float(q.get("price") or q.get("px"))
                        if px == px:
                            s.price = round(px, 4)
                            refresh_target_buy(s)
                    except Exception:
                        pass
                else:
                    refresh_target_buy(s)
                (kept_buys if s.side == "buy" else kept_sells).append(s)
            buys, sells = kept_buys, kept_sells
            if dropped_st:
                print(f"(二次校验剔除 ST {len(dropped_st)}: {', '.join(dropped_st[:8])}{'…' if len(dropped_st)>8 else ''})")
        except Exception as e:
            print(f"(实时价/ST二次校验跳过: {e})")

    # 买侧可选：近 3～5 日底背离 → 强化买入（不与放量/金叉等做 AND）
    use_div = bool(getattr(args, "divergence", False))
    div_days = int(getattr(args, "div_days", 5) or 5)
    if use_div and buys:
        from lib.gu_feng import annotate_buy_divergence, clamp_div_lookback
        div_days = clamp_div_lookback(div_days)
        print(f"买侧叠加底背离过滤（近{div_days}日，复用 patterns.底背离）…", flush=True)
        annotate_buy_divergence(buys, lookback=div_days)

    # 买侧可选：缩量（换手率% < 阈值；数据=基本面 turn / 新浪 turnoverratio）
    from lib.gu_feng import (
        annotate_buy_turnover,
        filter_rows_by_turnover,
        resolve_turnover_max,
    )
    to_max = resolve_turnover_max(
        low_turnover=bool(getattr(args, "low_turnover", False)),
        turnover_max=getattr(args, "turnover_max", None),
    )
    use_lt = to_max is not None
    if buys:
        annotate_buy_turnover(buys, fund=fund)
    if use_lt:
        print(f"买侧叠加缩量过滤（换手率 < {to_max:g}%）…", flush=True)

    buy_rows = drop_st_signals(signals_to_rows(buys))
    sell_rows = drop_st_signals(signals_to_rows(sells))
    buy_div_rows = [r for r in buy_rows if r.get("divergence")] if use_div else []
    # 缩量叠在「当前买侧主名单」上：有底背离则叠强化买入，否则叠纯一谷
    buy_focus = buy_div_rows if use_div else buy_rows
    buy_lt_rows = filter_rows_by_turnover(buy_focus, to_max, buy_only=True) if use_lt else []
    n_focus_before = len(buy_focus)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    buy_path = os.path.join(OUTPUT, f"gufeng_buy_{ts}.csv")
    sell_path = os.path.join(OUTPUT, f"gufeng_sell_{ts}.csv")
    all_path = os.path.join(OUTPUT, f"gufeng_all_{ts}.csv")
    fields = ["side", "code", "sym", "name", "ref_price", "buy_ceiling", "buy_prefer", "target_buy", "target_note", "sell_floor", "val_pct_pct", "pe", "pb", "turnover", "asof", "metric"]
    if use_div:
        fields = fields + ["divergence", "div_ago"]

    def _w(path, rows):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)

    _w(buy_path, buy_rows)
    _w(sell_path, sell_rows)
    _w(all_path, buy_rows + sell_rows)
    div_path = None
    lt_path = None
    if use_div:
        div_path = os.path.join(OUTPUT, f"gufeng_buy_div_{ts}.csv")
        _w(div_path, buy_div_rows)
    if use_lt:
        tag = "div_lt" if use_div else "lt"
        lt_path = os.path.join(OUTPUT, f"gufeng_buy_{tag}_{ts}.csv")
        _w(lt_path, buy_lt_rows)

    def _fmt_px(v):
        try:
            return f"{float(v):.3f}" if v is not None and v == v else ""
        except Exception:
            return ""

    def _fmt_turn(v):
        try:
            return f"{float(v):.2f}" if v is not None and v == v else ""
        except Exception:
            return ""

    def _show(title, rows, n=30, show_div=False, show_turn=False):
        print(f"\n--- {title} 共{len(rows)} ---")
        hdr = (
            f"{'代码':8s} {'名称':10s} {'参考价':>8s} {'目标买':>8s} {'上限30%':>8s} "
            f"{'分批20%':>8s} {'分位%':>7s} {'换手%':>6s} {'PE':>8s} {'PB':>6s}"
        )
        if show_div:
            hdr += f" {'背离':>4s} {'日前':>4s}"
        print(hdr)
        for r in rows[:n]:
            pe = "" if r["pe"] is None else f"{r['pe']:.1f}"
            pb = "" if r["pb"] is None else f"{r['pb']:.2f}"
            note = str(r.get("target_note") or "")
            tb = _fmt_px(r.get("target_buy"))
            if note == "现价已在一谷" and tb:
                tb = tb + "*"
            line = (
                f"{r['code']:8s} {str(r['name'])[:10]:10s} {r['ref_price']:8.3f} "
                f"{tb:>8s} {_fmt_px(r.get('buy_ceiling')):>8s} {_fmt_px(r.get('buy_prefer')):>8s} "
                f"{r['val_pct_pct']:7.1f} {_fmt_turn(r.get('turnover')):>6s} {pe:>8s} {pb:>6s}"
            )
            if show_div:
                dflag = "Y" if r.get("divergence") else "-"
                ago = "" if r.get("div_ago") is None else str(r.get("div_ago"))
                line += f" {dflag:>4s} {ago:>4s}"
            print(line)
        print("  *目标买=现价表示「现价已在一谷」；上限=30%分位价，分批=20%分位价（历史映射非预测）")
        if len(rows) > n:
            print(f"  …其余 {len(rows)-n} 只见 CSV")

    if use_lt:
        title_lt = (
            "缩量强化买入（一谷∩近{}日底背离∩换手<{:.3g}%）".format(div_days, to_max)
            if use_div
            else "缩量买入观察（一谷∩换手<{:.3g}%）".format(to_max)
        )
        _show(title_lt, buy_lt_rows, args.top, show_div=use_div, show_turn=True)
        print(f"  对照：叠缩量前 {n_focus_before} → 后 {len(buy_lt_rows)}（阈值换手<{to_max:g}%）")
    if use_div:
        _show(
            "强化买入（一谷∩近{}日底背离）".format(div_days),
            buy_div_rows,
            args.top if not use_lt else min(args.top, 15),
            show_div=True,
            show_turn=True,
        )
        if not use_lt:
            _show(
                "纯一谷买入观察（含未命中底背离，分位≤{:.0%}）".format(valley),
                buy_rows,
                min(args.top, 15),
                show_div=True,
                show_turn=True,
            )
    elif not use_lt:
        _show("买入观察（一谷 分位≤{:.0%}）".format(valley), buy_rows, args.top, show_turn=True)
    _show("卖出观察（一峰 分位≥{:.0%}）".format(peak), sell_rows, args.top, show_turn=False)
    print(f"\n→ {buy_path}")
    print(f"→ {sell_path}")
    print(f"→ {all_path}")
    if div_path:
        print(f"→ {div_path}  （一谷+底背离 {len(buy_div_rows)} 只）")
    if lt_path:
        label = "一谷+底背离+缩量" if use_div else "一谷+缩量"
        print(f"→ {lt_path}  （{label} {len(buy_lt_rows)}/{n_focus_before} 只，换手<{to_max:g}%）")
    print("研究用，不构成投资建议；参考价非委托价。目标买入价=历史分位映射（非预测必成交价）。")
    return 0


def cmd_feng3(args):
    """逢3必变 v1：观察层高低点研究回测（不进 action）。"""
    import importlib.util
    from lib.data import load_kline_csv, to_sym
    from lib.feng3 import detect_feng3_events

    def _load_bt():
        sp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "backtest_feng3.py")
        spec = importlib.util.spec_from_file_location("backtest_feng3", sp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.run_backtest

    if args.codes:
        print("=== 逢3必变 v1 个股 ===")
        for code in args.codes:
            sym = to_sym(code)
            df = load_kline_csv(sym, min_bars=120)
            if df is None:
                print(f"{sym}: 无K线")
                continue
            evs = detect_feng3_events(df, swing_k=args.swing_k, near_pct=args.near_pct)
            print(f"\n{sym}  事件数={len(evs)}  (swing_k={args.swing_k})")
            for e in evs[-8:]:
                print(
                    f"  {e['date']}  {e['pred']:4s} conf={e['conf']}  "
                    f"px={e['trigger_price']}  near={e['near_ok']}  struct={e['structure']}"
                )
        print("\n观察用，≠买入；计划见 docs/plans/2026-10-13-yijing-feng3-hl.md")
        return 0

    print("=== 逢3必变 v1 批量回测 ===")
    print(
        f"sample={args.sample} seed={args.seed} swing_k={args.swing_k} "
        f"near={args.near_pct}% hit_n={args.hit_n} hold_m={args.hold_m}"
    )
    run_backtest = _load_bt()
    r = run_backtest(
        sample_n=args.sample,
        seed=args.seed,
        swing_k=args.swing_k,
        near_pct=args.near_pct,
        hit_n=args.hit_n,
        hold_m=args.hold_m,
        mae_tol=args.mae_tol,
        cost_bps=args.cost_bps,
        exit_mode=args.exit,
    )
    if not r.get("ok"):
        print("失败:", r.get("error"))
        return 1
    print("结论:", r.get("verdict"))
    print(f"→ {r.get('md_path')}")
    print("不构成投资建议。")
    return 0


def cmd_formulas(args):
    """经典公开量化公式：list / compare / scan"""
    from lib.formulas import list_formulas

    if args.action == "list":
        print("=== 已内置经典公式（公开体系，非荐股）===\n")
        for k, name, desc in list_formulas():
            print(f"  {k:14s}  {name}")
            print(f"                 {desc}")
        print("\n  super_rs       欧奈尔式 RPS120 截面（仅 scan 当日）")
        print("\n用法:")
        print("  python cli.py formulas compare")
        print("  python cli.py formulas scan --theme")
        return 0
    if args.action == "compare":
        from lib.formula_backtest import compare_formulas
        compare_formulas(
            codes=args.codes or None,
            period=args.period,
            exit_mode=args.exit,
            cost_bps=args.cost_bps,
        )
        return 0
    if args.action == "scan":
        from lib.formula_backtest import scan_formulas_today
        scan_formulas_today(theme_only=args.theme or not args.all)
        return 0
    print("未知 action，用: list | compare | scan")
    return 2


def main(argv=None):
    p = argparse.ArgumentParser(description="A股量化研究工具（结构评估，非荐股）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="大盘红绿灯 + 缓存状态")

    pr = sub.add_parser("refresh", help="刷新基本面/K线（默认主板非ST）")
    pr.add_argument("codes", nargs="*", help="指定代码；默认主题池")
    pr.add_argument("--all", action="store_true", help="刷新缓存内主板股票")
    pr.add_argument("--universe", action="store_true", help="拉全市场主板非ST再刷K线（慢）")
    pr.add_argument("--max", type=int, default=None, help="universe 时最多刷 N 只")
    pr.add_argument("--include-all-boards", action="store_true", help="不排除创业板/科创板（不推荐）")
    pr.add_argument("--theme", action="store_true", help="仅主题池（默认行为）")
    pr.add_argument("--no-fund", action="store_true", help="不刷新基本面")

    ps = sub.add_parser("score", help="个股自检打分")
    ps.add_argument("codes", nargs="+")
    ps.add_argument("--fetch", action="store_true", help="强制重拉该股K线")
    ps.add_argument("--mode", choices=["base", "strict", "elite", "winrate", "robust", "boost"], default="robust",
                    help="robust=v5 反热+低波; boost=robust+指数20日>0; elite/winrate/strict/base")
    ps.add_argument("--no-low-vol", action="store_true",
                    help="关闭 robust 指数低波过滤（v5 默认开）")

    psc = sub.add_parser("screen", help="扫池：公式三+打分")
    psc.add_argument("--theme", action="store_true")
    psc.add_argument("--mode", choices=["base", "strict", "elite", "winrate", "robust", "boost"], default="robust")
    psc.add_argument("--no-low-vol", action="store_true", help="扫盘关闭 robust 低波（v5 默认开）")

    _modes = [
        "base", "strict", "elite", "winrate", "robust", "boost",
        "dual_mom", "donchian", "ma_squeeze", "golden_cross",
        "boll_sq", "macd_trend", "atr_break", "rps_break", "composite",
    ]
    pb = sub.add_parser("backtest", help="主题回测（高胜率对照）")
    pb.add_argument("--codes", nargs="*", default=None)
    pb.add_argument("--cost-bps", type=float, default=15, help="单边成本基点，往返=2x")
    pb.add_argument("--mode", choices=_modes, default="robust")
    pb.add_argument("--exit", choices=["fixed", "tpsl", "asym", "wr", "tight"], default="tight",
                    help="tight=TP4/SL3 PATH较优; wr=TP5/SL3; asym=TP8/SL5")
    pb.add_argument("--no-low-vol", action="store_true",
                    help="关闭 robust 指数低波入场（v5 默认开）")
    pb.add_argument("--no-compare", action="store_true", help="不跑 base/fixed 对照")
    pb.add_argument("--no-plot", action="store_true")

    prep = sub.add_parser("report", help="生成 HTML 结果页")
    prep.add_argument("--offline", action="store_true", help="不重跑，只用已有 output")
    prep.add_argument("--open", action="store_true", help="用浏览器打开")

    pf = sub.add_parser("formulas", help="经典公开量化公式（双重动量/海龟/金叉/共振…）")
    pf.add_argument("action", choices=["list", "compare", "scan"], help="list|compare|scan")
    pf.add_argument("--codes", nargs="*", default=None)
    pf.add_argument("--period", type=int, default=20)
    pf.add_argument("--exit", choices=["fixed", "tpsl", "asym", "wr", "tight"], default="wr")
    pf.add_argument("--cost-bps", type=float, default=15)
    pf.add_argument("--theme", action="store_true", help="scan 仅主题池")
    pf.add_argument("--all", action="store_true", help="scan 全部缓存")

    psrv = sub.add_parser("serve", help="启动本地 Web 系统（浏览器界面）")
    psrv.add_argument("--port", type=int, default=8765)

    pfcast = sub.add_parser("forecast", help="历史相似→次日/短线条件统计(+可选大模型)")
    pfcast.add_argument("code", help="股票代码 如 600428")
    pfcast.add_argument("--top-k", type=int, default=50, help="相似历史日数量(默认50，v2校准)")
    pfcast.add_argument("--no-llm", action="store_true", help="不用大模型，仅统计+模板")
    pfcast.add_argument("--no-industry", action="store_true", help="不用行业同伴（仅本股）")
    pfcast.add_argument("--json", action="store_true", help="打印完整JSON")

    ppat = sub.add_parser("patterns", help="形态扫描：黄金坑/一阳穿三线/金叉等")
    ppat.add_argument("--list", action="store_true", help="列出形态定义")
    ppat.add_argument("--pattern", type=str, default=None, help="只看某一形态，如 黄金坑")
    ppat.add_argument("--min-hits", type=int, default=1, help="至少命中几个形态")

    pygo = sub.add_parser("yaogu", help="妖股启动前画像扫描（非预测谁必成妖）")
    pygo.add_argument("--top", type=int, default=50, help="输出前N")

    pal = sub.add_parser("alpha", help="自研 MENGα 指标（数据驱动均值回归主轴）")
    pal.add_argument("--top", type=int, default=40, help="输出前N")
    pal.add_argument("--refresh-stale", action="store_true", help="扫描前给滞后个股补尾（慢，仅CLI）")
    pal.add_argument("codes", nargs="*", help="可选：查询指定代码在截面中的百分位")

    pcool = sub.add_parser("cool", help="冷静观察池（MENGα精炼，非荐股）")
    pcool.add_argument("--top", type=int, default=40, help="输出前N")
    pcool.add_argument("--cached", action="store_true", help="复用已有 menga_today.csv，不重扫")

    sub.add_parser("action", help="今日操作口径：空仓 或 可关注候选（唯一买入向出口）")

    prc = sub.add_parser("recap", help="复盘报告：Grok骨架×少妇仓位×游资槽位，票只来自本仓")
    prc.add_argument("--cached", action="store_true", help="不重跑 action，用 output 缓存")
    prc.add_argument("--no-observe", action="store_true", help="不附冷静池/α 观察层")

    plive = sub.add_parser("live", help="实时行情快照（默认沪深300）")
    plive.add_argument("codes", nargs="*", help="可选个股代码")

    pst = sub.add_parser("stress", help="压力测试：乐观窗 vs 路径出场/成本/T+1/熊市")
    pst.add_argument("--sample", type=int, default=900, help="抽样股票数")
    pst.add_argument("--seed", type=int, default=42)

    phv = sub.add_parser("hist-validate", help="多窗口历史验证：MENGα IC + PATH + 红绿灯纪律")
    phv.add_argument("--sample", type=int, default=600, help="抽样主板股数")
    phv.add_argument("--seed", type=int, default=42)
    phv.add_argument("--step", type=int, default=20, help="MENGα 截面间隔（交易日）")

    pup = sub.add_parser("up-prob", help="固定持有1/3/5/10/20日上涨概率（收盘对收盘）")
    pup.add_argument("--sample", type=int, default=600)
    pup.add_argument("--seed", type=int, default=42)

    pchan = sub.add_parser("chan", help="缠论v1：观察扫描 / PATH对照（否决过滤，非买入）")
    pf3 = sub.add_parser("feng3", help="逢3必变v1：局部高低点研究回测（观察层，非买入）")
    pf3.add_argument("codes", nargs="*", help="可选：个股代码，省略则批量回测")
    pf3.add_argument("--sample", type=int, default=800)
    pf3.add_argument("--seed", type=int, default=42)
    pf3.add_argument("--swing-k", type=int, default=3)
    pf3.add_argument("--near-pct", type=float, default=2.0)
    pf3.add_argument("--hit-n", type=int, default=10)
    pf3.add_argument("--hold-m", type=int, default=20)
    pf3.add_argument("--mae-tol", type=float, default=2.0)
    pf3.add_argument("--cost-bps", type=float, default=15.0)
    pf3.add_argument("--exit", default="tight", choices=["tight", "wr", "asym", "fixed"])
    pgf = sub.add_parser("gufeng", help="估值一谷买/一峰卖：主板扫描参考价（观察层，非荐股）")
    pgf.add_argument("codes", nargs="*", help="可选：个股代码，缺省则全池扫描")
    pgf.add_argument("--valley", type=float, default=0.30, help="一谷分位阈值，默认0.30")
    pgf.add_argument("--peak", type=float, default=0.70, help="一峰分位阈值，默认0.70")
    pgf.add_argument("--top", type=int, default=40, help="终端打印前N条")
    pgf.add_argument("--limit", type=int, default=0, help="调试：最多扫N只，0=全池")
    pgf.add_argument("--no-live", action="store_true", help="不拉实时价，仅用收盘价")
    pgf.add_argument(
        "--divergence",
        action="store_true",
        help="买侧叠加近3～5日底背离：筛强化买入并写 gufeng_buy_div_*.csv（卖侧不变）",
    )
    pgf.add_argument(
        "--div-days",
        type=int,
        default=5,
        help="底背离回看交易日数，默认5，限制在3～5（回测口径）",
    )
    pgf.add_argument(
        "--low-turnover",
        action="store_true",
        help="买侧叠加缩量：换手率<1%%（可用 --turnover-max 覆盖；单位%%）",
    )
    pgf.add_argument(
        "--turnover-max",
        type=float,
        default=None,
        help="买侧换手率上限（单位%%，如 1.0 / 0.5）；与 --divergence 可叠加",
    )
    pchan.add_argument("codes", nargs="*", help="可选：个股结构摘要")
    pchan.add_argument("--compare", action="store_true", help="PATH对照 robust vs 缠论过滤/买卖点")
    pchan.add_argument("--sample", type=int, default=300, help="对照抽样股数")
    pchan.add_argument("--seed", type=int, default=42)
    pchan.add_argument("--period", type=int, default=20, help="持仓天数")
    pchan.add_argument("--exit", choices=["asym", "wr", "tight"], default="tight")
    pchan.add_argument("--cost-bps", type=float, default=15)
    pchan.add_argument("--no-low-vol", action="store_true", help="robust 关闭指数低波")
    pchan.add_argument("--limit", type=int, default=200, help="扫描输出上限")

    pedge = sub.add_parser("edge", help="边沿阶梯 / 组合PATH：robust之上过滤器 + playbook")
    pedge.add_argument("--sample", type=int, default=350)
    pedge.add_argument("--seed", type=int, default=42)
    pedge.add_argument("--period", type=int, default=20)
    pedge.add_argument("--exit", choices=["asym", "wr", "tight"], default="tight")
    pedge.add_argument("--cost-bps", type=float, default=15)
    pedge.add_argument("--no-low-vol", action="store_true")
    pedge.add_argument("--daily-cap", type=int, default=None, help="同日最多保留N只（按bias升序）")
    pedge.add_argument("--portfolio", action="store_true", help="组合级PATH：同日cap/持仓上限/环境闸")
    pedge.add_argument("--equity", action="store_true", help="日频资金曲线政策对照（NAV/回撤/超额）")

    pdisc = sub.add_parser("discipline", help="破戒检查日报：playbook 纪律审计")
    pdisc.add_argument("--holdings", type=str, default=None, help="持仓CSV路径，默认 output/holdings.csv")
    pdisc.add_argument("--cached", action="store_true", help="不重跑 action，用 output 缓存")
    pdisc.add_argument("--template", action="store_true", help="只生成持仓模板")
    pdisc.add_argument("--week", action="store_true", help="近N天破戒汇总（读已有日报）")
    pdisc.add_argument("--days", type=int, default=14, help="周汇总天数，默认14")

    phold = sub.add_parser("holdings", help="持仓助手：init/from-action/sync-px/show/fill-industry/check")
    phold.add_argument(
        "action",
        choices=["init", "from-action", "sync-px", "show", "equal-weight", "fill-industry", "check"],
    )
    phold.add_argument("--path", type=str, default=None, help="持仓CSV，默认 output/holdings.csv")
    phold.add_argument("--force", action="store_true", help="init 时覆盖已有文件")
    phold.add_argument("--merge", action="store_true", help="from-action 合并旧仓")
    phold.add_argument("--no-equal", action="store_true", help="from-action 不自动均权")
    phold.add_argument("--max-weight", type=float, default=12.5, help="单只权重上限%")
    phold.add_argument("--no-new", action="store_true", help="from-action 不标今日新建")
    phold.add_argument("--no-live", action="store_true", help="sync-px/check 不用实时价")
    phold.add_argument("--max", type=int, default=150, help="fill-industry 最多补几只")
    phold.add_argument("--sleep", type=float, default=0.12, help="fill-industry 间隔秒")
    phold.add_argument("--all", action="store_true", help="fill-industry 含已有（重拉）")

    pjoint = sub.add_parser("joint", help="联合信号v2：完整通过∩MENGα冷静")

    pdaily = sub.add_parser("daily", help="收盘后日更：刷新+扫盘+通知")
    pdaily.add_argument("--no-refresh", action="store_true", help="不拉数，只用缓存扫")
    pdaily.add_argument("--no-notify", action="store_true", help="不发 macOS 通知")
    pdaily.add_argument("--open", action="store_true", help="结束后打开报告")
    pdaily.add_argument("--force-weekend", action="store_true", help="周末也跑")

    psch = sub.add_parser("schedule", help="安装/卸载 macOS 定时日更")
    psch.add_argument("action", choices=["install", "uninstall", "status"])
    psch.add_argument("--hour", type=int, default=15, help="小时（默认15）")
    psch.add_argument("--minute", type=int, default=35, help="分钟（默认35）")

    args = p.parse_args(argv)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "refresh":
        return cmd_refresh(args)
    if args.cmd == "score":
        return cmd_score(args)
    if args.cmd == "screen":
        return cmd_screen(args)
    if args.cmd == "backtest":
        return cmd_backtest(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "formulas":
        return cmd_formulas(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "forecast":
        return cmd_forecast(args)
    if args.cmd == "patterns":
        return cmd_patterns(args)
    if args.cmd == "yaogu":
        return cmd_yaogu(args)
    if args.cmd == "alpha":
        return cmd_alpha(args)
    if args.cmd == "cool":
        return cmd_cool(args)
    if args.cmd == "action":
        return cmd_action(args)
    if args.cmd == "recap":
        return cmd_recap(args)
    if args.cmd == "live":
        return cmd_live(args)
    if args.cmd == "stress":
        return cmd_stress(args)
    if args.cmd == "hist-validate":
        return cmd_hist_validate(args)
    if args.cmd == "up-prob":
        return cmd_up_prob(args)
    if args.cmd == "chan":
        return cmd_chan(args)
    if args.cmd == "feng3":
        return cmd_feng3(args)
    if args.cmd == "gufeng":
        return cmd_gufeng(args)
    if args.cmd == "edge":
        return cmd_edge(args)
    if args.cmd == "discipline":
        return cmd_discipline(args)
    if args.cmd == "holdings":
        return cmd_holdings(args)
    if args.cmd == "joint":
        return cmd_joint(args)
    if args.cmd == "daily":
        return cmd_daily(args)
    if args.cmd == "schedule":
        return cmd_schedule(args)
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
