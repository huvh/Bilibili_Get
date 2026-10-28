"""@brief 按功能生成便携 ZIP，仅从固定源码白名单收集文件。"""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ("README.md", ".gitignore", ".gitattributes", "bilibili.py", "cookie_extension.py",
              "docx_to_txt.py", "navigation.py", "server.py", "subtitles.py", "tingwu.py",
              "video.py", "open_workbench.py", "requirements-video.txt", "start.cmd", "打开独立字幕助手.cmd",
              "听悟DOCX转TXT.cmd")
FOLDERS = {"static": {".html", ".css", ".js"}, "docs": {".md"}, "scripts": {".py"},
           "tests/python": {".py"}, "tests/javascript": {".cjs"},
           "helper": {".py", ".js", ".mjs", ".html", ".md"},
           "helper/dist": {".html", ".txt"},
           "browser-extension": {".md"}, "browser-extension/dist": {".zip"},
           "browser-extension/bili-cookie-extension": {".js", ".json", ".txt"}}


def write_zip(target, files, prefix="", extra=None):
    """只接受已选定的文件；不遍历运行目录、不读取用户内容。"""
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for source in files:
            relative = source.relative_to(ROOT).as_posix()
            archive.write(source, prefix + relative)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


def main():
    subprocess.run([sys.executable, str(ROOT / "helper" / "build.py")], check=True, cwd=ROOT)
    spec = importlib.util.spec_from_file_location("release_extension", ROOT / "cookie_extension.py")
    extension = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extension)
    extension_dist = ROOT / "browser-extension" / "dist"
    extension_dist.mkdir(exist_ok=True)
    with extension.extension_package() as package:
        extension_bytes = package.read()
    (extension_dist / "b站cookie获取扩展.zip").write_bytes(extension_bytes)

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    packages = []
    complete = [ROOT / name for name in ROOT_FILES]
    for directory, suffixes in FOLDERS.items():
        complete.extend(path for path in sorted((ROOT / directory).iterdir())
                        if path.is_file() and path.suffix in suffixes)
    app = dist / "Bilibili_Get.zip"
    write_zip(app, complete, "Bilibili_Get/")
    packages.append(app)
    cookie = dist / "Bilibili_Get_Cookie_Extension.zip"
    cookie.write_bytes(extension_bytes)
    packages.append(cookie)
    helper = dist / "Bilibili_Get_Subtitle_Helper.zip"
    with ZipFile(helper, "w", ZIP_DEFLATED) as archive:
        for name in ("B站字幕转TXT助手.html", "bookmarklet.txt"):
            archive.write(ROOT / "helper" / "dist" / name, name)
        archive.write(ROOT / "helper" / "README.md", "README.md")
    packages.append(helper)
    document = dist / "Bilibili_Get_DOCX_to_TXT.zip"
    write_zip(document, [ROOT / "docx_to_txt.py", ROOT / "听悟DOCX转TXT.cmd"], extra={
        "README.md": "# DOCX 转 TXT\n\n需要 Python 3.10+，无需 Word 或其他依赖。\n\n"
        "将 DOCX / 文件夹拖到 听悟DOCX转TXT.cmd，或执行：\n\n"
        "```bash\npython docx_to_txt.py \"导出文件夹\"\n```\n\n"
        "ZIP 请先解压。保留原 DOCX，默认跳过已有 TXT；--overwrite 允许覆盖。\n"
        "支持正文、原有说话人和时间戳、段落、超链接文字及表格；不读取页眉页脚与图片文字。\n"})
    packages.append(document)
    checksums = []
    for package in packages:
        checksum = hashlib.sha256(package.read_bytes()).hexdigest()
        checksums.append(f"{checksum}  {package.name}")
        print(f"{package.name}: {package.stat().st_size} bytes")
    (dist / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
