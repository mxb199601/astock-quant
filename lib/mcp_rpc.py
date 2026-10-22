# -*- coding: utf-8 -*-
"""MCP JSON-RPC（stdio 换行分隔）。不写 stdout 日志。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .mcp_dispatch import TOOLS, TOOL_NAMES, build_argv, run_cli

PROTOCOL = "2025-03-26"
SERVER_INFO = {"name": "astock-quant", "version": "1.0.0"}
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18"}


def _ok(id_, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_text(text: str, is_error: bool) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": bool(is_error),
    }


def handle_rpc(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method")
    id_ = msg.get("id")
    params = msg.get("params") or {}

    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None

    if method == "initialize":
        ver = params.get("protocolVersion") or PROTOCOL
        if ver not in SUPPORTED:
            ver = PROTOCOL
        return _ok(
            id_,
            {
                "protocolVersion": ver,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "本仓 A 股研究工具 MCP。买入向出口只有 astock_action；"
                    "红灯空仓；MENGα/冷静池不是买点；决策看 PATH 不看 OPT。"
                    "研究工具，不构成投资建议。"
                ),
            },
        )

    if method == "ping":
        return _ok(id_, {})

    if method == "tools/list":
        return _ok(id_, {"tools": TOOLS})

    if method == "tools/call":
        name = (params.get("name") or "").strip()
        arguments = params.get("arguments") or {}
        if name not in TOOL_NAMES:
            return _ok(id_, _tool_text("未知工具: %s" % name, True))
        try:
            argv, timeout = build_argv(name, arguments)
        except ValueError as e:
            return _ok(id_, _tool_text(str(e), True))
        code, text = run_cli(argv, timeout)
        body = text if text.strip() else "(无输出)"
        if code != 0:
            body = "[exit %s]\n%s" % (code, body)
        return _ok(id_, _tool_text(body, code != 0))

    if id_ is None:
        return None
    return _err(id_, -32601, "Method not found: %s" % method)
