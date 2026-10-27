/** @brief 验证自动填入、连续提交补取、手动输入保留与退出登录后的清空。 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {randomUUID} = require("node:crypto");
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture(available = true) {
    let listener, login = "SESSDATA=mock-login-1", downloads = 0, reads = 0;
    const fields = Array.from({length: 3}, () => ({value: "", dataset: {},
        addEventListener(type, fn) {this.oninput = fn;}}));
    const controls = new Map();
    for (const id of ["#bce-status", "#bce-get", "#bce-guide", "#bce-path", "#bce-copy-path", "#bce-download"])
        controls.set(id, {value: "", textContent: "", click() {downloads++;}});
    const panel = {setAttribute() {}, querySelector: id => controls.get(id), querySelectorAll: () => []};
    const document = {querySelectorAll: () => fields, createElement: () => panel,
        querySelector: () => ({after() {}})};
    const window = {addEventListener(type, fn) {listener = fn;}, postMessage(data) {
        if (!available) return;
        if (data.type === "bili-cookie-get") reads++;
        const result = data.type === "bili-cookie-ping" ? {ok: true} :
            login ? {ok: true, cookie: login} : {ok: false, error: "请先登录 B 站"};
        queueMicrotask(() => listener({source: window, origin: "http://127.0.0.1:8765",
            data: {source: "bili-cookie-extension", type: "response", id: data.id, result}}));
    }};
    vm.runInNewContext(fs.readFileSync("static/cookie-extension.js", "utf8"), {
        document, window, location: {origin: "http://127.0.0.1:8765"},
        crypto: {randomUUID}, navigator: {}, setTimeout, clearTimeout,
        fetch: async () => ({ok: true, json: async () => ({unpacked_path: "test-folder"})})
    });
    return {fields, controls, window, reads: () => reads, downloads: () => downloads,
        setLogin(value) {login = value;},
        changed() {listener({source: window, origin: "http://127.0.0.1:8765",
            data: {source: "bili-cookie-extension", type: "changed"}});}};
}

(async () => {
    const page = fixture();
    await tick();
    assert.ok(page.fields.every(field => field.value === "SESSDATA=mock-login-1"));
    assert.match(page.controls.get("#bce-status").textContent, /已自动填入/);
    assert.equal(page.controls.get("#bce-path").value, "test-folder");
    page.setLogin("SESSDATA=mock-login-2");
    await page.window.BiliCookieExtension.fillIfEmpty(page.fields[0]);
    assert.equal(page.fields[0].value, "SESSDATA=mock-login-2", "refresh autofill before first submit");
    page.fields[0].value = "";
    await page.window.BiliCookieExtension.fillIfEmpty(page.fields[0]);
    assert.equal(page.fields[0].value, "SESSDATA=mock-login-2", "refill after a previous submit cleared the field");
    page.fields[1].value = "manual-cookie";
    page.fields[1].oninput();
    const before = page.reads();
    await page.window.BiliCookieExtension.fillIfEmpty(page.fields[1]);
    assert.equal(page.reads(), before, "manual values must not be replaced implicitly");
    page.setLogin(""); page.changed();
    await new Promise(resolve => setTimeout(resolve, 470));
    assert.equal(page.fields[0].value, "");
    assert.equal(page.fields[2].value, "");
    assert.equal(page.fields[1].value, "manual-cookie");
    assert.match(page.controls.get("#bce-status").textContent, /请先登录/);

    const missing = fixture(false);
    await missing.controls.get("#bce-get").onclick();
    assert.equal(missing.downloads(), 1);
    assert.equal(missing.controls.get("#bce-guide").open, true);
    await missing.window.BiliCookieExtension.fillIfEmpty(missing.fields[0]);
    assert.equal(missing.fields[0].value, "", "extension absence must retain anonymous operation");
    console.log("Cookie page autofill, resubmission, manual override, logout and download tests passed.");
})();
