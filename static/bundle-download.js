/** @brief 打包时显示加载反馈；响应头发出、浏览器开始下载后隐藏提示，不在页面缓存大 ZIP。 */
(() => {
    "use strict";
    const slots = new Map();
    const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
    async function api(path, payload) {
        const response = await fetch(path, {method: payload ? "POST" : "GET",
            headers: payload ? {"Content-Type": "application/json"} : {},
            body: payload ? JSON.stringify(payload) : undefined});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "下载准备请求失败");
        return data;
    }
    function render(slot) {
        slot.views = slot.views.filter(view => view.box.isConnected);
        for (const {button, hint, label} of slot.views) {
            button.disabled = slot.busy;
            button.setAttribute("aria-busy", String(slot.busy));
            button.replaceChildren();
            if (slot.busy) {
                const spinner = document.createElement("span"); spinner.className = "bundle-spinner";
                spinner.setAttribute("aria-hidden", "true"); button.append(spinner);
            }
            button.append(document.createTextNode(slot.busy ? "正在准备下载…" : label));
            hint.textContent = slot.error || slot.hint || "";
            hint.hidden = !slot.busy && !slot.error;
            hint.classList.toggle("tw-error", !!slot.error);
        }
    }
    async function releaseFrame(frame, id) {
        try {
            while (true) {
                await sleep(2000);
                const data = await api("/api/tingwu/bundle/status?id=" + id);
                if (["finished", "error"].includes(data.status)) break;
            }
        } catch { /* 下载由浏览器管理；服务重启时停止清理轮询即可。 */ }
        frame.remove();
    }
    async function start(slot, url) {
        if (slot.busy) return;
        slot.busy = true; slot.error = "";
        slot.hint = "正在打包文件，大批量内容可能需要较长时间，请勿重复点击。";
        render(slot);
        let frame;
        try {
            const source = new URL(url, location.origin);
            let data = await api("/api/tingwu/bundle/start", {
                job_id: source.searchParams.get("job"), kind: source.searchParams.get("kind")});
            while (true) {
                if (data.status === "error") throw new Error(data.error || "打包失败，请重试。");
                if (data.status === "ready" && !frame) {
                    slot.hint = "打包完成，正在启动浏览器下载…"; render(slot);
                    frame = document.createElement("iframe"); frame.hidden = true;
                    frame.setAttribute("title", "打包文件下载");
                    frame.src = "/api/tingwu/bundle/download?id=" + data.id;
                    document.body.append(frame);
                }
                if (["started", "finished"].includes(data.status)) {
                    if (frame) void releaseFrame(frame, data.id);
                    frame = null;
                    break;
                }
                await sleep(500);
                data = await api("/api/tingwu/bundle/status?id=" + data.id);
            }
        } catch (error) { slot.error = error.message; }
        finally { frame?.remove(); slot.busy = false; render(slot); }
    }
    window.BundleDownload = {createButton(label, url) {
        if (!slots.has(url)) slots.set(url, {busy: false, error: "", views: []});
        const slot = slots.get(url), box = document.createElement("div"); box.className = "bundle-download";
        const button = document.createElement("button"); button.type = "button"; button.className = "secondary";
        const hint = document.createElement("small"); hint.className = "bundle-hint"; hint.setAttribute("role", "status");
        hint.setAttribute("aria-live", "polite"); hint.hidden = true;
        button.onclick = () => start(slot, url);
        box.append(button, hint);
        // 新视图尚未挂载，先设置内容；每次点击/轮询会移除已被批次刷新替换的旧视图。
        slot.views = slot.views.filter(view => view.box.isConnected);
        slot.views.push({box, button, hint, label});
        button.disabled = slot.busy; button.textContent = slot.busy ? "正在准备下载…" : label;
        button.setAttribute("aria-busy", String(slot.busy));
        if (slot.busy) {
            const spinner = document.createElement("span"); spinner.className = "bundle-spinner";
            spinner.setAttribute("aria-hidden", "true"); button.prepend(spinner);
        }
        hint.textContent = slot.error || slot.hint || ""; hint.hidden = !slot.busy && !slot.error;
        hint.classList.toggle("tw-error", !!slot.error);
        return box;
    }};
})();
