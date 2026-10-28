"""@brief 一键打开批量转写页；服务未运行时先在后台启动。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8765"


def is_running():
    """回环健康检查始终直连，不经过代理。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE + "/api/health", timeout=1) as response:
            return json.load(response).get("service") == "local-bilibili-subtitle"
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="启动服务并打开批量转写工作台")
    parser.add_argument("--no-browser", action="store_true", help="只检查或启动服务")
    args = parser.parse_args()
    if not is_running():
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        with (ROOT / "server.log").open("ab") as output, (ROOT / "server-error.log").open("ab") as error:
            subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "server.py")],
                             cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                             creationflags=flags, start_new_session=os.name != "nt")
        deadline = time.monotonic() + 15
        while not is_running():
            if time.monotonic() >= deadline:
                raise RuntimeError("服务未能启动，请查看项目目录的 server-error.log。")
            time.sleep(.2)
    if not args.no_browser:
        webbrowser.open(BASE + "/transcribe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
