"""@brief B 站网络适配：链接解析、分 P、字幕轨道和白名单下载。"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from subtitles import SubtitleError

DEFAULT_PROXY = "http://127.0.0.1:7892"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
MIXIN_ORDER = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,
               29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,
               25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]


def is_subtitle_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and host.endswith(".hdslb.com")
                and parsed.path.startswith(("/bfs/subtitle/", "/bfs/ai_subtitle/")))
    except ValueError:
        return False


def validate_network_url(url):
    """限制重定向和下载目标，避免把本地服务变成任意 URL 代理。"""
    try:
        parsed = urllib.parse.urlsplit(url)
        allowed = parsed.hostname in ("api.bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv")
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not (allowed or is_subtitle_url(url))):
            raise ValueError()
    except ValueError:
        raise SubtitleError("仅支持 B 站链接和 hdslb.com 的字幕直链。", "invalid_host") from None


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        validate_network_url(new_url)
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        if redirected and urllib.parse.urlsplit(new_url).hostname != urllib.parse.urlsplit(request.full_url).hostname:
            redirected.remove_header("Cookie")
        return redirected


class BilibiliClient:
    def __init__(self, proxy=None):
        self.proxy = proxy if proxy is not None else os.environ.get("BILI_PROXY", DEFAULT_PROXY)
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy} if self.proxy else {}),
            SafeRedirect())

    def read(self, url, cookie=""):
        validate_network_url(url)
        headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
        # Cookie 只用于 API，绝不发送给短链或字幕 CDN。
        if cookie and urllib.parse.urlsplit(url).hostname == "api.bilibili.com":
            if len(cookie) > 16000 or "\n" in cookie or "\r" in cookie:
                raise SubtitleError("Cookie 格式无效，请粘贴单行 Cookie 请求头。")
            headers["Cookie"] = cookie
        try:
            with self.opener.open(urllib.request.Request(url, headers=headers), timeout=25) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise SubtitleError("远端字幕文件超过 8 MB。", "too_large", 413)
                return body, response.url
        except urllib.error.HTTPError as error:
            raise SubtitleError(f"B 站返回 HTTP {error.code}，链接可能已过期或被风控。",
                                "upstream_http", 502) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SubtitleError("无法连接 B 站，请检查本机 127.0.0.1:7892 代理或 BILI_PROXY 设置。",
                                "network_error", 502) from None

    def api(self, path, params, cookie=""):
        body, _ = self.read("https://api.bilibili.com" + path + "?" + urllib.parse.urlencode(params), cookie)
        try:
            result = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise SubtitleError("B 站返回了非 JSON 响应。", "upstream_format", 502) from None
        return result

    def resolve(self, text):
        """接受 BV/av/ep 标识、分享文本、标准链接和 b23.tv 短链。"""
        text = text.strip()
        if re.fullmatch(r"BV[A-Za-z0-9]{10}|av[1-9]\d*|ep[1-9]\d*", text):
            kind = "bangumi/play" if text.startswith("ep") else "video"
            text = f"https://www.bilibili.com/{kind}/{text}"
        found = re.search(r"https?://[^\s<>\"“”]+", text)
        if not found:
            raise SubtitleError("请输入有效的 BV/av/ep 标识或 B 站视频链接。")
        url = found.group(0).rstrip("。，！)）")
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname not in ("www.bilibili.com", "bilibili.com", "m.bilibili.com", "b23.tv"):
            raise SubtitleError("视频地址必须来自 bilibili.com 或 b23.tv。", "invalid_host")
        # 标准 B 站地址可以安全升级为 HTTPS；禁止携带端口或用户信息。
        if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise SubtitleError("视频地址不能包含账号或自定义端口。")
        host = "www.bilibili.com" if parsed.hostname == "bilibili.com" else parsed.hostname
        url = urllib.parse.urlunsplit(("https", host, parsed.path, parsed.query, ""))
        if host == "b23.tv":
            _, url = self.read(url)
            # 重定向终点也必须是可解析的视频地址。
            if urllib.parse.urlsplit(url).hostname == "b23.tv":
                raise SubtitleError("短链没有跳转到视频，请粘贴完整 B 站链接。", "invalid_short_link")
            return self.resolve(url)
        parsed = urllib.parse.urlsplit(url)
        match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10}|av[1-9]\d*)/?", parsed.path)
        episode = re.fullmatch(r"/bangumi/play/(ep[1-9]\d*)/?", parsed.path)
        if not match and not episode:
            raise SubtitleError("仅支持具体视频或番剧单集链接。")
        query = urllib.parse.parse_qs(parsed.query)
        page_text = query.get("p", ["1"])[0]
        if not re.fullmatch(r"[1-9]\d{0,5}", page_text):
            raise SubtitleError("分 P 参数必须是正整数。")
        return {"id": (match or episode).group(1), "page": int(page_text), "url": url}

    @staticmethod
    def require_data(response):
        code = response.get("code")
        if code != 0:
            messages = {-101: "此接口需要登录。", -404: "视频不存在或已下架。", -403: "B 站拒绝了请求。",
                        -352: "B 站触发了访问风控，请稍后重试。"}
            raise SubtitleError(messages.get(code, f"B 站接口错误（{code}）。"), "bilibili_error", 502)
        return response.get("data") or response.get("result") or {}

    def metadata(self, text, cookie=""):
        """解析视频元信息与当前分 P，供字幕和视频下载共同使用。"""
        source = self.resolve(text)
        identifier = source["id"]
        if identifier.startswith("ep"):
            data = self.require_data(self.api("/pgc/view/web/season", {"ep_id": identifier[2:]}, cookie))
            episode = next((ep for ep in data.get("episodes", []) if str(ep.get("id")) == identifier[2:]), None)
            if not episode:
                raise SubtitleError("此番剧单集没有可用的视频信息。", "no_episode", 404)
            view = {"title": data.get("title", "番剧"), "bvid": episode.get("bvid"), "aid": episode["aid"],
                    "pic": episode.get("cover", ""), "desc": data.get("evaluate", ""),
                    "pages": [{"cid": episode["cid"], "page": 1, "part": episode.get("long_title", "单集"),
                               "duration": episode.get("duration", 0) / 1000}]}
        else:
            key = "bvid" if identifier.startswith("BV") else "aid"
            value = identifier if key == "bvid" else identifier[2:]
            view = self.require_data(self.api("/x/web-interface/view", {key: value}, cookie))
        pages = view.get("pages", [])
        page = next((page for page in pages if page["page"] == source["page"]), None)
        if page is None:
            raise SubtitleError(f"分 P 超出范围，该视频共有 {len(pages)} P。", "invalid_page")
        return source, view, page

    def inspect(self, text, cookie=""):
        source, view, page = self.metadata(text, cookie)
        identifier = source["id"]
        params = {"cid": page["cid"], "aid": view["aid"]}
        player_response = self.api("/x/player/v2", params, cookie)
        # 常规端点拒绝签名时才请求 nav 获取当次 WBI 公钥，不硬编码过期键。
        if player_response.get("code") in (-400, -403):
            nav_response = self.api("/x/web-interface/nav", {}, cookie)
            wbi = nav_response.get("data", {}).get("wbi_img", {})
            if wbi.get("img_url") and wbi.get("sub_url"):
                signed = sign_wbi(params, wbi["img_url"], wbi["sub_url"])
                player_response = self.api("/x/player/wbi/v2", signed, cookie)
        player = self.require_data(player_response)
        rows = player.get("subtitle", {}).get("subtitles", [])
        tracks = []
        for index, row in enumerate(rows):
            url = row.get("subtitle_url", "")
            url = "https:" + url if url.startswith("//") else url
            if not url:
                continue
            if url.startswith("http://"):
                url = "https://" + url[7:]
            if not is_subtitle_url(url):
                continue
            tracks.append({"language_tag": row.get("lan", "und"),
                           "language_name": row.get("lan_doc", "未知语言"), "url": url,
                           "automatic": bool(row.get("ai_type")) or row.get("lan", "").startswith("ai-"),
                           "source_index": index})
        if not tracks:
            if player.get("need_login_subtitle"):
                raise SubtitleError("B 站要求登录后读取此视频的字幕。请展开登录选项提供自己的 Cookie，"
                                    "或用登录导入助手取得 BCC 文件后导入。", "login_required", 401)
            raise SubtitleError("当前分 P 没有可读取的字幕；本工具不执行语音转写。", "no_subtitles", 404)
        return {"title": view["title"], "part": page.get("part", ""), "id": view.get("bvid") or identifier,
                "page": page["page"], "pages": [{"page": p["page"], "part": p["part"]} for p in pages],
                "duration": page.get("duration", 0), "source_url": source["url"], "tracks": tracks}

    def subtitle(self, url):
        if not is_subtitle_url(url):
            raise SubtitleError("这不是支持的 B 站字幕直链。", "invalid_host")
        body, _ = self.read(url)
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise SubtitleError("字幕响应不是 BCC JSON；链接可能已过期，请重新提取。",
                                "invalid_bcc", 422) from None


def sign_wbi(params, image_url, sub_url, now=None):
    """动态计算 WBI 查询签名；不涉及用户登录密钥。"""
    image_key = image_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    source_key = image_key + sub_key
    if len(source_key) < 64:
        raise SubtitleError("WBI 公钥格式发生变化。", "wbi_changed", 502)
    mixin = "".join(source_key[index] for index in MIXIN_ORDER)[:32]
    clean = {key: re.sub(r"[!'()*]", "", str(value)) for key, value in params.items()}
    clean["wts"] = str(int(time.time() if now is None else now))
    query = urllib.parse.urlencode(sorted(clean.items()))
    clean["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return clean
