/** @brief 运行于用户点击书签的页面：取得 JSON 后直接生成 TXT，不落地中间 JSON。 */
void (async () => {
    "use strict";
    const tools = createSubtitleTools();
    const hostId = "bilibili-subtitle-txt-helper";
    if (document.getElementById(hostId)) return;
    const host = document.createElement("div");
    host.id = hostId;
    const shadow = host.attachShadow({ mode: "open" });
    const sheet = document.createElement("style");
    sheet.textContent = `:host{all:initial;position:fixed;right:24px;top:24px;z-index:2147483647;font:14px/1.7 "Microsoft YaHei",sans-serif;color:#213c30}*{box-sizing:border-box}.panel{width:min(370px,calc(100vw - 32px));padding:22px;background:white;border:1px solid #d9e5dc;border-radius:14px;box-shadow:0 12px 60px #0003}h2{font-size:17px;margin:0 22px 12px 0}p{margin:10px 0;white-space:pre-wrap;overflow-wrap:anywhere}small{color:#738479}.close{position:absolute;right:15px;top:12px;border:0;background:none;font-size:22px;color:#738479;cursor:pointer}button,select,a{font:inherit}select{width:100%;padding:9px;border:1px solid #c6d9cd;border-radius:6px;margin:8px 0}button,a.download{display:inline-block;margin-top:10px;border:0;border-radius:7px;padding:9px 16px;background:#086b57;color:white;cursor:pointer;text-decoration:none}a.download{background:#eaf4ed;color:#086b57;font-size:12px}`;
    const panel = document.createElement("section");
    panel.className = "panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "B 站字幕转 TXT 助手");
    const heading = document.createElement("h2");
    heading.textContent = "B 站字幕 → TXT";
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.textContent = "正在读取字幕…";
    const hint = document.createElement("small");
    hint.textContent = "使用当前 B 站登录状态，自动转换并下载 TXT。";
    const close = document.createElement("button");
    close.className = "close";
    close.textContent = "×";
    close.setAttribute("aria-label", "关闭字幕助手");
    panel.append(close, heading, status, hint);
    shadow.append(sheet, panel);
    document.documentElement.append(host);
    let closed = false;
    let objectUrl;
    const pending = new Set();
    const release = () => { if (objectUrl) URL.revokeObjectURL(objectUrl); objectUrl = undefined; };
    close.onclick = () => {
        closed = true;
        for (const controller of pending) controller.abort();
        release();
        host.remove();
    };

    async function getJson(url, credentials = "omit") {
        const controller = new AbortController();
        pending.add(controller);
        const timeout = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(url, { credentials, signal: controller.signal });
            if (!response.ok) throw new Error(`读取失败（HTTP ${response.status}），请刷新页面后重试。`);
            return await response.json();
        } catch (error) {
            if (error.name === "AbortError") throw new Error("读取超时，请稍后重试。");
            throw error;
        } finally {
            clearTimeout(timeout);
            pending.delete(controller);
        }
    }

    async function api(path, params) {
        const result = await getJson("https://api.bilibili.com" + path + "?" + new URLSearchParams(params), "include");
        if (result.code !== 0) {
            const messages = { "-101": "请先在 B 站登录后重试。", "-352": "B 站触发访问风控，请稍后重试。" };
            throw new Error(messages[result.code] || `B 站接口错误 ${result.code}，请刷新视频页重试。`);
        }
        return result.data;
    }

    function downloadTxt(input, title, language) {
        if (closed) return;
        const converted = tools.convert(input);
        release();
        // 加 UTF-8 BOM，便于 Windows 记事本识别中文编码。
        objectUrl = URL.createObjectURL(new Blob(["\uFEFF", converted.text], { type: "text/plain;charset=utf-8" }));
        const link = document.createElement("a");
        link.className = "download";
        link.href = objectUrl;
        link.download = tools.filename(title, language);
        link.textContent = "再次下载 TXT";
        panel.append(link);
        link.click();
        status.textContent = `已将 ${converted.count} 条字幕转为 TXT，并请求浏览器下载。\n${link.download}`;
        hint.textContent = "若未开始下载，请点击“再次下载 TXT”。不需要再导入 JSON。";
        // 链接随面板关闭释放，允许浏览器阻止自动下载时手动重试。
    }

    try {
        if (location.hostname.endsWith(".hdslb.com")) {
            const url = tools.subtitleUrl(location.href);
            const input = await getJson(url);
            downloadTxt(input, "B站字幕", "");
            return;
        }
        if (!/^(www\.|m\.)?bilibili\.com$/.test(location.hostname)) {
            throw new Error("请在已登录的 B 站视频页点击此书签；已有 JSON 文件可用独立页面自动转 TXT。");
        }
        const match = location.pathname.match(/^\/video\/(BV[A-Za-z0-9]{10}|av[1-9]\d*)\/?$/);
        if (!match) throw new Error("请打开具体的 BV / av 视频页面后使用助手。");
        const identifier = match[1];
        const pageNumber = Number(new URLSearchParams(location.search).get("p") || 1);
        if (!Number.isInteger(pageNumber) || pageNumber < 1) throw new Error("分 P 参数无效。");
        const view = await api("/x/web-interface/view", identifier.startsWith("BV")
            ? { bvid: identifier } : { aid: identifier.slice(2) });
        const page = view.pages?.find((item) => item.page === pageNumber);
        if (!page) throw new Error("找不到当前分 P，请刷新视频页后重试。");
        const player = await api("/x/player/v2", { aid: view.aid, cid: page.cid });
        const tracks = (player.subtitle?.subtitles || []).filter((track) => track.subtitle_url);
        if (!tracks.length) throw new Error(player.need_login_subtitle
            ? "此字幕需要登录。请先在当前浏览器登录 B 站后重试。" : "当前分 P 没有可读取的字幕。");
        const title = view.title + (view.pages.length > 1 ? ` P${pageNumber} ${page.part}` : "");
        async function extract(track) {
            status.textContent = "正在获取字幕 JSON 并自动转换 TXT…";
            const input = await getJson(tools.subtitleUrl(track.subtitle_url));
            downloadTxt(input, title, track.lan || "");
        }
        if (tracks.length === 1) {
            await extract(tracks[0]);
        } else {
            status.textContent = "找到多条字幕，选择需要的语言后即可下载 TXT。";
            const select = document.createElement("select");
            select.setAttribute("aria-label", "字幕语言");
            tracks.forEach((track, index) => {
                const option = document.createElement("option");
                option.value = String(index);
                option.textContent = `${track.lan_doc || track.lan || "未知语言"} · 轨道 ${index + 1}`;
                select.append(option);
            });
            const button = document.createElement("button");
            button.textContent = "获取并下载 TXT";
            panel.append(select, button);
            button.onclick = async () => {
                button.disabled = true;
                select.disabled = true;
                try { await extract(tracks[Number(select.value)]); select.remove(); button.remove(); }
                catch (error) { if (!closed) status.textContent = error.message; button.disabled = false; select.disabled = false; }
            };
        }
    } catch (error) {
        if (!closed) {
            status.textContent = error instanceof TypeError
                ? "浏览器未能读取字幕。请检查网络，或刷新 B 站页面后重试。" : error.message;
            hint.textContent = "关闭面板后可再次点击书签。";
        }
    }
})();
