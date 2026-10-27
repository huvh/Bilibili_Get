/** @brief 扩展检测、首次安装指引和 Cookie 自动填入；不使用浏览器持久存储。 */
(() => {
    "use strict";
    const inputs = [...document.querySelectorAll("#cookie, #video-cookie, #tw-cookie")];
    if (!inputs.length || location.origin !== "http://127.0.0.1:8765") return;
    const pending = new Map();
    let installed = false, busy = false, refreshTimer;
    const panel = document.createElement("section");
    panel.className = "cookie-extension-card";
    panel.setAttribute("aria-label", "B 站登录态自动获取");
    panel.innerHTML = `<div class="cookie-extension-heading"><div><strong>B 站登录态</strong><p id="bce-status" role="status" aria-live="polite">正在检测 Cookie 获取扩展…</p></div><button id="bce-get" class="secondary" type="button">b站cookie获取扩展</button></div>
        <p class="field-hint">安装一次，自动填入当前浏览器的 B 站 Cookie。请在安装扩展的 Chrome / Edge 中打开本工作台。</p>
        <details id="bce-guide"><summary>首次安装与使用说明</summary>
            <p>按钮会下载扩展 ZIP；浏览器要求首次由你确认加载，无法从网页静默安装。</p>
            <ol><li>在地址栏打开 <code>chrome://extensions</code> 或 <code>edge://extensions</code>。<button class="bce-copy secondary" type="button" data-copy="chrome://extensions">复制 Chrome 地址</button> <button class="bce-copy secondary" type="button" data-copy="edge://extensions">复制 Edge 地址</button></li>
            <li>打开“开发者模式”，点击“加载已解压的扩展程序”。</li>
            <li>本机直接选择下面的现成目录，无需解压。其他电脑请先解压 ZIP，再选择其中含 <code>manifest.json</code> 的文件夹。<div class="bce-path-row"><input id="bce-path" readonly aria-label="本机扩展目录" placeholder="正在读取本机目录…"><button id="bce-copy-path" class="secondary" type="button">复制目录</button></div></li>
            <li>在同一浏览器中登录 <a href="https://www.bilibili.com/" target="_blank" rel="noopener">B 站</a>，刷新工作台。扩展会自动填入 Cookie，后续提交也会自动补取。</li></ol>
            <a class="secondary" id="bce-download" href="/api/cookie-extension/download" download>重新下载扩展 ZIP</a>
            <p class="field-hint">仅读取 B 站登录态，回传本机 8765 工作台；不保存到磁盘或浏览器存储。Cookie 隐藏在密码输入框内，提交后清空。</p>
        </details>`;
    document.querySelector(".intro").after(panel);
    const find = selector => panel.querySelector(selector);
    const status = text => { find("#bce-status").textContent = text; };
    inputs.forEach(input => input.addEventListener("input", () => delete input.dataset.biliAuto));

    function request(type, timeout = 1800) {
        return new Promise(resolve => {
            const id = crypto.randomUUID();
            const timer = setTimeout(() => { pending.delete(id); resolve(null); }, timeout);
            pending.set(id, result => { clearTimeout(timer); pending.delete(id); resolve(result); });
            window.postMessage({source: "bili-cookie-workbench", type, id}, location.origin);
        });
    }

    function clearAutofill() {
        for (const input of inputs.filter(input => input.dataset.biliAuto)) {
            input.value = "";
            delete input.dataset.biliAuto;
        }
    }

    async function fill(input = null, replace = false) {
        if (!installed) return;
        const targets = input ? [input] : inputs;
        const result = await request("bili-cookie-get", 5000);
        if (!result?.ok || typeof result.cookie !== "string" || !result.cookie ||
            result.cookie.length > 16000 || /[\r\n]/.test(result.cookie)) {
            clearAutofill();
            status(result?.error || "扩展未响应，请刷新本地工作台后重试。");
            return;
        }
        for (const field of targets) {
            if (replace || !field.value.trim() || field.dataset.biliAuto) {
                field.value = result.cookie;
                field.dataset.biliAuto = "true";
            }
        }
        status("已连接扩展 · B 站 Cookie 已自动填入，提交前会自动补取。");
    }

    async function detect() {
        const result = await request("bili-cookie-ping");
        if (result?.ok) {
            installed = true;
            await fill();
        } else if (!installed) status("尚未检测到扩展 · 点击右侧按钮下载并查看安装步骤。");
    }

    window.addEventListener("message", event => {
        if (event.source !== window || event.origin !== location.origin || event.data?.source !== "bili-cookie-extension") return;
        const data = event.data;
        if (data.type === "response") pending.get(data.id)?.(data.result);
        else if (data.type === "ready") void detect();
        else if (data.type === "changed" && installed) {
            clearTimeout(refreshTimer);
            refreshTimer = setTimeout(() => void fill(), 450);
        }
    });

    // 现有业务仍直接使用密码框；不向全局对象暴露 Cookie 字符串。
    window.BiliCookieExtension = Object.freeze({
        async fillIfEmpty(input) {
            if (!input || (input.value.trim() && !input.dataset.biliAuto)) return;
            try { await fill(input); } catch { /* 扩展不可用时仍允许匿名解析。 */ }
        }
    });
    find("#bce-get").onclick = async () => {
        if (busy) return;
        busy = true;
        find("#bce-get").disabled = true;
        try {
            const result = await request("bili-cookie-ping", 1000);
            installed = !!result?.ok;
            if (installed) await fill(null, true);
            else {
                find("#bce-guide").open = true;
                find("#bce-download").click();
                status("已发起扩展下载 · 首次请按下方步骤加载；安装后刷新工作台即可自动填入。");
            }
        } finally { busy = false; find("#bce-get").disabled = false; }
    };
    async function copy(text) {
        if (!text) { status("目录尚未读取，可下载 ZIP 后解压安装。"); return; }
        try { await navigator.clipboard.writeText(text); status("已复制，请粘贴到浏览器地址栏或目录选择框。"); }
        catch { status("未能自动复制，请选中地址或目录后手动复制。"); }
    }
    panel.querySelectorAll(".bce-copy").forEach(button => { button.onclick = () => void copy(button.dataset.copy); });
    find("#bce-copy-path").onclick = () => void copy(find("#bce-path").value);
    fetch("/api/cookie-extension/info").then(response => {
        if (!response.ok) throw new Error();
        return response.json();
    }).then(info => { find("#bce-path").value = info.unpacked_path; })
        .catch(() => { find("#bce-path").placeholder = "读取目录失败，请刷新或下载 ZIP 后解压安装"; });
    void detect();
})();
