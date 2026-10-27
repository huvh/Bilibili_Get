"""@brief 各功能页面共用的顶部导航，标签顺序、样式和目标地址统一维护。"""
from html import escape

NAV_ITEMS = (("video", "视频下载", "/?tool=video"),
             ("transcribe", "批量转写", "/transcribe"),
             ("subtitle", "字幕提取", "/"),
             ("helper", "独立字幕助手", "/helper"),
             ("docx", "DOCX 转 TXT", "/docx"))

NAV_STYLE = """
.workspace-tabs{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0 0;padding:0}
.workspace-tabs a{display:inline-block;padding:9px 20px;border:1px solid #d8e3da;border-radius:8px;background:white;color:#758b7c;font:13px/1.8 "Segoe UI","Microsoft YaHei",sans-serif;text-decoration:none;white-space:nowrap}
.workspace-tabs a.active{background:#086b57;color:white;border-color:#086b57}
.workspace-tabs a:focus-visible{outline:3px solid #70b8a4;outline-offset:3px}
.topbar{height:82px;max-width:1240px;margin:auto;display:flex;align-items:center;justify-content:space-between;padding:0 40px;border-bottom:1px solid #e0e7e3}
.brand{font-size:18px;font-weight:700;text-decoration:none;color:#242b30;display:flex;align-items:center;gap:10px}
.brand-icon{background:#086b57;color:white;width:31px;height:31px;display:grid;place-items:center;border-radius:8px;font-size:18px}
.local-tag{font-size:9px;letter-spacing:1.2px;border:1px solid #cbd9d0;padding:3px 6px;border-radius:4px;color:#63776d;margin-left:4px}
.connection{font-size:12px;color:#65766e;display:flex;align-items:center;gap:7px}.connection i{width:6px;height:6px;border-radius:50%;background:#2a956e}
.helper-intro{padding:65px 0 31px}.helper-intro .eyebrow{margin:0 0 20px}
@media(max-width:680px){.topbar{height:65px;padding:0 20px}main{margin:0 auto;padding:0 18px}.workspace-tabs a{padding:9px 13px;font-size:12px}.helper-intro{padding-top:42px}}
"""


def render_navigation(active="subtitle", origin=""):
    links = []
    for key, label, target in NAV_ITEMS:
        selected = ' class="active" aria-current="page"' if key == active else ""
        links.append(f'<a id="{key}-mode" href="{escape(origin + target, quote=True)}"{selected}>{label}</a>')
    return '<nav class="workspace-tabs" aria-label="工作台功能">' + "".join(links) + "</nav>"
