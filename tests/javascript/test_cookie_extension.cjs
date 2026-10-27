/** @brief 验证 Cookie 域名筛选、顶层来源校验及隔离脚本的消息边界。 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const origin = "http://127.0.0.1:8765";
const root = "browser-extension/bili-cookie-extension/";

(async () => {
    let receive, changed, installed, clicked, calls = 0;
    const sent = [], opened = [];
    let cookies = [
        {domain: ".bilibili.com", name: "SESSDATA", value: "test-login", httpOnly: true},
        {domain: ".bilibili.com", name: "bili_jct", value: "test-csrf"},
        {domain: "api.bilibili.com", hostOnly: true, name: "api", value: "test"},
        {domain: "evil.bilibili.com", name: "evil", value: "test"},
        {domain: "evil.com", name: "outside", value: "test"},
        {domain: ".bilibili.com", name: "expired", value: "test", expirationDate: 1},
        {domain: ".bilibili.com", name: "unsafe", value: "test\r\nheader"},
        {domain: ".bilibili.com", name: "SESSDATA", value: "duplicate"},
    ];
    const chrome = {
        runtime: {id: "self", getManifest: () => ({version: "1.0.0"}),
            onMessage: {addListener(fn) {receive = fn;}}, onInstalled: {addListener(fn) {installed = fn;}}},
        cookies: {getAll: async filter => {calls++; assert.equal(filter.url, "https://api.bilibili.com/"); return cookies;},
            onChanged: {addListener(fn) {changed = fn;}}},
        tabs: {query: async () => [
            {id: 1, url: origin + "/transcribe"}, {id: 2, url: origin + "/?tool=video"},
            {id: 3, url: "http://127.0.0.1:8888/transcribe"}, {id: 4, url: "https://evil.com/"},
            {id: 5, url: origin + "/docx"}],
            sendMessage: async (id, value) => sent.push({id, value}),
            create: tab => opened.push(tab.url)},
        action: {onClicked: {addListener(fn) {clicked = fn;}}}
    };
    vm.runInNewContext(fs.readFileSync(root + "background.js", "utf8"), {chrome, URL, setTimeout, clearTimeout});
    const sender = {id: "self", frameId: 0, url: origin + "/transcribe", tab: {id: 1, url: origin + "/transcribe"}};
    const get = who => new Promise(resolve => receive({type: "bili-cookie-get"}, who, resolve));
    const result = await get(sender);
    assert.equal(result.cookie, "SESSDATA=test-login; bili_jct=test-csrf; api=test");
    assert.equal(result.ok, true);
    for (const invalid of [
        {...sender, id: "other"}, {...sender, frameId: 1}, {...sender, url: "https://evil.com/"},
        {...sender, url: "http://127.0.0.1:8888/transcribe"}, {...sender, url: origin + "/docx"},
        {...sender, tab: {id: 1, url: "https://evil.com/"}}, {...sender, tab: null}
    ]) assert.equal(receive({type: "bili-cookie-get"}, invalid, () => assert.fail("invalid reply")), false);
    assert.equal(calls, 1, "unauthorized callers must not read cookies");
    cookies = cookies.filter(cookie => cookie.name !== "SESSDATA");
    assert.equal((await get(sender)).ok, false, "logout must not return a stale header");
    changed({cookie: {domain: ".bilibili.com"}});
    await new Promise(resolve => setTimeout(resolve, 450));
    assert.deepEqual(sent.map(item => item.id), [1, 2]);
    assert.ok(sent.every(item => !JSON.stringify(item.value).includes("cookie:")));
    installed({reason: "install"}); clicked();
    assert.deepEqual(opened, [origin + "/transcribe", origin + "/transcribe"]);

    // 隔离脚本拒绝其他窗口、其他来源与未知动作。
    let pageReceive, runtimeReceive;
    const replies = [], actions = [];
    const window = {addEventListener(type, fn) {pageReceive = fn;},
        postMessage(value, target) {replies.push({value, target});}};
    window.top = window;
    const location = {origin, pathname: "/transcribe"};
    vm.runInNewContext(fs.readFileSync(root + "content.js", "utf8"), {window, location,
        chrome: {runtime: {sendMessage: async value => {actions.push(value); return {ok: true, cookie: "mock"};},
            onMessage: {addListener(fn) {runtimeReceive = fn;}}}}});
    const data = {source: "bili-cookie-workbench", type: "bili-cookie-get", id: "a".repeat(36)};
    for (const event of [
        {source: {}, origin, data}, {source: window, origin: "https://evil.com", data},
        {source: window, origin, data: {...data, type: "unknown"}},
        {source: window, origin, data: {...data, id: "invalid"}}
    ]) await pageReceive(event);
    assert.equal(actions.length, 0);
    await pageReceive({source: window, origin, data});
    assert.equal(actions.length, 1);
    assert.equal(replies.at(-1).value.result.cookie, "mock");
    assert.ok(replies.every(reply => reply.target === origin));
    runtimeReceive({type: "bili-cookie-changed"});
    assert.equal(replies.at(-1).value.type, "changed");
    console.log("Cookie extension boundary and login-state tests passed.");
})();
