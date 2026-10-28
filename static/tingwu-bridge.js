function tingwuConnect(origin, key) {
    "use strict";
    if (location.origin !== "https://tingwu.aliyun.com") {
        alert("请先打开并登录通义听悟，再点击此书签。"); return;
    }
    if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin) || !/^[a-f0-9]{48}$/.test(key)) return;
    const popup = window.open(origin + "/transcribe#tingwu=" + key, "bili-tingwu-" + key);
    if (!popup) { alert("浏览器未打开工作台，请允许本次弹出窗口后重新点击连接书签。"); return; }
    if (window.__biliTingwuStop) window.__biliTingwuStop();
    const calls = new Map();
    const endpoints = {account: "/api/tingwu/account/info", parse: "/api/trans/parseNetSourceUrl",
        parse_status: "/api/trans/queryNetSourceParse", submit: "/api/trans/request",
        status: "/api/trans/request", find: "/api/trans/request",
        export: "/api/export/request", export_status: "/api/export/request"};
    async function request(method, params) {
        let body;
        switch (method) {
        case "account": break;
        case "parse": body = {action: "parseNetSourceUrl", version: "1.0", url: params.url}; break;
        case "parse_status": body = {action: "queryNetSourceParse", version: "1.0", taskId: params.taskId}; break;
        case "submit": body = {action: "putNetSourceUrl", version: "1.0", files: params.files}; break;
        case "status": body = {action: "getTransStatus", version: "1.0", userId: "", taskIds: params.taskIds,
            transIds: params.transIds, preview: 1}; break;
        case "find": body = {action: "getTransList", version: "1.0", userId: "", preview: 1,
            pageNo: Number.isInteger(params.pageNo) ? params.pageNo : 1, pageSize: 100, orderDesc: true,
            filter: {status: [0, 1, 2, 3, 4, 11], fileTypes: ["net_source"], showName: params.title,
                beginTime: "", endTime: "", mediaType: "", read: "", lang: "", client: "", shareUserId: ""}}; break;
        case "export": body = {action: "exportTrans", transIds: params.transIds, userId: "",
            exportDetails: [{docType: 1, fileType: 0, withSpeaker: true, withTimeStamp: true}]}; break;
        case "export_status": body = {action: "getExportStatus", exportTaskId: params.exportTaskId}; break;
        default: throw new Error("不支持的听悟操作。");
        }
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 65000);
        try {
            const response = await fetch(endpoints[method] + "?c=web", {method: body ? "POST" : "GET",
                credentials: "include", headers: {"Content-Type": "application/json", Accept: "application/json"},
                body: body ? JSON.stringify(body) : undefined, signal: controller.signal});
            if (!response.ok) throw new Error("听悟 HTTP " + response.status + "，请检查登录状态。");
            const result = await response.json();
            // 账号信息只用于确认登录，不把姓名、头像等资料发给本地工作台。
            if (method === "account") return {code: result.code, success: result.success,
                message: result.message, data: {loggedIn: String(result.code) === "0"}};
            return result;
        } finally { clearTimeout(timer); }
    }
    async function receive(event) {
        if (event.origin !== origin || event.source !== popup) return;
        const data = event.data;
        // 本地服务重启后只更新当前配对窗口的临时密钥，已安装的书签仍可使用。
        if (data?.type === "tingwu-bind" && /^[a-f0-9]{48}$/.test(data.key)) {
            key = data.key; popup.postMessage({type: "tingwu-ready", key, version: 3}, origin); return;
        }
        if (data?.key !== key) return;
        if (data.type === "tingwu-ping") { popup.postMessage({type: "tingwu-ready", key, version: 3}, origin); return; }
        if (data.type !== "tingwu-request" || !endpoints[data.method] || !/^[a-f0-9]{32}$/.test(data.id)) return;
        // 重发同一 ID 时复用 Promise，防止网络重连导致重复提交。
        if (!calls.has(data.id)) calls.set(data.id, request(data.method, data.params || {}).then(
            result => ({result}), error => ({error: error.name === "AbortError" ? "听悟响应超时" : error.message})));
        const result = await calls.get(data.id);
        popup.postMessage({type: "tingwu-result", key, id: data.id, ...result}, origin);
    }
    window.addEventListener("message", receive);
    window.__biliTingwuStop = () => window.removeEventListener("message", receive);
    popup.focus();
}
