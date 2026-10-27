/** @brief 独立转换和完整书签的模拟网络/下载流程验证，不读取浏览器登录信息。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { createSubtitleTools } from "./subtitle-core.mjs";

const tools = createSubtitleTools();
// 用合成数据覆盖长字幕，不依赖个人下载或第三方字幕正文。
const sample = {body: Array.from({length: 997}, (_, index) =>
    ({from: index * 2, to: index * 2 + 1, content: `测试字幕 ${index + 1}`}))};
const source = await readFile(new URL("../static/login-helper.js", import.meta.url), "utf8");
const simple = { body: [{ from: 2, to: 3, content: "第二句" },
    { from: 0, to: 1, content: "第一句" }, { from: 3, to: 4, content: "第二句" }] };

test("997 条合成 BCC 和旧版包装文件都能生成逐句 TXT", () => {
    assert.equal(tools.convert(sample).count, 997);
    assert.equal(tools.convert({ title: "课程", document: simple }).text, "第一句\n第二句\n第二句\n");
});

test("非法时间、空内容被过滤，保留尖括号原文而非执行 HTML", () => {
    const result = tools.convert({ body: [{ from: null, to: 1, content: "空值" },
        { from: true, to: 1, content: "布尔值" }, { from: 2, to: 1, content: "逆序" },
        { from: 0, to: 1, content: " <script> & 文本\r\n下一行 " }] });
    assert.equal(result.text, "<script> & 文本\n下一行\n");
    assert.throws(() => tools.convert({ body: [] }), /有效字幕/);
    assert.throws(() => tools.convert({}), /body/);
});

test("中文文件名、保留名称和不可信 URL", () => {
    assert.equal(tools.filename("课程/二.json", "ai-zh"), "课程二.ai-zh.txt");
    assert.equal(tools.filename("CON"), "B站字幕.txt");
    assert.match(tools.subtitleUrl("//aisubtitle.hdslb.com/bfs/ai_subtitle/abc"), /^https:/);
    for (const url of ["https://evil.test/bfs/subtitle/a", "https://aisubtitle.hdslb.com.evil.test/bfs/subtitle/a",
        "https://u@aisubtitle.hdslb.com/bfs/subtitle/a", "https://aisubtitle.hdslb.com:123/bfs/subtitle/a"]) {
        assert.throws(() => tools.subtitleUrl(url));
    }
});

async function runBookmark({ tracks, needLogin = false, url, subtitle = simple } = {}) {
    const downloads = [];
    const requests = [];
    const blobs = new Map();
    class Element {
        constructor(tag) { this.tag = tag; this.children = []; this.value = ""; this.textContent = ""; }
        append(...elements) {
            this.children.push(...elements);
            if (this.tag === "select" && !this.value && elements.length) this.value = elements[0].value;
        }
        attachShadow() { this.shadowRoot = new Element("shadow"); return this.shadowRoot; }
        setAttribute(name, value) { this[name] = value; }
        remove() { this.removed = true; }
        click() { if (this.tag === "a") downloads.push({ name: this.download, blob: blobs.get(this.href) }); }
    }
    const root = new Element("html");
    const find = (element, predicate) => {
        if (element.removed) return undefined;
        if (predicate(element)) return element;
        for (const child of [...element.children, ...(element.shadowRoot ? [element.shadowRoot] : [])]) {
            const result = find(child, predicate);
            if (result) return result;
        }
    };
    class BrowserURL extends URL {
        static createObjectURL(blob) { const key = "blob:fixture/" + blobs.size; blobs.set(key, blob); return key; }
        static revokeObjectURL(key) { blobs.delete(key); }
    }
    const location = new URL(url || "https://www.bilibili.com/video/BV1th411z7sn/?p=2");
    const context = vm.createContext({ document: {
        documentElement: root, createElement: (tag) => new Element(tag),
        getElementById: (id) => find(root, (element) => element.id === id),
    }, location, URL: BrowserURL, URLSearchParams, Blob, AbortController, setTimeout, clearTimeout,
    fetch: async (requestUrl, options) => {
        requests.push({ url: requestUrl, credentials: options.credentials });
        let data;
        if (requestUrl.includes("/x/web-interface/view?")) data = { code: 0, data: {
            aid: 1, title: "测试课程", pages: [{ page: 1, cid: 11, part: "第一课" }, { page: 2, cid: 22, part: "第二课" }],
        } };
        else if (requestUrl.includes("/x/player/v2?")) data = { code: 0, data: {
            need_login_subtitle: needLogin, subtitle: { subtitles: tracks ?? [{ lan: "zh-CN", lan_doc: "中文",
                subtitle_url: "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/test" }] },
        } };
        else data = subtitle;
        return { ok: true, json: async () => data };
    } });
    vm.runInContext(source, context);
    await new Promise((resolve) => setImmediate(resolve));
    return { downloads, requests, find: (predicate) => find(root, predicate), root,
        runAgain: () => vm.runInContext(source, context) };
}

test("单轨视频：选中当前分 P，获取 JSON 后自动只下载 TXT", async () => {
    const state = await runBookmark({ subtitle: sample });
    assert.equal(state.downloads.length, 1);
    assert.equal(state.downloads[0].name, "测试课程 P2 第二课.zh-CN.txt");
    assert.equal(state.downloads[0].blob.type, "text/plain;charset=utf-8");
    const bytes = new Uint8Array(await state.downloads[0].blob.arrayBuffer());
    assert.deepEqual([...bytes.slice(0, 3)], [239, 187, 191]);
    const text = new TextDecoder().decode(bytes);
    assert.equal(text, tools.convert(sample).text);
    assert.match(state.requests[1].url, /cid=22/);
    assert.deepEqual(state.requests.map((request) => request.credentials), ["include", "include", "omit"]);
    state.runAgain();
    assert.equal(state.downloads.length, 1, "重复点击不会重复触发下载");
});

test("多轨字幕先选择语言，选择后自动转换下载", async () => {
    const state = await runBookmark({ tracks: [
        { lan: "zh", lan_doc: "中文", subtitle_url: "//i0.hdslb.com/bfs/subtitle/one" },
        { lan: "en", lan_doc: "English", subtitle_url: "//i0.hdslb.com/bfs/subtitle/two" },
    ] });
    assert.equal(state.downloads.length, 0);
    state.find((element) => element.tag === "select").value = "1";
    await state.find((element) => element.textContent === "获取并下载 TXT").onclick();
    assert.equal(state.downloads.length, 1);
    assert.match(state.downloads[0].name, /\.en\.txt$/);
    assert.match(state.requests[2].url, /\/two$/);
});

test("需要登录或字幕为空时显示原因，不产生假下载", async () => {
    const state = await runBookmark({ tracks: [], needLogin: true });
    assert.equal(state.downloads.length, 0);
    assert.match(state.find((element) => element.role === "status").textContent, /需要登录/);
});

test("直接打开的字幕 JSON 页面也可一键变成 TXT", async () => {
    const state = await runBookmark({ url: "https://i0.hdslb.com/bfs/subtitle/test" });
    assert.equal(state.requests.length, 1);
    assert.equal(state.requests[0].credentials, "omit");
    assert.equal(state.downloads[0].name, "B站字幕.txt");
});

test("异常轨道域名不会被请求或下载", async () => {
    const state = await runBookmark({ tracks: [{ lan: "zh", subtitle_url: "https://evil.test/a" }] });
    assert.equal(state.requests.length, 2);
    assert.equal(state.downloads.length, 0);
    assert.match(state.find((element) => element.role === "status").textContent, /字幕 CDN/);
});

test("独立页面内嵌完整书签，无本地服务或外部脚本依赖", async () => {
    const bookmark = await readFile(new URL("./dist/bookmarklet.txt", import.meta.url), "utf8");
    assert.equal(decodeURIComponent(bookmark.slice("javascript:".length)), source.replace(/\r\n/g, "\n"));
    const page = await readFile(new URL("./dist/B站字幕转TXT助手.html", import.meta.url), "utf8");
    assert.ok(page.includes(bookmark));
    assert.doesNotMatch(page, /<script[^>]+src=|<link[^>]+href=/i);
    assert.doesNotMatch(source, /127\.0\.0\.1|localhost|snapany\.com/);
    const script = page.match(/<script>([\s\S]*)<\/script>/)[1];
    assert.doesNotThrow(() => new vm.Script(script));
});
