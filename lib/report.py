# -*- coding: utf-8 -*-
"""生成可浏览器打开的结果页。"""
from __future__ import annotations

import html
import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .paths import OUTPUT, ROOT


def _run_capture(argv: list) -> str:
    try:
        r = subprocess.run(
            [sys.executable, *argv],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    except Exception as e:
        return f"(运行失败: {e})"


def build_dashboard(run_live: bool = True) -> str:
    out = Path(OUTPUT)
    out.mkdir(parents=True, exist_ok=True)
    sections = []

    if run_live:
        status = _run_capture(["cli.py", "status"])
        (out / "last_status.txt").write_text(status, encoding="utf-8")
        screen = _run_capture(["screen_market.py", "--theme"])
        (out / "last_screen.txt").write_text(screen, encoding="utf-8")
        watch = _run_capture(["theme_watch.py"])
        (out / "last_watch.txt").write_text(watch, encoding="utf-8")
        score = _run_capture([
            "cli.py", "score",
            "600519", "002371", "002747", "600150", "603728", "688028", "002050", "002085",
        ])
        (out / "last_score.txt").write_text(score, encoding="utf-8")

    for title, name in [
        ("大盘状态 status", "last_status.txt"),
        ("扫盘 screen --theme", "last_screen.txt"),
        ("主题监控 theme_watch", "last_watch.txt"),
        ("重点打分 score", "last_score.txt"),
        ("公式三 test_formula3", "last_formula3.txt"),
    ]:
        p = out / name
        body = p.read_text(encoding="utf-8", errors="replace") if p.exists() else "(无输出，可先 python cli.py report)"
        sections.append(
            f"<section><h2>{html.escape(title)}</h2><pre>{html.escape(body)}</pre></section>"
        )

    plots = []
    plot_dir = out / "backtest_plots"
    if plot_dir.exists():
        for p in sorted(plot_dir.glob("*.png")):
            rel = p.relative_to(out).as_posix()
            plots.append(
                f'<figure><img src="{html.escape(rel)}" alt="{html.escape(p.name)}">'
                f"<figcaption>{html.escape(p.name)}</figcaption></figure>"
            )

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>量化工具 · 运行结果</title>
<style>
  body {{ font-family: ui-sans-serif, system-ui, -apple-system, "PingFang SC", sans-serif;
    margin:0; background:#0f1419; color:#e7ecf3; line-height:1.5; }}
  header {{ padding:20px 24px; border-bottom:1px solid #243044;
    background:linear-gradient(180deg,#151c27,#0f1419); position:sticky; top:0; }}
  h1 {{ margin:0 0 6px; font-size:1.35rem; }}
  .meta {{ color:#8b9bb4; font-size:.9rem; }}
  main {{ max-width:1100px; margin:0 auto; padding:20px 24px 48px; }}
  section {{ background:#151c27; border:1px solid #243044; border-radius:12px;
    padding:16px 18px; margin-bottom:16px; }}
  h2 {{ margin:0 0 12px; font-size:1.05rem; color:#9ecbff; }}
  pre {{ margin:0; white-space:pre-wrap; word-break:break-word;
    font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12.5px;
    color:#d5deeb; background:#0c1017; border-radius:8px; padding:14px;
    overflow:auto; max-height:520px; }}
  .plots {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
  figure {{ margin:0; background:#0c1017; border-radius:8px; padding:10px; border:1px solid #243044; }}
  img {{ width:100%; height:auto; border-radius:6px; display:block; background:#fff; }}
  figcaption {{ margin-top:8px; color:#8b9bb4; font-size:.85rem; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:999px;
    background:#1d3a2a; color:#7ddea0; font-size:12px; margin-left:8px; }}
  code {{ color:#c8e0ff; }}
</style>
</head>
<body>
<header>
  <h1>量化工具 · 运行结果 <span class="badge">研究用·非荐股</span></h1>
  <div class="meta">生成：{html.escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}
    · 命令：python cli.py report --open</div>
</header>
<main>
  <section>
    <h2>使用方式</h2>
    <p><code>python cli.py status</code> ·
       <code>python cli.py refresh --theme</code> ·
       <code>python cli.py score 600519</code> ·
       <code>python cli.py screen --theme</code> ·
       <code>python cli.py backtest</code></p>
    <p>核心立场：没有必涨股；工具评估「结构是否符合规则」。红灯环境优先空仓。</p>
  </section>
  {''.join(sections)}
  <section>
    <h2>回测图</h2>
    <div class="plots">{''.join(plots) if plots else '<p>暂无，可 python cli.py backtest</p>'}</div>
  </section>
</main>
</body>
</html>
"""
    path = out / "dashboard.html"
    path.write_text(doc, encoding="utf-8")
    return str(path)
