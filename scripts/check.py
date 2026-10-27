"""@brief 从项目根目录验证源码语法与各功能测试，不访问真实账号。"""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    for folder in (ROOT, ROOT / "scripts", ROOT / "tests" / "python", ROOT / "helper"):
        for source in sorted(folder.glob("*.py")):
            ast.parse(source.read_text("utf-8"), filename=str(source))
    environment = {**os.environ, "PYTHONUTF8": "1"}
    commands = [[sys.executable, "-m", "unittest", "discover", "-s", "tests/python", "-v"]]
    node = shutil.which("node")
    if node:
        for folder in (ROOT / "static", ROOT / "browser-extension" / "bili-cookie-extension", ROOT / "helper"):
            for source in sorted(folder.iterdir()):
                if source.suffix in {".js", ".mjs"}:
                    commands.append([node, "--check", str(source)])
        commands.extend([node, str(source)] for source in sorted((ROOT / "tests" / "javascript").glob("*.cjs")))
        commands.append([node, "--test", "helper/test-helper.mjs"])
    else:
        print("Node.js unavailable; JavaScript checks will be skipped.", flush=True)
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=environment)
        if result.returncode:
            return result.returncode
    print("All available checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
