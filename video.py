"""@brief 视频流解析、带 Referer 的分块下载、FFmpeg 合并及后台任务管理。"""
from concurrent.futures import ThreadPoolExecutor
import http.client
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from bilibili import BilibiliClient, USER_AGENT, sign_wbi
from subtitles import SubtitleError

ROOT = Path(__file__).parent
QUALITY_NAMES = {127: "8K", 126: "杜比视界", 125: "HDR", 120: "4K", 116: "1080P 60帧",
                 112: "1080P 高码率", 80: "1080P", 74: "720P 60帧", 64: "720P", 32: "480P", 16: "360P", 6: "240P"}


def media_url(value):
    """媒体只允许 B 站 CDN 的标准 HTTPS 端口，拒绝凭据、内网和任意代理目标。"""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower()
        allowed = any(host.endswith("." + domain) for domain in ("bilivideo.com", "bilivideo.cn", "hdslb.com"))
        if (parsed.scheme not in ("http", "https") or not allowed or parsed.username or parsed.password
                or parsed.port not in (None, 80, 443)):
            raise ValueError()
        return urllib.parse.urlunsplit(("https", host, parsed.path, parsed.query, ""))
    except (TypeError, ValueError):
        raise SubtitleError("视频地址不是支持的 B 站 CDN。", "invalid_media_host") from None


def stream_urls(stream):
    candidates = [stream.get("baseUrl") or stream.get("base_url") or stream.get("url")]
    candidates += stream.get("backupUrl") or stream.get("backup_url") or []
    urls = []
    for value in candidates:
        if not value:
            continue
        try:
            url = media_url(value)
        except SubtitleError:
            continue
        if url not in urls:
            urls.append(url)
    return urls


def normalize_formats(data, duration):
    """只列出接口真实返回的流，不能将 accept_quality 当成已授权清晰度。"""
    if data.get("is_preview") or data.get("is_drm"):
        raise SubtitleError("接口仅提供试看或受保护内容，无法下载完整视频。", "restricted_video", 403)
    dash = data.get("dash") or {}
    audio_rows = [row for row in dash.get("audio", []) if stream_urls(row)]
    aac_rows = [row for row in audio_rows if str(row.get("codecs", "mp4a")).startswith("mp4a")]
    if audio_rows and not aac_rows:
        raise SubtitleError("当前视频没有支持合并为 MP4 的 AAC 音轨。", "unsupported_audio", 422)
    audio = max(aac_rows, key=lambda row: row.get("bandwidth", 0), default=None)
    grouped = {}
    for row in dash.get("video", []):
        if not stream_urls(row):
            continue
        quality = row.get("id")
        if not isinstance(quality, int):
            continue
        # 同清晰度优先 AVC，避免把 AV1/HEVC 当成普遍可播放的 H.264。
        rank = (str(row.get("codecs", "")).startswith("avc1"), row.get("bandwidth", 0))
        if quality not in grouped or rank > grouped[quality][0]:
            grouped[quality] = (rank, row)
    formats = []
    for quality in sorted(grouped, reverse=True):
        row = grouped[quality][1]
        bandwidth = row.get("bandwidth", 0) + (audio.get("bandwidth", 0) if audio else 0)
        formats.append({"id": f"dash-{quality}", "quality": quality,
                        "label": QUALITY_NAMES.get(quality, f"清晰度 {quality}"), "mode": "dash",
                        "width": row.get("width", 0), "height": row.get("height", 0),
                        "codec": row.get("codecs", ""), "has_audio": audio is not None,
                        "estimated_bytes": int(bandwidth * duration / 8),
                        "video_urls": stream_urls(row), "audio_urls": stream_urls(audio) if audio else []})
    segments = [row for row in data.get("durl", []) if stream_urls(row)]
    if segments:
        quality = data.get("quality", 0)
        formats.append({"id": f"progressive-{quality}", "quality": quality,
                        "label": QUALITY_NAMES.get(quality, "原始文件"), "mode": "progressive",
                        "codec": "原始编码", "has_audio": True,
                        "estimated_bytes": sum(row.get("size", 0) for row in segments),
                        "segments": [stream_urls(row) for row in segments]})
    if not formats:
        raise SubtitleError("当前视频没有可下载的媒体流，请检查登录权限或稍后重试。", "no_video", 404)
    return formats


