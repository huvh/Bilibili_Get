/** @brief 分 P 选择状态与异步输入竞态验证；不调用听悟或真实浏览器。 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const tick = () => new Promise(resolve => setImmediate(resolve));
class Element {
    constructor(tag) {this.tag = tag; this.children = []; this.value = ""; this.hidden = false;
        this.classList = {toggle() {}}; this.listeners = {};}
    append(...items) {this.children.push(...items);}
    replaceChildren(...items) {this.children = items;}
    addEventListener(name, fn) {this.listeners[name] = fn;}
    showModal() {this.open = true;}
    close() {this.open = false;}
}
function fixture() {
    const ids = ["tw-urls", "tw-cookie", "tw-page-dialog", "tw-page-summary", "tw-page-edit",
        "tw-page-list", "tw-page-confirm", "tw-page-cancel", "tw-page-count"];
    const controls = Object.fromEntries(ids.map(id => [id, new Element(id)]));
    let timer, fail = false, deferred;
    const calls = [];
    const window = {};
    vm.runInNewContext(fs.readFileSync("static/tingwu-pages.js", "utf8"), {
        document: {querySelector: selector => controls[selector.slice(1)], createElement: tag => new Element(tag)},
        window, setTimeout: fn => {timer = fn; return 1;}, clearTimeout: () => {timer = null;},
        fetch: async (path, options) => {
            const source = JSON.parse(options.body).url; calls.push(source);
            if (deferred) {const wait = deferred; deferred = null; await wait;}
            if (fail) return {ok: false, json: async () => ({error: "网络不可用"})};
            const single = source === "single", direct = source.startsWith("https://cdn");
            const pages = direct ? [] : Array.from({length: single ? 1 : 3}, (_, i) =>
                ({page: i + 1, part: `课程 ${i + 1}`, duration: 60, url: `https://video/${single ? "single" : "multi"}?p=${i + 1}`}));
            return {ok: true, json: async () => ({id: single ? "single" : "multi", title: "课程",
                page: source.endsWith("2") ? 2 : 1, pages})};
        }
    });
    const setInput = value => {controls["tw-urls"].value = value; controls["tw-urls"].listeners.input();};
    return {controls, calls, window, setInput,
        async detect() {const run = timer; timer = null; if (run) run(); await tick();},
        fail(value) {fail = value;}, defer(value) {deferred = value;},
        checkbox(page) {return controls["tw-page-list"].children[0].children[page + 1].children[0];}};
}
(async () => {
    const p = fixture(), c = p.controls;
    p.setInput("multi2\nmulti1\nmulti2\nhttps://cdn/audio.mp4"); await p.detect();
    assert.equal(c["tw-page-dialog"].open, true, "paste automatically opens the multi-P picker");
    assert.equal(c["tw-page-list"].children.length, 1, "same video is grouped");
    assert.equal(p.checkbox(1).checked, true); assert.equal(p.checkbox(2).checked, true);
    const prepare = p.window.TingwuPages.prepare(); await tick();
    p.checkbox(1).checked = false; p.checkbox(1).onchange();
    p.checkbox(3).checked = true; p.checkbox(3).onchange();
    c["tw-page-confirm"].onclick();
    assert.deepEqual(Array.from(await prepare), ["https://video/multi?p=2", "https://video/multi?p=3", "https://cdn/audio.mp4"]);
    assert.equal(p.calls.length, 3, "submission reuses metadata");
    const edit = c["tw-page-edit"].onclick(); await tick();
    assert.equal(p.checkbox(1).checked, false, "confirmed choices survive reopening");
    p.checkbox(1).checked = true; p.checkbox(1).onchange();
    c["tw-page-cancel"].onclick(); await edit;
    assert.equal(c["tw-page-dialog"].open, false, "cancel does not reopen the picker");
    assert.deepEqual(Array.from(await p.window.TingwuPages.prepare()),
        ["https://video/multi?p=2", "https://video/multi?p=3", "https://cdn/audio.mp4"], "cancel preserves confirmed choices");

    const cancel = fixture(); cancel.setInput("multi1"); await cancel.detect();
    const cancelled = cancel.window.TingwuPages.prepare(); await tick();
    cancel.controls["tw-page-cancel"].onclick(); assert.equal(await cancelled, null);
    const retry = cancel.window.TingwuPages.prepare(); await tick();
    assert.equal(cancel.controls["tw-page-dialog"].open, true, "unconfirmed choices cannot bypass picker");
    const group = cancel.controls["tw-page-list"].children[0];
    group.children[1].children[1].onclick();
    assert.equal(cancel.controls["tw-page-confirm"].disabled, true, "empty selection cannot submit");
    group.children[1].children[0].onclick();
    cancel.controls["tw-page-confirm"].onclick(); assert.equal((await retry).length, 3);

    const error = fixture(); error.fail(true); error.setInput("multi1"); await error.detect();
    await assert.rejects(error.window.TingwuPages.prepare(), /链接识别未完成/);
    error.fail(false); const recovered = error.controls["tw-page-edit"].onclick(); await tick();
    error.controls["tw-page-confirm"].onclick(); await recovered;
    assert.equal((await error.window.TingwuPages.prepare()).length, 1, "retry resolves previous failure");

    const race = fixture(); let release;
    race.defer(new Promise(resolve => {release = resolve;}));
    race.setInput("multi1"); await race.detect(); race.setInput("single"); release(); await tick();
    assert.equal(race.controls["tw-page-dialog"].open, undefined, "stale multi-P responses cannot open dialog");
    assert.deepEqual(Array.from(await race.window.TingwuPages.prepare()), ["https://video/single?p=1"]);
    const limit = fixture(); limit.setInput(Array.from({length: 201}, (_, i) => `url${i}`).join("\n"));
    await assert.rejects(limit.window.TingwuPages.prepare(), /链接识别未完成/);
    assert.equal(limit.calls.length, 0);
    // 提交模块必须使用选择器结果，不能再次展开全部分 P；取消时不能建立批次。
    const fields = new Map();
    const get = id => {
        if (!fields.has(id)) {const field = new Element(id); field.querySelectorAll = () => []; fields.set(id, field);}
        return fields.get(id);
    };
    let selected = ["https://video/multi?p=2", "https://video/multi?p=3"];
    const submissions = [];
    vm.runInNewContext(fs.readFileSync("static/tingwu.js", "utf8"), {
        document: {querySelector: get, createElement: tag => new Element(tag)},
        window: {addEventListener() {}, TingwuPages: {prepare: async () => selected}},
        location: {hash: "", origin: "http://127.0.0.1:8765"}, URLSearchParams,
        setInterval() {}, fetch: async (path, options) => {
            if (path === "/api/tingwu/start") submissions.push(JSON.parse(options.body));
            return {ok: true, text: async () => "", json: async () =>
                path.endsWith("status") ? {connected: true, jobs: []} : {key: "test"}};
        }
    });
    await tick();
    get("#tw-cookie").value = "test-cookie";
    await get("#tw-form").onsubmit({preventDefault() {}});
    assert.deepEqual(submissions[0], {urls: selected, cookie: "test-cookie", all_pages: false});
    assert.equal(get("#tw-cookie").value, "");
    selected = null; await get("#tw-form").onsubmit({preventDefault() {}});
    assert.equal(submissions.length, 1, "cancelled picker never submits a batch");
    console.log("分 P 自动弹窗、合并去重、选择提交、取消、全选、错误重试、输入竞态与数量限制验证通过。");
})().catch(error => {console.error(error); process.exitCode = 1;});
