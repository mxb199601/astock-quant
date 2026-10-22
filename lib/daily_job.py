# -*- coding: utf-8 -*-
"""每个交易日收盘后自动任务：刷新 → 扫盘 → 写报告 → macOS 通知。

建议时间：工作日 15:35（A股收盘 15:00，留数据落盘时间）
安装：python cli.py schedule install
手动跑：python cli.py daily
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from .paths import OUTPUT, ROOT


def _is_weekend(dt: Optional[datetime] = None) -> bool:
    d = dt or datetime.now()
    return d.weekday() >= 5  # 5=Sat 6=Sun


def _terminal_notifier_bin() -> Optional[str]:
    """Locate terminal-notifier (clickable notifications)."""
    found = shutil.which("terminal-notifier")
    if found:
        return found
    for p in (
        "/opt/homebrew/bin/terminal-notifier",
        "/usr/local/bin/terminal-notifier",
    ):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _notify(title: str, body: str, open_path: Optional[str] = None) -> None:
    """macOS 通知中心。

    优先 terminal-notifier：点击通知会执行 `open <报告路径>`。
    无 binary 时退回 osascript（点「显示」无动作，与旧行为一致）。
    """
    body = (body or "")[:180]
    open_path = os.path.abspath(open_path) if open_path else None
    if open_path and not os.path.exists(open_path):
        open_path = None

    tn = _terminal_notifier_bin()
    if tn:
        cmd = [
            tn,
            "-title",
            title,
            "-message",
            body if body else " ",
            "-sound",
            "Glass",
            "-group",
            "com.meng.quant-daily",
        ]
        if open_path:
            # 点击通知 → open 本地报告（md 用 -execute 比 -open file:// 更稳）
            cmd += ["-execute", f"open {subprocess.list2cmdline([open_path])}"]
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=15)
            return
        except Exception:
            pass

    # fallback: 纯 AppleScript（无点击动作）
    try:
        t = title.replace("\\", "\\\\").replace('"', '\\"')
        b = body.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{b}" with title "{t}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except Exception:
        pass


def _write_report(
    asof: str,
    regime: dict,
    full_pass: List[dict],
    tech_pass: List[dict],
    strict_struct: List[dict],
    refresh_stats: dict,
) -> str:
    day_dir = os.path.join(OUTPUT, "daily")
    os.makedirs(day_dir, exist_ok=True)
    path = os.path.join(day_dir, f"{asof}.md")
    light = regime.get("light", "?")
    ret20 = regime.get("ret20")
    ret_s = f"{ret20:+.2f}%" if ret20 == ret20 else "—"

    lines = [
        f"# 日更报告 {asof}",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 大盘: **{light}** | 沪深300近20日 {ret_s} | 收盘 {regime.get('close')}",
        f"- MA20={regime.get('above_ma20')} MA60={regime.get('above_ma60')}",
        f"- 刷新: ok={refresh_stats.get('kline_ok')} fail={refresh_stats.get('kline_fail')} "
        f"stale={refresh_stats.get('kline_stale', 0)} skip={refresh_stats.get('kline_skip')}",
        "",
        f"## 完整通过 ★（{len(full_pass)} 只）",
        "",
    ]
    if not full_pass:
        lines.append("无。红灯/未站 MA20+MA60 时为空（纪律性空仓）。")
        lines.append("")
    else:
        lines.append("| 名称 | 代码 | 加分 | 现价 | RSI | 60日% |")
        lines.append("|------|------|------|------|-----|-------|")
        for r in full_pass:
            lines.append(
                f"| {r.get('name')} | {r.get('sym')} | {r.get('plus')}/9 | "
                f"{r.get('px')} | {r.get('rsi')} | {r.get('zf60')}% |"
            )
        lines.append("")
        lines.append("CODES: " + " ".join(r["sym"] for r in full_pass))
        lines.append("")

    lines += [
        f"## 严结构观察（{len(strict_struct)} 只，忽略大盘）",
        "",
    ]
    if strict_struct:
        lines.append("| 名称 | 代码 | 加分 | RSI | 60日% |")
        lines.append("|------|------|------|-----|-------|")
        for r in strict_struct[:30]:
            lines.append(
                f"| {r.get('name')} | {r.get('sym')} | {r.get('plus')}/9 | "
                f"{r.get('rsi')} | {r.get('zf60')}% |"
            )
    else:
        lines.append("无")
    lines += [
        "",
        f"## 结构观察共 {len(tech_pass)} 只",
        "",
        "详见 `output/pass_stock_structure.csv` / Web 系统。",
        "",
        "> 研究工具，不构成投资建议。完整通过才是系统口径的可操作候选。",
        "",
    ]
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    # 同步 latest
    latest = os.path.join(day_dir, "latest.md")
    with open(latest, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run_daily(
    skip_weekend: bool = True,
    refresh_data: bool = True,
    notify: bool = True,
    open_report: bool = False,
) -> Dict[str, Any]:
    """执行日更。返回摘要 dict。"""
    log_lines = []
    def log(msg: str):
        print(msg, flush=True)
        log_lines.append(msg)

    now = datetime.now()
    if skip_weekend and _is_weekend(now):
        msg = f"周末跳过日更 ({now.strftime('%Y-%m-%d %A')})"
        log(msg)
        if notify:
            latest = os.path.join(OUTPUT, "daily", "latest.md")
            _notify("量化日更", msg, open_path=latest if os.path.exists(latest) else None)
        return {"ok": True, "skipped": True, "reason": "weekend"}

    log(f"=== 量化日更开始 {now.strftime('%Y-%m-%d %H:%M:%S')} ===")
    refresh_stats: dict = {"kline_ok": 0, "kline_fail": 0, "kline_skip": 0}

    if refresh_data:
        from .data import fetch_kline, list_cached_symbols, load_fundamentals, refresh

        log("1) 刷新基本面 + 沪深300…")
        load_fundamentals(force=True)
        fetch_kline("sh000300", force=True, min_bars=80)
        from .data import load_kline_csv
        idx = load_kline_csv("sh000300", min_bars=30)
        if idx is None:
            log("错误: 无法获取沪深300")
            if notify:
                _notify("量化日更失败", "无法获取沪深300")
            return {"ok": False, "error": "no_index"}
        asof = str(idx["date"].iloc[-1])
        log(f"   指数最新日: {asof}")

        log("2) 增量刷新主板 K 线（仅补未到最新日的）…")
        syms = ["sh000300"] + list_cached_symbols(mainboard_only=True, drop_st=True)
        refresh_stats = refresh(
            symbols=syms,
            fund=False,
            force_kline=True,
            sleep=0.03,
            mainboard_only=True,
            skip_if_fresh=True,
            asof=asof,
        )
        log(
            f"   刷新完成 ok={refresh_stats.get('kline_ok')} "
            f"fail={refresh_stats.get('kline_fail')} "
            f"stale={refresh_stats.get('kline_stale', 0)} "
            f"skip={refresh_stats.get('kline_skip')}"
        )
    else:
        from .data import load_kline_csv
        idx = load_kline_csv("sh000300", min_bars=30)
        asof = str(idx["date"].iloc[-1]) if idx is not None else now.strftime("%Y-%m-%d")

    log("3) 全市场结构扫盘…")
    from .scan import run_structure_scan

    result = run_structure_scan()
    if not result.get("ok"):
        log(f"扫盘失败: {result.get('error')}")
        if notify:
            _notify("量化日更失败", str(result.get("error")))
        return {"ok": False, "error": result.get("error")}

    regime = result.get("regime") or {}
    full = result.get("full_pass") or []
    tech = result.get("tech_pass") or []
    strict = result.get("strict_struct") or []
    light = regime.get("light", "?")

    log("3b) 联合信号 B（完整通过∩高置信偏多）…")
    from .joint import run_joint_signals
    # 复用刚扫的 full：joint 内部会再扫一次；可接受
    joint_r = run_joint_signals(use_llm=False)
    joint = joint_r.get("joint") or []

    log("3c) MENGα 自研指标…")
    alpha_r: Dict[str, Any] = {}
    try:
        from .own_alpha import scan_own_alpha
        alpha_r = scan_own_alpha(top_n=30)
        log(f"   MENGα n={alpha_r.get('n')} top={len(alpha_r.get('rows') or [])}")
    except Exception as e:
        log(f"   MENGα 失败: {e}")
        alpha_r = {"ok": False, "error": str(e), "rows": [], "hot_rows": []}

    log("3d) 形态 + 妖股启动前…")
    pat_r: Dict[str, Any] = {}
    yao_r: Dict[str, Any] = {}
    try:
        from .pattern_scan import run_pattern_scan
        pat_r = run_pattern_scan()
        log(f"   形态命中股 {len(pat_r.get('rows') or [])}")
    except Exception as e:
        log(f"   形态失败: {e}")
        pat_r = {"rows": [], "counts": {}}
    try:
        from .yaogu import scan_yaogu_prelaunch
        yao_r = scan_yaogu_prelaunch(top_n=30)
        log(f"   妖股启动前候选 {len(yao_r.get('rows') or [])}")
    except Exception as e:
        log(f"   妖股启动前失败: {e}")
        yao_r = {"rows": []}

    report = _write_report(asof, regime, full, tech, strict, refresh_stats)

    log("3e) 破戒检查日报…")
    disc_r: Dict[str, Any] = {}
    try:
        from .discipline import run_discipline_check

        disc_r = run_discipline_check(refresh_action=True, asof=asof)
        log(
            f"   破戒: {disc_r.get('grade')} "
            f"pass={disc_r.get('n_pass')} fail={disc_r.get('n_fail')} "
            f"crit={disc_r.get('n_crit')} → {disc_r.get('report')}"
        )
    except Exception as e:
        log(f"   破戒检查失败: {e}")
        disc_r = {"ok": False, "error": str(e)}
    # 追加联合信号 / MENGα / 形态 / 妖股到报告
    try:
        with open(report, "a", encoding="utf-8") as f:
            f.write(f"\n## 联合信号 ★★（{len(joint)} 只）\n\n")
            if not joint:
                f.write("无（需完整通过 + 涨跌研究可行动偏多 + 置信中/高）。\n")
            else:
                f.write("| 名称 | 代码 | 置信 | 次日涨% | 均值 |\n|------|------|------|---------|------|\n")
                for x in joint:
                    f.write(
                        f"| {x.get('name')} | {x.get('sym')} | {x.get('forecast_conf')} | "
                        f"{x.get('up_rate_1d')}% | {x.get('mean_1d')}% |\n"
                    )
                f.write("\nCODES: " + " ".join(x["sym"] for x in joint) + "\n")

            arows = alpha_r.get("rows") or []
            f.write(f"\n## MENGα 自研 Top（{len(arows)} 展示 / 全市场 {alpha_r.get('n', '—')}）\n\n")
            f.write(f"{alpha_r.get('note') or ''}\n\n")
            if arows:
                f.write("| α | 热 | 洗 | 芽 | 拥 | 名称 | 代码 | 60日% | 标签 |\n")
                f.write("|---|----|----|----|----|------|------|-------|------|\n")
                for x in arows[:25]:
                    f.write(
                        f"| {x.get('menga')} | {x.get('heat')} | {x.get('wash')} | "
                        f"{x.get('spark')} | {x.get('crowd')} | {x.get('name')} | "
                        f"{x.get('sym')} | {x.get('ret60')}% | {x.get('tag')} |\n"
                    )
                f.write("\n详见 `output/menga_today.csv`。高α≠必涨。\n")
            hots = alpha_r.get("hot_rows") or []
            if hots:
                f.write("\n### 过热对照（Heat 最高）\n\n")
                f.write("| 热 | α | 名称 | 代码 | 60日% |\n|----|----|------|------|-------|\n")
                for x in hots[:10]:
                    f.write(
                        f"| {x.get('heat')} | {x.get('menga')} | {x.get('name')} | "
                        f"{x.get('sym')} | {x.get('ret60')}% |\n"
                    )

            yrows = yao_r.get("rows") or []
            f.write(f"\n## 妖股启动前画像 Top（{len(yrows)}）\n\n")
            if yrows:
                f.write("| 分 | 名称 | 代码 | RSI | bias | 形态 |\n|----|------|------|-----|------|------|\n")
                for x in yrows[:20]:
                    pats = "、".join((x.get("patterns") or [])[:3])
                    f.write(
                        f"| {x.get('yaogu_score')} | {x.get('name')} | {x.get('sym')} | "
                        f"{x.get('rsi')} | {x.get('bias20')}% | {pats} |\n"
                    )
                f.write("\n详见 `output/yaogu_prelaunch.csv`。命中≠成妖。\n")

            counts = pat_r.get("counts") or {}
            if counts:
                f.write("\n## 形态命中统计\n\n")
                for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
                    if v:
                        f.write(f"- {k}: {v}\n")
                f.write("\n详见 `output/patterns_today.csv`。\n")

            # 破戒摘要
            if disc_r.get("ok"):
                f.write(f"\n## 破戒检查 · {disc_r.get('grade')}\n\n")
                f.write(
                    f"- 通过 {disc_r.get('n_pass')} / 失败 {disc_r.get('n_fail')} / "
                    f"严重 {disc_r.get('n_crit')}\n"
                )
                f.write(f"- 报告: `{disc_r.get('report')}`\n")
                fails = [c for c in (disc_r.get("checks") or []) if not c.get("ok")]
                if fails:
                    f.write("\n失败项:\n\n")
                    for c in fails[:12]:
                        f.write(f"- **{c.get('title')}**: {c.get('detail')}\n")
                else:
                    f.write("\n无失败项 — 守戒。\n")
                f.write("\n")

            f.write("\n> 研究工具，不构成投资建议。\n")
        # 同步 latest
        latest = os.path.join(OUTPUT, "daily", "latest.md")
        with open(report, encoding="utf-8") as rf:
            text = rf.read()
        with open(latest, "w", encoding="utf-8") as lf:
            lf.write(text)
    except Exception as e:
        log(f"   追加报告失败: {e}")

    log(f"4) 报告: {report}")
    a_n = len(alpha_r.get("rows") or [])
    y_n = len(yao_r.get("rows") or [])
    log(
        f"   大盘 {light} | 完整通过 {len(full)} | 联合 {len(joint)} | "
        f"严结构 {len(strict)} | 结构 {len(tech)} | MENGαTop {a_n} | 妖股前 {y_n}"
        f" | 破戒 {disc_r.get('grade', '—')}"
    )

    # 通知：优先联合信号；红灯补充 MENGα。点击通知 → 打开报告（terminal-notifier）
    if notify:
        if disc_r.get("ok") and (disc_r.get("n_crit") or 0) > 0:
            disc_path = disc_r.get("report") or report
            _notify(
                f"破戒警报·{disc_r.get('grade')}",
                f"严重{disc_r.get('n_crit')}项 失败{disc_r.get('n_fail')} · 见 discipline 日报",
                open_path=disc_path,
            )
        if joint:
            names = "、".join(f"{r['name']}" for r in joint[:8])
            more = f" 等{len(joint)}只" if len(joint) > 8 else f" 共{len(joint)}只"
            _notify(
                f"量化·联合信号 {len(joint)}只 [{light}]",
                names + more + " · 完整通过∩高置信偏多 · 点开看报告",
                open_path=report,
            )
        elif full:
            names = "、".join(f"{r['name']}" for r in full[:8])
            more = f" 等{len(full)}只" if len(full) > 8 else f" 共{len(full)}只"
            _notify(
                f"量化·完整通过 {len(full)}只 [{light}]",
                names + more + " · 联合信号0 · 点开看报告",
                open_path=report,
            )
        else:
            atop = "、".join(
                f"{r.get('name')}" for r in (alpha_r.get("rows") or [])[:5]
            )
            body = f"联合0·完整0·结构{len(tech)}·α观察{a_n}"
            if atop:
                body += f" · 高α:{atop}"
            body += " · 点开看报告"
            _notify(f"量化日更 [{light}] {asof}", body[:180], open_path=report)

    if open_report:
        subprocess.run(["open", report], check=False)

    # 日志
    os.makedirs(os.path.join(OUTPUT, "daily"), exist_ok=True)
    with open(os.path.join(OUTPUT, "daily", "job.log"), "a", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    log("=== 日更结束 ===")
    return {
        "ok": True,
        "asof": asof,
        "light": light,
        "full_pass_count": len(full),
        "full_pass": full,
        "joint_count": len(joint),
        "tech_count": len(tech),
        "strict_count": len(strict),
        "alpha_top": a_n,
        "yaogu_top": y_n,
        "report": report,
        "refresh": refresh_stats,
        "discipline": {
            "grade": disc_r.get("grade"),
            "n_fail": disc_r.get("n_fail"),
            "n_crit": disc_r.get("n_crit"),
            "report": disc_r.get("report"),
        },
    }


# launchd
PLIST_LABEL = "com.meng.quant-daily"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist")


def install_schedule(hour: int = 15, minute: int = 35, python: Optional[str] = None) -> str:
    """安装 macOS LaunchAgent：工作日 hour:minute 跑 daily。"""
    py = python or sys.executable
    cli = os.path.join(ROOT, "cli.py")
    log_out = os.path.join(OUTPUT, "daily", "launchd.out.log")
    log_err = os.path.join(OUTPUT, "daily", "launchd.err.log")
    os.makedirs(os.path.join(OUTPUT, "daily"), exist_ok=True)

    # Weekday 1-5 = Mon-Fri in launchd? Actually StartCalendarInterval Weekday: 0=Sun ... 6=Sat
    # We emit 5 intervals Mon-Fri
    intervals = ""
    for wd in (1, 2, 3, 4, 5):  # Mon-Fri
        intervals += f"""
    <dict>
      <key>Weekday</key><integer>{wd}</integer>
      <key>Hour</key><integer>{hour}</integer>
      <key>Minute</key><integer>{minute}</integer>
    </dict>"""

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{cli}</string>
    <string>daily</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{ROOT}</string>
  <key>StartCalendarInterval</key>
  <array>{intervals}
  </array>
  <key>StandardOutPath</key>
  <string>{log_out}</string>
  <key>StandardErrorPath</key>
  <string>{log_err}</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
"""
    with open(PLIST_PATH, "w", encoding="utf-8") as f:
        f.write(plist)

    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    r = subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True, text=True)
    if r.returncode != 0:
        # macOS newer: bootstrap
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", PLIST_PATH],
            capture_output=True,
            text=True,
        )
    return PLIST_PATH


def uninstall_schedule() -> bool:
    if not os.path.exists(PLIST_PATH):
        return False
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{PLIST_LABEL}"],
        capture_output=True,
    )
    try:
        os.remove(PLIST_PATH)
    except OSError:
        pass
    return True


def schedule_status() -> str:
    r = subprocess.run(
        ["launchctl", "list"],
        capture_output=True,
        text=True,
    )
    loaded = PLIST_LABEL in (r.stdout or "")
    exists = os.path.exists(PLIST_PATH)
    return f"plist={'存在' if exists else '无'} path={PLIST_PATH}\nlaunchctl={'已加载' if loaded else '未加载'}"
