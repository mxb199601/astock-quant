# -*- coding: utf-8 -*-
"""MCP 工具 → cli.py argv。校验在边界完成，禁止拼进 shell。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("ASTOCK_PY") or sys.executable
CLI = os.path.join(ROOT, "cli.py")

CODE_RE = re.compile(r"^(?:sh|sz)?(\d{6})$", re.I)
TOKEN_BAD = re.compile(r"[;|&$<>`\\\n\r]")
FLAG_RE = re.compile(r"^--[a-z0-9-]+$")
SIMPLE_RE = re.compile(r"^[\w.+%-]+$", re.UNICODE)

MODES = {
    "base", "strict", "elite", "winrate", "robust", "boost",
    "dual_mom", "donchian", "ma_squeeze", "golden_cross",
    "boll_sq", "macd_trend", "atr_break", "rps_break", "composite",
}
EXITS = {"fixed", "tpsl", "asym", "wr", "tight"}
HOLDINGS = {
    "init", "from-action", "sync-px", "show",
    "equal-weight", "fill-industry", "check",
}
FORMULA_ACTIONS = {"list", "compare", "scan"}
ALLOWED_RUN = {
    "status", "refresh", "score", "screen", "action", "joint", "alpha", "cool",
    "live", "backtest", "stress", "hist-validate", "up-prob", "patterns",
    "formulas", "chan", "yaogu", "forecast", "discipline", "holdings",
    "daily", "report", "edge", "recap",
}
FORBIDDEN_RUN = {"serve", "schedule"}
FORBIDDEN_FLAGS = {"--universe", "--include-all-boards", "--hour", "--minute"}

MAX_CODES = 20
MAX_EXTRA = 16
MAX_OUT = 18000


def _codes(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
    elif isinstance(raw, list):
        parts = [str(x).strip() for x in raw]
    else:
        raise ValueError("codes 必须是字符串或列表")
    if len(parts) > MAX_CODES:
        raise ValueError("codes 最多 %d 个" % MAX_CODES)
    out: List[str] = []
    for p in parts:
        if TOKEN_BAD.search(p):
            raise ValueError("非法 code")
        m = CODE_RE.match(p)
        if not m:
            raise ValueError("非法 code: %s" % p)
        out.append(m.group(1))
    return out


def _mode(v: Any, default: str = "robust") -> str:
    if v is None or v == "":
        return default
    s = str(v)
    if s not in MODES:
        raise ValueError("非法 mode")
    return s


def _exit(v: Any, default: str = "tight") -> str:
    if v is None or v == "":
        return default
    s = str(v)
    if s not in EXITS:
        raise ValueError("非法 exit")
    return s


def _int(v: Any, default: int, lo: int, hi: int, name: str) -> int:
    if v is None or v == "":
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError("非法 %s" % name)
    if n < lo or n > hi:
        raise ValueError("%s 超出范围" % name)
    return n


def _bool(v: Any) -> bool:
    if v is True or v == 1 or str(v).lower() in ("1", "true", "yes"):
        return True
    return False


def _extra_token(t: str) -> str:
    if TOKEN_BAD.search(t) or t in FORBIDDEN_FLAGS:
        raise ValueError("非法参数: %s" % t)
    if t.startswith("--"):
        if not FLAG_RE.match(t):
            raise ValueError("非法 flag")
        return t
    if not SIMPLE_RE.match(t):
        raise ValueError("非法参数: %s" % t)
    return t


def build_argv(name: str, arguments: Optional[Dict[str, Any]] = None) -> Tuple[List[str], int]:
    args = arguments or {}
    if name == "astock_status":
        return ["status"], 60
    if name == "astock_action":
        return ["action"], 300
    if name == "astock_recap":
        argv = ["recap"]
        if _bool(args.get("cached")):
            argv.append("--cached")
        if _bool(args.get("no_observe")):
            argv.append("--no-observe")
        return argv, 300
    if name == "astock_live":
        return ["live"] + _codes(args.get("codes")), 60
    if name == "astock_score":
        codes = _codes(args.get("codes"))
        if not codes:
            raise ValueError("score 需要 codes")
        argv = ["score"] + codes + ["--mode", _mode(args.get("mode"))]
        if _bool(args.get("no_low_vol")):
            argv.append("--no-low-vol")
        return argv, 60
    if name == "astock_joint":
        return ["joint"], 300
    if name == "astock_screen":
        argv = ["screen", "--mode", _mode(args.get("mode"))]
        if _bool(args.get("theme")):
            argv.append("--theme")
        if _bool(args.get("no_low_vol")):
            argv.append("--no-low-vol")
        return argv, 300
    if name == "astock_alpha":
        argv = ["alpha", "--top", str(_int(args.get("top"), 40, 1, 200, "top"))]
        argv += _codes(args.get("codes"))
        return argv, 300
    if name == "astock_cool":
        argv = ["cool", "--top", str(_int(args.get("top"), 30, 1, 200, "top"))]
        if _bool(args.get("cached")):
            argv.append("--cached")
        return argv, 300
    if name == "astock_discipline":
        argv = ["discipline"]
        if _bool(args.get("week")):
            argv.append("--week")
        return argv, 120
    if name == "astock_holdings":
        act = str(args.get("action") or "check")
        if act not in HOLDINGS:
            raise ValueError("非法 holdings action")
        argv = ["holdings", act]
        if _bool(args.get("merge")):
            argv.append("--merge")
        return argv, 120
    if name == "astock_refresh":
        scope = str(args.get("scope") or "all")
        if scope not in ("theme", "all", "universe"):
            raise ValueError("非法 refresh scope")
        if scope == "universe" and not _bool(args.get("confirm")):
            raise ValueError("universe 需要 confirm=true")
        argv = ["refresh"]
        codes = _codes(args.get("codes"))
        if codes:
            argv += codes
        elif scope == "all":
            argv.append("--all")
        elif scope == "universe":
            argv.append("--universe")
        return argv, 1800 if scope == "universe" else 600
    if name == "astock_backtest":
        argv = [
            "backtest",
            "--mode", _mode(args.get("mode")),
            "--exit", _exit(args.get("exit")),
            "--no-plot",
        ]
        if _bool(args.get("no_low_vol")):
            argv.append("--no-low-vol")
        return argv, 300
    if name == "astock_stress":
        return ["stress", "--sample", str(_int(args.get("sample"), 400, 50, 2000, "sample"))], 300
    if name == "astock_run":
        cmd = str(args.get("cmd") or "")
        if cmd in FORBIDDEN_RUN or cmd not in ALLOWED_RUN:
            raise ValueError("禁止或未知 cmd: %s" % cmd)
        extra = args.get("extra") or []
        if isinstance(extra, str):
            extra = extra.split()
        if not isinstance(extra, list):
            raise ValueError("extra 必须是列表")
        if len(extra) > MAX_EXTRA:
            raise ValueError("extra 过多")
        tokens = [_extra_token(str(x)) for x in extra]
        return [cmd] + tokens, 300
    raise ValueError("未知工具: %s" % name)


def run_cli(argv: List[str], timeout: int) -> Tuple[int, str]:
    cmd = [PY, CLI] + argv
    try:
        p = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "超时（%ss）：%s" % (timeout, " ".join(argv))
    text = (p.stdout or "")
    if p.stderr:
        text += ("\n[stderr]\n" + p.stderr) if text else p.stderr
    if len(text) > MAX_OUT:
        text = text[:MAX_OUT] + "\n…(truncated)"
    return p.returncode, text


def _tool(name: str, description: str, props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


TOOLS: List[Dict[str, Any]] = [
    _tool("astock_status", "大盘红绿灯 + 缓存新鲜度。决策前先调。红灯应空仓。", {}),
    _tool("astock_action", "今日操作口径：唯一买入向出口。红灯返回空仓。不是荐股。", {}),
    _tool(
        "astock_recap",
        "复盘 Markdown：Grok 报告骨架 + 少妇仓位 + 游资槽位，票只来自 action。红灯不凑 3×3。cached=true 用已有 output。",
        {"cached": {"type": "boolean"}, "no_observe": {"type": "boolean"}},
    ),
    _tool(
        "astock_live",
        "实时行情快照。默认沪深300；可传主板代码。",
        {"codes": {"type": "array", "items": {"type": "string"}, "description": "6位代码"}},
    ),
    _tool(
        "astock_score",
        "个股自检打分。默认 mode=robust。",
        {
            "codes": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "description": "robust/elite/strict/…"},
            "no_low_vol": {"type": "boolean"},
        },
        ["codes"],
    ),
    _tool("astock_joint", "联合信号 v2：完整通过 ∩ MENGα冷静。观察层，红灯为空。", {}),
    _tool(
        "astock_screen",
        "结构扫盘。默认 robust。",
        {
            "mode": {"type": "string"},
            "theme": {"type": "boolean"},
            "no_low_vol": {"type": "boolean"},
        },
    ),
    _tool(
        "astock_alpha",
        "MENGα 截面。高α≠买入。",
        {
            "top": {"type": "integer"},
            "codes": {"type": "array", "items": {"type": "string"}},
        },
    ),
    _tool(
        "astock_cool",
        "冷静观察池。不是买入名单。",
        {"top": {"type": "integer"}, "cached": {"type": "boolean"}},
    ),
    _tool(
        "astock_discipline",
        "破戒检查日报。",
        {"week": {"type": "boolean"}},
    ),
    _tool(
        "astock_holdings",
        "持仓助手。",
        {
            "action": {
                "type": "string",
                "description": "init|from-action|sync-px|show|equal-weight|fill-industry|check",
            },
            "merge": {"type": "boolean"},
        },
    ),
    _tool(
        "astock_refresh",
        "刷新K线/基本面。默认 scope=all。universe 必须 confirm=true。",
        {
            "scope": {"type": "string", "description": "theme|all|universe"},
            "confirm": {"type": "boolean"},
            "codes": {"type": "array", "items": {"type": "string"}},
        },
    ),
    _tool(
        "astock_backtest",
        "事件回测。默认 robust×tight --no-plot。输出多为 OPT 窗，决策看 PATH。",
        {"mode": {"type": "string"}, "exit": {"type": "string"}, "no_low_vol": {"type": "boolean"}},
    ),
    _tool(
        "astock_stress",
        "OPT vs PATH 压力测试。仓位预期看 PATH。",
        {"sample": {"type": "integer"}},
    ),
    _tool(
        "astock_run",
        "允许名单内的其它 cli 子命令（patterns/formulas/chan/forecast/daily/edge/up-prob…）。禁止 serve/schedule。",
        {
            "cmd": {"type": "string"},
            "extra": {"type": "array", "items": {"type": "string"}, "description": "已校验的 flag/值，禁止 --universe"},
        },
        ["cmd"],
    ),
]

TOOL_NAMES = {t["name"] for t in TOOLS}
