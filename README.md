# Bilibili_Get

本地运行的 B 站视频、字幕与批量转写工作台。包含视频下载与音视频合并、字幕提取与格式导出、多 P 选择、听悟批量转写、DOCX 转 TXT，以及 Chrome / Edge 登录态获取扩展。

后端使用 Python 标准库，前端使用原生 HTML / CSS / JavaScript，无需 Node.js、数据库或前端构建工具即可运行。视频下载与合并需要额外安装 FFmpeg；语音转写需要你自己的听悟账号和浏览器登录会话。

## 功能入口

| 功能 | 页面 / 独立入口 | 能做什么 |
| --- | --- | --- |
| 视频下载 | `/?tool=video` | 解析 B 站视频、选择实际可用清晰度、下载并合并音视频、预览 MP4 |
| 批量转写 | `/transcribe` | 自动识别多 P、弹窗勾选分 P、提交听悟、跟踪进度、导出 DOCX / TXT |
| 字幕提取 | `/` | 读取已有字幕、导入字幕直链 / BCC JSON、搜索与复制、导出五种格式 |
| 独立字幕助手 | `/helper` 或 `helper/dist/B站字幕转TXT助手.html` | 在 B 站页用书签提取字幕并自动下载 TXT，离线 JSON 也能转换 |
| DOCX 转 TXT | `/docx` 或 `docx_to_txt.py` | 批量转换 DOCX / ZIP，保留正文、说话人、原有时间戳与表格文字 |
| Cookie 获取扩展 | `browser-extension/bili-cookie-extension/` | 从同一 Chrome / Edge 的 B 站登录态自动填入本地工作台 |

字幕提取读取视频已有字幕；没有字幕的内容可通过批量转写交给听悟处理。

## 快速开始

### 1. 下载

