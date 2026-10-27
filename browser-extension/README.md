# B 站 Cookie 获取扩展

Chrome / Edge Manifest V3 扩展，为本地工作台提供同一浏览器配置下的 B 站登录态。

## 安装

1. 下载 `dist/b站cookie获取扩展.zip` 后解压，或直接使用仓库的 `bili-cookie-extension/` 源码目录。
2. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`。
3. 开启开发者模式，点击“加载已解压的扩展程序”。
4. 选择含 `manifest.json` 的 `bili-cookie-extension` 文件夹。
5. 在同一浏览器配置登录 B 站，启动本地工作台，打开 `http://127.0.0.1:8765/transcribe`。

安装或点击扩展图标会打开工作台。视频、字幕和批量转写页面会自动填入 Cookie，
提交前自动补取；页面按钮也可手动获取。退出 B 站登录后自动填入的旧值会清空，手工输入保留。

## 文件与权限

- `manifest.json`：扩展声明；`background.js`：读取适用于 B 站 API 的 Cookie；
  `content.js`：限定页面与扩展之间的桥接；`README.txt`：随 ZIP 附带的安装说明。
- 只申请 `cookies`、B 站 HTTPS 域名和回环主机权限。
- 主机匹配规则涵盖本机端口，代码进一步校验 `http://127.0.0.1:8765` 和允许的页面。
- 只读 Cookie（含 HttpOnly），不修改 Cookie，不申请历史、密码、下载或所有网站权限。
- Cookie 仅经内存传给本地密码输入框。扩展不上传、不记录、不存储 Cookie；后端仅向 B 站 API 发送。
- 隐私模式不启用；Codex 内置浏览器、其他浏览器及其他配置不会共享该扩展的登录态。

## 打包与测试

项目根目录执行：

```bash
python cookie_extension.py
node tests/javascript/test_cookie_extension.cjs
node tests/javascript/test_cookie_extension_page.cjs
python -m unittest discover -s tests/python -p test_cookie_extension.py
```

ZIP 只包含上面四个固定文件，不包含任何本机状态或项目配置。
