"""视频清晰度、下载完整性、取消状态、真实 FFmpeg 合并和 HTTP Range 验证。"""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import server
from subtitles import SubtitleError
from video import VideoClient, VideoService, find_ffmpeg, media_url, normalize_formats

URL = "https://upos-sz-mirrorcos.bilivideo.com/upgcxcode/fixture.m4s"


def fixture_formats():
    return {"accept_quality": [80, 64, 32, 16], "dash": {
        "video": [{"id": 16, "baseUrl": URL, "codecs": "av01", "bandwidth": 5000},
                  {"id": 16, "baseUrl": URL, "codecs": "avc1.64001E", "bandwidth": 4000},
                  {"id": 32, "baseUrl": URL, "codecs": "avc1.64001F", "bandwidth": 8000}],
        "audio": [{"baseUrl": URL, "codecs": "mp4a.40.2", "bandwidth": 128000}]}}


class FormatTests(unittest.TestCase):
    def test_actual_qualities_and_h264_preference(self):
        formats = normalize_formats(fixture_formats(), 2)
        self.assertEqual([item["quality"] for item in formats], [32, 16])
        self.assertTrue(formats[1]["codec"].startswith("avc1"))
        self.assertTrue(all(item["has_audio"] for item in formats))

    def test_progressive_segments(self):
        formats = normalize_formats({"quality": 16, "durl": [
            {"url": URL, "size": 10}, {"url": URL, "size": 20}]}, 2)
        self.assertEqual(formats[0]["mode"], "progressive")
        self.assertEqual(len(formats[0]["segments"]), 2)
        self.assertEqual(formats[0]["estimated_bytes"], 30)

    def test_preview_and_empty_rejected(self):
        for payload in ({"is_preview": True}, {"is_drm": True}, {}):
            with self.subTest(payload=payload), self.assertRaises(SubtitleError):
                normalize_formats(payload, 1)

    def test_cdn_validation(self):
        self.assertTrue(media_url(URL).startswith("https:"))
        for url in ("http://127.0.0.1/file", "https://bilivideo.com.evil.test/a",
                    "https://user@a.bilivideo.com/a", "https://a.bilivideo.com:4483/a"):
            with self.subTest(url=url), self.assertRaises(SubtitleError):
                media_url(url)

    def test_partial_download_not_marked_success(self):
        class Response(io.BytesIO):
            status = 200
            headers = {"Content-Length": "9"}
        class Opener:
            def open(self, request, timeout):
                self.headers = dict(request.header_items())
                return Response(b"short")
        client = VideoClient()
        client.opener = Opener()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(SubtitleError):
            client.download([URL], Path(directory) / "file.part", threading.Event(), lambda *args: None, 100)
        self.assertIn("Referer", client.opener.headers)
        self.assertNotIn("Cookie", client.opener.headers)


class FakeClient:
    def __init__(self, video_bytes=b"fake", audio_bytes=b"fake", wait=False, fail=False):
        self.video_bytes, self.audio_bytes = video_bytes, audio_bytes
        self.wait, self.fail = wait, fail

    def inspect(self, url, cookie):
        return {"title": "测试视频", "part": "第二 P", "page": 2, "pages": [
            {"page": 1, "part": "一"}, {"page": 2, "part": "二"}], "id": "BV1GJ411x7h7",
            "duration": 1, "source_url": url, "formats": [{"id": "dash-16", "label": "360P",
            "mode": "dash", "has_audio": True, "estimated_bytes": 1, "quality": 16,
            "video_urls": [URL + "?video"], "audio_urls": [URL + "?audio"]}]}

    def download(self, urls, destination, cancel, progress, limit):
        if self.wait:
            cancel.wait(3)
            if cancel.is_set():
                raise SubtitleError("下载已取消。", "canceled", 409)
        data = self.video_bytes if urls[0].endswith("?video") else self.audio_bytes
        destination.write_bytes(data)
        if self.fail:
            raise OSError("private transport details must not leak")
        progress(len(data), len(data))
        return len(data)


