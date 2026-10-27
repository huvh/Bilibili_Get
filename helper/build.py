"""@brief 生成不依赖服务端或外部资源的单文件 HTML 与完整书签。"""
import json
from pathlib import Path
import urllib.parse
import sys

ROOT = Path(__file__).resolve().parent


def build():
    sys.path.insert(0, str(ROOT.parent))
    from navigation import NAV_STYLE, render_navigation
    core = (ROOT / "subtitle-core.mjs").read_text("utf-8").replace("export function", "function", 1)
    runner = (ROOT / "bookmarklet-runner.js").read_text("utf-8")
    source = "(function(){\n" + core + "\n" + runner + "\n})();"
    bookmarklet = "javascript:" + urllib.parse.quote(source, safe="")
    template = (ROOT / "install.template.html").read_text("utf-8")
    page = template.replace("__NAV_STYLE__", NAV_STYLE).replace(
        "__APP_NAV__", render_navigation("helper", "http://127.0.0.1:8765"))
    page = page.replace("__CORE__", core).replace(
        "__BOOKMARKLET__", json.dumps(bookmarklet, ensure_ascii=False).replace("<", "\\u003c"))
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "B站字幕转TXT助手.html").write_text(page, encoding="utf-8")
    (dist / "bookmarklet.txt").write_text(bookmarklet, encoding="utf-8")
    static = ROOT.parent / "static"
    (static / "helper.html").write_text(page, encoding="utf-8")
    (static / "login-helper.js").write_text(source, encoding="utf-8")
    print(f"Built standalone helper: {dist}")
    print(f"Bookmarklet length: {len(bookmarklet)} characters")


if __name__ == "__main__":
    build()
