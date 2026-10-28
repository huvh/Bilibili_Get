/** @brief 独立 DOCX/ZIP 导入、批量转换及 TXT 下载页面；无需听悟会话。 */
(() => {
    "use strict";
    const $ = selector => document.querySelector(selector);
    let busy = false;

    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function message(text, error = false) {
        $("#docx-message").textContent = text;
        $("#docx-message").hidden = !text;
        $("#docx-message").classList.toggle("error", error);
    }
    async function api(path, payload) {
        const response = await fetch(path, {method: payload ? "POST" : "GET",
            headers: payload ? {"Content-Type": "application/json"} : {},
            body: payload ? JSON.stringify(payload) : undefined});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "转换请求失败");
        return result;
    }
    function download(text, url) {
        const link = element("a", text, "secondary");
        link.href = url; link.download = "";
        return link;
    }
    async function refresh(preferredId) {
        try {
            const {jobs} = await api("/api/tingwu/status");
            const imports = jobs.filter(job => job.local_import);
            const open = new Set([...$("#docx-jobs").querySelectorAll("details[open]")].map(node => node.dataset.job));
            const first = !$("#docx-jobs").children.length;
            $("#docx-jobs").replaceChildren(...imports.map((job, index) => {
                const box = element("details", undefined, "docx-batch");
                box.dataset.job = job.id;
                box.open = job.id === preferredId || open.has(job.id) || (first && index === 0);
                const files = job.rows.flatMap(row => row.files || []).filter(file => file.kind === "txt");
                box.append(element("summary", `${new Date(job.created * 1000).toLocaleString()} · ${files.length} 份 TXT`));
                const actions = element("div", undefined, "tw-actions");
                actions.append(window.BundleDownload.createButton("打包下载全部 TXT", `/api/tingwu/bundle?job=${job.id}&kind=txt`));
                box.append(actions);
                for (const row of job.rows) {
                    const item = element("article", undefined, "docx-result-row");
                    item.append(element("strong", row.title));
                    const links = element("div", undefined, "tw-actions");
                    for (const file of row.files || []) {
                        if (file.kind === "txt") links.append(download(file.name + " ↓", file.url));
                    }
                    item.append(links); box.append(item);
                }
                return box;
            }));
            $("#docx-empty").hidden = imports.length > 0;
        } catch (error) { message("无法加载转换结果：" + error.message, true); }
    }
    async function convert(files) {
        if (busy || !files.length) return;
        busy = true; $("#docx-files").disabled = true;
        const errors = []; let converted = 0, jobId = "";
        try {
            for (const file of files) {
                if (!/\.(docx|zip)$/i.test(file.name) || file.size > 64 * 1024 ** 2) {
                    errors.push(file.name + "：仅支持 64 MB 以内的 DOCX/ZIP"); continue;
                }
                try {
                    message(`正在转换 ${file.name}（${converted + 1}/${files.length}）…`);
                    const content = await new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onload = () => resolve(reader.result.split(",")[1]);
                        reader.onerror = () => reject(new Error("文件读取失败"));
                        reader.readAsDataURL(file);
                    });
                    const result = await api("/api/tingwu/import", {name: file.name, content, job_id: jobId});
                    jobId = result.id; converted++;
                } catch (error) { errors.push(file.name + "：" + error.message); }
            }
            message(`已处理 ${converted} 个文件${errors.length ? "；" + errors.join("；") : "，可以单独下载或打包下载 TXT。"}`, !!errors.length);
            await refresh(jobId);
        } finally {
            busy = false; $("#docx-files").disabled = false; $("#docx-files").value = "";
        }
    }
    $("#docx-files").addEventListener("change", event => void convert([...event.target.files]));
    const drop = $("#docx-drop");
    for (const type of ["dragenter", "dragover"]) drop.addEventListener(type, event => {
        event.preventDefault(); drop.classList.add("dragging");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
    drop.addEventListener("drop", event => {
        event.preventDefault(); drop.classList.remove("dragging");
        void convert([...event.dataTransfer.files]);
    });
    void refresh();
})();
