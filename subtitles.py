"""@brief BCC 字幕的规范化、段落合并和文件导出；不执行网络请求。"""
from dataclasses import dataclass
import html
import json
import math
import re


class SubtitleError(Exception):
    """供 HTTP 层展示的业务错误，避免泄露网络请求中的认证信息。"""

    def __init__(self, message, code="invalid_input", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def parse_bcc(document):
    """@return 按开始时间稳定排序的有效字幕；时间单位为毫秒。"""
    if not isinstance(document, dict) or not isinstance(document.get("body"), list):
        raise SubtitleError("文件不是 B 站 BCC JSON：缺少 body 数组。", "invalid_bcc")
    cues = []
    for row in document["body"]:
        if not isinstance(row, dict):
            continue
        # JSON 布尔值在 Python 中也属于数值，必须单独排除。
        values = [row.get("from"), row.get("to")]
        if any(isinstance(value, bool) or value is None for value in values):
            continue
        try:
            start, end = map(float, values)
        except (ValueError, TypeError, OverflowError):
            continue
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0
                or end < start or end > 9_000_000_000_000):
            continue
        text = row.get("content")
        if not isinstance(text, str):
            continue
        text = "\n".join(line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip())
        if text:
            # 对非负秒数使用 half-up，兼容浏览器 Math.round 的时间进位。
            cues.append(Cue(math.floor(start * 1000 + .5), math.floor(end * 1000 + .5), text))
    cues.sort(key=lambda cue: cue.start_ms)
    if not cues:
        raise SubtitleError("BCC 中没有有效字幕。", "empty_subtitle", 422)
    return cues


def timestamp(milliseconds, separator=","):
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def paragraphs(cues, language="zh-CN"):
    """按字数、句末和停顿合并文本；保留真实重复句，不对 BCC 强行去重。"""
    base_language = re.sub(r"^(?:ai-|a\.)", "", language.lower())
    joiner = "" if base_language.startswith(("zh", "ja", "ko")) else " "
    result, text, start_ms = [], "", 0
    for index, cue in enumerate(cues):
        if not text:
            start_ms = cue.start_ms
        text = text + joiner + cue.text if text else cue.text
        next_cue = cues[index + 1] if index + 1 < len(cues) else None
        gap = next_cue.start_ms - cue.end_ms if next_cue else math.inf
        if (len(text) >= 400 and re.search(r"[。！？.!?][\"'”’]?\Z", cue.text)) or len(text) >= 500 or gap > 5000:
            result.append({"start_ms": start_ms, "text": text})
            text = ""
    return result


def export_subtitle(document, format_name, language="zh-CN"):
    """@return (UTF-8 文件字节, MIME 类型)；JSON 保留原有字段。"""
    cues = parse_bcc(document)
    if format_name == "json":
        text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
        mime = "application/json"
    elif format_name in ("srt", "vtt"):
        separator = "," if format_name == "srt" else "."
        blocks = []
        for number, cue in enumerate(cues, 1):
            # 转义字幕标记，避免含尖括号的原文被播放器解释成标签。
            content = html.escape(cue.text, quote=False)
            prefix = f"{number}\n" if format_name == "srt" else ""
            blocks.append(f"{prefix}{timestamp(cue.start_ms, separator)} --> "
                          f"{timestamp(cue.end_ms, separator)}\n{content}\n")
        text = ("WEBVTT\n\n" if format_name == "vtt" else "") + "\n".join(blocks)
        mime = "application/x-subrip" if format_name == "srt" else "text/vtt"
    elif format_name in ("txt", "timestamped"):
        rows = paragraphs(cues, language)
        text = "\n\n".join((f"[{timestamp(row['start_ms'], '.')[:-4]}] "
                              if format_name == "timestamped" else "") + row["text"] for row in rows)
        mime = "text/plain"
    else:
        raise SubtitleError("不支持的导出格式。")
    return text.encode("utf-8"), mime + "; charset=utf-8"


def safe_filename(title, language, index, format_name):
    """过滤 Windows 文件名保留字符，并用轨道序号区分同语言字幕。"""
    title = re.sub(r'[\\/?:*<>|"%\x00-\x1f]', "", title).strip(" .")[:80] or "subtitle"
    language = re.sub(r"[^\w.-]", "", language)[:30] or "und"
    extension = "txt" if format_name == "timestamped" else format_name
    return f"{title}.{language}.{index + 1}.{extension}"
