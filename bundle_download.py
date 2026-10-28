"""@brief 在后台准备 ZIP，并单独报告浏览器下载是否已经开始。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import secrets
import threading
import time

from subtitles import SubtitleError


class BundleDownloads:
    def __init__(self, builder, clock=time.monotonic):
        self.builder, self.clock = builder, clock
        self.lock = threading.RLock()
        self.items = {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bundle")

    def _cleanup(self):
        for key, item in list(self.items.items()):
            if self.clock() - item["created"] > 1800 and not item["busy"]:
                if item.get("file"):
                    item["file"].close()
                del self.items[key]

    def create(self, job_id, kind):
        if kind not in {"txt", "docx"}:
            raise SubtitleError("不支持的打包格式。")
        with self.lock:
            self._cleanup()
            for token, item in list(self.items.items()):
                if len(self.items) < 8:
                    break
                if item["status"] in {"finished", "error"} and not item["busy"]:
                    del self.items[token]
            if len(self.items) >= 8:
                raise SubtitleError("打包请求较多，请等待已有下载完成。", "busy", 409)
            key = secrets.token_hex(16)
            self.items[key] = {"id": key, "status": "preparing", "created": self.clock(),
                               "kind": kind, "busy": False, "file": None, "size": 0}
            self.pool.submit(self._prepare, key, job_id, kind)
            return self.status(key)

    def _prepare(self, key, job_id, kind):
        try:
            source = self.builder(job_id, kind)
            source.seek(0, 2)
            size = source.tell()
            source.seek(0)
            with self.lock:
                if key not in self.items:
                    source.close()
                    return
                self.items[key].update(status="ready", file=source, size=size)
        except Exception as error:
            with self.lock:
                if key in self.items:
                    self.items[key].update(status="error", error=str(error) if isinstance(error, SubtitleError)
                                           else "打包失败，请重新尝试。")

    def _get(self, key):
        self._cleanup()
        if key not in self.items:
            raise SubtitleError("下载准备记录已过期，请重新打包。", "expired", 410)
        return self.items[key]

    def status(self, key):
        with self.lock:
            item = self._get(key)
            return {k: v for k, v in item.items() if k in {"id", "status", "size", "error", "kind"}}

    def started(self, key):
        with self.lock:
            self._get(key)["status"] = "started"

    @contextmanager
    def open(self, key):
        with self.lock:
            item = self._get(key)
            if item["status"] != "ready" or item["busy"]:
                raise SubtitleError("文件尚未准备好或已开始下载，请勿重复点击。", "not_ready", 409)
            item["busy"] = True
            source = item["file"]
        try:
            yield source, "tingwu-" + item["kind"] + ".zip"
        except Exception:
            with self.lock:
                item.update(status="error", error="下载连接中断，请重新打包下载。")
            raise
        else:
            with self.lock:
                item["status"] = "finished"
        finally:
            with self.lock:
                source.close()
                item.update(file=None, busy=False)
