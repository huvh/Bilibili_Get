/** @brief 隔离环境中的桥接；只接受本机工作台顶层窗口的明确请求。 */
(() => {
    "use strict";
    if (window !== window.top || location.origin !== "http://127.0.0.1:8765" ||
        !["/", "/transcribe"].includes(location.pathname)) return;
    const send = message => window.postMessage({source: "bili-cookie-extension", ...message}, location.origin);
    window.addEventListener("message", async event => {
        const data = event.data;
        if (event.source !== window || event.origin !== location.origin || data?.source !== "bili-cookie-workbench" ||
            !/^[a-f0-9-]{36}$/.test(data.id) || !["bili-cookie-ping", "bili-cookie-get"].includes(data.type)) return;
        try {
            const result = await chrome.runtime.sendMessage({type: data.type});
            send({type: "response", id: data.id, result: result || {ok: false, error: "扩展未响应，请刷新页面。"}});
        } catch { send({type: "response", id: data.id, result: {ok: false, error: "扩展已更新，请刷新本地工作台。"}}); }
    });
    chrome.runtime.onMessage.addListener(message => {
        if (message?.type === "bili-cookie-changed") send({type: "changed"});
    });
    send({type: "ready"});
})();
