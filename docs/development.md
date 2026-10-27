# 开发与验证

在项目根目录运行命令。Python 3.10+ 用于服务与测试；Node.js 18+ 仅用于前端测试。

## 检查

```bash
python scripts/check.py
```

检查器会解析 Python 源码、检查 JavaScript 语法、运行 Python 单元 / HTTP 集成测试、
逐个运行扩展 / 分 P / 听悟桥接测试，以及独立助手的 Node 测试。
所有远端请求都由测试样本替代，不提交真实听悟任务，也不读取浏览器 Cookie。

单独运行：

```bash
python -m unittest discover -s tests/python -v
node tests/javascript/test_tingwu_pages.cjs
node tests/javascript/test_tingwu_bridge.cjs
node tests/javascript/test_cookie_extension.cjs
node tests/javascript/test_cookie_extension_page.cjs
node --test helper/test-helper.mjs
```

已安装 FFmpeg 时，Python 测试会生成短合成音视频，验证实际下载和合并；未安装时跳过该项。
手动验收时可在新端口启动服务，但 Cookie 扩展只支持 8765。

## 构建独立功能

```bash
python helper/build.py
python cookie_extension.py
python scripts/package_release.py
```

`helper/build.py` 更新 `helper/dist`、`static/helper.html` 和 `static/login-helper.js`。
`cookie_extension.py` 从固定四文件生成扩展 ZIP；修改扩展文件名单时须同步调整测试和清单。
`scripts/package_release.py` 会先重新生成这些产物，再生成四个功能包和 SHA-256 校验清单。

发布前应在无本地数据的解压目录运行检查器，确认源码与构建产物足以独立运行。
包中不带 FFmpeg 二进制；使用视频下载的用户按 README 安装依赖。

## 更改约定

- 功能边界保持在现有模块内，中文注释解释接口职责和特殊约束。
- 新页面 / 静态资源需加入 `server.py` 白名单；不开放任意文件路径。
- Cookie、听悟配对码、签名 CDN 地址和个人转写内容不得提交仓库。
- 不依赖本机绝对路径、真实字幕或第三方站点的打包脚本作为测试夹具。
- 更新独立助手后重新构建；已安装书签的用户需要手动替换旧书签。
