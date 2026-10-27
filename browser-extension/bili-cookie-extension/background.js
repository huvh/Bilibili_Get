/** @brief 只向指定本地工作台返回 B 站 Cookie；不缓存、不记录、不上传。 */
"use strict";
const WORKBENCH = "http://127.0.0.1:8765";

function allowedPage(raw) {
    try {
        const url = new URL(raw);
        return url.origin === WORKBENCH && ["/", "/transcribe"].includes(url.pathname);
    } catch { return false; }
}

function cookieHeader(cookies) {
    // getAll 的目标 URL 限定实际 API 主机；再校验域名与请求头格式。
    const values = new Map();
    for (const cookie of cookies) {
        const domain = cookie.domain.replace(/^\./, "");
        if (!["bilibili.com", "api.bilibili.com"].includes(domain)) continue;
        if (cookie.hostOnly && domain !== "api.bilibili.com") continue;
        if (cookie.expirationDate && cookie.expirationDate <= Date.now() / 1000) continue;
        if (!/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(cookie.name)) continue;
        if (!/^[\x21-\x7e]*$/.test(cookie.value) || /[;"\\]/.test(cookie.value)) continue;
        if (!values.has(cookie.name)) values.set(cookie.name, cookie.value);
    }
    if (!values.get("SESSDATA")) throw new Error("请先在安装扩展的同一浏览器中登录 B 站，再点击按钮重试。");
    const header = [...values].map(([name, value]) => `${name}=${value}`).join("; ");
    if (header.length > 16000) throw new Error("B 站 Cookie 过长，请重新登录 B 站后重试。");
    return header;
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
    if (sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0 ||
        !allowedPage(sender.url) || !allowedPage(sender.tab.url)) return false;
    if (message?.type === "bili-cookie-ping") {
        reply({ok: true, version: chrome.runtime.getManifest().version});
        return false;
    }
    if (message?.type !== "bili-cookie-get") return false;
    chrome.cookies.getAll({url: "https://api.bilibili.com/"})
        .then(cookies => reply({ok: true, cookie: cookieHeader(cookies)}))
        .catch(() => reply({ok: false, error: "未取得 B 站登录态，请在同一浏览器中登录 B 站并允许扩展访问网站，然后重试。"}));
    return true;
});

let changeTimer;
chrome.cookies.onChanged.addListener(({cookie}) => {
    if (!/(^|\.)bilibili\.com$/.test(cookie.domain.replace(/^\./, ""))) return;
    clearTimeout(changeTimer);
    changeTimer = setTimeout(async () => {
        // 通知只含事件名；页面自行申请读取，不向其他本地端口发送 Cookie。
        const tabs = await chrome.tabs.query({url: "http://127.0.0.1/*"});
        for (const tab of tabs.filter(tab => allowedPage(tab.url))) {
            chrome.tabs.sendMessage(tab.id, {type: "bili-cookie-changed"}, {frameId: 0}).catch(() => {});
        }
    }, 400);
});
chrome.action.onClicked.addListener(() => chrome.tabs.create({url: WORKBENCH + "/transcribe"}));
chrome.runtime.onInstalled.addListener(({reason}) => {
    if (reason === "install") chrome.tabs.create({url: WORKBENCH + "/transcribe"});
});
