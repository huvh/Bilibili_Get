B站Cookie获取扩展 1.0.0（Chrome / Edge，Manifest V3）

首次安装：
1. Chrome 打开 chrome://extensions；Edge 打开 edge://extensions。
2. 开启“开发者模式”，点击“加载已解压的扩展程序”。
3. 选择包含 manifest.json 的 bili-cookie-extension 文件夹。
   本机可直接选择网站指引中的现成目录；其他机器先解压下载的 ZIP。
4. 用同一浏览器配置登录 B 站，打开 http://127.0.0.1:8765/transcribe。
   安装时会自动打开工作台，已有页面也可刷新后使用。

安装后：
打开视频、字幕或批量转写页面，登录态会自动填入；提交前会自动补取。
网站上的“b站cookie获取扩展”按钮也可以随时重新获取。
浏览器隐私模式、Codex 内置浏览器与其他浏览器不共享此扩展或登录态。

权限与数据：
仅申请 cookies、HTTPS B 站域名和 127.0.0.1 主机权限。
Chrome 的主机匹配规则覆盖本机端口，代码额外限定 8765 和工作台允许的页面。
只读 https://api.bilibili.com/ 适用的 Cookie（含 HttpOnly），不修改 Cookie。
Cookie 只经内存桥接到本机工作台密码输入框，不保存、不打印、不上传。
本地工具仍仅向 B 站 API 发送 Cookie；听悟账号沿用原连接助手。
不申请所有网站、下载、浏览器历史、storage 或密码读取权限。

普通网站无法静默安装本地扩展包，首次需用户在扩展管理页加载；
不需要关闭浏览器安全设置或运行安装程序。
