#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP 包装层：allowlist / 代码注入 / JSON-RPC 握手。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.mcp_dispatch import TOOL_NAMES, build_argv  # noqa: E402
from lib.mcp_rpc import handle_rpc  # noqa: E402


def _must(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)


def test_tool_names_include_buy_exit():
    _must("astock_status" in TOOL_NAMES, "missing astock_status")
    _must("astock_action" in TOOL_NAMES, "missing astock_action")
    _must("astock_score" in TOOL_NAMES, "missing astock_score")
    _must("astock_recap" in TOOL_NAMES, "missing astock_recap")


def test_score_argv_default_robust():
    argv, timeout = build_argv("astock_score", {"codes": ["600519"]})
    _must(argv[:2] == ["score", "600519"], argv)
    _must("--mode" in argv and "robust" in argv, argv)
    _must(timeout > 0, str(timeout))


def test_reject_shell_injection_in_code():
    try:
        build_argv("astock_score", {"codes": ["600519;rm -rf /"]})
    except ValueError as e:
        _must("code" in str(e).lower() or "非法" in str(e), str(e))
        return
    raise SystemExit("FAIL: injection code was accepted")


def test_reject_serve_and_schedule():
    for cmd in ("serve", "schedule"):
        try:
            build_argv("astock_run", {"cmd": cmd})
        except ValueError:
            continue
        raise SystemExit("FAIL: astock_run accepted forbidden cmd " + cmd)


def test_universe_requires_confirm():
    try:
        build_argv("astock_refresh", {"scope": "universe"})
    except ValueError:
        return
    raise SystemExit("FAIL: universe without confirm was accepted")


def test_initialize_handshake():
    resp = handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    )
    _must(resp["id"] == 1, resp)
    _must(resp["result"]["serverInfo"]["name"] == "astock-quant", resp)
    _must("tools" in resp["result"]["capabilities"], resp)


def test_tools_list_schema():
    resp = handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    _must("astock_action" in names, names)
    for t in resp["result"]["tools"]:
        _must("inputSchema" in t, t["name"])
        _must(t["inputSchema"].get("type") == "object", t["name"])


def test_unknown_tool_is_error():
    resp = handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "place_order", "arguments": {}},
        }
    )
    _must(resp["result"]["isError"] is True, resp)


def test_stdio_newline_json():
    server = os.path.join(ROOT, "mcp_server.py")
    py = sys.executable
    proc = subprocess.Popen(
        [py, server],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        proc.stdin.write(msg + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        data = json.loads(line)
        _must(data["result"]["serverInfo"]["name"] == "astock-quant", data)
    finally:
        proc.kill()
        proc.wait(timeout=3)


def main():
    test_tool_names_include_buy_exit()
    test_score_argv_default_robust()
    test_reject_shell_injection_in_code()
    test_reject_serve_and_schedule()
    test_universe_requires_confirm()
    test_initialize_handshake()
    test_tools_list_schema()
    test_unknown_tool_is_error()
    test_stdio_newline_json()
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
