"""@brief 听悟网页会话桥接、批量转写调度及 DOCX/TXT 文件归档。"""
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
import copy
import hmac
import io
import json
from pathlib import Path
import re
import secrets
import threading
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from docx_to_txt import extract_docx_text
from subtitles import SubtitleError
from video import VideoClient, media_url

ROOT = Path(__file__).resolve().parent
MAX_FILE = 64 * 1024 * 1024
TERMINAL = {"done", "error"}


def filename(value):
    """生成本地文件名，不接受远程路径。"""
    return re.sub(r'[\\/?:*<>|"\x00-\x1f]', "_", str(value)).strip(" .")[:100] or "听悟文字稿"


class BridgeUnavailable(Exception):
    pass


class RemotePending(Exception):
    """远端尚未生成可关联的任务，释放工作线程后定时查询。"""


class TaskConflict(Exception):
    """关联结果存在歧义，必须保留现场供人工核对。"""


class BrowserBridge:
    """浏览器只转发白名单操作，登录 Cookie 始终留在听悟网页中。"""

    def __init__(self, key_file=None):
        self.key = secrets.token_hex(24)
        if key_file is not None:
            key_file = Path(key_file)
            saved = key_file.read_text("ascii").strip() if key_file.exists() else ""
            if re.fullmatch(r"[a-f0-9]{48}", saved):
                self.key = saved
            else:
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_text(self.key, "ascii")
        self.pending = {}
        self.lock = threading.RLock()
        self.last_seen = 0
        self.version = 0
        self.wake = threading.Event()

    @property
    def online(self):
        return time.monotonic() - self.last_seen < 45 and self.version >= 2

    @property
    def needs_update(self):
        return time.monotonic() - self.last_seen < 45 and self.version < 2

    def authorize(self, key):
        if not isinstance(key, str) or not hmac.compare_digest(key, self.key):
            raise SubtitleError("听悟连接已失效，请重新安装当前页面的连接书签。", "bridge_key", 403)

    def poll(self, key, ready=False, wait=False, version=2):
        self.authorize(key)
        if ready:
            self.last_seen = time.monotonic()
            self.version = version if isinstance(version, int) else 0
        deadline = time.monotonic() + (10 if wait else 0)
        while True:
            with self.lock:
                now = time.monotonic()
                requests = []
                for item in self.pending.values():
                    if not item["event"].is_set() and now - item["sent"] > 10:
                        item["sent"] = now
                        requests.append({k: item[k] for k in ("id", "method", "params")})
                if requests or now >= deadline:
                    if ready:
                        self.last_seen = now
                    return {"requests": requests}
                self.wake.clear()
            self.wake.wait(max(0, deadline - time.monotonic()))

    def result(self, payload):
        self.authorize(payload.get("key"))
        with self.lock:
            item = self.pending.get(payload.get("id"))
            if item and not item["event"].is_set():
                item["result"] = payload.get("result")
                item["error"] = str(payload.get("error") or "")[:500]
                item["event"].set()
        return {"ok": True}

    def call(self, method, params=None):
        if not self.online:
            raise BridgeUnavailable("听悟连接已断开。请在听悟网页点击连接书签，保持两个页面打开。")
        key = secrets.token_hex(16)
        item = {"id": key, "method": method, "params": params or {},
                "event": threading.Event(), "sent": 0, "result": None, "error": ""}
        with self.lock:
            self.pending[key] = item
            self.wake.set()
        try:
            if not item["event"].wait(85):
                raise BridgeUnavailable("听悟网页响应超时。恢复连接后点击继续，已提交任务不会重复提交。")
            if item["error"]:
                raise SubtitleError(item["error"], "tingwu_request", 502)
            result = item["result"]
            if not isinstance(result, dict):
                raise SubtitleError("听悟返回格式已变化。", "tingwu_schema", 502)
            if str(result.get("code", "0")) not in {"0", "200"} or result.get("success") is False:
                code = str(result.get("code", "unknown"))
                if code in {"CMN.NotLogin", "SHA.NotLogin", "TRS.NeedLogin"}:
                    raise BridgeUnavailable("听悟登录已失效，请重新登录听悟后连接。")
                raise SubtitleError("听悟请求失败：" + code + " " + str(result.get("message", ""))[:200],
                                    "tingwu_request", 502)
            return result
        finally:
            with self.lock:
                self.pending.pop(key, None)


