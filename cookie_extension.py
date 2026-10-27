"""@brief Cookie 扩展的固定文件打包与本机安装路径说明。"""
import io
import json
from pathlib import Path
import zipfile

EXTENSION_ROOT = Path(__file__).resolve().parent / "browser-extension" / "bili-cookie-extension"
PACKAGE_FILES = ("manifest.json", "background.js", "content.js", "README.txt")


def extension_info():
    """只提供安装元信息，不接收或存储任何 Cookie。"""
    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text("utf-8"))
    return {"version": manifest["version"], "unpacked_path": str(EXTENSION_ROOT)}


def extension_package():
    """白名单打包，不把项目配置或下载内容带入扩展包。"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in PACKAGE_FILES:
            archive.writestr("bili-cookie-extension/" + filename,
                             (EXTENSION_ROOT / filename).read_bytes())
    output.seek(0)
    return output


if __name__ == "__main__":
    target = EXTENSION_ROOT.parent / "dist" / "b站cookie获取扩展.zip"
    target.parent.mkdir(exist_ok=True)
    with extension_package() as package:
        target.write_bytes(package.read())
    print(target)