class JobTests(unittest.TestCase):
    def make_service(self, client):
        directory = tempfile.TemporaryDirectory()
        executor = ThreadPoolExecutor(max_workers=1)
        service = VideoService(client, Path(directory.name), executor)
        self.addCleanup(directory.cleanup)
        self.addCleanup(lambda: executor.shutdown(wait=True))
        return service

    def wait_terminal(self, service, key):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = service.snapshot(key)
            if status["status"] in ("done", "error", "canceled"):
                return status
            time.sleep(.03)
        self.fail("job did not terminate")

    def test_inspection_does_not_return_urls_or_store_cookie(self):
        service = self.make_service(FakeClient())
        result = service.inspect({"url": "BV1GJ411x7h7", "cookie": "private-example"})
        self.assertNotIn("video_urls", result["formats"][0])
        self.assertNotIn("private-example", repr(service.inspections))

    def test_expired_inspection_and_missing_ffmpeg(self):
        service = self.make_service(FakeClient())
        result = service.inspect({"url": "BV1GJ411x7h7"})
        payload = {"inspection_id": result["inspection_id"], "format_id": "dash-16"}
        with patch("video.find_ffmpeg", return_value=None), self.assertRaises(SubtitleError) as context:
            service.start(payload)
        self.assertEqual(context.exception.code, "missing_ffmpeg")
        service.inspections[result["inspection_id"]]["expires"] = 0
        with self.assertRaises(SubtitleError) as context:
            service.start(payload)
        self.assertEqual(context.exception.code, "expired")

    def test_cancel_does_not_publish_or_leave_parts(self):
        service = self.make_service(FakeClient(wait=True))
        result = service.inspect({"url": "BV1GJ411x7h7"})
        with patch("video.find_ffmpeg", return_value="fixture"):
            job = service.start({"inspection_id": result["inspection_id"], "format_id": "dash-16"})
        service.cancel(job["id"])
        self.assertEqual(self.wait_terminal(service, job["id"])["status"], "canceled")
        self.assertFalse(list(service.root.rglob("*.mp4")))
        self.assertFalse(list(service.root.rglob("*.part")))

    def test_download_failure_is_not_success(self):
        service = self.make_service(FakeClient(fail=True))
        result = service.inspect({"url": "BV1GJ411x7h7"})
        with patch("video.find_ffmpeg", return_value="fixture"):
            job = service.start({"inspection_id": result["inspection_id"], "format_id": "dash-16"})
        status = self.wait_terminal(service, job["id"])
        self.assertEqual(status["status"], "error")
        self.assertNotIn("private transport", status["error"])
        self.assertFalse(list(service.root.rglob("*.mp4")))

    @unittest.skipUnless(find_ffmpeg(), "FFmpeg unavailable")
    def test_real_ffmpeg_merges_two_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path, audio_path = Path(directory) / "video.mp4", Path(directory) / "audio.m4a"
            common = [find_ffmpeg(), "-nostdin", "-y", "-v", "error"]
            subprocess.run(common + ["-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.5:r=10",
                           "-c:v", "libx264", str(video_path)], check=True, capture_output=True)
            subprocess.run(common + ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                           "-c:a", "aac", str(audio_path)], check=True, capture_output=True)
            service = self.make_service(FakeClient(video_path.read_bytes(), audio_path.read_bytes()))
        result = service.inspect({"url": "BV1GJ411x7h7"})
        job = service.start({"inspection_id": result["inspection_id"], "format_id": "dash-16"})
        status = self.wait_terminal(service, job["id"])
        self.assertEqual(status["status"], "done", status["error"])
        path, _ = service.file(job["id"])
        verification = subprocess.run([find_ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v:0",
                                      "-map", "0:a:0", "-f", "null", "-"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        self.assertEqual(verification.returncode, 0, verification.stderr)


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "test.mp4"
        path.write_bytes(b"0123456789abcdef")
        self.service = VideoService(FakeClient(), self.directory.name)
        self.service.jobs["test"] = {"status": "done", "path": path, "filename": "测试.mp4"}
        self.original = server.VIDEOS
        server.VIDEOS = self.service
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/api/video/file?job=test"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server.VIDEOS = self.original
        self.service.executor.shutdown(wait=True)
        self.directory.cleanup()

    def test_full_and_range_download(self):
        with self.opener.open(self.url) as response:
            self.assertEqual(response.read(), b"0123456789abcdef")
            self.assertIn("attachment", response.headers["Content-Disposition"])
        request = urllib.request.Request(self.url, headers={"Range": "bytes=2-5"})
        with self.opener.open(request) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], "bytes 2-5/16")
            self.assertEqual(response.read(), b"2345")

    def test_suffix_and_invalid_range(self):
        request = urllib.request.Request(self.url, headers={"Range": "bytes=-3"})
        with self.opener.open(request) as response:
            self.assertEqual(response.read(), b"def")
        request = urllib.request.Request(self.url, headers={"Range": "bytes=999-"})
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.opener.open(request)
        self.assertEqual(context.exception.code, 416)


if __name__ == "__main__":
    unittest.main(verbosity=2)