def export_url(url):
    """导出下载仅接受听悟及阿里云文件域名，避免成为任意地址代理。"""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443) or not (host == "tingwu.aliyun.com"
            or host.endswith(".aliyuncs.com") or host.endswith(".alicdn.com"))):
        raise SubtitleError("听悟返回了不支持的导出下载域名。", "invalid_export_url")
    return url


class ExportRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return super().redirect_request(request, response, code, message, headers, export_url(new_url))


def download_export(url):
    """下载签名导出链接；不向文件 CDN 发送任何用户 Cookie。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({
        "http": "http://127.0.0.1:7892", "https": "http://127.0.0.1:7892"}), ExportRedirect())
    request = urllib.request.Request(export_url(url), headers={"Referer": "https://tingwu.aliyun.com/"})
    with opener.open(request, timeout=60) as response:
        content = response.read(MAX_FILE + 1)
    if len(content) > MAX_FILE:
        raise SubtitleError("导出文件超过 64 MB。", "too_large", 413)
    return content


class TingwuService:
    """本地任务持久化；远端提交前记录 fileId，恢复时只查询，避免重复转写。"""

    def __init__(self, root=None, bridge=None, video=None, downloader=None, interval=12,
                 export_gap=5, retry_delay=5, submit_gap=2):
        self.root = Path(root or ROOT / "downloads" / "tingwu")
        self.bridge = bridge or BrowserBridge(self.root / ".bridge-key")
        self.video = video or VideoClient()
        self.downloader = downloader or download_export
        self.interval = interval
        self.export_gap, self.retry_delay = export_gap, retry_delay
        self.export_lock = threading.Lock()
        self.last_export = 0
        self.submit_lock = threading.Lock()
        self.last_submit, self.submit_gap = 0, submit_gap
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tingwu")
        self.jobs, self.running, self.stops = {}, set(), {}
        state = self.root / "state.json"
        if state.exists():
            try:
                self.jobs = json.loads(state.read_text("utf-8"))
                for job in self.jobs.values():
                    if job["status"] in {"running", "waiting_connection", "pausing"}:
                        job["status"] = "paused"
                    for row in job["rows"]:
                        if row["status"] not in TERMINAL:
                            row.update(status="paused", detail="服务已重启，连接听悟后可继续。")
            except (OSError, ValueError, KeyError):
                self.jobs = {}

    def _save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.root / "state.tmp"
        temp.write_text(json.dumps(self.jobs, ensure_ascii=False), "utf-8")
        temp.replace(self.root / "state.json")

    def snapshot(self, key=None):
        with self.lock:
            if key:
                if key not in self.jobs:
                    raise SubtitleError("批量任务不存在。", "not_found", 404)
                return copy.deepcopy(self.jobs[key])
            return {"connected": self.bridge.online, "bridge_update": getattr(self.bridge, "needs_update", False),
                    "jobs": [copy.deepcopy(job)
                    for job in reversed(list(self.jobs.values()))]}

    def _update(self, job, row=None, **fields):
        with self.lock:
            (row if row is not None else job).update(fields)
            self._save()

    def inspect_source(self, payload):
        """只读取分 P 元数据，不解析媒体、不建立任务，也不保存 Cookie。"""
        url, cookie = payload.get("url", ""), payload.get("cookie", "")
        if (not isinstance(url, str) or not 0 < len(url.strip()) <= 16000
                or not isinstance(cookie, str)):
            raise SubtitleError("链接或 Cookie 格式无效。")
        url = url.strip()
        if self._is_cdn(url) or re.fullmatch(
                r"https://tingwu\.aliyun\.com/doc/transcripts/([A-Za-z0-9_-]{8,64})/?", url):
            return {"source_url": url, "pages": []}
        source, view, selected = self.video.bilibili.metadata(url, cookie)
        identifier = view.get("bvid") or source["id"]
        base = (f"https://www.bilibili.com/video/{identifier}"
                if not source["id"].startswith("ep") else source["url"].split("?")[0])
        return {"id": identifier, "title": view["title"], "page": selected["page"],
                "source_url": base, "pages": [
                    {"page": page["page"], "part": page.get("part", ""),
                     "duration": page.get("duration", 0), "url": f"{base}?p={page['page']}"}
                    for page in view["pages"]]}

    def create(self, payload):
        sources = payload.get("urls", [])
        cookie = payload.get("cookie", "")
        if (not isinstance(sources, list) or not sources or len(sources) > 200
                or not all(isinstance(url, str) and 0 < len(url) <= 16000 for url in sources)
                or not isinstance(cookie, str)):
            raise SubtitleError("请输入 1～200 条 B 站链接、BV 号或 CDN 直链。")
        if not self.bridge.online:
            raise SubtitleError("请先连接听悟网页。", "bridge_offline", 409)
        key = secrets.token_hex(16)
        rows = [{"id": secrets.token_hex(8), "source": source.strip(), "title": source.strip(),
                 "status": "queued", "detail": "等待解析", "files": []}
                for source in dict.fromkeys(sources) if source.strip()]
        if not rows:
            raise SubtitleError("请输入至少一条非空链接。")
        job = {"id": key, "created": time.time(), "status": "running", "rows": rows,
               "all_pages": payload.get("all_pages") is True}
        with self.lock:
            if len(self.jobs) >= 100:
                raise SubtitleError("历史批次已达 100 个，请先备份任务文件。", "history_full", 409)
            self.jobs[key] = job
            self._save()
        self._launch(job, cookie)
        return self.snapshot(key)

    def _launch(self, job, cookie=""):
        with self.lock:
            if job["id"] in self.running:
                raise SubtitleError("批次正在运行，请等待当前操作结束。", "busy", 409)
            self.running.add(job["id"])
            self.stops[job["id"]] = threading.Event()
            job.update(status="running", error=None)
            for row in job["rows"]:
                if row["status"] != "done":
                    row.update(status="queued", next_retry_at=0, detail="等待恢复原任务" if row.get("submit_sent") else "等待解析")
            self._save()
        threading.Thread(target=self._run, args=(job, cookie), daemon=True).start()

    def control(self, payload):
        key = payload.get("job_id")
        with self.lock:
            if key not in self.jobs:
                raise SubtitleError("批次不存在。", "not_found", 404)
            job = self.jobs[key]
            if payload.get("action") == "pause":
                if key in self.stops:
                    self.stops[key].set()
                self._update(job, status="pausing" if key in self.running else "paused")
            elif payload.get("action") == "resume":
                self._launch(job)
            else:
                raise SubtitleError("不支持的任务操作。")
        return self.snapshot(key)

    def _wait(self, job, seconds):
        if self.stops[job["id"]].wait(seconds):
            raise InterruptedError("已暂停本地跟踪；听悟已接收的任务会继续转写。")

    def _run(self, job, cookie):
        try:
            # 先展开分 P，再按固定并发处理；Cookie 仅保留在本次调用内存中。
            if job["all_pages"] and not job.get("expanded"):
                expanded = []
                for row in job["rows"]:
                    try:
                        if not self._is_cdn(row["source"]):
                            metadata = self.video.inspect(row["source"], cookie)
                            for page in metadata["pages"]:
                                url = urllib.parse.urlsplit(metadata["source_url"])
                                query = urllib.parse.parse_qs(url.query)
                                query["p"] = [str(page["page"])]
                                link = urllib.parse.urlunsplit(url._replace(query=urllib.parse.urlencode(query, doseq=True)))
                                expanded.append({"id": secrets.token_hex(8), "source": link,
                                    "title": f"{metadata['title']} P{page['page']} {page['part']}",
                                    "status": "queued", "detail": "等待解析", "files": []})
                        else:
                            expanded.append(row)
                    except Exception:
                        expanded.append(row)
                if len(expanded) > 200:
                    raise SubtitleError("展开后超过 200 个分 P，请拆分为多个批次。")
                self._update(job, rows=list({row["source"]: row for row in expanded}.values()), expanded=True)
            # 只向线程池投递当前可运行的三项；断线保留队列，不把整批排队项耗成暂停。
            active = {}
            while True:
                for future in list(active):
                    if future.done():
                        future.result()
                        del active[future]
                stopped = self.stops[job["id"]].is_set()
                pending = [row for row in job["rows"] if row["status"] not in TERMINAL | {"paused"}]
                if not active and (stopped or not pending):
                    break
                if not stopped and self.bridge.online:
                    if job["status"] == "waiting_connection":
                        self._update(job, status="running")
                    busy = set(active.values())
                    for row in pending:
                        if len(active) >= 3:
                            break
                        if row["id"] not in busy and row.get("next_retry_at", 0) <= time.time():
                            active[self.pool.submit(self._process, job, row, cookie)] = row["id"]
                elif not stopped and job["status"] != "waiting_connection":
                    self._update(job, status="waiting_connection")
                if active:
                    wait(active, timeout=.25, return_when=FIRST_COMPLETED)
                else:
                    delay = (min((row.get("next_retry_at", 0) - time.time() for row in pending), default=.5)
                             if self.bridge.online else .5)
                    self.stops[job["id"]].wait(max(.01, min(.5, delay)))
            if stopped:
                for row in job["rows"]:
                    if row["status"] not in TERMINAL:
                        self._update(job, row, status="paused", detail="已暂停本地跟踪，远端已提交任务继续处理。")
            status = "paused" if any(row["status"] == "paused" for row in job["rows"]) else (
                "partial" if any(row["status"] == "error" for row in job["rows"]) else "done")
            self._update(job, status=status)
        except Exception as error:
            self._update(job, status="paused", error=str(error) if isinstance(error, SubtitleError)
                         else "批次中断，恢复连接后可继续。")
        finally:
            with self.lock:
                self.running.discard(job["id"])

    @staticmethod
    def _is_cdn(source):
        try:
            media_url(source)
            return True
        except SubtitleError:
            return False

    def _resolve(self, job, row, cookie):
        if self._is_cdn(row["source"]):
            name = urllib.parse.unquote(urllib.parse.urlsplit(row["source"]).path.rsplit("/", 1)[-1])
            self._update(job, row, title=filename(name))
            return media_url(row["source"])
        self._update(job, row, status="resolving", detail="正在解析带声音的完整 MP4 CDN 直链")
        metadata = self.video.transcription_source(row["source"], cookie)
        self._update(job, row, title=metadata["title"])
        return metadata["cdn_url"]

    @contextmanager
    def _submission_slot(self, job):
        """网页提交串行化，避免同时请求导致远端接收与列表更新相互干扰。"""
        while not self.submit_lock.acquire(timeout=.5):
            self._wait(job, 0)
        try:
            self._wait(job, max(0, self.last_submit + self.submit_gap - time.monotonic()))
            yield
        finally:
            self.last_submit = time.monotonic()
            self.submit_lock.release()

    @staticmethod
    def _remote_candidates(row, records):
        """优先匹配稳定 ID；文件名和字节数只能关联提交前快照之外的新记录。"""
        records = [item for item in records if isinstance(item, dict)]
        exact = [item for item in records if row.get("file_id") and
                 row["file_id"] in {item.get("taskId"), item.get("fileId")}]
        if exact:
            return exact
        names = {row["title"], row.get("remote_file", {}).get("showName")}
        size = row.get("remote_file", {}).get("size")
        return [item for item in records if isinstance(item.get("tag"), dict)
                and item["tag"].get("showName") in names and size
                and str(item.get("fileSize")) == str(size)
                and item.get("transId") not in row.get("baseline_ids", [])]

    def _process(self, job, row, cookie):
        try:
            self._wait(job, 0)
            if not self.bridge.online:
                raise BridgeUnavailable("听悟连接已断开，请保持配对的工作台打开后继续。")
            existing = re.fullmatch(r"https://tingwu\.aliyun\.com/doc/transcripts/([A-Za-z0-9_-]{8,64})/?", row["source"])
            if existing and not row.get("trans_id"):
                self._update(job, row, trans_id=existing[1], submit_sent=True,
                             title="听悟记录 " + existing[1])
            if not row.get("file_id") and not row.get("trans_id"):
                cdn = self._resolve(job, row, cookie)
                self._update(job, row, cdn_url=cdn, status="remote_parsing", detail="听悟正在解析 CDN 文件")
                # 过期的 CDN 不复用；每次尚未提交的重试重新解析来源。
                parsed = self.bridge.call("parse", {"url": cdn})
                task = parsed.get("data", {}).get("taskId")
                if not task:
                    raise SubtitleError("听悟未返回链接解析任务 ID。")
                deadline = time.monotonic() + 300
                while True:
                    self._wait(job, 0)
                    result = self.bridge.call("parse_status", {"taskId": task}).get("data", {})
                    if result.get("status") == 0:
                        urls = result.get("urls", [])
                        if len(urls) != 1 or not urls[0].get("fileId"):
                            raise SubtitleError("听悟没有返回唯一有效的 CDN 文件，未提交。")
                        remote = urls[0]
                        self._update(job, row, file_id=remote["fileId"], remote_file=remote)
                        break
                    if result.get("status") != -1 or time.monotonic() > deadline:
                        raise SubtitleError("听悟无法解析该 CDN 直链，请检查直链是否过期或需要 Referer。")
                    self._wait(job, 3)
            if not row.get("submit_sent"):
                remote = row["remote_file"]
                file = {"fileId": row["file_id"], "dirId": 0, "fileSize": remote.get("size", 0),
                        "tag": {"fileType": "net_source", "showName": row["title"], "lang": "cn",
                                "roleSplitNum": 0, "translateSwitch": 0, "transTargetValue": "en",
                                "client": "web", "originalTag": '{"isVideo":1}' if remote.get("isVideo") else ""}}
                # 听悟会把 fileId 换成 net-source taskId。先记录已有记录，提交后只关联新增项。
                with self._submission_slot(job):
                    before = self.bridge.call("find", {"title": row["title"]}).get("data", [])
                    baseline = [item.get("transId") for item in before if isinstance(item, dict)] if isinstance(before, list) else []
                    # 先落盘再发送。响应丢失时恢复只查原任务，绝不自动重复提交。
                    self._update(job, row, submit_sent=True, baseline_ids=baseline, submitted_at=time.time(),
                                 status="submitting", detail="正在提交听悟转写")
                    submitted = self.bridge.call("submit", {"files": [file]})
                    self._update(job, row, submit_ack={"code": submitted.get("code"),
                                 "success": submitted.get("success")})
                data = submitted.get("data")
                if isinstance(data, dict) and data.get("transId"):
                    self._update(job, row, trans_id=data["transId"])
                if isinstance(data, list):
                    matched = [item for item in data if isinstance(item, dict)
                               and item.get("taskId", item.get("fileId")) == row["file_id"]]
                    if len(matched) == 1 and matched[0].get("transId"):
                        self._update(job, row, trans_id=matched[0]["transId"])
            self._update(job, row, status="transcribing", detail="听悟转写中，正在跟踪进度")
            deadline = time.monotonic() + 24 * 3600
            while not row.get("transcribed"):
                self._wait(job, 0)
                if not row.get("trans_id"):
                    # 网页提交接口可能只返回 success，需从任务列表按原 fileId 取得 transId。
                    discovered = self.bridge.call("find", {"title": row["title"]}).get("data", [])
                    if isinstance(discovered, list):
                        candidates = self._remote_candidates(row, discovered)
                        if len(candidates) == 1 and candidates[0].get("transId"):
                            self._update(job, row, trans_id=candidates[0]["transId"],
                                         remote_task_id=candidates[0].get("taskId"))
                        if len(candidates) > 1:
                            raise TaskConflict("查到多个同名同大小的听悟任务，请在听悟核对后使用记录链接导出，避免关联错误。")
                        self._update(job, row, discovered_ids=[{"task_id": item.get("taskId"),
                                     "trans_id": item.get("transId"), "title": item.get("tag", {}).get("showName"),
                                     "file_size": item.get("fileSize")} for item in discovered[:10]])
                task_id = row.get("remote_task_id") or row.get("file_id")
                data = self.bridge.call("status", {"taskIds": [task_id] if task_id else [],
                                       "transIds": [row["trans_id"]] if row.get("trans_id") else []}).get("data", [])
                if not isinstance(data, list):
                    raise SubtitleError("听悟任务查询格式已变化。")
                matching = [item for item in data if (task_id and item.get("taskId") == task_id)
                            or (row.get("trans_id") and item.get("transId") == row["trans_id"])]
                if not matching:
                    if time.time() - row.get("submitted_at", time.time()) > 24 * 3600:
                        raise TaskConflict("听悟记录超过 24 小时仍未确认，请在听悟核对；本地未重复提交。")
                    raise RemotePending("已提交，等待听悟生成任务记录；自动查询中，不会重复提交。")
                else:
                    item = matching[0]
                    if item.get("transId"):
                        self._update(job, row, trans_id=item["transId"])
                    status = item.get("status")
                    if status == 0 and row.get("trans_id"):
                        self._update(job, row, transcribed=True)
                        break
                    if status in {2, 11, 20, 21, 22, 100, 200, 301, 302, 303}:
                        raise SubtitleError("听悟处理失败：" + str(item.get("statusMsg") or status))
                    self._update(job, row, detail=str(item.get("statusMsg") or "听悟转写中")
                                 + (f" · {item['progress']}%" if item.get("progress") is not None else ""))
                if time.monotonic() > deadline:
                    raise BridgeUnavailable("已跟踪 24 小时，可稍后继续查询该任务。")
                self._wait(job, self.interval)
            with self._export_slot(job, row):
                if not row.get("export_id"):
                    self._create_export(job, row)
                deadline = time.monotonic() + 900
                while True:
                    self._wait(job, 0)
                    data = self.bridge.call("export_status", {"exportTaskId": row["export_id"]}).get("data", {})
                    if data.get("exportStatus") == 1:
                        exports = [item for item in data.get("exportUrls", []) if item.get("success") and item.get("url")]
                        if not exports:
                            raise SubtitleError("听悟导出完成，但没有可下载的 DOCX。")
                        break
                    if data.get("exportStatus") == -1 or time.monotonic() > deadline:
                        self._update(job, row, export_id=None)
                        raise SubtitleError("听悟 DOCX 导出失败或超时，可重试导出。")
                    self._wait(job, 3)
                self._update(job, row, status="converting", detail="正在下载 DOCX 并转换 TXT")
                files = []
                for index, item in enumerate(exports):
                    content = self.downloader(item["url"])
                    files.extend(self._store_documents(job["id"], row["id"], content,
                                                      row["title"] + (f" {index+1}" if len(exports) > 1 else "")))
                self._update(job, row, status="done", detail="DOCX 和 TXT 已就绪", files=files)
        except RemotePending as error:
            self._update(job, row, status="awaiting_confirmation", detail=str(error),
                         next_retry_at=time.time() + max(.01, self.interval))
        except BridgeUnavailable as error:
            self._update(job, row, status="waiting_connection", detail=str(error) + " 将自动恢复查询。",
                         next_retry_at=time.time() + max(.01, self.interval))
        except (TaskConflict, InterruptedError) as error:
            self._update(job, row, status="paused", detail=str(error))
        except Exception as error:
            self._update(job, row, status="error", detail=str(error) if isinstance(error, SubtitleError)
                         else "处理失败，请恢复连接后重试；已提交的任务会继续按原 ID 查询。")

    @contextmanager
    def _export_slot(self, job, row):
        """网页导出串行排队，等待锁时仍可响应暂停。"""
        self._update(job, row, status="exporting", detail="等待导出 DOCX（自动排队）")
        while not self.export_lock.acquire(timeout=.5):
            self._wait(job, 0)
        try:
            yield
        finally:
            self.export_lock.release()

    def _create_export(self, job, row):
        """对网页明确返回的导出限流退避重试，不重新提交转写。"""
        for attempt in range(4):
            self._wait(job, max(0, self.last_export + self.export_gap - time.monotonic()))
            self._update(job, row, detail="正在生成 DOCX")
            self.last_export = time.monotonic()
            try:
                data = self.bridge.call("export", {"transIds": [row["trans_id"]]}).get("data", {})
                if not data.get("exportTaskId"):
                    raise SubtitleError("听悟没有返回 DOCX 导出任务 ID。")
                self._update(job, row, export_id=data["exportTaskId"])
                return
            except SubtitleError as error:
                limited = any(code in str(error) for code in ("EPO.RequestTooFast", "EPO.ExportTaskConcurrencyExceed"))
                if not limited or attempt == 3:
                    raise
                delay = self.retry_delay * (2 ** attempt)
                self._update(job, row, detail=f"听悟限制导出频率，{delay:g} 秒后自动重试")
                self._wait(job, delay)

    def _store_documents(self, job_id, row_id, content, title):
        """接收单 DOCX 或听悟 ZIP；不按压缩包路径落盘，逐份生成 TXT。"""
        try:
            archive = ZipFile(io.BytesIO(content))
            if "word/document.xml" in archive.namelist():
                documents = [(title + ".docx", content)]
            else:
                infos = [item for item in archive.infolist() if not item.is_dir()
                         and item.filename.lower().endswith(".docx") and not Path(item.filename).name.startswith("~$")]
                if not infos or len(infos) > 200 or sum(item.file_size for item in infos) > 128 * 1024 * 1024:
                    raise SubtitleError("ZIP 中没有 DOCX 或解压后超过大小限制。")
                documents = [(Path(item.filename.replace("\\", "/")).name, archive.read(item)) for item in infos]
            archive.close()
        except (BadZipFile, OSError, RuntimeError) as error:
            raise SubtitleError("文件不是有效的 DOCX 或 DOCX 压缩包。") from error
        folder = self.root / job_id / row_id
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for index, (name, body) in enumerate(documents):
            stem = filename(Path(name).stem) + (f"_{index+1}" if len(documents) > 1 else "")
            docx = folder / (stem + ".docx")
            txt = folder / (stem + ".txt")
            # 先验证与转换，损坏文档不登记成成功文件。
            try:
                text = extract_docx_text(io.BytesIO(body))
            except (ValueError, ET.ParseError, BadZipFile, RuntimeError) as error:
                raise SubtitleError("DOCX 正文无法读取：" + str(error)[:150], "invalid_docx", 422) from None
            docx.write_bytes(body)
            txt.write_text(text, encoding="utf-8", newline="\n")
            for path in (docx, txt):
                relative = path.relative_to(self.root).as_posix()
                files.append({"name": path.name, "kind": path.suffix[1:], "path": relative,
                              "url": "/api/tingwu/file?path=" + urllib.parse.quote(relative)})
        return files

    def import_document(self, payload):
        name = payload.get("name", "")
        if not isinstance(name, str) or Path(name).suffix.lower() not in {".docx", ".zip"}:
            raise SubtitleError("请选择 DOCX 或包含 DOCX 的 ZIP。")
        try:
            body = base64.b64decode(payload.get("content", ""), validate=True)
        except (ValueError, TypeError):
            raise SubtitleError("文件编码无效。") from None
        if not body or len(body) > MAX_FILE:
            raise SubtitleError("单文件最大 64 MB。", "too_large", 413)
        key = payload.get("job_id") or secrets.token_hex(16)
        with self.lock:
            if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
                raise SubtitleError("导入批次标识无效。")
            if payload.get("job_id") and (key not in self.jobs or not self.jobs[key].get("local_import")):
                raise SubtitleError("只能将文件追加到已有的 DOCX 导入批次。")
        row_id = secrets.token_hex(8)
        files = self._store_documents(key, row_id, body, Path(name).stem)
        with self.lock:
            job = self.jobs.setdefault(key, {"id": key, "created": time.time(), "status": "done",
                                            "local_import": True, "rows": []})
            job["rows"].append({"id": row_id, "source": name, "title": name, "status": "done",
                                "detail": "DOCX 已转换为 TXT", "files": files})
            self._save()
        return self.snapshot(key)

    def file(self, relative):
        if not isinstance(relative, str):
            raise SubtitleError("文件路径无效。")
        path = (self.root / relative).resolve()
        if (not path.is_relative_to(self.root.resolve()) or path.suffix not in {".txt", ".docx"}
                or not path.is_file()):
            raise SubtitleError("文件不存在。", "not_found", 404)
        return path

    def bundle(self, key, kind):
        job = self.snapshot(key)
        if kind not in {"txt", "docx", "all"}:
            raise SubtitleError("不支持的打包格式。")
        files = [(row, file) for row in job["rows"] for file in row.get("files", [])
                 if kind == "all" or file["kind"] == kind]
        if not files:
            raise SubtitleError("尚无可下载文件。", "not_ready", 409)
        # 大批 DOCX 打包写入临时磁盘文件，避免把整个批次同时放进内存。
        output = tempfile.TemporaryFile()
        try:
            with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
                for index, (row, file) in enumerate(files):
                    archive.write(self.file(file["path"]), f"{index+1:03d}_{file['name']}")
            output.seek(0)
            return output
        except Exception:
            output.close()
            raise
