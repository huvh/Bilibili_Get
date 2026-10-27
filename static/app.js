/** @file app.js @brief 页面交互、请求调度和文字稿展示。 */
"use strict";
const $ = (selector) => document.querySelector(selector);
const state = { metadata: null, track: 0, content: null, view: "plain", busy: false, generation: 0 };

function showMessage(message, error = false) {
    const box = $("#message");
    box.textContent = message;
    box.classList.toggle("error", error);
    box.hidden = !message;
}

function setBusy(busy) {
    state.busy = busy;
    $("#extract-button").disabled = busy;
    $("#extract-button").textContent = busy ? "正在提取…" : "提取字幕 →";
    $("#import-json").disabled = busy;
    $("#bcc-file").disabled = busy;
    $("#page-select").disabled = busy;
    $("#track-select").disabled = busy;
}

async function request(path, payload) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 95000);
    try {
        const response = await fetch(path, {
            method: payload === undefined ? "GET" : "POST",
            headers: payload === undefined ? {} : { "Content-Type": "application/json" },
            body: payload === undefined ? undefined : JSON.stringify(payload),
            signal: controller.signal,
        });
        const data = await response.json();
        if (!response.ok) {
            const error = new Error(data.error || "请求失败，请重试。");
            error.code = data.code;
            throw error;
        }
        return data;
    } catch (error) {
        if (error.name === "AbortError") throw new Error("请求超时，请稍后重试。");
        throw error;
    } finally {
        clearTimeout(timer);
    }
}

function clock(milliseconds) {
    const seconds = Math.floor(milliseconds / 1000);
    const hours = Math.floor(seconds / 3600);
    return `${hours ? hours + ":" : ""}${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function switchInput(mode) {
    for (const name of ["link", "file"]) {
        const active = name === mode;
        $(`#${name}-tab`).classList.toggle("active", active);
        $(`#${name}-tab`).setAttribute("aria-selected", String(active));
        $(`#${name}-panel`).hidden = !active;
    }
}

function downloadUrl(format) {
    return "/api/download?" + new URLSearchParams({
        job: state.metadata.job_id, track: String(state.track), format,
    });
}

function renderTranscript() {
    const root = $("#transcript");
    root.replaceChildren();
    if (!state.content) return;
    const search = $("#search").value.trim().toLocaleLowerCase();
    const rows = state.view === "raw" ? state.content.cues : state.content.paragraphs;
    let shown = 0;
    const fragment = document.createDocumentFragment();
    for (const row of rows) {
        if (search && !row.text.toLocaleLowerCase().includes(search)) continue;
        shown++;
        const node = document.createElement(state.view === "plain" ? "p" : "div");
        if (state.view === "plain") {
            node.textContent = row.text;
        } else {
            node.className = "cue";
            const time = document.createElement("time");
            time.textContent = clock(row.start_ms);
            const text = document.createElement("span");
            text.textContent = row.text;
            node.append(time, text);
        }
        fragment.append(node);
    }
    if (!shown) {
        const empty = document.createElement("p");
        empty.textContent = "没有匹配的字幕。";
        fragment.append(empty);
    }
    root.append(fragment);
    $("#cue-count").textContent = `${state.content.cues.length} 条字幕${search ? ` · ${shown} 处匹配` : ""}`;
    for (const button of document.querySelectorAll("[data-view]")) {
        const active = button.dataset.view === state.view;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
    }
}

async function loadTrack(index) {
    state.track = index;
    state.content = null;
    const generation = ++state.generation;
    $("#transcript").textContent = "正在读取字幕…";
    $("#copy-text").disabled = true;
    for (const link of document.querySelectorAll("[data-format]")) link.removeAttribute("href");
    try {
        const data = await request("/api/load?" + new URLSearchParams({ job: state.metadata.job_id, track: String(index) }));
        if (generation !== state.generation) return;
        state.content = data;
        $("#copy-text").disabled = false;
        for (const link of document.querySelectorAll("[data-format]")) {
            link.href = downloadUrl(link.dataset.format);
            link.download = "";
        }
        renderTranscript();
    } catch (error) {
        if (generation !== state.generation) return;
        $("#transcript").textContent = "字幕读取失败，请重新提取或导入。";
        showMessage(error.message, true);
    }
}

