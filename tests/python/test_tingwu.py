"""批量听悟流程、恢复去重和文件归档的针对性验证；远端使用协议样本。"""
import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch
from zipfile import ZipFile

from subtitles import SubtitleError
from tingwu import BrowserBridge, BridgeUnavailable, TingwuService
from video import VideoClient


def document():
    data = io.BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                         '<w:body><w:p><w:r><w:t>00:01 说话人：听悟正文</w:t></w:r></w:p></w:body></w:document>')
    return data.getvalue()


class ProtocolBridge:
    online = True

    def __init__(self, lose_submit=False):
        self.calls = []
        self.lose_submit = lose_submit

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "submit" and self.lose_submit:
            self.lose_submit = False
            raise BridgeUnavailable("响应丢失")
        values = {"parse": {"taskId": "parse1"},
                  "parse_status": {"status": 0, "urls": [{"fileId": "file1", "size": 100}]},
                  "submit": {"transId": "trans1"},
                  "find": [{"taskId": "file1", "transId": "trans1"}],
                  "status": [{"taskId": "file1", "transId": "trans1", "status": 0}],
                  "export": {"exportTaskId": "export1"},
                  "export_status": {"exportStatus": 1, "exportUrls": [{"success": True,
                                    "url": "https://result.oss-cn-beijing.aliyuncs.com/result.docx"}]}}
        return {"code": "0", "success": True, "data": values[method]}


class TingwuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = ProtocolBridge()
        self.service = TingwuService(root=self.temp.name, bridge=self.bridge,
                                    downloader=lambda url: document(), interval=.001,
                                    export_gap=0, retry_delay=.001, submit_gap=0)

    def tearDown(self):
        self.service.pool.shutdown(wait=True)
        self.temp.cleanup()

    def settled(self, key):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if key not in self.service.running:
                return self.service.snapshot(key)
            time.sleep(.01)
        self.fail("本地协议样本任务超时")

    def test_pipeline_exports_real_docx_and_txt(self):
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.m4s"]})
        result = self.settled(job["id"])
        self.assertEqual(result["status"], "done")
        self.assertEqual([call[0] for call in self.bridge.calls],
                         ["parse", "parse_status", "find", "submit", "status", "export", "export_status"])
        submit = next(params for method, params in self.bridge.calls if method == "submit")
        self.assertEqual(submit["files"][0]["tag"]["fileType"], "net_source")
        with self.service.bundle(job["id"], "txt") as source, ZipFile(source) as archive:
            self.assertEqual(archive.read(archive.namelist()[0]).decode("utf-8"), "00:01 说话人：听悟正文\n")

    def test_lost_submission_response_resume_never_submits_twice(self):
        self.bridge.lose_submit = True
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.m4s"]})
        self.assertEqual(self.settled(job["id"])["status"], "done")
        self.assertEqual(sum(method == "submit" for method, _ in self.bridge.calls), 1)

    def test_parse_failures_refresh_source_and_rotate_backup_before_single_submit(self):
        original = self.bridge.call
        attempts = 0
        self.service.video = Mock()
        self.service.video.transcription_source.return_value = {
            "title": "课程", "cdn_url": "https://one.bilivideo.com/audio.mp4",
            "cdn_urls": ["https://one.bilivideo.com/audio.mp4", "https://two.bilivideo.com/audio.mp4"]}
        def call(method, params):
            nonlocal attempts
            result = original(method, params)
            if method == "parse_status":
                attempts += 1
                if attempts <= 2:
                    result["data"] = {"status": 1}
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["BV1th411z7sn"]})
        result = self.settled(job["id"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.service.video.transcription_source.call_count, 3)
        urls = [params["url"] for method, params in self.bridge.calls if method == "parse"]
        self.assertEqual(urls, ["https://one.bilivideo.com/audio.mp4", "https://two.bilivideo.com/audio.mp4",
                                "https://one.bilivideo.com/audio.mp4"])
        self.assertEqual(sum(method == "submit" for method, _ in self.bridge.calls), 1)

    def test_permanent_parse_failure_stops_after_three_retries_without_submitting(self):
        original = self.bridge.call
        def call(method, params):
            result = original(method, params)
            if method == "parse_status":
                result["data"] = {"status": 1}
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.mp4"]})
        result = self.settled(job["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["rows"][0]["parse_attempts"], 4)
        self.assertIn("自动重试 3 次", result["rows"][0]["detail"])
        self.assertEqual(sum(method == "parse" for method, _ in self.bridge.calls), 4)
        self.assertNotIn("submit", [method for method, _ in self.bridge.calls])

    def test_browser_timeout_before_submission_retries_parse(self):
        original = self.bridge.call
        first = True
        def call(method, params):
            nonlocal first
            if method == "parse" and first:
                first = False
                raise SubtitleError("听悟响应超时", "tingwu_timeout", 502)
            return original(method, params)
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.mp4"]})
        result = self.settled(job["id"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["rows"][0]["parse_attempts"], 2)

    def test_browser_timeout_after_submission_only_queries_existing_task(self):
        original = self.bridge.call
        def call(method, params):
            result = original(method, params)
            if method == "submit":
                raise SubtitleError("听悟响应超时", "tingwu_timeout", 502)
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.mp4"]})
        self.assertEqual(self.settled(job["id"])["status"], "done")
        self.assertEqual(sum(method == "submit" for method, _ in self.bridge.calls), 1)
        self.assertEqual(sum(method == "parse" for method, _ in self.bridge.calls), 1)

    def test_record_lookup_reads_second_page_and_rejects_legacy_truncation(self):
        first = [{"taskId": f"old-{i}", "transId": f"trans-{i}"} for i in range(100)]
        target = {"taskId": "net-target", "transId": "target", "tag": {"showName": "课程"}, "fileSize": 100}
        pages = []
        def call(method, params):
            self.assertEqual(params["title"], "")  # 全文检索可能漏掉同名记录，必须读取完整列表。
            pages.append(params["pageNo"])
            return {"data": first if params["pageNo"] == 1 else [target], "total": 101}
        self.bridge.call = call
        found = self.service._find_records("课程")
        self.assertEqual(pages, [1, 2])
        self.assertEqual(self.service._remote_candidates(
            {"title": "课程", "remote_file": {"size": 100}}, found), [target])
        self.bridge.version = 2
        with self.assertRaisesRegex(BridgeUnavailable, "新版连接书签"):
            self.service._find_records("课程")

    def test_missing_remote_size_requires_matching_submission_time(self):
        row = {"title": "课程", "submitted_at": 1700000000, "remote_file": {"size": 100},
               "baseline_ids": ["old"]}
        matching = {"taskId": "net-new", "transId": "new", "showTime": 1700000001000,
                    "status": 2, "tag": {"showName": "课程"}}
        old = {**matching, "transId": "old"}
        unrelated = {**matching, "transId": "other", "showTime": 1700000500000}
        no_time = {key: value for key, value in matching.items() if key != "showTime"}
        self.assertEqual(self.service._remote_candidates(row, [matching, old, unrelated, no_time]), [matching])
        self.assertEqual(self.service._remote_candidates(row, [{**matching, "fileSize": 101}]), [])

    def test_retry_selected_failure_preserves_done_active_and_original_submission(self):
        job = {"id": "selected-retry", "status": "partial", "created": time.time(), "rows": [
            {"id": "done", "status": "done", "files": ["keep"]},
            {"id": "other", "status": "error", "detail": "keep-other"},
            {"id": "active", "status": "transcribing", "trans_id": "keep-active"},
            {"id": "retry", "status": "paused", "submit_sent": True, "file_id": "original-file",
             "submitted_at": 123, "detail": "old"}]}
        self.service.jobs[job["id"]] = job
        with patch.object(self.service, "_launch") as launch:
            result = self.service.control({"job_id": job["id"], "action": "retry_failed", "row_ids": ["retry"]})
        self.assertEqual([row["status"] for row in result["rows"]], ["done", "error", "transcribing", "queued"])
        self.assertEqual(result["rows"][0]["files"], ["keep"])
        self.assertEqual(result["rows"][3]["file_id"], "original-file")
        self.assertEqual(result["rows"][3]["submitted_at"], 123)
        self.assertTrue(result["rows"][3]["submit_sent"])
        launch.assert_called_once_with(job, "", reset=False)
        with self.assertRaises(SubtitleError):
            self.service.control({"job_id": job["id"], "action": "retry_failed", "row_ids": ["done"]})

    def test_retry_known_remote_failure_archives_attempt_but_not_export_failure(self):
        job = {"id": "remote-retry", "status": "partial", "created": time.time(), "rows": [
            {"id": "failed", "status": "error", "error_code": "remote_transcription", "remote_status": 2,
             "submit_sent": True, "trans_id": "failed-record", "file_id": "failed-file"},
            {"id": "export", "status": "error", "submit_sent": True, "trans_id": "keep-record",
             "transcribed": True, "export_id": "keep-export"}]}
        self.service.jobs[job["id"]] = job
        with patch.object(self.service, "_launch"):
            result = self.service.control({"job_id": job["id"], "action": "retry_failed"})
        failed, exported = result["rows"]
        self.assertNotIn("submit_sent", failed)
        self.assertEqual(failed["previous_submissions"][0]["trans_id"], "failed-record")
        self.assertEqual(exported["trans_id"], "keep-record")
        self.assertTrue(exported["transcribed"])
        self.assertEqual(exported["export_id"], "keep-export")

    def test_record_without_trans_id_can_be_matched_by_new_task_id(self):
        row = {"title": "课程", "remote_file": {"size": 100}, "baseline_ids": [None],
               "baseline_task_ids": ["old"]}
        old = {"taskId": "old", "tag": {"showName": "课程"}, "fileSize": 100}
        failed = {"taskId": "failed", "tag": {"showName": "课程"}, "fileSize": 100}
        self.assertEqual(self.service._remote_candidates(row, [old, failed]), [failed])

    def test_more_than_ten_empty_status_responses_auto_recover_without_resubmission(self):
        original = self.bridge.call
        queries = 0
        def call(method, params):
            nonlocal queries
            result = original(method, params)
            if method == "status":
                queries += 1
                if queries <= 12:
                    result["data"] = []
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.mp4"]})
        self.assertEqual(self.settled(job["id"])["status"], "done")
        self.assertEqual(queries, 13)
        self.assertEqual(sum(method == "submit" for method, _ in self.bridge.calls), 1)

    def test_disconnect_preserves_queue_and_reconnect_resumes_automatically(self):
        original = self.bridge.call
        disconnected = threading.Event()
        def call(method, params):
            if not disconnected.is_set():
                self.bridge.online = False
                disconnected.set()
                raise BridgeUnavailable("测试断线")
            return original(method, params)
        self.bridge.call = call
        job = self.service.create({"urls": [f"https://test.bilivideo.com/{i}.mp4" for i in range(8)]})
        self.assertTrue(disconnected.wait(1))
        time.sleep(.05)
        snapshot = self.service.snapshot(job["id"])
        self.assertFalse(any(row["status"] == "paused" for row in snapshot["rows"]))
        self.assertGreaterEqual(sum(row["status"] == "queued" for row in snapshot["rows"]), 5)
        self.bridge.online = True
        self.assertEqual(self.settled(job["id"])["status"], "done")

    def test_resume_offline_waits_for_connection_and_user_pause_still_works(self):
        self.bridge.online = False
        job = {"id": "offline-resume", "created": time.time(), "all_pages": False,
               "status": "paused", "rows": [{"id": "r1", "source": "https://test.bilivideo.com/audio.mp4",
               "title": "audio", "status": "paused", "files": []}]}
        self.service.jobs[job["id"]] = job
        self.service.control({"job_id": job["id"], "action": "resume"})
        deadline = time.monotonic() + 1
        while self.service.snapshot(job["id"])["status"] != "waiting_connection" and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(self.service.snapshot(job["id"])["status"], "waiting_connection")
        self.service.control({"job_id": job["id"], "action": "pause"})
        self.assertEqual(self.settled(job["id"])["status"], "paused")
        self.assertEqual(self.bridge.calls, [])

    def test_submissions_are_serialized_and_record_remote_task_id(self):
        original = self.bridge.call
        active = maximum = 0
        def call(method, params):
            nonlocal active, maximum
            if method == "submit":
                active += 1
                maximum = max(maximum, active)
                time.sleep(.01)
            result = original(method, params)
            if method == "submit":
                active -= 1
            return result
        self.bridge.call = call
        job = self.service.create({"urls": [f"https://test.bilivideo.com/{i}.mp4" for i in range(4)]})
        self.assertEqual(self.settled(job["id"])["status"], "done")
        self.assertEqual(maximum, 1)

    def test_submit_without_trans_id_uses_web_task_list(self):
        original = self.bridge.call
        def call(method, params):
            result = original(method, params)
            if method == "submit":
                result["data"] = None
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.mp4"]})
        result = self.settled(job["id"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["rows"][0]["trans_id"], "trans1")
        self.assertIn("find", [method for method, _ in self.bridge.calls])

    def test_generated_net_source_id_matches_new_record_not_existing_duplicate(self):
        original = self.bridge.call
        submitted = False
        def call(method, params):
            nonlocal submitted
            result = original(method, params)
            old = {"taskId": "net-source-old", "transId": "old", "fileSize": 100,
                   "tag": {"showName": "audio.mp4"}}
            new = {"taskId": "net-source-new", "transId": "new", "fileSize": 100,
                   "tag": {"showName": "audio.mp4"}}
            if method == "find":
                result["data"] = [old, new] if submitted else [old]
            if method == "submit":
                submitted = True
                result["data"] = None
            if method == "status":
                result["data"] = [{"taskId": "net-source-new", "transId": "new", "status": 0}]
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.mp4"]})
        row = self.settled(job["id"])["rows"][0]
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["trans_id"], "new")
        self.assertEqual(row["baseline_ids"], ["old"])
        self.assertEqual(row["remote_task_id"], "net-source-new")
        query = next(params for method, params in self.bridge.calls if method == "status")
        self.assertEqual(query["taskIds"], ["net-source-new"])

    def test_unconfirmed_existing_task_does_not_block_new_queue_or_resubmit(self):
        original = self.bridge.call
        def call(method, params):
            if method == "status" and params.get("taskIds") == ["missing-file"]:
                return {"code": "0", "data": []}
            return original(method, params)
        self.bridge.call = call
        job = {"id": "resume-existing", "created": time.time(), "all_pages": False, "status": "paused",
            "rows": [{"id": "old", "source": "https://test.bilivideo.com/old.mp4", "title": "old",
                      "status": "paused", "files": [], "submit_sent": True, "file_id": "missing-file",
                      "submitted_at": time.time(), "remote_file": {"size": 999}},
                     {"id": "new", "source": "https://test.bilivideo.com/new.mp4", "title": "new",
                      "status": "paused", "files": []}]}
        self.service.jobs[job["id"]] = job
        self.service.control({"job_id": job["id"], "action": "resume"})
        try:
            deadline = time.monotonic() + 2
            while self.service.snapshot(job["id"])["rows"][1]["status"] != "done" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.service.snapshot(job["id"])["rows"][1]["status"], "done")
            self.assertEqual(sum(method == "submit" for method, _ in self.bridge.calls), 1)
        finally:
            self.service.control({"job_id": job["id"], "action": "pause"})
            self.settled(job["id"])

    def test_ambiguous_new_records_pause_instead_of_exporting_wrong_file(self):
        original = self.bridge.call
        submitted = False
        def call(method, params):
            nonlocal submitted
            result = original(method, params)
            if method == "find":
                result["data"] = ([{"taskId": "net-" + ident, "transId": ident, "fileSize": 100,
                                   "tag": {"showName": "audio.mp4"}} for ident in ("a", "b")]
                                  if submitted else [])
            if method == "submit":
                submitted = True
                result["data"] = None
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.mp4"]})
        row = self.settled(job["id"])["rows"][0]
        self.assertEqual(row["status"], "paused")
        self.assertNotIn("export", [method for method, _ in self.bridge.calls])

    def test_export_rate_limit_retries_without_resubmitting_transcription(self):
        original = self.bridge.call
        limited = True
        def call(method, params):
            nonlocal limited
            if method == "export" and limited:
                limited = False
                raise SubtitleError("EPO.RequestTooFast request too fast")
            return original(method, params)
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.mp4"]})
        self.assertEqual(self.settled(job["id"])["status"], "done")
        self.assertEqual(sum(method == "submit" for method, _ in self.bridge.calls), 1)

    def test_batch_exports_are_serialized(self):
        original = self.bridge.call
        active = maximum = 0
        def call(method, params):
            nonlocal active, maximum
            if method == "export":
                active += 1
                maximum = max(maximum, active)
                time.sleep(.02)
            result = original(method, params)
            if method == "export_status":
                active -= 1
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["https://test.bilivideo.com/a.mp4", "https://test.bilivideo.com/b.mp4"]})
        self.assertEqual(self.settled(job["id"])["status"], "done")
        self.assertEqual(maximum, 1)

    def test_zip_names_cannot_escape_or_overwrite_each_other(self):
        data = io.BytesIO()
        with ZipFile(data, "w") as archive:
            archive.writestr("../../课程.docx", document())
            archive.writestr("folder/课程.docx", document())
        job = self.service.import_document({"name": "导出.zip", "content": base64.b64encode(data.getvalue()).decode()})
        files = job["rows"][0]["files"]
        self.assertEqual(len(files), 4)
        self.assertEqual(len({item["path"] for item in files}), 4)
        for item in files:
            self.assertTrue(self.service.file(item["path"]).is_relative_to(Path(self.temp.name)))
        with self.assertRaises(SubtitleError):
            self.service.file("../../outside.txt")

    def test_restart_retains_results_without_storing_cookie(self):
        job = self.service.create({"urls": ["https://test.bilivideo.com/audio.m4s"], "cookie": "secret-cookie"})
        self.settled(job["id"])
        state = Path(self.temp.name, "state.json").read_text("utf8")
        self.assertNotIn("secret-cookie", state)
        restored = TingwuService(root=self.temp.name, bridge=self.bridge)
        self.addCleanup(restored.pool.shutdown)
        self.assertEqual(restored.snapshot(job["id"])["status"], "done")

    def test_multiple_imports_share_one_download_bundle(self):
        content = base64.b64encode(document()).decode()
        first = self.service.import_document({"name": "第一份.docx", "content": content})
        job = self.service.import_document({"name": "第二份.docx", "content": content, "job_id": first["id"]})
        self.assertEqual(len(job["rows"]), 2)
        with self.service.bundle(job["id"], "txt") as source, ZipFile(source) as archive:
            self.assertEqual(len(archive.namelist()), 2)

    def test_existing_tingwu_record_only_queries_and_exports(self):
        original = self.bridge.call
        def call(method, params):
            result = original(method, params)
            if method == "status":
                result["data"] = [{"transId": "existingRecord1234", "status": 0}]
            return result
        self.bridge.call = call
        job = self.service.create({"urls": ["https://tingwu.aliyun.com/doc/transcripts/existingRecord1234"]})
        self.assertEqual(self.settled(job["id"])["status"], "done")
        self.assertEqual([method for method, _ in self.bridge.calls], ["status", "export", "export_status"])

    def test_invalid_docx_and_blank_input_are_errors(self):
        with self.assertRaises(SubtitleError):
            self.service.import_document({"name": "broken.docx", "content": base64.b64encode(b"not a zip").decode()})
        with self.assertRaises(SubtitleError):
            self.service.create({"urls": [" "]})

    def test_page_inspection_only_reads_metadata_without_bridge_or_jobs(self):
        metadata = Mock(return_value=(
            {"id": "av123", "url": "https://www.bilibili.com/video/av123?p=2"},
            {"bvid": "BV1xx411c7mD", "title": "多 P 课程", "pages": [
                {"page": 1, "part": "第一课", "duration": 60},
                {"page": 2, "part": "第二课", "duration": 90}]},
            {"page": 2}))
        self.service.video = Mock(bilibili=Mock(metadata=metadata))
        self.bridge.online = False
        result = self.service.inspect_source({"url": "av123?p=2", "cookie": "test-cookie"})
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["pages"][1]["url"], "https://www.bilibili.com/video/BV1xx411c7mD?p=2")
        metadata.assert_called_once_with("av123?p=2", "test-cookie")
        self.assertFalse(self.service.video.inspect.called)
        self.assertEqual(self.service.jobs, {})
        self.assertEqual(self.bridge.calls, [])
        self.assertNotIn("test-cookie", json.dumps(result))

    def test_page_inspection_passthrough_and_invalid_input(self):
        self.service.video = Mock()
        for source in ("https://test.bilivideo.com/audio.mp4",
                       "https://tingwu.aliyun.com/doc/transcripts/existingRecord1234"):
            self.assertEqual(self.service.inspect_source({"url": source})["pages"], [])
        self.assertFalse(self.service.video.bilibili.metadata.called)
        for payload in ({}, {"url": []}, {"url": " "}, {"url": "BV123", "cookie": []}):
            with self.assertRaises(SubtitleError):
                self.service.inspect_source(payload)

    def test_page_inspection_http_route(self):
        import server
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(base + "/api/tingwu/inspect",
            data=b'{"url":"BV123"}', headers={"Content-Type": "application/json"})
        with patch.object(server.TINGWU, "inspect_source", return_value={"pages": []}) as inspect:
            with opener.open(request) as response:
                self.assertEqual(json.load(response), {"pages": []})
            inspect.assert_called_once_with({"url": "BV123"})
        with opener.open(base + "/tingwu-pages.js") as response:
            self.assertIn(b"TingwuPages", response.read())

    def test_bridge_checks_pairing_key_and_routes_reply(self):
        bridge = BrowserBridge()
        with self.assertRaises(SubtitleError):
            bridge.poll("wrong", True)
        bridge.poll(bridge.key, True)
        result = []
        thread = threading.Thread(target=lambda: result.append(bridge.call("status", {"taskIds": ["x"]})))
        thread.start()
        deadline = time.monotonic() + 2
        while not bridge.pending and time.monotonic() < deadline:
            time.sleep(.001)
        request = bridge.poll(bridge.key, True)["requests"][0]
        bridge.result({"key": bridge.key, "id": request["id"], "result": {"code": "0", "data": []}})
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [{"code": "0", "data": []}])

    def test_tingwu_document_navigation_allowed_but_cross_site_api_rejected(self):
        import server
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        for page in ("/?tool=video", "/docx", "/helper", "/transcribe"):
            with opener.open(urllib.request.Request(base + page, headers=headers)) as response:
                self.assertEqual(response.status, 200)
        for path in ("/api/tingwu/setup", "/api/tingwu/status"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(urllib.request.Request(base + path, headers=headers))
            self.assertEqual(error.exception.code, 403)

    def test_long_poll_is_woken_by_new_remote_request(self):
        bridge = BrowserBridge()
        bridge.poll(bridge.key, True)
        results = []
        poller = threading.Thread(target=lambda: results.append(bridge.poll(bridge.key, True, True)))
        poller.start()
        remote = threading.Thread(target=lambda: bridge.call("status", {}))
        remote.start()
        poller.join(2)
        self.assertFalse(poller.is_alive())
        request = results[0]["requests"][0]
        bridge.result({"key": bridge.key, "id": request["id"], "result": {"code": "0", "data": []}})
        remote.join(2)
        self.assertFalse(remote.is_alive())

    def test_pairing_key_survives_service_restart(self):
        path = Path(self.temp.name) / "pairing-key"
        first = BrowserBridge(path)
        self.assertEqual(first.key, BrowserBridge(path).key)

    def test_tingwu_source_uses_complete_mp4_and_rejects_partial_segments(self):
        class Client:
            proxy = ""
            count = 1
            def metadata(self, url, cookie):
                return ({"id": "BV123"}, {"aid": 1, "title": "课程", "pages": [{"page": 1}]},
                        {"cid": 2, "page": 1, "duration": 10})
            def api(self, endpoint, params, cookie):
                self.params = params
                return {"data": {"quality": 16, "durl": [{"url": "https://media.bilivideo.com/file.mp4", "size": 100}]
                                                          * self.count}}
            def require_data(self, response):
                return response["data"]
        client = Client()
        source = VideoClient(client).transcription_source("BV123")
        self.assertTrue(source["cdn_url"].endswith("file.mp4"))
        self.assertEqual(client.params["fnval"], 0)
        client.count = 2
        with self.assertRaises(SubtitleError):
            VideoClient(client).transcription_source("BV123")


if __name__ == "__main__":
    unittest.main()
