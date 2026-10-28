/** @brief 大文件下载的准备反馈、真正开始传输、重复点击与失败提示。 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const tick = () => new Promise(resolve => setImmediate(resolve));
class Element {
    constructor(tag) {this.tag = tag; this.children = []; this.attrs = {}; this.hidden = false;
        this.isConnected = false; this.classList = {toggle() {}};}
    connect(value) {this.isConnected = value; for (const child of this.children) child.connect?.(value);}
    append(...children) {for (const child of children) {this.children.push(child); child.parent = this; child.connect?.(this.isConnected);}}
    prepend(child) {this.children.unshift(child); child.parent = this; child.connect?.(this.isConnected);}
    replaceChildren(...children) {for (const child of this.children) child.connect?.(false); this.children = []; this.append(...children);}
    setAttribute(name, value) {this.attrs[name] = value;}
    set textContent(value) {this.replaceChildren({textContent: value});}
    get textContent() {return this.children.map(child => child.textContent).join("");}
    remove() {this.connect(false); if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);}
}
function fixture(failed = false) {
    const body = new Element("body"); body.connect(true);
    const calls = [], timers = [], window = {};
    let status = "preparing";
    vm.runInNewContext(fs.readFileSync("static/bundle-download.js", "utf8"), {
        window, document: {body, createElement: tag => new Element(tag), createTextNode: text => ({textContent: text})},
        location: {origin: "http://127.0.0.1:8765"}, URL,
        setTimeout: (fn, ms) => {timers.push({fn, ms});},
        fetch: async (path, options) => {
            calls.push({path, options});
            return {ok: true, json: async () => ({id: "download-id", status: failed ? "error" : status,
                error: failed ? "打包失败样本" : undefined})};
        }
    });
    const box = window.BundleDownload.createButton("打包下载 TXT", "/api/tingwu/bundle?job=job&kind=txt");
    body.append(box);
    return {body, box, calls, window, setStatus(value) {status = value;},
        async advance(ms = 500) {
            const index = timers.findIndex(timer => timer.ms === ms);
            assert.ok(index >= 0, `expected ${ms}ms application timer`);
            timers.splice(index, 1)[0].fn(); await tick();
        }};
}
(async () => {
    const page = fixture(), [button, hint] = page.box.children;
    const completion = button.onclick(); await tick();
    assert.equal(button.disabled, true);
    assert.equal(button.attrs["aria-busy"], "true");
    assert.equal(hint.hidden, false);
    assert.ok(button.children.some(child => child.className === "bundle-spinner"));
    await button.onclick();
    assert.equal(page.calls.filter(call => call.path.endsWith("/start")).length, 1);
    page.setStatus("ready"); await page.advance();
    assert.equal(button.disabled, true, "ready archive has not yet started browser download");
    assert.equal(hint.hidden, false);
    const frame = page.body.children.find(child => child.tag === "iframe");
    assert.ok(frame.src.includes("/bundle/download?id="));
    // 批次刷新替换按钮后，新视图仍显示加载反馈。
    const newBox = page.window.BundleDownload.createButton("打包下载 TXT", "/api/tingwu/bundle?job=job&kind=txt");
    page.box.remove(); page.body.append(newBox);
    assert.equal(newBox.children[0].disabled, true);
    page.setStatus("started"); await page.advance(); await completion;
    assert.equal(newBox.children[0].disabled, false);
    assert.equal(newBox.children[0].attrs["aria-busy"], "false");
    assert.equal(newBox.children[1].hidden, true, "prompt disappears when transmission starts");
    page.setStatus("finished"); await page.advance(2000);
    assert.equal(frame.isConnected, false);

    const error = fixture(true), [failedButton, failedHint] = error.box.children;
    await failedButton.onclick();
    assert.equal(failedButton.disabled, false);
    assert.equal(failedHint.hidden, false);
    assert.match(failedHint.textContent, /打包失败样本/);
    assert.equal(error.body.children.some(child => child.tag === "iframe"), false);
    console.log("ZIP preparation spinner, actual download start, rerender, duplicate prevention and error feedback passed.");
})().catch(error => {console.error(error); process.exitCode = 1;});
