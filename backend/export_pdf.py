"""导出 PDF 用的 HTML 渲染：把项目/场景数据渲染成结构化文档页面，浏览器打印为 PDF。"""

from __future__ import annotations

import html
from datetime import datetime

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; color: #1f2329; font-size: 12px; line-height: 1.7; margin: 0; }
h1 { font-size: 22px; border-bottom: 2px solid #2563eb; padding-bottom: 6px; }
h2 { font-size: 16px; margin: 26px 0 8px; border-left: 4px solid #2563eb; padding-left: 8px; page-break-after: avoid; }
h3 { font-size: 13.5px; margin: 16px 0 6px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 12px; page-break-inside: auto; }
th, td { border: 1px solid #cbd5e1; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #eff6ff; font-weight: 600; }
tr { page-break-inside: avoid; }
ul, ol { margin: 4px 0 10px; padding-left: 22px; }
.meta { color: #64748b; font-size: 11px; }
.scenario { page-break-before: always; }
.scenario:first-of-type { page-break-before: auto; }
.tag { display: inline-block; background: #eff6ff; color: #2563eb; border-radius: 4px; padding: 0 8px; margin: 0 4px 4px 0; font-size: 11px; }
blockquote { border-left: 3px solid #cbd5e1; margin: 6px 0; padding: 2px 10px; color: #475569; }
pre { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; font-size: 11px; }
.process-step { border: 1px solid #e2e8f0; border-radius: 8px; margin: 8px 0; padding: 8px 12px; page-break-inside: avoid; }
.process-step .step-head { font-weight: 700; margin-bottom: 4px; }
.actor-block { border-left: 3px solid #2563eb; background: #f8fafc; margin: 6px 0; padding: 4px 10px; }
.actor-block.system { border-left-color: #7c3aed; }
.actor-block .actor-name { font-weight: 600; }
.actor-block.system .actor-name { color: #7c3aed; }
.kind { display: inline-block; font-size: 10px; border-radius: 3px; padding: 0 5px; margin-right: 5px; background: #e2e8f0; color: #334155; }
.kind.act { background: #dbeafe; color: #1d4ed8; }
.kind.call { background: #fef3c7; color: #b45309; }
.kind.read { background: #dcfce7; color: #15803d; }
.kind.write { background: #fce7f3; color: #be185d; }
.kind.show { background: #ede9fe; color: #6d28d9; }
.cover { text-align: center; margin-top: 180px; page-break-after: always; }
.cover h1 { border: none; font-size: 28px; }
.toc li { margin: 2px 0; }
@media print { .no-print { display: none; } }
"""

KIND_LABEL = {"act": "动作", "call": "调用", "read": "获取", "write": "存储", "show": "展示"}


def esc(s) -> str:
    return html.escape(str(s or ""))


def render_notes(notes: str) -> str:
    """notes 中的 ```mermaid 块保留给前端 JS 渲染，其余文本原样。"""
    import re
    parts = []
    re_mmd = re.compile(r"```mermaid\s*\n([\s\S]*?)```")
    last = 0
    for m in re_mmd.finditer(notes):
        before = notes[last:m.start()].strip()
        if before:
            parts.append(f"<pre>{esc(before)}</pre>")
        parts.append(f"<pre class='mermaid'>{esc(m.group(1).strip())}</pre>")
        last = m.end()
    rest = notes[last:].strip()
    if rest:
        parts.append(f"<pre>{esc(rest)}</pre>")
    return "".join(parts)


def render_role_table(roles: list[dict]) -> str:
    rows = "".join(
        f"<tr><td><b>{esc(r['name'])}</b></td><td>{esc(r.get('abbr',''))}</td>"
        f"<td>{esc(r.get('core_work',''))}</td><td>{esc(r.get('permissions',''))}</td></tr>"
        for r in roles
    )
    return f"""<h2>角色定义</h2>
<table><tr><th style="width:18%">角色</th><th style="width:14%">英文简称</th><th>核心工作</th><th style="width:24%">权限特点</th></tr>{rows}</table>"""


def render_steps_table(steps: list[dict]) -> str:
    rows = "".join(
        f"<tr><td style='width:6%;text-align:center'>{esc(s['no'])}</td><td style='width:16%'>{esc(s['actor'])}</td>"
        f"<td style='width:36%'>{esc(s['action'])}</td><td>{esc(s.get('response',''))}</td></tr>"
        for s in steps
    )
    return f"<table><tr><th>步骤</th><th>角色</th><th>用户操作</th><th>系统响应</th></tr>{rows}</table>"


def render_list(items: list[str], ordered: bool = False) -> str:
    if not items:
        return "<p class='meta'>（无）</p>"
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(f"<li>{esc(i)}</li>" for i in items) + f"</{tag}>"


def render_process_spec(spec: dict | None) -> str:
    if not spec or not spec.get("stepDetails"):
        return ""
    parts = ['<h3>过程拆解</h3>']
    for step_no, detail in spec["stepDetails"].items():
        actors_html = ""
        for a in detail.get("actors", []):
            is_sys = a.get("kind") == "system"
            cls = "actor-block system" if is_sys else "actor-block"
            label = "（系统）" if is_sys else ""
            items = "".join(
                f"<li><span class='kind {esc(it.get('kind',''))}'>{esc(KIND_LABEL.get(it.get('kind',''), it.get('kind','')))}</span>{esc(it.get('text',''))}</li>"
                for it in a.get("items", [])
            )
            actors_html += f"<div class='{cls}'><span class='actor-name'>{esc(a['name'])}{label}</span><ul>{items}</ul></div>"
        parts.append(f"<div class='process-step'><div class='step-head'>步骤 {esc(step_no)}</div>{actors_html}</div>")
    return "".join(parts)


def render_scenario(sc: dict, process_spec: dict | None = None) -> str:
    d = sc["data"]
    parts = [f"<div class='scenario'><h2>场景 {esc(sc['code'])}：{esc(sc['title'])}</h2>"]
    if d.get("value"):
        parts.append(f"<blockquote><b>业务价值</b>：{esc(d['value'])}</blockquote>")
    if d.get("main_users"):
        parts.append("<p><b>主要用户</b>：" + "".join(f"<span class='tag'>{esc(u)}</span>" for u in d["main_users"]) + "</p>")
    if d.get("not_covered"):
        parts.append(f"<p><b>不覆盖</b>：<span class='meta'>{esc('、'.join(d['not_covered']))}</span></p>")
    if d.get("triggers"):
        parts.append("<h3>触发条件</h3>" + render_list(d["triggers"], ordered=True))
    if d.get("preconditions"):
        parts.append("<h3>前置条件</h3>" + render_list(d["preconditions"]))
    if d.get("steps"):
        parts.append("<h3>主流程</h3>" + render_steps_table(d["steps"]))
    for v in d.get("variants", []):
        parts.append(f"<h3>分支：{esc(v['name'])}</h3>" + render_steps_table(v.get("steps", [])))
    parts.append(render_process_spec(process_spec))
    if d.get("acceptance"):
        parts.append("<h3>验收标准</h3>" + render_list(d["acceptance"]))
    if d.get("exceptions"):
        rows = "".join(f"<tr><td style='width:35%'>{esc(e['condition'])}</td><td>{esc(e['handling'])}</td></tr>" for e in d["exceptions"])
        parts.append(f"<h3>异常分支</h3><table><tr><th>异常情况</th><th>系统行为</th></tr>{rows}</table>")
    if d.get("ui_material"):
        parts.append("<h3>关键界面 / 交互</h3><pre>" + esc("\n".join(d["ui_material"])) + "</pre>")
    if sc.get("pending"):
        rows = "".join(
            f"<tr><td style='width:8%;text-align:center'>{i+1}</td><td style='width:14%'>{esc(p.get('kind',''))}</td><td>{esc(p.get('text',''))}</td></tr>"
            for i, p in enumerate(sc["pending"])
        )
        parts.append(f"<h3>待确认 / 待办</h3><table><tr><th>序号</th><th>类型</th><th>事项</th></tr>{rows}</table>")
    if d.get("notes"):
        parts.append(f"<h3>流程图 / 补充说明</h3>{render_notes(d['notes'])}")
    parts.append("</div>")
    return "".join(parts)


MERMAID_HEAD = """<script src="/api/static/mermaid.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', async () => {
  if (!document.querySelector('pre.mermaid')) return;
  mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });
  await mermaid.run({ querySelector: 'pre.mermaid' });
  document.body.classList.add('mermaid-done');
});
</script>"""


def render_project_html(ws: dict, roles: list[dict], scenarios: list[dict], process_specs: dict[str, dict]) -> str:
    """整项目导出：封面 + 目录 + 角色定义 + 全部场景。"""
    date = datetime.now().strftime("%Y-%m-%d")
    toc = "".join(f"<li>场景 {esc(s['code'])}：{esc(s['title'])}</li>" for s in scenarios)
    body = f"""<div class='cover'><h1>{esc(ws['name'])}</h1>
<p class='meta'>{esc(ws.get('description',''))}</p><p class='meta'>导出时间：{date}</p></div>
<h2>目录</h2><ul class='toc'>{toc}</ul>
{render_role_table(roles)}
{''.join(render_scenario(s, process_specs.get(s['code'])) for s in scenarios)}"""
    return f"<html><head><meta charset='utf-8'><title>{esc(ws['name'])}</title><style>{CSS}</style>{MERMAID_HEAD}</head><body>{body}</body></html>"


def render_scenario_html(ws_name: str, sc: dict, process_spec: dict | None) -> str:
    body = f"<p class='meta'>{esc(ws_name)} · 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>" + render_scenario(sc, process_spec)
    return f"<html><head><meta charset='utf-8'><title>场景 {esc(sc['code'])} {esc(sc['title'])}</title><style>{CSS}</style>{MERMAID_HEAD}</head><body>{body}</body></html>"
