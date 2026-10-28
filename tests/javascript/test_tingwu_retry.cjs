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
    const job = {id: "job", created: 1, status: "partial", rows}, requests = [];
    vm.runInNewContext(fs.readFileSync("static/tingwu.js", "utf8"), {
        document: {querySelector: get, createElement: tag => new Element(tag)},
        window: {addEventListener() {}, BundleDownload: {createButton: () => new Element("download")}},
        location: {hash: "", origin: "http://127.0.0.1:8765"}, URLSearchParams, setInterval() {},
        fetch: async (path, options) => {
            if (path === "/api/tingwu/control") {
                const data = JSON.parse(options.body); requests.push(data);
                for (const row of rows) if (data.row_ids.includes(row.id)) row.status = "queued";
            }
            return {ok: true, text: async () => "", json: async () =>
                path.endsWith("status") ? {connected: true, jobs: [job]} : {key: "test"}};
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
    console.log("Single-row and batch failure retry scope and completed/active row preservation passed.");
})().catch(error => {console.error(error); process.exitCode = 1;});
