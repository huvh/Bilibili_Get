/** @file subtitle-core.mjs @brief 独立字幕助手的纯转换逻辑，无网络及 DOM 副作用。 */
export function createSubtitleTools() {
    function parseBcc(input) {
        const document = input?.document && typeof input.document === "object"
            ? input.document : input;
        if (!document || !Array.isArray(document.body)) {
            throw new Error("这不是 B 站字幕 JSON：缺少 body 数组。");
        }
        const cues = [];
        for (const row of document.body) {
            if (!row || typeof row.content !== "string") continue;
            if (![row.from, row.to].every((value) =>
                typeof value === "number" || (typeof value === "string" && value.trim()))) continue;
            const start = Number(row.from);
            const end = Number(row.to);
            if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < start) continue;
            const text = row.content.replace(/\r\n?/g, "\n").split("\n")
                .map((line) => line.trim()).filter(Boolean).join("\n");
            if (text) cues.push({ start, end, text });
        }
        cues.sort((a, b) => a.start - b.start);
        if (!cues.length) throw new Error("这个 JSON 中没有有效字幕。");
        return cues;
    }

    function convert(input) {
        const cues = parseBcc(input);
        // 逐句换行，不去重、不改写原文，也不把 HTML 实体误当成标签执行。
        return { text: cues.map((cue) => cue.text).join("\n") + "\n", count: cues.length };
    }

    function filename(title, language = "") {
        const base = String(title || "B站字幕").replace(/\.(json|bcc|txt)$/i, "")
            .replace(/[\\/?:*<>|"\x00-\x1f]/g, "").trim().replace(/[. ]+$/, "").slice(0, 140);
        const safeBase = !base || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(base)
            ? "B站字幕" : base;
        const tag = String(language).replace(/[^\w.-]/g, "").slice(0, 30);
        return safeBase + (tag ? "." + tag : "") + ".txt";
    }

    function subtitleUrl(value, base = "https://www.bilibili.com") {
        const url = new URL(value, base);
        if (!url.hostname.endsWith(".hdslb.com") || url.username || url.password
                || (url.port && url.port !== "443")
                || !/^\/bfs\/(ai_subtitle|subtitle)\//.test(url.pathname)
                || !["https:", "http:"].includes(url.protocol)) {
            throw new Error("字幕地址不属于支持的 B 站字幕 CDN。");
        }
        url.protocol = "https:";
        return url.href;
    }

    return { parseBcc, convert, filename, subtitleUrl };
}