def find_ffmpeg():
    configured = os.environ.get("FFMPEG_PATH")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    system = shutil.which("ffmpeg")
    if system:
        return system
    for path in (ROOT / ".deps" / "imageio_ffmpeg" / "binaries").glob("ffmpeg*.exe"):
        return str(path.resolve())
    return None


class MediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return super().redirect_request(request, response, code, message, headers, media_url(new_url))


class VideoClient:
    def __init__(self, client=None):
        self.bilibili = client or BilibiliClient()
        proxy = self.bilibili.proxy
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy} if proxy else {}), MediaRedirect())

    def inspect(self, url, cookie=""):
        source, view, page = self.bilibili.metadata(url, cookie)
        params = {"avid": view["aid"], "cid": page["cid"], "qn": 127, "fnval": 4048, "fourk": 1}
        if source["id"].startswith("ep"):
            params["ep_id"] = source["id"][2:]
            response = self.bilibili.api("/pgc/player/web/playurl", params, cookie)
        else:
            response = self.bilibili.api("/x/player/playurl", params, cookie)
            if response.get("code") in (-400, -403):
                nav = self.bilibili.api("/x/web-interface/nav", {}, cookie).get("data", {})
                wbi = nav.get("wbi_img", {})
                if wbi.get("img_url") and wbi.get("sub_url"):
                    response = self.bilibili.api("/x/player/wbi/playurl", sign_wbi(
                        params, wbi["img_url"], wbi["sub_url"]), cookie)
        data = self.bilibili.require_data(response)
        duration = page.get("duration", 0)
        formats = normalize_formats(data, duration)
        return {"title": view["title"], "part": page.get("part", ""), "id": view.get("bvid") or source["id"],
                "page": page["page"], "pages": [{"page": row["page"], "part": row["part"]} for row in view["pages"]],
                "duration": duration, "source_url": source["url"], "formats": formats}

    def transcription_source(self, url, cookie=""):
        """听悟优先使用带声音的完整 MP4，避免其链接解析器拒绝 DASH 的 m4s 音轨。"""
        source, view, page = self.bilibili.metadata(url, cookie)
        params = {"avid": view["aid"], "cid": page["cid"], "qn": 16, "fnval": 0,
                  "fnver": 0, "fourk": 0, "platform": "html5"}
        endpoint = "/x/player/playurl"
        if source["id"].startswith("ep"):
            endpoint = "/pgc/player/web/playurl"
            params["ep_id"] = source["id"][2:]
        data = self.bilibili.require_data(self.bilibili.api(endpoint, params, cookie))
        formats = normalize_formats(data, page.get("duration", 0))
        for fmt in formats:
            if fmt["mode"] == "progressive" and len(fmt["segments"]) == 1:
                title = view["title"] + (f" P{page['page']} {page.get('part', '')}" if len(view["pages"]) > 1 else "")
                return {"title": title, "cdn_url": fmt["segments"][0][0], "cdn_urls": fmt["segments"][0]}
        raise SubtitleError("当前视频没有听悟可直接读取的完整 MP4 CDN；请提供可导入听悟的音视频直链。",
                            "no_tingwu_media", 422)

    def download(self, urls, destination, cancel, progress, limit):
        """分块写磁盘；备用 CDN 失败后从头重试，完整性不满足时拒绝成功。"""
        for url in urls:
            received = 0
            try:
                if cancel.is_set():
                    raise SubtitleError("下载已取消。", "canceled", 409)
                request = urllib.request.Request(media_url(url), headers={"User-Agent": USER_AGENT,
                    "Referer": "https://www.bilibili.com/", "Accept-Encoding": "identity"})
                with self.opener.open(request, timeout=30) as response:
                    size = int(response.headers.get("Content-Length", "0"))
                    if size > limit:
                        raise SubtitleError("视频超过本机设置的单任务大小限制。", "too_large", 413)
                    if response.status != 200:
                        raise OSError("unexpected partial response")
                    with destination.open("wb") as output:
                        while True:
                            if cancel.is_set():
                                raise SubtitleError("下载已取消。", "canceled", 409)
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > limit:
                                raise SubtitleError("视频超过本机设置的单任务大小限制。", "too_large", 413)
                            output.write(chunk)
                            progress(received, size)
                    if not received or (size and received != size):
                        raise OSError("incomplete media")
                    return received
            except SubtitleError:
                raise
            except (OSError, ValueError, urllib.error.URLError, http.client.IncompleteRead):
                progress(0, 0)
                continue
        raise SubtitleError("视频 CDN 下载失败；直链可能已过期，请重新解析后重试。", "media_download_failed", 502)


