"""@brief 仅监听回环地址的字幕工具；网络适配、转换和页面互相隔离。"""
import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.parse
import uuid
import webbrowser
import re

from bilibili import BilibiliClient, is_subtitle_url
from subtitles import SubtitleError, export_subtitle, paragraphs, parse_bcc, safe_filename
from video import VideoService
from navigation import NAV_STYLE, render_navigation
from tingwu import TingwuService
from cookie_extension import extension_info, extension_package

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
TTL_SECONDS = 25 * 60


def render_workbench(video_mode=False):
    """首屏按链接直接呈现目标功能；即使切换脚本未加载也能进入视频页。"""
    page = (STATIC / "index.html").read_text("utf-8")
    page = page.replace("__APP_NAV__", render_navigation("video" if video_mode else "subtitle"))
    if video_mode:
        page = page.replace('<div id="subtitle-workspace">', '<div id="subtitle-workspace" hidden>')
        page = page.replace('<section id="video-workspace" aria-label="视频下载工作台" hidden>',
                            '<section id="video-workspace" aria-label="视频下载工作台">')
    return page.encode("utf-8")


class SubtitleService:
    """只缓存字幕和元信息；Cookie 不进入缓存或日志。"""

    def __init__(self, client=None, clock=time.monotonic):
        self.client = client or BilibiliClient()
        self.clock = clock
        self.jobs = {}
        self.lock = threading.RLock()

    def create(self, metadata, documents=None):
        now = self.clock()
        with self.lock:
            self.jobs = {key: job for key, job in self.jobs.items() if now < job["expires"]}
            if len(self.jobs) >= 32:
                del self.jobs[next(iter(self.jobs))]
            key = uuid.uuid4().hex
            job = {"metadata": metadata, "expires": now + TTL_SECONDS,
                   "documents": documents or {}, "lock": threading.RLock()}
            self.jobs[key] = job
        return {**self.public_metadata(metadata), "job_id": key, "expires_in": TTL_SECONDS}

    @staticmethod
    def public_metadata(metadata):
        return {**metadata, "tracks": [{key: value for key, value in track.items() if key != "url"}
                                       for track in metadata["tracks"]]}

    def extract(self, payload):
        url = payload.get("url", "")
        if not isinstance(url, str) or len(url) > 8000:
            raise SubtitleError("链接格式无效。")
        if is_subtitle_url(url.strip()):
            document = self.client.subtitle(url.strip())
            return self.import_document({"document": document, "title": "B 站字幕直链", "source_url": url})
        cookie = payload.get("cookie", "")
        if not isinstance(cookie, str):
            raise SubtitleError("Cookie 格式无效。")
        return self.create(self.client.inspect(url, cookie))

    def import_document(self, payload):
        document = payload.get("document")
        title = str(payload.get("title", "导入的字幕"))[:200]
        language = str(payload.get("language_tag", "zh-CN"))[:40]
        # 登录助手的包装文件保留原始 BCC，普通 BCC 文件也直接支持。
        if isinstance(document, dict) and isinstance(document.get("document"), dict):
            title = str(document.get("title", title))[:200]
            language = str(document.get("language_tag", language))[:40]
            document = document["document"]
        cues = parse_bcc(document)
        metadata = {"title": title, "part": "", "id": "", "page": 1, "pages": [],
                    "duration": cues[-1].end_ms / 1000, "source_url": "",
                    "tracks": [{"language_tag": language, "language_name": language,
                                "automatic": False, "source_index": 0}]}
        return self.create(metadata, {0: document})

    def load(self, job_id, track_index):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or self.clock() >= job["expires"]:
                self.jobs.pop(job_id, None)
                raise SubtitleError("字幕会话已过期，请重新提取或导入。", "expired", 410)
        tracks = job["metadata"]["tracks"]
        if not 0 <= track_index < len(tracks):
            raise SubtitleError("字幕轨道不存在。", "invalid_track", 404)
        with job["lock"]:
            if track_index not in job["documents"]:
                document = self.client.subtitle(tracks[track_index]["url"])
                parse_bcc(document)
                job["documents"][track_index] = document
        return job, job["documents"][track_index], tracks[track_index]


