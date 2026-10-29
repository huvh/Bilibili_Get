/** @brief 批次与单行重试只发送失败/暂停项，已完成及运行中任务保持原状。 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const tick = () => new Promise(resolve => setImmediate(resolve));
class Element {
    constructor(tag, text = "") {this.tag = tag; this.children = []; this.textContent = text;
        this.value = ""; this.dataset = {}; this.classList = {toggle() {}};}
    append(...children) {this.children.push(...children);}
    replaceChildren(...children) {this.children = children;}
    setAttribute() {}
    querySelectorAll() {return [];}
}
(async () => {
    const ids = new Map(), get = id => {
        if (!ids.has(id)) ids.set(id, new Element(id));
        return ids.get(id);
    };
    const rows = [{id: "done", status: "done", title: "完成", files: []},
        {id: "error", status: "error", title: "失败", files: []},
        {id: "paused", status: "paused", title: "暂停", files: []},
        {id: "active", status: "transcribing", title: "运行", files: []}];
    const job = {id: "job", created: 1, status: "partial", rows}, requests = [], submissions = [];
    let bridgeConnected = true, refreshTimer, prepared = 0;
    vm.runInNewContext(fs.readFileSync("static/tingwu.js", "utf8"), {
        document: {querySelector: get, createElement: tag => new Element(tag)},
        window: {addEventListener() {}, BundleDownload: {createButton: () => new Element("download")},
            TingwuPages: {prepare: async () => {prepared++; return ["https://video/example"];}}},
        location: {hash: "", origin: "http://127.0.0.1:8765"}, URLSearchParams,
        setInterval(fn) {refreshTimer = fn;},
        fetch: async (path, options) => {
            if (path === "/api/tingwu/control") {
                const data = JSON.parse(options.body); requests.push(data);
                for (const row of rows) if (data.row_ids.includes(row.id)) row.status = "queued";
            }
            if (path === "/api/tingwu/start") submissions.push(JSON.parse(options.body));
            return {ok: true, text: async () => "", json: async () =>
                path.endsWith("status") ? {connected: bridgeConnected, jobs: [job]} : {key: "test"}};
        }
    });
    await tick();
    const find = (root, text) => root.textContent === text ? root : root.children.map(child => find(child, text)).find(Boolean);
    const first = find(get("#tw-jobs"), "重试此项");
    await first.onclick();
    assert.deepEqual(requests[0].row_ids, ["error"]);
    const all = find(get("#tw-jobs"), "重试失败/中断项（1）");
    await all.onclick();
    assert.equal(requests[1].action, "retry_failed");
    assert.deepEqual(requests[1].row_ids, ["paused"]);
    assert.equal(rows[0].status, "done");
    assert.equal(rows[3].status, "transcribing");
    // 未连接时按钮仍能解释原因；空表单点击及回车提交都不能触发解析或转写。
    bridgeConnected = false; refreshTimer(); await tick();
    assert.equal(get("#tw-start").disabled, false);
    assert.equal(get("#tw-start-hint").hidden, false);
    let prevented = false;
    get("#tw-start").onclick({preventDefault() {prevented = true;}});
    assert.equal(prevented, true);
    assert.match(get("#tw-message").textContent, /尚未连接听悟，转写尚未启动/);
    assert.equal(get("#tw-connect-help").open, true);
    await get("#tw-form").onsubmit({preventDefault() {}});
    assert.equal(prepared, 0);
    assert.equal(submissions.length, 0);
    bridgeConnected = true; refreshTimer(); await tick();
    assert.equal(get("#tw-start-hint").hidden, true);
    assert.equal(get("#tw-message").hidden, true);
    await get("#tw-form").onsubmit({preventDefault() {}});
    assert.equal(prepared, 1);
    assert.equal(submissions.length, 1);
    // 上次刷新显示在线、点击前刚断线时，仍须在分 P 解析之前阻止提交。
    bridgeConnected = false;
    await get("#tw-form").onsubmit({preventDefault() {}});
    assert.equal(prepared, 1);
    assert.equal(submissions.length, 1);
    assert.equal(get("#tw-start").disabled, false);
    assert.equal(get("#tw-start-hint").hidden, false);
    console.log("Failure retry scope, offline start feedback, reconnection and stale connection guard passed.");
})().catch(error => {console.error(error); process.exitCode = 1;});