def mux_media(ffmpeg, inputs, target, cancel, mode="dash"):
    """本地流复制封装，不重编码；FFmpeg 仅接收已下载的本地文件。"""
    args = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    concat_list = None
    if mode == "progressive" and len(inputs) > 1:
        concat_list = target.parent / "segments.txt"
        concat_list.write_text("\n".join(f"file '{path.name}'" for path in inputs), encoding="utf-8")
        args += ["-f", "concat", "-safe", "1", "-i", str(concat_list)]
    else:
        for path in inputs:
            args += ["-i", str(path)]
    if mode == "dash":
        args += ["-map", "0:v:0"]
        if len(inputs) > 1:
            args += ["-map", "1:a:0"]
    args += ["-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(target)]
    log = target.parent / "merge-error.txt"
    try:
        with log.open("wb") as stderr:
            process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = time.monotonic()
            while process.poll() is None:
                if cancel.wait(.15) or time.monotonic() - started > 900:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise SubtitleError("下载已取消。" if cancel.is_set() else "音视频合并超时。",
                                        "canceled" if cancel.is_set() else "mux_timeout", 409)
            if process.returncode:
                raise SubtitleError("音视频合并失败，请重新解析并下载。", "mux_failed", 502)
        if not target.exists() or target.stat().st_size == 0:
            raise SubtitleError("合并结果为空。", "mux_failed", 502)
    finally:
        if concat_list:
            concat_list.unlink(missing_ok=True)
        log.unlink(missing_ok=True)


class VideoService:
    def __init__(self, client=None, root=None, executor=None):
        self.client = client or VideoClient()
        self.root = Path(root or ROOT / "downloads").resolve()
        self.executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="bili-video")
        self.lock = threading.RLock()
        self.inspections = {}
        self.jobs = {}

    def inspect(self, payload):
        url, cookie = payload.get("url", ""), payload.get("cookie", "")
        if not isinstance(url, str) or len(url) > 8000 or not isinstance(cookie, str):
            raise SubtitleError("链接或 Cookie 格式无效。")
        metadata = self.client.inspect(url, cookie)
        key = uuid.uuid4().hex
        with self.lock:
            now = time.monotonic()
            self.inspections = {key: value for key, value in self.inspections.items() if now < value["expires"]}
            if len(self.inspections) >= 32:
                del self.inspections[next(iter(self.inspections))]
            self.inspections[key] = {"metadata": metadata, "expires": now + 1200}
        public_formats = [{key: value for key, value in item.items()
                           if key not in ("video_urls", "audio_urls", "segments")} for item in metadata["formats"]]
        return {**metadata, "formats": public_formats, "inspection_id": key, "ffmpeg_available": bool(find_ffmpeg())}

    def start(self, payload):
        if not isinstance(payload.get("inspection_id"), str) or not isinstance(payload.get("format_id"), str):
            raise SubtitleError("解析标识或清晰度格式无效。")
        with self.lock:
            inspection = self.inspections.get(payload.get("inspection_id"))
            if not inspection or time.monotonic() >= inspection["expires"]:
                raise SubtitleError("视频解析结果已过期，请重新解析。", "expired", 410)
            fmt = next((item for item in inspection["metadata"]["formats"] if item["id"] == payload.get("format_id")), None)
            if not fmt:
                raise SubtitleError("清晰度不存在。", "invalid_format")
            if not find_ffmpeg():
                raise SubtitleError("缺少 FFmpeg，请按 README 安装视频合并依赖。", "missing_ffmpeg", 503)
            active = [job for job in self.jobs.values() if job["status"] in ("queued", "downloading", "merging", "canceling")]
            if len(active) >= 4:
                raise SubtitleError("同时最多排队 4 个任务，请等待已有下载完成。", "busy", 429)
            if len(self.jobs) >= 64:
                oldest = next((key for key, job in self.jobs.items() if job["status"] not in
                               ("queued", "downloading", "merging", "canceling")), None)
                if oldest:
                    del self.jobs[oldest]
            key = uuid.uuid4().hex
            metadata = inspection["metadata"]
            title = metadata["title"] + (f" P{metadata['page']} {metadata['part']}" if len(metadata["pages"]) > 1 else "")
            title = re.sub(r'[\\/?:*<>|"\x00-\x1f]', "", title).strip(" .")[:100] or "bilibili"
            filename = f"{title}.{fmt['label']}.mp4"
            job = {"id": key, "title": title, "filename": filename, "status": "queued", "bytes": 0,
                   "total_bytes": fmt["estimated_bytes"], "progress": 0, "error": "", "cancel": threading.Event(),
                   "path": self.root / key / filename, "format": fmt}
            self.jobs[key] = job
            self.executor.submit(self._run, key)
            return self.snapshot(key)

    def snapshot(self, key):
        if not isinstance(key, str):
            raise SubtitleError("任务标识格式无效。")
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise SubtitleError("下载任务不存在。", "not_found", 404)
            result = {field: job[field] for field in ("id", "title", "filename", "status", "bytes", "total_bytes", "progress", "error")}
            if job["status"] == "done":
                result["download_url"] = "/api/video/file?job=" + key
                result["file_bytes"] = job["path"].stat().st_size
            return result

    def cancel(self, key):
        if not isinstance(key, str):
            raise SubtitleError("任务标识格式无效。")
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise SubtitleError("任务不存在。", "not_found", 404)
            if job["status"] in ("queued", "downloading", "merging"):
                job["cancel"].set()
                job["status"] = "canceling"
            return self.snapshot(key)

    def file(self, key):
        with self.lock:
            job = self.jobs.get(key)
            if not job or job["status"] != "done" or not job["path"].is_file():
                raise SubtitleError("文件尚未准备完成。", "not_ready", 409)
            return job["path"], job["filename"]

    def _run(self, key):
        job = self.jobs[key]
        folder = job["path"].parent
        inputs = []
        partial = folder / "merged.part"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            fmt = job["format"]
            sources = [fmt["video_urls"]] + ([fmt["audio_urls"]] if fmt["audio_urls"] else []) if fmt["mode"] == "dash" else fmt["segments"]
            limit = int(os.environ.get("BILI_MAX_DOWNLOAD_MB", "4096")) * 1024 * 1024
            completed = 0
            for index, urls in enumerate(sources):
                if job["cancel"].is_set():
                    raise SubtitleError("下载已取消。", "canceled", 409)
                destination = folder / f"stream-{index}.part"
                inputs.append(destination)
                def progress(received, total):
                    with self.lock:
                        job["bytes"] = completed + received
                        estimate = max(job["total_bytes"], completed + total)
                        job["total_bytes"] = estimate
                        job["progress"] = min(.94, job["bytes"] / estimate * .94) if estimate else 0
                with self.lock:
                    job["status"] = "downloading"
                completed += self.client.download(urls, destination, job["cancel"], progress, limit - completed)
            if job["cancel"].is_set():
                raise SubtitleError("下载已取消。", "canceled", 409)
            with self.lock:
                job["status"] = "merging"
                job["progress"] = .96
            mux_media(find_ffmpeg(), inputs, partial, job["cancel"], fmt["mode"])
            with self.lock:
                if job["cancel"].is_set():
                    raise SubtitleError("下载已取消。", "canceled", 409)
                partial.replace(job["path"])
                job.update(status="done", progress=1, total_bytes=completed, bytes=completed)
        except Exception as error:
            with self.lock:
                canceled = job["cancel"].is_set() or (isinstance(error, SubtitleError) and error.code == "canceled")
                job["status"] = "canceled" if canceled else "error"
                job["error"] = "下载已取消。" if canceled else str(error) if isinstance(error, SubtitleError) else "下载处理失败，请重新解析后重试。"
        finally:
            # 仅清理本任务已知临时文件，不递归操作用户文件夹或完成文件。
            for path in inputs + [partial]:
                path.unlink(missing_ok=True)