从 [Releases](https://github.com/huvh/Bilibili_Get/releases) 下载完整工作台或按功能下载独立包，也可克隆源码：

```bash
git clone https://github.com/huvh/Bilibili_Get.git
cd Bilibili_Get
```

需要 **Python 3.10 或更新版本**。不使用视频下载功能时，无需安装任何 Python 第三方库。

### 2. 启动工作台

Windows 双击 `start.cmd`，或在项目根目录执行：

```bash
python server.py --open
```

浏览器访问 **http://127.0.0.1:8765/**。服务只监听本机回环地址。关闭服务窗口或按 `Ctrl+C` 停止。

更换端口可执行 `python server.py --port 8766 --open`；**Cookie 扩展仅支持默认的 `127.0.0.1:8765`**。

### 3. 配置出站网络

项目默认使用 `http://127.0.0.1:7892` 代理访问 B 站及下载资源。本地页面走回环地址。
没有这个代理时，请在启动前禁用代理，或设置为你自己的地址。

PowerShell 直连：

```powershell
$env:BILI_PROXY = ''
python server.py --open
```

macOS / Linux 直连：

```bash
BILI_PROXY='' python server.py --open
```

使用其他代理时，把 `BILI_PROXY` 设置为完整代理地址，例如 `http://127.0.0.1:7890`。

### 4. 安装视频合并依赖（使用视频下载时需要）

以下命令将依赖安装到项目自己的 `.deps`，不会打包或上传本机依赖：

```bash
python -m pip install --target .deps -r requirements-video.txt
```

程序也支持系统 `PATH` 中的 `ffmpeg`，或通过环境变量 `FFMPEG_PATH` 指向 FFmpeg 可执行文件。
Python 依赖安装的网络代理可通过 `HTTP_PROXY`、`HTTPS_PROXY` 配置；这与应用的 `BILI_PROXY` 分开。

## 使用说明

### 视频下载

1. 打开顶部“视频下载”。
2. 粘贴 BV / av 视频地址或 B 站短链，点击解析。
3. 选择接口实际返回的清晰度，开始下载。
4. 服务下载音视频、调用 FFmpeg 合并，完成后可保存 MP4 或在页面预览。

支持带 `?p=2` 的分 P 地址、取消下载、错误清理。完成文件保存在 `downloads/<任务ID>/`。
高清档位可能需要登录及相应账号权限；只能获取 B 站当前返回且账号有权访问的媒体流。
单个任务默认最多下载 4096 MB，可通过 `BILI_MAX_DOWNLOAD_MB` 调整。

### 字幕提取与导出

- 粘贴 BV / av / ep、视频地址、短链或 B 站字幕 CDN 直链。
- 也可选择、拖放或粘贴 BCC JSON，支持旧助手的 `{title, language_tag, document}` 包装格式。
- 支持多字幕轨道、逐句时间轴、段落、文字稿、搜索与复制。
- 可导出 TXT、带时间戳 TXT、SRT、VTT、JSON。

需要登录时使用下方扩展，或手动填入自己的 Cookie。字幕直链可能因签名过期失效。

### 批量转写：自动选 P

1. 打开顶部“批量转写”，每行粘贴一条 B 站链接、BV 号、CDN 直链或已有听悟记录链接。
2. 粘贴后自动读取视频元数据；多 P 视频会弹窗显示每个 P 的序号、标题和时长。
3. 勾选需要转写的分 P，支持全选、全不选、重新选择。同一视频的多条链接会合并展示。
4. 默认勾选链接指定的 P；未指定时默认 P1。确认后仅提交选中的 P。取消后不会提交未确认的选择。
5. 将页面的“连接听悟工作台”按钮拖入书签栏，在已登录的 `https://tingwu.aliyun.com/` 页面点击书签。
6. 在配对打开的工作台点击“开始批量转写”。请保持听悟页与该工作台打开。

流程：**B 站媒体 CDN → 听悟解析文件 → 转写 → 导出 DOCX → 转换 UTF-8 TXT**。
结果支持单文件下载、整批 DOCX / TXT ZIP 下载。已有听悟记录只跟踪与导出，不重复转写。

每批最多 200 项（选 P 后计数），最多 3 个并行转写任务；导出串行排队，遇到限流退避重试。
暂停只停止本地跟踪；远端已提交任务继续执行。断线后可重新连接并“继续跟踪 / 重试导出”。
服务持久化任务状态，提交前记录 fileId，响应丢失时先查询，避免自动重复提交。

**听悟账号及额度由听悟管理。** 本项目不使用 AccessKey；听悟登录 Cookie 留在听悟页面。
网页内部接口发生变化时，批量转写可能需要更新本项目。

### 安装 Chrome / Edge Cookie 扩展

扩展源码与安装包都包含在仓库中，详见 [扩展说明](browser-extension/README.md)。

1. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`。
2. 开启“开发者模式”，点击“加载已解压的扩展程序”。
3. 选择仓库的 **`browser-extension/bili-cookie-extension`** 文件夹；从 ZIP 下载时先解压再选择包含 `manifest.json` 的文件夹。
4. 在同一浏览器配置登录 B 站，打开或刷新 `http://127.0.0.1:8765/transcribe`。
5. 工作台自动填入 Cookie，也可点击“b站cookie获取扩展”重新获取。

扩展 ZIP 位于 [`browser-extension/dist/b站cookie获取扩展.zip`](browser-extension/dist/b站cookie获取扩展.zip)。
浏览器首次需要手动加载扩展，网页无法静默安装。隐私窗口及其他浏览器不会共享登录态。

### 独立字幕助手

双击 `打开独立字幕助手.cmd` 或 `helper/dist/B站字幕转TXT助手.html`。
把“B 站字幕转 TXT”按钮拖入书签栏，在已登录的 B 站 BV / av 视频页点击书签：单轨直接下载 TXT，多轨先选语言。
安装好书签后不依赖 Python 或本地服务。已有 JSON 可直接拖入离线页面转换。

详见 [独立助手说明](helper/README.md)。修改源码后需重新生成并替换浏览器中安装的书签。

### DOCX 转 TXT

工作台 `/docx` 支持多个 DOCX 和含 DOCX 的 ZIP。命令行转换无需本地服务、Word 或其他依赖：

```bash
python docx_to_txt.py "导出文件夹"
python docx_to_txt.py "课程.docx" -o "转换结果"
python docx_to_txt.py "导出文件夹" --overwrite
```

Windows 也可把文件或文件夹拖到 `听悟DOCX转TXT.cmd`。默认保留原 DOCX、跳过已有 TXT。
命令行处理 ZIP 时先解压；网页可直接导入 ZIP。

## 按功能打包

在项目根目录执行：

```bash
python scripts/package_release.py
```

生成到 `dist/`：

| 压缩包 | 内容 |
| --- | --- |
| `Bilibili_Get.zip` | 完整工作台、扩展、独立助手、文档与测试；不含 FFmpeg / 本地运行数据 |
| `Bilibili_Get_Cookie_Extension.zip` | 可加载到 Chrome / Edge 的 Cookie 扩展 |
| `Bilibili_Get_Subtitle_Helper.zip` | 自包含字幕助手 HTML、书签代码和使用说明 |
| `Bilibili_Get_DOCX_to_TXT.zip` | 独立 DOCX 转换脚本、Windows 启动器与说明 |

打包使用固定目录与文件白名单。所有 ZIP 只含项目源码或构建产物，不携带本机 Cookie、配对码、任务和下载文件。

## 项目结构

```text
Bilibili_Get/
├── server.py                 # 本机 HTTP 服务与固定路由
├── bilibili.py               # B 站链接、元数据、分 P 和字幕网络适配
├── video.py                  # 媒体解析、下载、取消和 FFmpeg 合并
├── subtitles.py              # 字幕转换与格式导出
├── tingwu.py                 # 听悟任务、进度、归档、恢复与导出
├── docx_to_txt.py             # 独立文档转换器
├── cookie_extension.py       # 扩展信息与白名单打包
├── navigation.py             # 五功能统一导航
├── static/                   # 工作台五页面及各自交互脚本
├── browser-extension/        # 登录态扩展源码与 ZIP
├── helper/                   # 自包含字幕助手源码与生成页面
├── tests/python/             # 后端与 HTTP 集成测试
├── tests/javascript/         # 分 P、听悟桥接、Cookie 扩展测试
├── scripts/                  # 验证与按功能发布打包
└── docs/                     # 架构、数据边界与开发说明
```

## 数据与验证

Cookie 不写入项目文件、日志、浏览器存储或会话缓存；本地后端只把 B 站 Cookie 发往 B 站 API。
听悟与工作台通过限定来源、配对窗口和本机随机配对码通信，配对码保存在本地运行目录。
任务、CDN 地址及导出结果保存在 `downloads/`，属于本地数据，不应提交仓库。

详见 [架构与数据边界](docs/architecture.md) 和 [开发与验证](docs/development.md)。
完整离线测试：

```bash
python scripts/check.py
```

Node.js 18+ 仅用于开发时的 JavaScript 测试，不影响工作台运行。
测试覆盖转换边界、HTTP 访问限制、媒体下载恢复、桥接去重、扩展权限、分 P 选择与提交。
测试使用合成数据和协议样本，不携带第三方字幕正文或个人账号状态；FFmpeg 集成测试在未安装 FFmpeg 时会跳过。

## 常见问题

- **无法连接 B 站**：检查 `BILI_PROXY`，无本地代理时设为空字符串。登录权限、风控或签名过期也会导致失败。
- **没有某档清晰度 / 字幕**：先确认视频本身提供该资源，并使用有相应权限的登录态。
- **扩展未检测到**：使用安装扩展的 Chrome / Edge 配置，地址固定为 `http://127.0.0.1:8765`，刷新页面。
- **没有 FFmpeg**：执行视频依赖安装命令，或配置 `FFMPEG_PATH`。
- **听悟断线**：回到已登录的听悟网页再次点击连接书签，在配对工作台恢复跟踪。
- **更新后接口不存在**：先停止旧服务，再启动新版本并刷新页面。不要同时运行多个服务实例。

请仅处理自己有权访问和下载的内容，并遵守平台规则及内容授权。
