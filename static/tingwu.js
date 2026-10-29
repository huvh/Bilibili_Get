/** @brief 独立批量转写页的听悟会话桥接、任务跟踪和自动导出。 */
(() => {
    "use strict";
    if (!document.querySelector("#tingwu-workspace")) return;
    const $ = selector => document.querySelector(selector);
    let connected = false, submitting = false, refreshing = false, serviceFailed = false, lastSnapshot = "";
    let peerKey = new URLSearchParams(location.hash.slice(1)).get("tingwu") || "", peerSeen = 0, peerVersion = 0;
    const sent = new Set();
    const connectionRequired = "尚未连接听悟，转写尚未启动。请先在已登录的听悟网页点击“连接听悟工作台”书签，并保持听悟页和打开的工作台都不关闭。";
    const stage = {queued: "等待解析", resolving: "解析 CDN", remote_parsing: "听悟解析文件",
        submitting: "提交听悟", transcribing: "转写中", exporting: "导出 DOCX",
        converting: "下载并转 TXT", awaiting_confirmation: "等待听悟确认", waiting_connection: "等待重连",
        retry_wait: "等待重试解析", paused: "已暂停", done: "已完成", error: "失败"};
    async function api(path, payload) {
        const response = await fetch(path, {method: payload ? "POST" : "GET",
            headers: payload ? {"Content-Type": "application/json"} : {},
            body: payload ? JSON.stringify(payload) : undefined});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "本地请求失败");
        return result;
    }
    function message(text, error = false) {
        $("#tw-message").textContent = text; $("#tw-message").hidden = !text;
        $("#tw-message").classList.toggle("error", error);
    }
    function showConnectionRequired() {
        message(connectionRequired, true);
        $("#tw-connect-help").open = true;
        $("#tw-start-hint").hidden = false;
    }
    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function downloadLink(text, url) {
        const link = element("a", text, "secondary"); link.href = url; link.download = ""; return link;
    }
    async function retryFailed(job, rowIds) {
        try {
            await window.BiliCookieExtension?.fillIfEmpty($("#tw-cookie"));
            const cookie = $("#tw-cookie").value.trim(); $("#tw-cookie").value = "";
            await api("/api/tingwu/control", {job_id: job.id, action: "retry_failed", row_ids: rowIds, cookie});
            message("失败或中断项已加入重试队列；已完成项保留，已提交项先查询原听悟记录。");
        } catch (error) { message(error.message, true); }
        await refresh();
    }
    function render(jobs) {
        const open = new Set([...$("#tw-jobs").querySelectorAll("details[open]")].map(node => node.dataset.job));
        const firstRender = !$("#tw-jobs").children.length;
        const nodes = jobs.map((job, index) => {
            const box = element("details", undefined, "tw-batch"); box.dataset.job = job.id;
            box.open = open.has(job.id) || (firstRender && index === 0);
            const done = job.rows.filter(row => row.status === "done").length;
            const errors = job.rows.filter(row => row.status === "error").length;
            const waiting = job.rows.filter(row => row.status === "awaiting_confirmation").length;
            box.append(element("summary", `${job.local_import ? "DOCX 转 TXT" : "听悟批量转写"} · ${new Date(job.created * 1000).toLocaleString()} · ${done}/${job.rows.length} 完成${errors ? ` · ${errors} 失败` : ""}${waiting ? ` · ${waiting} 等待确认` : ""}${job.status === "waiting_connection" ? " · 等待听悟重连，连接后自动继续" : ""}`));
            const actions = element("div", undefined, "tw-actions");
            if (done) for (const [kind, label] of [["txt", "打包下载 TXT"], ["docx", "打包下载 DOCX"]])
                actions.append(window.BundleDownload.createButton(label, `/api/tingwu/bundle?job=${job.id}&kind=${kind}`));
            const failed = job.rows.filter(row => ["error", "paused"].includes(row.status));
            if (!job.local_import && failed.length) {
                const retry = element("button", `重试失败/中断项（${failed.length}）`, "secondary");
                retry.disabled = job.status === "pausing";
                retry.onclick = async () => {
                    retry.disabled = true;
                    try { await retryFailed(job, failed.map(row => row.id)); }
                    finally { retry.disabled = job.status === "pausing"; }
                };
                actions.append(retry);
            }
            if (!job.local_import && job.status !== "done") {
                const running = ["running", "pausing", "waiting_connection"].includes(job.status);
                const button = element("button", running ? "暂停本地跟踪" : "继续跟踪 / 重试导出", "secondary");
                button.disabled = job.status === "pausing";
                button.onclick = async () => {
                    button.disabled = true;
                    try { await api("/api/tingwu/control", {job_id: job.id, action: running ? "pause" : "resume"}); }
                    catch (error) { message(error.message, true); }
                    void refresh();
                };
                actions.append(button);
            }
            const cdns = job.rows.filter(row => row.cdn_url).map(row => row.cdn_url);
            if (cdns.length) {
                const copy = element("button", "复制本批 CDN 直链", "secondary");
                copy.onclick = async () => {
                    try { await navigator.clipboard.writeText(cdns.join("\n")); message("CDN 直链已复制；失效后需重新解析。"); }
                    catch { message("无法写入剪贴板，请从任务详情选择 CDN 地址复制。", true); }
                };
                actions.append(copy);
            }
            box.append(actions);
            if (job.error) box.append(element("p", job.error, "tw-error"));
            for (const row of job.rows) {
                const item = element("article", undefined, "tw-row");
                const head = element("div", undefined, "tw-row-head");
                head.append(element("strong", row.title), element("span", stage[row.status] || row.status, "tw-stage " + row.status));
                item.append(head, element("p", row.detail));
                if (row.trans_id) {
                    const link = element("a", "在听悟查看", "text-button");
                    link.href = "https://tingwu.aliyun.com/doc/transcripts/" + encodeURIComponent(row.trans_id);
                    link.target = "_blank"; link.rel = "noopener"; item.append(link);
                }
                if (row.cdn_url) {
                    const details = element("details"); details.append(element("summary", "CDN 直链"));
                    const url = element("textarea"); url.value = row.cdn_url; url.readOnly = true; url.rows = 2;
                    url.setAttribute("aria-label", row.title + " CDN 直链"); details.append(url); item.append(details);
                }
                const files = element("div", undefined, "tw-actions");
                if (["error", "paused"].includes(row.status)) {
                    const retry = element("button", "重试此项", "secondary");
                    retry.disabled = job.status === "pausing";
                    retry.onclick = async () => {
                        retry.disabled = true;
                        try { await retryFailed(job, [row.id]); }
                        finally { retry.disabled = job.status === "pausing"; }
                    };
                    files.append(retry);
                }
                for (const file of row.files || []) files.append(downloadLink(file.kind.toUpperCase() + " · " + file.name, file.url));
                item.append(files); box.append(item);
            }
            return box;
        });
        $("#tw-jobs").replaceChildren(...nodes); $("#tw-empty").hidden = jobs.length > 0;
    }
    async function refresh() {
        if (refreshing) return;
        refreshing = true;
        try {
            const data = await api("/api/tingwu/status"); connected = data.connected;
            if (serviceFailed) {message("本地服务已恢复连接。"); serviceFailed = false;}
            $("#tw-connection").textContent = data.bridge_update ? "连接书签需要更新" :
                (connected ? "● 听悟网页已连接" : "○ 尚未连接听悟");
            $("#tw-connection").classList.toggle("online", connected);
            // 未连接时仍允许点击，让用户立即看到原因；只在提交过程中禁用，防止重复操作。
            $("#tw-start").disabled = submitting;
            $("#tw-start-hint").hidden = connected;
            if (connected && $("#tw-message").textContent === connectionRequired) message("");
            const snapshot = JSON.stringify([connected, data.jobs.filter(job => !job.local_import)]);
            if (snapshot !== lastSnapshot) {render(data.jobs.filter(job => !job.local_import)); lastSnapshot = snapshot;}
        } catch (error) { serviceFailed = true; message("本地服务连接中断：" + error.message, true); }
        finally { refreshing = false; }
    }
    window.addEventListener("message", async event => {
        if (!peerKey || event.origin !== "https://tingwu.aliyun.com" || event.source !== window.opener || event.data?.key !== peerKey) return;
        if (event.data.type === "tingwu-ready") {peerSeen = Date.now(); peerVersion = event.data.version || 0;}
        if (event.data.type === "tingwu-result" && sent.has(event.data.id)) {
            try {
                await api("/api/tingwu/bridge/result", {key: peerKey, id: event.data.id, result: event.data.result, error: event.data.error});
                sent.delete(event.data.id);
            } catch (error) { message(error.message, true); }
        }
    });
    async function pump() {
        if (!peerKey || !window.opener) return;
        // 使用连续网络长轮询，避免后台标签页的定时器节流让长时间转写断开。
        while (peerKey && window.opener && !window.opener.closed) {
            try {
                window.opener.postMessage({type: "tingwu-ping", key: peerKey}, "https://tingwu.aliyun.com");
                const ready = Date.now() - peerSeen < 30000;
                const data = await api("/api/tingwu/bridge/poll", {key: peerKey, ready, wait: true, version: peerVersion});
                for (const request of data.requests) {
                    sent.add(request.id);
                    window.opener.postMessage({type: "tingwu-request", key: peerKey, ...request}, "https://tingwu.aliyun.com");
                }
            } catch {
                message("听悟连接中断，正在重连；请保持配对的两个页面打开。", true);
                await new Promise(resolve => setTimeout(resolve, 1500));
            }
        }
    }
    $("#tw-bookmark").onclick = event => {
        event.preventDefault();
        message("将“连接听悟工作台”拖到浏览器书签栏，然后在已登录的听悟网页点击这个书签。连接后会打开批量转写页面。");
    };
    $("#tw-start").onclick = event => {
        // 点击拦截放在表单校验之前，即使还没输入链接也能解释未连接的原因。
        if (!connected) {event.preventDefault(); showConnectionRequired();}
    };
    $("#tw-form").onsubmit = async event => {
        event.preventDefault(); if (submitting) return;
        if (!connected) {showConnectionRequired(); return;}
        submitting = true; $("#tw-start").disabled = true;
        try {
            // 连接状态可能在上一次定时刷新后改变，开始解析前再次核对。
            const status = await api("/api/tingwu/status"); connected = status.connected;
            if (!connected) {showConnectionRequired(); return;}
            const urls = await window.TingwuPages.prepare();
            if (!urls) return;
            await window.BiliCookieExtension?.fillIfEmpty($("#tw-cookie"));
            const cookie = $("#tw-cookie").value.trim(); $("#tw-cookie").value = "";
            await api("/api/tingwu/start", {urls, cookie, all_pages: false});
            message("批次已启动。请保持听悟页和连接助手打开，任务与结果会保存在本机。");
        } catch (error) { message(error.message, true); }
        finally { submitting = false; $("#tw-start").disabled = false; await refresh(); }
    };
    (async () => {
        try {
            const [config, source] = await Promise.all([api("/api/tingwu/setup"), fetch("/tingwu-bridge.js").then(response => response.text())]);
            $("#tw-bookmark").href = "javascript:" + encodeURIComponent(`void (${source})(${JSON.stringify(location.origin)},${JSON.stringify(config.key)});`);
            if (peerKey && window.opener) {
                peerKey = config.key;
                window.opener.postMessage({type: "tingwu-bind", key: peerKey}, "https://tingwu.aliyun.com");
            }
        } catch (error) { message(error.message, true); }
        await refresh();
        void pump();
    })();
    setInterval(() => void refresh(), 2500);
})();
