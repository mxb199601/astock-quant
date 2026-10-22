#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股量化工具 MCP（stdio，零第三方依赖）。包装 cli.py。"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.mcp_rpc import handle_rpc  # noqa: E402


def main() -> int:
    stdin = sys.stdin
    stdout = sys.stdout
    while True:
        line = stdin.readline()
        if line == "":
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: %s" % e},
            }
            stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
            stdout.flush()
            continue
        resp = handle_rpc(msg)
        if resp is None:
            continue
        stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
