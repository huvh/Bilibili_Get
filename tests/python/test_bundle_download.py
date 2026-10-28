"""@brief 打包准备与真正开始传输的边界、错误和资源清理验证。"""
import io
import json
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

from bundle_download import BundleDownloads
from subtitles import SubtitleError


class BundleTests(unittest.TestCase):
    def manager(self, builder, **kwargs):
        manager = BundleDownloads(builder, **kwargs)
        self.addCleanup(manager.pool.shutdown)
        return manager

    def ready(self, manager, key):
        deadline = time.monotonic() + 2
        while manager.status(key)["status"] == "preparing" and time.monotonic() < deadline:
            time.sleep(.005)
        return manager.status(key)

    def test_preparation_returns_before_zip_ready_and_start_only_after_header_callback(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        def build(job, kind):
            gate.wait(2)
            return io.BytesIO(b"zip-content")
        manager = self.manager(build)
        key = manager.create("job", "txt")["id"]
        self.assertEqual(manager.status(key)["status"], "preparing")
        gate.set()
        self.assertEqual(self.ready(manager, key)["status"], "ready")
        with manager.open(key) as (source, filename):
            self.assertEqual(manager.status(key)["status"], "ready")
            manager.started(key)
            self.assertEqual(manager.status(key)["status"], "started")
            self.assertEqual(source.read(), b"zip-content")
            self.assertEqual(filename, "tingwu-txt.zip")
            with self.assertRaises(SubtitleError):
                with manager.open(key):
                    self.fail("duplicate download opened")
        self.assertTrue(source.closed)
        self.assertEqual(manager.status(key)["status"], "finished")

    def test_preparation_error_is_reported_without_fake_download(self):
        def broken(job, kind):
            raise SubtitleError("该批次没有可打包的 TXT。")
        manager = self.manager(broken)
        key = manager.create("job", "txt")["id"]
        result = self.ready(manager, key)
        self.assertEqual(result["status"], "error")
        self.assertIn("没有可打包", result["error"])
        with self.assertRaises(SubtitleError):
            with manager.open(key):
                self.fail("failed ZIP opened")

    def test_expired_ready_file_is_closed(self):
        clock = [0]
        source = io.BytesIO(b"zip")
        manager = self.manager(lambda job, kind: source, clock=lambda: clock[0])
        key = manager.create("job", "docx")["id"]
        self.ready(manager, key)
        clock[0] = 1801
        with self.assertRaises(SubtitleError):
            manager.status(key)
        self.assertTrue(source.closed)

    def test_completed_downloads_do_not_fill_request_limit(self):
        manager = self.manager(lambda job, kind: io.BytesIO(b"zip"))
        for _ in range(10):
            key = manager.create("job", "txt")["id"]
            self.ready(manager, key)
            with manager.open(key):
                manager.started(key)

    def test_http_prepare_status_and_download(self):
        import server
        manager = self.manager(lambda job, kind: io.BytesIO(b"test-zip"))
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with patch.object(server, "BUNDLES", manager):
            request = urllib.request.Request(base + "/api/tingwu/bundle/start",
                data=b'{"job_id":"job","kind":"txt"}', headers={"Content-Type": "application/json"})
            with opener.open(request) as response:
                key = json.load(response)["id"]
            self.ready(manager, key)
            with opener.open(base + "/api/tingwu/bundle/download?id=" + key) as response:
                self.assertIn("attachment;", response.headers["Content-Disposition"])
                self.assertEqual(response.read(), b"test-zip")
            with opener.open(base + "/api/tingwu/bundle/status?id=" + key) as response:
                self.assertEqual(json.load(response)["status"], "finished")
