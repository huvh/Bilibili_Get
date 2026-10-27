/** @brief 批量输入的分 P 识别与选择；只在确认后交给听悟提交模块。 */
(() => {
    "use strict";
    const $ = selector => document.querySelector(selector);
    const input = $("#tw-urls"), dialog = $("#tw-page-dialog");
    if (!input || !dialog) return;
    const cache = new Map(), choices = new Map();
    let timer, scanning, scannedText = null, errors = [], groups = [], finishDialog;
    const sources = () => [...new Set(input.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean))];
    function node(tag, text, className) {
        const result = document.createElement(tag);
        if (text !== undefined) result.textContent = text;
        if (className) result.className = className;
        return result;
    }
    function summary(text, failed = false) {
        $("#tw-page-summary").textContent = text;
        $("#tw-page-summary").classList.toggle("tw-error", failed);
        $("#tw-page-edit").hidden = !groups.length && !errors.length;
        $("#tw-page-edit").textContent = errors.length ? "重新识别 / 选择分 P" : "重新选择分 P";
    }
    function selectedUrls() {
        const result = new Set();
        for (const source of sources()) {
            const metadata = cache.get(source);
            if (!metadata || !metadata.pages.length) result.add(source);
            else for (const page of metadata.pages) {
                if (metadata.pages.length === 1 || choices.get(metadata.id)?.pages.has(page.page))
                    result.add(page.url);
            }
        }
        return [...result];
    }
    function updateSummary() {
        if (errors.length) return summary(errors.join("；") + "。请检查链接或登录状态后重新识别。", true);
        if (!sources().length) return summary("粘贴链接后自动识别多 P 视频，弹窗勾选需要转写的分 P。");
        const pending = groups.some(group => !choices.get(group.id).confirmed);
        summary(groups.length ? `已识别 ${groups.length} 个多 P 视频，${pending ? "待确认分 P" : `已选择 ${selectedUrls().length} 项转写内容`}。` :
            `已识别 ${sources().length} 条链接，无需选择分 P。`);
    }
    /** 合并同一视频的不同链接，默认勾选链接指定的 P；用户确认后保留选择。 */
    function collectGroups() {
        const videos = new Map();
        for (const source of sources()) {
            const metadata = cache.get(source);
            if (!metadata || metadata.pages.length <= 1) continue;
            videos.set(metadata.id, metadata);
            if (!choices.has(metadata.id)) choices.set(metadata.id, {pages: new Set(), confirmed: false});
            const choice = choices.get(metadata.id);
            if (!choice.confirmed) choice.pages.add(metadata.page);
        }
        groups = [...videos.values()];
        for (const id of choices.keys()) if (!videos.has(id)) choices.delete(id);
    }
    /** 并发限制为三条，输入变动时丢弃旧结果的展示并继续识别最新输入。 */
    async function scan() {
        if (scanning) return scanning;
        scanning = (async () => {
            do {
                const text = input.value, urls = sources();
                errors = [];
                if (urls.length > 200) {
                    errors.push("每个批次最多输入 200 条链接");
                    scannedText = text; groups = []; updateSummary(); return;
                }
                if (urls.length) summary("正在识别视频与分 P…");
                await window.BiliCookieExtension?.fillIfEmpty($("#tw-cookie"));
                const cookie = $("#tw-cookie").value.trim();
                let next = 0;
                await Promise.all(Array.from({length: Math.min(3, urls.length)}, async () => {
                    while (next < urls.length) {
                        const source = urls[next++];
                        if (cache.has(source)) continue;
                        try {
                            const response = await fetch("/api/tingwu/inspect", {method: "POST",
                                headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({url: source, cookie})});
                            const data = await response.json();
                            if (!response.ok) throw new Error(data.error || "识别失败");
                            cache.set(source, data);
                        } catch (error) {
                            errors.push(`${source.slice(0, 90)}：${error.message}`);
                        }
                    }
                }));
                scannedText = text;
            } while (scannedText !== input.value);
            // 移除已删除链接的缓存，避免长时间编辑积累无用元数据。
            const active = new Set(sources());
            for (const source of cache.keys()) if (!active.has(source)) cache.delete(source);
            collectGroups(); updateSummary();
        })();
        try { await scanning; } finally { scanning = null; }
    }
    function closePicker(accepted) {
        if (!dialog.open) return;
        dialog.close();
        const done = finishDialog; finishDialog = null;
        done?.(accepted);
        updateSummary();
    }
    function openPicker() {
        if (dialog.open) return finishDialog.promise;
        if (!groups.length) return Promise.resolve(true);
        const draft = new Map(), checks = [];
        $("#tw-page-list").replaceChildren();
        const updateCount = () => {
            const count = [...draft.values()].reduce((sum, pages) => sum + pages.size, 0);
            const missing = [...draft.values()].some(pages => !pages.size);
            const total = count + selectedUrls().length - groups.reduce((sum, group) =>
                sum + choices.get(group.id).pages.size, 0);
            $("#tw-page-count").textContent = `已勾选 ${count} P` + (missing ? " · 每个视频至少选择一 P" :
                total > 200 ? " · 超过 200 项，请拆分批次" : "");
            $("#tw-page-confirm").disabled = missing || total > 200;
        };
        for (const group of groups) {
            const pages = new Set(choices.get(group.id).pages); draft.set(group.id, pages);
            const section = node("section", undefined, "tw-page-group");
            section.append(node("h3", `${group.title} · ${group.pages.length} P`));
            const actions = node("div", undefined, "tw-actions");
            for (const [label, select] of [["全选", true], ["全不选", false]]) {
                const button = node("button", label, "secondary"); button.type = "button";
                button.onclick = () => {
                    pages.clear();
                    for (const item of checks.filter(item => item.id === group.id)) {
                        item.input.checked = select;
                        if (select) pages.add(item.page);
                    }
                    updateCount();
                };
                actions.append(button);
            }
            section.append(actions);
            for (const page of group.pages) {
                const label = node("label", undefined, "tw-page-option");
                const checkbox = node("input"); checkbox.type = "checkbox"; checkbox.checked = pages.has(page.page);
                checkbox.onchange = () => {
                    if (checkbox.checked) pages.add(page.page); else pages.delete(page.page);
                    updateCount();
                };
                checks.push({id: group.id, page: page.page, input: checkbox});
                label.append(checkbox, node("span", `P${page.page} · ${page.part}`));
                if (page.duration) label.append(node("small", `${Math.floor(page.duration / 60)}:${String(page.duration % 60).padStart(2, "0")}`));
                section.append(label);
            }
            $("#tw-page-list").append(section);
        }
        $("#tw-page-confirm").onclick = () => {
            for (const [id, pages] of draft) choices.set(id, {pages, confirmed: true});
            closePicker(true);
        };
        updateCount();
        const promise = new Promise(resolve => { finishDialog = resolve; });
        finishDialog.promise = promise;
        dialog.showModal();
        return promise;
    }
    async function detect() {
        try {
            await scan();
            if (groups.some(group => !choices.get(group.id).confirmed)) await openPicker();
        } catch (error) { summary(error.message, true); }
    }
    input.addEventListener("input", () => {
        clearTimeout(timer); closePicker(false);
        timer = setTimeout(() => void detect(), 450);
    });
    $("#tw-page-edit").onclick = async () => {
        try { await scan(); await openPicker(); }
        catch (error) { summary(error.message, true); }
    };
    $("#tw-page-cancel").onclick = () => closePicker(false);
    dialog.addEventListener("cancel", event => {event.preventDefault(); closePicker(false);});
    window.TingwuPages = {async prepare() {
        clearTimeout(timer);
        await scan();
        if (errors.length) throw new Error("链接识别未完成，请先修正错误或点击重新识别。");
        if (!sources().length) throw new Error("请输入至少一条链接。");
        if (groups.some(group => !choices.get(group.id).confirmed) && !await openPicker()) return null;
        // 弹窗打开期间输入发生改变时，不允许提交旧选择。
        if (scannedText !== input.value) return null;
        const urls = selectedUrls();
        if (urls.length > 200) throw new Error("选择后超过 200 项，请拆分批次。");
        return urls;
    }};
})();