SERVICE = SubtitleService()
VIDEOS = VideoService()
TINGWU = TingwuService()


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalSubtitle/1.0"

    def log_message(self, *args):
        # 请求参数可能包含有时效的字幕签名，默认不输出访问日志。
        pass

    def send_bytes(self, body, mime, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False, allow_nan=False).encode(),
                        "application/json; charset=utf-8", status)

    def send_document(self, source, mime, filename):
        """分块发送 DOCX、TXT 和 ZIP，避免大文件整体载入内存。"""
        source.seek(0, 2)
        length = source.tell()
        source.seek(0)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", "attachment; filename=document" + Path(filename).suffix
                         + "; filename*=UTF-8''" + urllib.parse.quote(filename))
        self.end_headers()
        while True:
            chunk = source.read(256 * 1024)
            if not chunk:
                break
            self.wfile.write(chunk)

    def send_video_file(self, path, filename, inline=False):
        """分块发送已完成的 MP4，并支持播放器及断点下载的单区间 Range。"""
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                self.send_bytes(b"", "video/mp4", 416, {"Content-Range": f"bytes */{size}"})
                return
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            else:
                start = max(0, size - int(right))
            if start >= size or end < start:
                self.send_bytes(b"", "video/mp4", 416, {"Content-Range": f"bytes */{size}"})
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        disposition = "inline" if inline else "attachment"
        self.send_header("Content-Disposition", disposition + "; filename=bilibili.mp4; filename*=UTF-8''" + urllib.parse.quote(filename))
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def local_request(self):
        """Host 与 Origin 双检查，阻止其他网页跨站调用本机 API。"""
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            raise SubtitleError("请通过 127.0.0.1 地址访问本地工具。", "invalid_host", 403)
        # 允许用户从听悟书签打开工作台文档；后续 API 仍只接受同源请求。
        if (self.command == "GET" and urllib.parse.urlsplit(self.path).path in {"/", "/docx", "/helper", "/transcribe"}
                and self.headers.get("Sec-Fetch-Mode") == "navigate"
                and self.headers.get("Sec-Fetch-Dest") == "document"):
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{expected}":
            raise SubtitleError("不允许跨站调用本地工具。", "invalid_origin", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise SubtitleError("不允许跨站调用本地工具。", "invalid_origin", 403)

    def handle_error(self, error):
        if isinstance(error, SubtitleError):
            self.send_json({"error": str(error), "code": error.code}, error.status)
        else:
            # 不回显异常中的 URL、响应和 Cookie。
            self.send_json({"error": "处理失败，请检查输入格式后重试。", "code": "internal_error"}, 500)

    def do_GET(self):
        try:
            self.local_request()
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/api/health":
                return self.send_json({"ok": True, "service": "local-bilibili-subtitle", "tingwu": True,
                                       "tingwu_recovery": 3})
            if parsed.path == "/api/cookie-extension/info":
                return self.send_json(extension_info())
            if parsed.path == "/api/cookie-extension/download":
                with extension_package() as source:
                    return self.send_document(source, "application/zip", "b站cookie获取扩展.zip")
            if parsed.path == "/api/tingwu/setup":
                return self.send_json({"key": TINGWU.bridge.key})
            if parsed.path == "/api/tingwu/status":
                return self.send_json(TINGWU.snapshot(query.get("job", [None])[0]))
            if parsed.path == "/api/tingwu/file":
                path = TINGWU.file(query.get("path", [""])[0])
                mime = "text/plain; charset=utf-8" if path.suffix == ".txt" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                with path.open("rb") as source:
                    return self.send_document(source, mime, path.name)
            if parsed.path == "/api/tingwu/bundle":
                kind = query.get("kind", ["txt"])[0]
                with TINGWU.bundle(query.get("job", [""])[0], kind) as source:
                    return self.send_document(source, "application/zip", "tingwu-" + kind + ".zip")
            if parsed.path == "/navigation.css":
                return self.send_bytes(NAV_STYLE.encode("utf-8"), "text/css; charset=utf-8")
            if parsed.path == "/api/video/status":
                return self.send_json(VIDEOS.snapshot(query.get("job", [""])[0]))
            if parsed.path == "/api/video/file":
                path, filename = VIDEOS.file(query.get("job", [""])[0])
                return self.send_video_file(path, filename, query.get("preview") == ["1"])
            if parsed.path in ("/api/load", "/api/download"):
                try:
                    index = int(query.get("track", ["0"])[0])
                except ValueError:
                    raise SubtitleError("轨道序号必须是整数。") from None
                job, document, track = SERVICE.load(query.get("job", [""])[0], index)
                if parsed.path == "/api/load":
                    cues = parse_bcc(document)
                    return self.send_json({"cues": [asdict(cue) for cue in cues], "document": document,
                                           "paragraphs": paragraphs(cues, track["language_tag"])})
                format_name = query.get("format", ["txt"])[0]
                body, mime = export_subtitle(document, format_name, track["language_tag"])
                filename = safe_filename(job["metadata"]["title"], track["language_tag"], index, format_name)
                disposition = "attachment; filename=subtitle." + ("txt" if format_name == "timestamped" else format_name)
                disposition += "; filename*=UTF-8''" + urllib.parse.quote(filename)
                return self.send_bytes(body, mime, headers={"Content-Disposition": disposition})
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/docx": ("docx.html", "text/html; charset=utf-8"),
                     "/transcribe": ("transcribe.html", "text/html; charset=utf-8"),
                     "/tingwu-pages.js": ("tingwu-pages.js", "text/javascript; charset=utf-8"),
                     "/docx.js": ("docx.js", "application/javascript; charset=utf-8"),
                     "/helper": ("helper.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                     "/cookie-extension.js": ("cookie-extension.js", "application/javascript; charset=utf-8"),
                     "/video.js": ("video.js", "application/javascript; charset=utf-8"),
                     "/tingwu.js": ("tingwu.js", "application/javascript; charset=utf-8"),
                     "/tingwu-bridge.js": ("tingwu-bridge.js", "application/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8"),
                     "/login-helper.js": ("login-helper.js", "application/javascript; charset=utf-8")}
            if parsed.path not in files:
                raise SubtitleError("页面不存在。", "not_found", 404)
            filename, mime = files[parsed.path]
            if filename == "index.html":
                body = render_workbench(query.get("tool") == ["video"])
            elif filename in {"docx.html", "transcribe.html"}:
                body = (STATIC / filename).read_text("utf-8").replace(
                    "__APP_NAV__", render_navigation("docx" if filename == "docx.html" else "transcribe")).encode("utf-8")
            else:
                body = (STATIC / filename).read_bytes()
            self.send_bytes(body, mime)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.handle_error(error)

    def do_POST(self):
        try:
            self.local_request()
            path = urllib.parse.urlsplit(self.path).path
            if self.headers.get_content_type() != "application/json":
                raise SubtitleError("请求必须使用 JSON。", "invalid_content_type", 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise SubtitleError("无效的请求长度。") from None
            limit = 90 * 1024 * 1024 if path == "/api/tingwu/import" else 8 * 1024 * 1024
            if size <= 0 or size > limit:
                raise SubtitleError("请求体为空或超过文件大小限制。", "too_large", 413)
            try:
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                raise SubtitleError("无效的 JSON 请求。") from None
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/extract":
                result = SERVICE.extract(payload)
            elif path == "/api/import":
                result = SERVICE.import_document(payload)
            elif path == "/api/video/inspect":
                result = VIDEOS.inspect(payload)
            elif path == "/api/video/start":
                result = VIDEOS.start(payload)
            elif path == "/api/video/cancel":
                result = VIDEOS.cancel(payload.get("job_id", ""))
            elif path == "/api/tingwu/inspect":
                result = TINGWU.inspect_source(payload)
            elif path == "/api/tingwu/start":
                result = TINGWU.create(payload)
            elif path == "/api/tingwu/control":
                result = TINGWU.control(payload)
            elif path == "/api/tingwu/import":
                result = TINGWU.import_document(payload)
            elif path == "/api/tingwu/bridge/poll":
                result = TINGWU.bridge.poll(payload.get("key"), payload.get("ready") is True,
                                            payload.get("wait") is True, payload.get("version", 0))
            elif path == "/api/tingwu/bridge/result":
                result = TINGWU.bridge.result(payload)
            else:
                raise SubtitleError("接口不存在。", "not_found", 404)
            self.send_json(result)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.handle_error(error)


def main():
    parser = argparse.ArgumentParser(description="B 站字幕本地提取与格式转换")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后在默认浏览器打开页面")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Local: {url}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
