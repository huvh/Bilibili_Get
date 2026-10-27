/** @brief 在隔离环境验证连接助手的消息边界、协议参数和提交去重。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
    const origin = 'http://127.0.0.1:8765', key = 'a'.repeat(48);
    const requests = [], replies = [];
    let receive;
    const popup = {focus() {}, postMessage(value, target) {replies.push({value, target});}};
    const window = {open(url) {assert.equal(url, origin + '/transcribe#tingwu=' + key); return popup;},
        addEventListener(type, handler) {receive = handler;}, removeEventListener() {}};
    const sandbox = {window, location: {origin: 'https://tingwu.aliyun.com'}, alert: () => {},
        setTimeout, clearTimeout, AbortController, Map,
        fetch: async (url, options) => {
            requests.push({url, options});
            return {ok: true, json: async () => ({code: '0', success: true, data: {privateName: 'do not forward'}})};
        }};
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync('static/tingwu-bridge.js', 'utf8') + `\ntingwuConnect('${origin}', '${key}');`, sandbox);
    const call = (id, method, params = {}, extra = {}) => receive({origin, source: popup,
        data: {type: 'tingwu-request', key, id, method, params}, ...extra});
    await call('1'.repeat(32), 'submit', {files: [{fileId: 'f1'}]}, {origin: 'https://untrusted.example'});
    assert.equal(requests.length, 0);
    await Promise.all([call('2'.repeat(32), 'submit', {files: [{fileId: 'f1'}]}),
        call('2'.repeat(32), 'submit', {files: [{fileId: 'f1'}]})]);
    assert.equal(requests.length, 1, '同一请求 ID 只提交一次');
    assert.equal(JSON.parse(requests[0].options.body).action, 'putNetSourceUrl');
    assert.equal(requests[0].options.credentials, 'include');
    await call('3'.repeat(32), 'export', {transIds: ['t1']});
    assert.deepEqual(JSON.parse(requests[1].options.body).exportDetails,
        [{docType: 1, fileType: 0, withSpeaker: true, withTimeStamp: true}]);
    await call('4'.repeat(32), 'account');
    assert.equal(JSON.stringify(replies.at(-1)).includes('privateName'), false);
    await receive({origin, source: popup, data: {type: 'tingwu-bind', key: 'b'.repeat(48)}});
    assert.equal(replies.at(-1).value.key, 'b'.repeat(48));
    assert.equal(replies.at(-1).value.type, 'tingwu-ready');
    console.log('PASS: origin/window checks, duplicate submit prevention, DOCX export contract, account data minimization, session rebinding');
})().catch(error => {console.error(error); process.exitCode = 1;});
