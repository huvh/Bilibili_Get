/** @file video.js @brief 视频解析、清晰度选择及后台任务的独立界面逻辑。 */
(() => {
    "use strict";
    // 旧听悟连接书签仍打开视频页时，沿用同一窗口和 opener 转入新的独立页面。
    const legacyBridgeKey = new URLSearchParams(location.hash.slice(1)).get("tingwu");
    if (legacyBridgeKey && /^[a-f0-9]{48}$/.test(legacyBridgeKey)) {
        location.replace("/transcribe" + location.hash);
        return;
    }
    const $ = (selector) => document.querySelector(selector);
    let metadata = null;
    let activeJob = null;
    let parsing = false;
    let preparing = false;
    let pollGeneration = 0;
    const jobStorageKey = "bili-video-last-job";
    const stageNames = { queued: "排队中", downloading: "正在下载音视频", merging: "正在合并 MP4",
        canceling: "正在取消…", canceled: "下载已取消", done: "MP4 已准备完成", error: "下载失败" };

    function rememberJob(id) {
        try {
            if (id) sessionStorage.setItem(jobStorageKey, id);
            else sessionStorage.removeItem(jobStorageKey);
        } catch { /* 浏览器禁用会话存储时仍可正常下载。 */ }
    }

    function size(bytes) {
        if (!bytes) return "大小待确认";
        return bytes >= 1024 ** 3 ? (bytes / 1024 ** 3).toFixed(2) + " GB" : (bytes / 1024 ** 2).toFixed(1) + " MB";
    }

    function message(text, error = false) {
        $("#video-message").textContent = text;
        $("#video-message").hidden = !text;
        $("#video-message").classList.toggle("error", error);
    }

    function controls() {
        const busy = parsing || preparing || activeJob !== null;
        $("#video-inspect").disabled = busy;
        $("#video-page").disabled = busy;
        $("#video-start").disabled = busy || !metadata || !metadata.ffmpeg_available;
        $("#video-format").disabled = busy;
    }

    async function api(path, payload) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 95000);
        try {
            const response = await fetch(path, { method: payload ? "POST" : "GET",
                headers: payload ? { "Content-Type": "application/json" } : {},
                body: payload ? JSON.stringify(payload) : undefined, signal: controller.signal });
            const result = await response.json();
            if (!response.ok) {
                const error = new Error(result.error || "请求失败，请重试。");
                error.status = response.status;
                throw error;
            }
            return result;
        } catch (error) {
            if (error.name === "AbortError") throw new Error("请求超时，请稍后重试。");
            throw error;
        } finally { clearTimeout(timeout); }
    }

    function formatNote() {
        const format = metadata?.formats.find((item) => item.id === $("#video-format").value);
        if (!format) return;
        $("#video-format-note").textContent = `${format.codec} · ${format.has_audio ? "包含声音" : "源视频没有可用音轨"} · 预计 ${size(format.estimated_bytes)}（实际大小以下载为准）`;
        $("#video-start").textContent = format.has_audio ? "下载 MP4（含声音） ↓" : "下载无音轨 MP4 ↓";
    }

    async function inspect(url) {
        if (parsing || preparing || activeJob) return;
        parsing = true;
        controls();
        message("正在读取视频信息和实际可用的清晰度…");
        await window.BiliCookieExtension?.fillIfEmpty($("#video-cookie"));
        const cookie = $("#video-cookie").value.trim();
        $("#video-cookie").value = "";
        metadata = null;
        $("#video-info").hidden = true;
        try {
            metadata = await api("/api/video/inspect", { url, cookie });
            $("#video-name").textContent = metadata.title;
            $("#video-description").textContent = [metadata.part, `${Math.floor(metadata.duration / 60)} 分 ${Math.round(metadata.duration % 60)} 秒`, `${metadata.formats.length} 个实际可用档位`].filter(Boolean).join(" · ");
            $("#video-format").replaceChildren(...metadata.formats.map((format) =>
                new Option(`${format.label} · ${format.has_audio ? "含声音" : "无音轨"}`, format.id)));
            $("#video-page-wrap").hidden = metadata.pages.length < 2;
            $("#video-page").replaceChildren(...metadata.pages.map((page) => new Option(`P${page.page} ${page.part}`, String(page.page))));
            $("#video-page").value = String(metadata.page);
            $("#video-info").hidden = false;
            formatNote();
            message(metadata.ffmpeg_available ? "解析完成。选择清晰度后即可下载，音视频会自动合并。" : "缺少视频合并依赖，请按 README 安装 FFmpeg。", !metadata.ffmpeg_available);
        } catch (error) { message(error.message, true); }
        finally { parsing = false; controls(); }
    }

    function renderJob(job) {
        $("#video-job").hidden = false;
        $("#video-job-name").textContent = job.filename;
        $("#video-job-stage").textContent = stageNames[job.status] || job.status;
        $("#video-progress").value = job.progress;
        $("#video-job-detail").textContent = job.error || `${size(job.bytes)} / 预计 ${size(job.total_bytes)} · ${Math.round(job.progress * 100)}%`;
        $("#video-cancel").hidden = ["done", "error", "canceled"].includes(job.status);
        $("#video-cancel").disabled = job.status === "canceling";
        if (job.status === "done") {
            $("#video-save").href = job.download_url;
            $("#video-save").download = job.filename;
            $("#video-save").hidden = false;
            $("#video-preview").src = job.download_url + "&preview=1";
            $("#video-preview").hidden = false;
            $("#video-job-detail").textContent = `${size(job.file_bytes)} · 已合并完成，可保存或直接预览。`;
        }
    }

    async function poll(jobId, generation, autoSave = true) {
        let failures = 0;
        while (generation === pollGeneration) {
            try {
                const job = await api("/api/video/status?job=" + encodeURIComponent(jobId));
                if (generation !== pollGeneration) return;
                failures = 0;
                renderJob(job);
                if (["done", "error", "canceled"].includes(job.status)) {
                    activeJob = null;
                    controls();
                    if (job.status === "done" && autoSave) $("#video-save").click();
                    if (job.status !== "done") rememberJob(null);
                    return;
                }
            } catch (error) {
                if (error.status === 404) {
                    $("#video-job-stage").textContent = "下载任务已失效";
                    $("#video-job-detail").textContent = "服务可能已重启。请查看项目 downloads 文件夹，或重新解析并下载。";
                    $("#video-cancel").hidden = true;
                    activeJob = null;
                    rememberJob(null);
                    controls();
                    return;
                }
                failures++;
                $("#video-job-detail").textContent = failures >= 3
                    ? "暂时无法连接本地服务，正在重连；任务可能仍在后台运行。" : "正在重新连接本地服务…";
            }
            await new Promise((resolve) => setTimeout(resolve, Math.min(1000 * Math.max(1, failures), 5000)));
        }
    }

    $("#video-form").addEventListener("submit", (event) => { event.preventDefault(); inspect($("#video-url").value.trim()); });
    $("#video-format").addEventListener("change", formatNote);
    $("#video-page").addEventListener("change", () => {
        const url = new URL(metadata.source_url);
        url.searchParams.set("p", $("#video-page").value);
        $("#video-url").value = url.href;
        inspect(url.href);
    });
    $("#video-start").addEventListener("click", async () => {
        if (!metadata || activeJob || preparing) return;
        preparing = true;
        controls();
        $("#video-save").hidden = true;
        $("#video-preview").pause();
        $("#video-preview").removeAttribute("src");
        $("#video-preview").hidden = true;
        try {
            const job = await api("/api/video/start", { inspection_id: metadata.inspection_id, format_id: $("#video-format").value });
            activeJob = job.id;
            rememberJob(job.id);
            renderJob(job);
            void poll(job.id, ++pollGeneration);
        } catch (error) { message(error.message, true); }
        finally { preparing = false; controls(); }
    });
    $("#video-cancel").addEventListener("click", async () => {
        if (!activeJob) return;
        $("#video-cancel").disabled = true;
        try { renderJob(await api("/api/video/cancel", { job_id: activeJob })); }
        catch (error) { message(error.message, true); $("#video-cancel").disabled = false; }
    });
    const query = new URLSearchParams(location.search);
    let savedJob = query.get("job");
    try { savedJob ||= sessionStorage.getItem(jobStorageKey); } catch { /* 存储不是运行前提。 */ }
    if (savedJob && /^[a-f0-9]{32}$/.test(savedJob)) {
        activeJob = savedJob;
        rememberJob(savedJob);
        controls();
        void poll(savedJob, ++pollGeneration, false);
    }
})();
