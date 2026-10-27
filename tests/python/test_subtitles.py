"""转换边界、分 P / 登录分支、缓存过期和 HTTP 下载的针对性验证。"""
import json
from html.parser import HTMLParser
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request

from bilibili import BilibiliClient, is_subtitle_url, sign_wbi
import server
from subtitles import SubtitleError, export_subtitle, parse_bcc, safe_filename, timestamp

SAMPLE = {"body": [{"from": 59.9996, "to": 62, "content": "时间进位 & <标记>"},
                   {"from": 0, "to": 1.5, "content": "第一句"},
                   {"from": 2, "to": 3, "content": "第一句"},
                   {"from": -1, "to": 1, "content": "无效"},
                   {"from": "nan", "to": 2, "content": "非数值"},
                   {"from": True, "to": 2, "content": "布尔值"},
                   {"from": 1e308, "to": 1e308, "content": "过大数值"}]}


class SubtitleTests(unittest.TestCase):
    def test_sort_filter_repeat_and_rounding(self):
        cues = parse_bcc(SAMPLE)
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[2].start_ms, 60000)
        self.assertEqual(timestamp(cues[2].start_ms), "00:01:00,000")
        self.assertEqual(cues[0].text, cues[1].text)

    def test_formats(self):
        srt, _ = export_subtitle(SAMPLE, "srt")
        self.assertIn(b"00:01:00,000 --> 00:01:02,000", srt)
        self.assertIn(b"&amp; &lt;", srt)
        vtt, _ = export_subtitle(SAMPLE, "vtt")
        self.assertTrue(vtt.startswith(b"WEBVTT\n\n"))
        self.assertIn(b"00:01:00.000", vtt)
        plain, _ = export_subtitle(SAMPLE, "txt")
        self.assertEqual(plain.decode().count("第一句"), 2)
        raw, _ = export_subtitle(SAMPLE, "json")
        self.assertEqual(json.loads(raw), SAMPLE)

    def test_invalid_document(self):
        for invalid in ({}, {"body": []}, {"body": [None]}, None):
            with self.subTest(invalid=invalid), self.assertRaises(SubtitleError):
                parse_bcc(invalid)

    def test_names_unique_for_duplicate_language(self):
        a = safe_filename('a:b/"c', "zh-CN", 0, "srt")
        self.assertEqual(a, "abc.zh-CN.1.srt")
        self.assertNotEqual(a, safe_filename('a:b/"c', "zh-CN", 1, "srt"))

    def test_link_parsing_and_ssrf(self):
        client = BilibiliClient(proxy="")
        self.assertEqual(client.resolve("https://www.bilibili.com/video/BV1th411z7sn/?p=39")["page"], 39)
        self.assertEqual(client.resolve("av206940155")["id"], "av206940155")
        self.assertEqual(client.resolve("ep1234")["id"], "ep1234")
        for url in ("https://bilibili.com.evil.test/video/BV1th411z7sn", "http://127.0.0.1/",
                    "https://www.bilibili.com/video/BV1th411z7sn?p=0"):
            with self.subTest(url=url), self.assertRaises(SubtitleError):
                client.resolve(url)
        self.assertFalse(is_subtitle_url("https://aisubtitle.hdslb.com.evil.test/bfs/subtitle/1"))
        self.assertFalse(is_subtitle_url("https://user@aisubtitle.hdslb.com/bfs/subtitle/1"))

    def test_wbi_fixed_input_regression(self):
        signed = sign_wbi({"foo": "114", "bar": "514", "baz": "1919810"},
                          "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                          "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png", 1702204169)
        self.assertEqual(signed["w_rid"], "6149fdadf571698ca7e6a567265cd0ee")

    def test_page_selected_before_player_request(self):
        class FakeClient(BilibiliClient):
            def api(self, path, params, cookie=""):
                if path == "/x/web-interface/view":
                    return {"code": 0, "data": {"title": "测试", "bvid": "BV1th411z7sn", "aid": 1,
                        "pages": [{"page": 1, "cid": 11, "part": "一"}, {"page": 2, "cid": 22, "part": "二"}]}}
                self.last_cid = params["cid"]
                return {"code": 0, "data": {"need_login_subtitle": True, "subtitle": {"subtitles": []}}}
        client = FakeClient(proxy="")
        with self.assertRaises(SubtitleError) as context:
            client.inspect("https://www.bilibili.com/video/BV1th411z7sn?p=2")
        self.assertEqual(client.last_cid, 22)
        self.assertEqual(context.exception.code, "login_required")

    def test_ttl(self):
        now = [0]
        service = server.SubtitleService(clock=lambda: now[0])
        metadata = service.import_document({"document": SAMPLE})
        service.load(metadata["job_id"], 0)
        now[0] = 1501
        with self.assertRaises(SubtitleError) as context:
            service.load(metadata["job_id"], 0)
        self.assertEqual(context.exception.status, 410)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.httpd.server_port)
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def test_bcc_all_downloads(self):
        sample = SAMPLE
        request = urllib.request.Request(self.base + "/api/import", data=json.dumps({
            "document": sample, "title": "合成字幕验证"}).encode(), headers={"Content-Type": "application/json"})
        with self.opener.open(request) as response:
            metadata = json.load(response)
        with self.opener.open(self.base + "/api/load?job=" + metadata["job_id"]) as response:
            loaded = json.load(response)
        self.assertEqual(len(loaded["cues"]), len(parse_bcc(sample)))
        for format_name in ("txt", "timestamped", "srt", "vtt", "json"):
            with self.subTest(format=format_name):
                url = self.base + "/api/download?job=" + metadata["job_id"] + "&format=" + format_name
                with self.opener.open(url) as response:
                    self.assertIn("attachment;", response.headers["Content-Disposition"])
                    body = response.read()
                expected, _ = export_subtitle(sample, format_name)
                self.assertEqual(body, expected)

    def test_cross_origin_and_static_escape_rejected(self):
        request = urllib.request.Request(self.base + "/api/import", data=b"{}", headers={
            "Origin": "https://example.com", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.opener.open(request)
        self.assertEqual(context.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.opener.open(self.base + "/evidence/real-subtitle.json")
        self.assertEqual(context.exception.code, 404)

    def test_standalone_helper_route(self):
        with self.opener.open(self.base + "/helper") as response:
            page = response.read()
        standalone = Path(__file__).resolve().parents[2] / "helper" / "dist" / "B站字幕转TXT助手.html"
        self.assertEqual(page, standalone.read_bytes())
        self.assertIn("选择文件，自动下载 TXT".encode(), page)

    def test_navigation_links_and_script_independent_initial_page(self):
        class ElementsById(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = {}
                self.nav_links = []
                self.in_navigation = False

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "nav" and attrs.get("aria-label") == "工作台功能":
                    self.in_navigation = True
                if tag == "a" and self.in_navigation:
                    self.nav_links.append(attrs)
                if "id" in attrs:
                    self.elements[attrs["id"]] = (tag, attrs)

            def handle_endtag(self, tag):
                if tag == "nav":
                    self.in_navigation = False

        for suffix, selected in (("/", "subtitle"), ("/?tool=video", "video"), ("/helper", "helper"),
                                 ("/docx", "docx"), ("/transcribe", "transcribe")):
            with self.subTest(page=suffix), self.opener.open(self.base + suffix) as response:
                parser = ElementsById()
                parser.feed(response.read().decode("utf-8"))
                nodes = parser.elements
                self.assertEqual([link["id"] for link in parser.nav_links],
                                 ["video-mode", "transcribe-mode", "subtitle-mode", "helper-mode", "docx-mode"])
                self.assertEqual([link["id"] for link in parser.nav_links if link.get("aria-current") == "page"],
                                 [selected + "-mode"])
                self.assertTrue(nodes["video-mode"][1]["href"].endswith("/?tool=video"))
                self.assertTrue(nodes["subtitle-mode"][1]["href"].endswith("/"))
                self.assertTrue(nodes["helper-mode"][1]["href"].endswith("/helper"))
                self.assertTrue(nodes["docx-mode"][1]["href"].endswith("/docx"))
                self.assertTrue(nodes["transcribe-mode"][1]["href"].endswith("/transcribe"))
                if selected == "transcribe":
                    self.assertIn("tingwu-workspace", nodes)
                    self.assertNotIn("video-workspace", nodes)
                if selected in ("subtitle", "video"):
                    self.assertNotIn("tingwu-workspace", nodes)
                    self.assertEqual("hidden" in nodes["subtitle-workspace"][1], selected == "video")
                    self.assertEqual("hidden" in nodes["video-workspace"][1], selected != "video")
                    self.assertNotIn("helper-bookmark", nodes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
