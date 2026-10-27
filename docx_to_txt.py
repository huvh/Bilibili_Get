"""@brief 批量提取听悟 DOCX 正文，导出 UTF-8 TXT；仅使用 Python 标准库。"""
import argparse
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile


DEFAULT_INPUT = Path(__file__).resolve().parent / "tingwu_exports"
MAX_XML_BYTES = 64 * 1024 * 1024
WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}


def _visible_text(element, namespace):
    """读取段落中的可见文字；保留超链接文本、制表符和换行，忽略修订删除内容。"""
    prefix = "{" + namespace + "}"
    if element.tag in {prefix + "del", prefix + "moveFrom", prefix + "pPr", prefix + "rPr"}:
        return ""
    if element.tag == prefix + "t":
        return element.text or ""
    if element.tag == prefix + "tab":
        return "\t"
    if element.tag in {prefix + "br", prefix + "cr"}:
        return "\n"
    if element.tag == prefix + "noBreakHyphen":
        return "\u2011"
    # Word 可能同时保存新版内容与兼容副本，只选一份，避免重复提取。
    if element.tag == "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent":
        selected = next(iter(element), None)
        return _visible_text(selected, namespace) if selected is not None else ""
    return "".join(_visible_text(child, namespace) for child in element)


def _body_lines(element, namespace):
    """按文档顺序读取段落和表格；表格单元格之间用 Tab 分隔。"""
    prefix = "{" + namespace + "}"
    for child in element:
        if child.tag in {prefix + "del", prefix + "moveFrom"}:
            continue
        if child.tag == prefix + "p":
            yield _visible_text(child, namespace)
        elif child.tag == prefix + "tbl":
            for row in child.findall(prefix + "tr"):
                cells = ["\n".join(_body_lines(cell, namespace))
                         for cell in row.findall(prefix + "tc")]
                yield "\t".join(cells)
        else:
            # 内容控件等包装节点不应遮住其内部的正文。
            yield from _body_lines(child, namespace)


def extract_docx_text(source):
    """@brief 返回 DOCX 正文文本；不读取页眉页脚，不修改来源文件。"""
    with ZipFile(source) as archive:
        try:
            info = archive.getinfo("word/document.xml")
        except KeyError as error:
            raise ValueError("缺少 word/document.xml，不是有效的 DOCX。") from error
        if info.file_size > MAX_XML_BYTES:
            raise ValueError("正文 XML 超过 64 MB，未转换。")
        root = ET.fromstring(archive.read(info))
    namespace = root.tag.partition("}")[0].lstrip("{")
    if namespace not in WORD_NAMESPACES or root.tag != "{" + namespace + "}document":
        raise ValueError("无法识别 DOCX 的 Word 正文格式。")
    body = root.find("{" + namespace + "}body")
    if body is None:
        raise ValueError("DOCX 缺少正文节点。")
    lines = list(_body_lines(body, namespace))
    if not any(line.strip() for line in lines):
        raise ValueError("DOCX 正文没有可提取的文字。")
    # 保留段内空格和段落；只去掉文档末尾空行，并统一补一个结尾换行。
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def convert_docx(source, target=None, overwrite=False):
    """@brief 将单个 DOCX 转成 TXT，默认拒绝覆盖；返回实际输出路径。"""
    source = Path(source)
    target = Path(target) if target is not None else source.with_suffix(".txt")
    if source.resolve() == target.resolve():
        raise ValueError("输出路径不能与原 DOCX 相同。")
    if target.exists() and not overwrite:
        raise FileExistsError(str(target))
    text = extract_docx_text(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return target


def main(argv=None):
    """解析命令行并逐个转换；单个文件失败不影响其余任务。"""
    parser = argparse.ArgumentParser(description="批量将听悟 DOCX 转为 UTF-8 TXT（无需 Word）。")
    parser.add_argument("inputs", nargs="*", type=Path, help="DOCX 文件或文件夹，可指定多个")
    parser.add_argument("-o", "--output", type=Path, help="输出文件夹；默认保存在 DOCX 旁边")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有 TXT")
    args = parser.parse_args(argv)
    inputs = args.inputs or [DEFAULT_INPUT]
    if not args.inputs:
        DEFAULT_INPUT.mkdir(parents=True, exist_ok=True)
    succeeded = skipped = failed = found = 0
    seen_sources, seen_targets = set(), set()
    for entry in inputs:
        if entry.is_dir():
            files = sorted((path for path in entry.rglob("*")
                            if path.is_file() and path.suffix.lower() == ".docx"),
                           key=lambda path: str(path).casefold())
        elif entry.is_file() and entry.suffix.lower() == ".docx":
            files = [entry]
        else:
            print(f"[失败] 不是 DOCX 文件或有效文件夹：{entry}", file=sys.stderr)
            failed += 1
            continue
        for source in files:
            if source.name.startswith("~$") or source.resolve() in seen_sources:
                continue
            seen_sources.add(source.resolve())
            found += 1
            relative = source.relative_to(entry) if entry.is_dir() else Path(source.name)
            target = (args.output / relative.with_suffix(".txt") if args.output
                      else source.with_suffix(".txt"))
            if target.resolve() in seen_targets:
                print(f"[失败] 多个来源对应同一输出，请分别指定输出文件夹：{target}", file=sys.stderr)
                failed += 1
                continue
            seen_targets.add(target.resolve())
            try:
                result = convert_docx(source, target, args.overwrite)
                print(f"[成功] {result}")
                succeeded += 1
            except FileExistsError:
                print(f"[跳过] TXT 已存在：{target}")
                skipped += 1
            except (OSError, ValueError, BadZipFile, ET.ParseError, RuntimeError,
                    NotImplementedError) as error:
                print(f"[失败] {source}：{error}", file=sys.stderr)
                failed += 1
    if not found:
        print("未找到 DOCX。请先将听悟导出的 ZIP 解压，再传入 DOCX 或所在文件夹。")
        if not args.inputs:
            print(f"双击默认读取：{DEFAULT_INPUT}")
    print(f"转换完成：成功 {succeeded}，跳过 {skipped}，失败 {failed}。")
    return 1 if failed or not found else 0


if __name__ == "__main__":
    raise SystemExit(main())