async function showResult(metadata) {
    state.metadata = metadata;
    $("#video-title").textContent = metadata.title;
    $("#video-meta").textContent = [metadata.part, `${clock(metadata.duration * 1000)}`, `${metadata.tracks.length} 条字幕轨道`].filter(Boolean).join(" · ");
    $("#track-select").replaceChildren(...metadata.tracks.map((track, index) =>
        new Option(`${track.language_name}${track.automatic ? " · 自动生成" : ""} · 轨道 ${index + 1}`, String(index))));
    $("#page-wrap").hidden = metadata.pages.length < 2;
    $("#page-select").replaceChildren(...metadata.pages.map((page) => new Option(`P${page.page} ${page.part}`, String(page.page))));
    $("#page-select").value = String(metadata.page);
    $("#search").value = "";
    $("#result").hidden = false;
    $("#features").hidden = true;
    await loadTrack(0);
}

async function extract(url) {
    if (state.busy) return;
    setBusy(true);
    showMessage("正在连接 B 站，读取视频信息和字幕轨道…");
    await window.BiliCookieExtension?.fillIfEmpty($("#cookie"));
    const cookie = $("#cookie").value.trim();
    $("#cookie").value = "";
    try {
        const metadata = await request("/api/extract", { url, cookie });
        showMessage("");
        await showResult(metadata);
    } catch (error) {
        showMessage(error.message, true);
        if (error.code === "login_required") $("#login-options").open = true;
    } finally {
        setBusy(false);
    }
}

async function importDocument(text, title = "导入的字幕") {
    if (state.busy) return;
    setBusy(true);
    try {
        let document;
        try { document = JSON.parse(text); }
        catch { throw new Error("文件内容不是合法的 JSON，请选择 BCC JSON 字幕文件。"); }
        const metadata = await request("/api/import", { document, title });
        showMessage("");
        await showResult(metadata);
    } catch (error) {
        showMessage(error.message, true);
    } finally {
        setBusy(false);
    }
}

async function importFile(file) {
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) return showMessage("文件不能超过 8 MB。", true);
    await importDocument(await file.text(), file.name.replace(/\.(json|bcc)$/i, ""));
}

$("#link-tab").addEventListener("click", () => switchInput("link"));
$("#file-tab").addEventListener("click", () => switchInput("file"));
$("#extract-form").addEventListener("submit", (event) => { event.preventDefault(); extract($("#source-url").value.trim()); });
$("#bcc-file").addEventListener("change", (event) => importFile(event.target.files[0]));
$("#import-json").addEventListener("click", () => importDocument($("#json-text").value));
$("#track-select").addEventListener("change", (event) => loadTrack(Number(event.target.value)));
$("#page-select").addEventListener("change", (event) => {
    const url = new URL(state.metadata.source_url);
    url.searchParams.set("p", event.target.value);
    $("#source-url").value = url.href;
    showMessage("切换分 P 会重新请求字幕；若需要登录，请再次填写 Cookie。");
    extract(url.href);
});
$("#search").addEventListener("input", renderTranscript);
for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => { state.view = button.dataset.view; renderTranscript(); });
$("#copy-text").addEventListener("click", async () => {
    if (!state.content) return;
    const rows = state.view === "raw" ? state.content.cues : state.content.paragraphs;
    const text = rows.map((row) => (state.view === "plain" ? "" : `[${clock(row.start_ms)}] `) + row.text).join("\n\n");
    try {
        await navigator.clipboard.writeText(text);
        $("#copy-text").textContent = "已复制 ✓";
        setTimeout(() => { $("#copy-text").textContent = "复制文字"; }, 1800);
    } catch { showMessage("浏览器未允许复制，请选中文字后手动复制。", true); }
});
$("#dropzone").addEventListener("dragover", (event) => { event.preventDefault(); event.currentTarget.classList.add("dragging"); });
$("#dropzone").addEventListener("dragleave", (event) => event.currentTarget.classList.remove("dragging"));
$("#dropzone").addEventListener("drop", (event) => { event.preventDefault(); event.currentTarget.classList.remove("dragging"); importFile(event.dataTransfer.files[0]); });
