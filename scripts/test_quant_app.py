#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化研究.app：打开软件即起页面服务，不重复起，退出只停自己的进程。"""
from __future__ import annotations

import os
import plistlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SCRIPT = os.path.join(ROOT, "scripts", "quant_app.sh")
BUILD = os.path.join(ROOT, "scripts", "build_quant_app.sh")
APP = os.path.expanduser("~/Applications/量化研究.app")
PY = "/Users/meng/测试/.venv/bin/python"


def _must(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_up(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/" % port, timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _listeners(port):
    out = subprocess.check_output(
        ["lsof", "-nP", "-iTCP:%s" % port, "-sTCP:LISTEN", "-t"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    return [x for x in out.split() if x.strip()]


def _run(root, port, app, *args, check=False):
    env = os.environ.copy()
    env.update(
        {
            "QUANT_ROOT": root,
            "QUANT_PY": PY,
            "QUANT_APP": app,
            "QUANT_PORT": str(port),
            "QUANT_NO_OPEN": "1",
        }
    )
    return subprocess.run([SCRIPT, *args], env=env, text=True, capture_output=True, check=check)


def _write_fake(path: Path):
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        body = b'quant-app'\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Length', str(len(body)))\n"
        "        self.end_headers()\n"
        "        self.wfile.write(body)\n"
        "    def log_message(self, fmt, *args):\n"
        "        return\n"
        "port = int(os.environ['QUANT_PORT'])\n"
        "ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_ensure_starts_once_and_stop_kills_only_that():
    _must(os.path.isfile(SCRIPT), "缺少 scripts/quant_app.sh")
    port = _free_port()
    with tempfile.TemporaryDirectory() as tmp:
        root = tmp
        fake = Path(tmp) / "fake_app.py"
        _write_fake(fake)
        r = _run(root, port, str(fake), "ensure")
        _must(r.returncode == 0, r.stderr or r.stdout or "ensure 失败")
        _must(_http_up(port), "ensure 后页面服务应可打开")
        _must(len(_listeners(port)) == 1, "应只有一个监听: %s" % _listeners(port))
        r2 = _run(root, port, str(fake), "ensure")
        _must(r2.returncode == 0, r2.stderr or r2.stdout)
        _must(len(_listeners(port)) == 1, "已在跑时不得再起一个: %s" % _listeners(port))
        r3 = _run(root, port, str(fake), "stop")
        _must(r3.returncode == 0, r3.stderr or r3.stdout)
        time.sleep(0.3)
        _must(not _http_up(port), "stop 应停下自己拉起的服务")


def test_stop_leaves_unrecorded_server():
    _must(os.path.isfile(SCRIPT), "缺少 scripts/quant_app.sh")
    port = _free_port()
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "fake_app.py"
        _write_fake(fake)
        env = os.environ.copy()
        env["QUANT_PORT"] = str(port)
        proc = subprocess.Popen([PY, str(fake)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if _http_up(port):
                    break
                time.sleep(0.05)
            _must(_http_up(port), "外部服务没起来")
            r = _run(tmp, port, str(fake), "stop")
            _must(r.returncode == 0, r.stderr or r.stdout)
            time.sleep(0.2)
            _must(_http_up(port), "没有 pid 记录时 stop 不得杀掉别人的服务")
            _must(proc.poll() is None, "外部进程应仍在")
        finally:
            proc.kill()
            proc.wait(timeout=3)


def test_app_bundle_opens_without_manual_backend():
    _must(os.path.isfile(BUILD), "缺少 scripts/build_quant_app.sh")
    subprocess.run(["bash", BUILD], check=True)
    plist_path = os.path.join(APP, "Contents", "Info.plist")
    exe = os.path.join(APP, "Contents", "MacOS", "applet")
    scpt = os.path.join(APP, "Contents", "Resources", "Scripts", "main.scpt")
    _must(os.path.isfile(plist_path), "应用程序里没有 Info.plist: " + APP)
    _must(os.access(exe, os.X_OK), "应用程序不能执行: " + exe)
    with open(plist_path, "rb") as f:
        info = plistlib.load(f)
    _must(info.get("CFBundleDisplayName") == "量化研究", info.get("CFBundleDisplayName"))
    _must(info.get("CFBundleIdentifier") == "com.meng.quant-research", info.get("CFBundleIdentifier"))
    text = subprocess.check_output(["osadecompile", scpt], text=True)
    _must("quant_app.sh" in text and "ensure" in text, "打开软件应自己拉起页面服务")
    _must("reopen" in text, "再次点开应回到页面")
    _must("stop" in text and "quit" in text, "退出软件应停下页面服务")
    _must("8765" in text, "应打开现有页面端口")


def test_launcher_can_skip_second_browser():
    os.environ["QUANT_NO_BROWSER"] = "1"
    import app
    _must(hasattr(app, "should_open_browser"), "app.should_open_browser")
    _must(app.should_open_browser() is False, "软件拉起时不应再自动开一次浏览器")
    os.environ.pop("QUANT_NO_BROWSER", None)
    _must(app.should_open_browser() is True, "命令行直接跑 app.py 仍应打开浏览器")


def main():
    test_ensure_starts_once_and_stop_kills_only_that()
    test_stop_leaves_unrecorded_server()
    test_launcher_can_skip_second_browser()
    test_app_bundle_opens_without_manual_backend()
    print("ok")


if __name__ == "__main__":
    main()
