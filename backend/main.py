"""场景平台 FastAPI 入口。

开发模式：uvicorn main:app --port 8000
前端由 Vite 开发服务器代理 /api 到本服务；生产模式可直接静态托管 frontend/dist。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from db import PlatformStore
from parser.scenario_doc import parse_document, resolve_actors
from export_pdf import render_project_html, render_scenario_html
from export_md import render_markdown

# 无头浏览器生成 PDF（优先 Chrome，其次 Edge）
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def html_to_pdf(html_doc: str, out_path: Path, server_url: str = "http://127.0.0.1:8000") -> bool:
    """把 HTML 写成临时文件，用无头浏览器打开并打印成 PDF（等 mermaid 渲染完成）。成功返回 True。"""
    import subprocess
    import tempfile

    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    if not browser:
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        # 相对路径 /api/... 在无头浏览器里要指向运行中的服务
        f.write(html_doc.replace('src="/api/', f'src="{server_url}/api/'))
        html_path = f.name
    try:
        subprocess.run(
            [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             "--virtual-time-budget=15000",  # 等 JS（mermaid 渲染）执行完
             f"--print-to-pdf={out_path}", html_path],
            capture_output=True, timeout=90,
        )
        return out_path.exists()
    finally:
        Path(html_path).unlink(missing_ok=True)

store = PlatformStore(BASE_DIR.parent / "data")

from ai_service import AiService, parse_changes
from dev_service import DevManager, PROMPT_TEMPLATE, _slugify

ai = AiService(BASE_DIR.parent / "data" / "ai-config.json")
dev = DevManager(BASE_DIR.parent / "data" / "projects")

app = FastAPI(title="场景流程原型平台")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ImportRequest(BaseModel):
    name: str
    path: str
    description: str = ""


class CreateRequest(BaseModel):
    name: str
    description: str = ""


TEMPLATE_DOC = """# {name}

## 一、角色定义

| 角色 | 英文简称 | 核心工作 | 权限特点 |
|:---|:---|:---|:---|
| **示例角色** | Demo Role | 示例角色的核心工作 | 示例权限特点 |

#### 场景 1.1：示例场景

> **业务价值**：这是模板自带的示例场景，展示解析器识别的全部结构。改完文档后点「↻ 重新导入」即可更新。

**主要用户**：示例角色

**触发条件**：
1. 触发条件示例

**前置条件**：
- 前置条件示例

**主流程**：

| 步骤 | 用户操作 | 系统响应 |
|:--:|:---|:---|
| 1 | 用户做某事 | 系统响应示例 |
| 2 | - | 用户操作为 `-` 的步骤归为系统步骤 |

**关键界面 / 交互**：
- **界面名称**：
  - 界面元素说明

**验收标准**：
- [ ] 验收标准示例

**异常分支**：

| 异常情况 | 系统行为 |
|:---|:---|
| 异常示例 | 处理方式 |

#### 场景 1.1 待确认 / 待办

| 序号 | 事项 | 说明 |
|:--:|:---|:---|
| 1 | 待确认事项示例 | 补充说明 |
"""


@app.post("/api/workspaces")
def create_workspace(req: CreateRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "项目名称不能为空")
    if any(w["name"] == name for w in store.list_workspaces()):
        raise HTTPException(409, f"项目「{name}」已存在")
    ws_id = store.create_workspace(name, req.description.strip())
    return {"workspace_id": ws_id}


@app.post("/api/workspaces/import-template")
def create_workspace_from_template(req: CreateRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "项目名称不能为空")
    if any(w["name"] == name for w in store.list_workspaces()):
        raise HTTPException(409, f"项目「{name}」已存在")
    docs_dir = BASE_DIR.parent / "data" / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / f"{name}.md"
    if not path.exists():
        path.write_text(TEMPLATE_DOC.format(name=name), encoding="utf-8")
    doc = parse_document(path)
    warnings = resolve_actors(doc)
    pending_by_scenario: dict[str, list] = {}
    for p in doc.pending:
        pending_by_scenario.setdefault(p.scenario_code, []).extend(asdict(it) for it in p.items)
    ws_id = store.create_workspace(name, req.description.strip(), str(path))
    store.project(ws_id).import_document(asdict(doc), pending_by_scenario, warnings)
    return {"workspace_id": ws_id, "source_path": str(path), "scenarios": len(doc.scenarios)}


@app.delete("/api/workspaces/{ws_id}")
def delete_workspace(ws_id: int):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    store.delete_workspace(ws_id)
    return {"deleted": True}


@app.get("/api/workspaces")
def list_workspaces():
    return store.list_workspaces()


@app.get("/api/workspaces/{ws_id}")
def get_workspace(ws_id: int):
    ws = store.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    ws["roles"] = proj.list_roles()
    ws["scenarios"] = proj.list_scenarios()
    ws["warnings"] = proj.get_latest_warnings()
    return ws


class WsSettingsRequest(BaseModel):
    system_label: str = ""   # 系统步骤的显示名（如「扩展」），空串恢复默认「系统」


@app.put("/api/workspaces/{ws_id}/settings")
def update_workspace_settings(ws_id: int, req: WsSettingsRequest):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    patch = {"system_label": req.system_label.strip() or "系统"}
    return store.update_workspace_settings(ws_id, patch)


@app.post("/api/workspaces/import")
def import_workspace(req: ImportRequest):
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(400, f"文档不存在：{req.path}")
    doc = parse_document(path)
    warnings = resolve_actors(doc)

    pending_by_scenario: dict[str, list] = {}
    for p in doc.pending:
        pending_by_scenario.setdefault(p.scenario_code, []).extend(asdict(it) for it in p.items)

    ws_id = store.create_workspace(req.name, req.description, str(path))
    store.project(ws_id).import_document(asdict(doc), pending_by_scenario, warnings)
    return {
        "workspace_id": ws_id,
        "roles": len(doc.roles),
        "scenarios": len(doc.scenarios),
        "pending_sections": len(doc.pending),
        "warnings": warnings,
    }


@app.post("/api/workspaces/{ws_id}/reimport")
def reimport_workspace(ws_id: int):
    ws = store.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "工作域不存在")
    path = Path(ws["source_path"])
    if not path.exists():
        raise HTTPException(400, f"源文档不存在：{ws['source_path']}")
    doc = parse_document(path)
    warnings = resolve_actors(doc)
    pending_by_scenario: dict[str, list] = {}
    for p in doc.pending:
        pending_by_scenario.setdefault(p.scenario_code, []).extend(asdict(it) for it in p.items)
    store.project(ws_id).import_document(asdict(doc), pending_by_scenario, warnings)
    return {"reimported": True, "scenarios": len(doc.scenarios), "warnings": warnings}


@app.get("/api/workspaces/{ws_id}/scenarios/{code}")
def get_scenario(ws_id: int, code: str):
    proj = store.project(ws_id) if store.get_workspace(ws_id) else None
    if proj is None:
        raise HTTPException(404, "工作域不存在")
    sc = proj.get_scenario(code)
    if not sc:
        raise HTTPException(404, f"场景 {code} 不存在")
    sc["roles"] = proj.list_roles()
    return sc


class ScenarioSaveRequest(BaseModel):
    code: str = ""
    title: str
    domain: str = ""
    data: dict
    pending: list = []


@app.post("/api/workspaces/{ws_id}/scenarios")
def create_scenario(ws_id: int, req: ScenarioSaveRequest):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    code = req.code.strip()
    if not code or not req.title.strip():
        raise HTTPException(400, "场景编号与标题不能为空")
    if proj.get_scenario(code):
        raise HTTPException(409, f"场景 {code} 已存在")
    proj.create_scenario(code, req.title.strip(), req.domain.strip(), req.data, req.pending)
    return {"saved": True, "code": code}


@app.put("/api/workspaces/{ws_id}/scenarios/{code}")
def update_scenario(ws_id: int, code: str, req: ScenarioSaveRequest):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    old = proj.get_scenario(code)
    if not old:
        raise HTTPException(404, f"场景 {code} 不存在")
    if not req.title.strip():
        raise HTTPException(400, "场景标题不能为空")
    proj.update_scenario(code, req.title.strip(), req.domain.strip(), req.data, req.pending)
    # 变更历史：手工编辑（平台总结字段差异）
    changed = _diff_scenario_fields(old, req)
    if changed:
        proj.log_scenario_change(code, "手工编辑：" + "、".join(changed),
                                 json.dumps(changed, ensure_ascii=False), old.get("version", ""), "manual_edit")
    return {"saved": True}


def _diff_scenario_fields(old: dict, req) -> list[str]:
    """对比新旧场景，返回变更了的字段名列表（手工编辑的历史摘要用）。"""
    changed = []
    if req.title.strip() != old["title"]:
        changed.append("标题")
    if req.domain.strip() != old.get("domain", ""):
        changed.append("分组")
    od, nd = old["data"], req.data
    for k in set(od) | set(nd):
        if od.get(k) != nd.get(k):
            changed.append(k)
    if req.pending != old["pending"]:
        changed.append("pending")
    return changed


@app.get("/api/workspaces/{ws_id}/scenarios/{code}/changes")
def list_scenario_changes(ws_id: int, code: str):
    """场景变更历史（倒序）。详情页「变更历史」区用。"""
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    if not proj.get_scenario(code):
        raise HTTPException(404, f"场景 {code} 不存在")
    return {"changes": proj.list_scenario_changes(code)}


# ---------------------------------------------------------- 实测记录（试用工具时记不一致点）


class FindingRequest(BaseModel):
    text: str


@app.get("/api/workspaces/{ws_id}/scenarios/{code}/findings")
def list_findings(ws_id: int, code: str):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    return {"findings": store.project(ws_id).list_findings(code)}


@app.post("/api/workspaces/{ws_id}/scenarios/{code}/findings")
def add_finding(ws_id: int, code: str, req: FindingRequest):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    if not proj.get_scenario(code):
        raise HTTPException(404, f"场景 {code} 不存在")
    if not req.text.strip():
        raise HTTPException(400, "内容不能为空")
    return {"id": proj.add_finding(code, req.text.strip())}


@app.delete("/api/workspaces/{ws_id}/findings/{finding_id}")
def delete_finding(ws_id: int, finding_id: int):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    store.project(ws_id).delete_finding(finding_id)
    return {"deleted": True}


class ScenarioStatusRequest(BaseModel):
    status: str = ""
    version: str | None = None   # None=不动版本；""=清空版本


@app.put("/api/workspaces/{ws_id}/scenarios/{code}/status")
def set_scenario_status(ws_id: int, code: str, req: ScenarioStatusRequest):
    """设置场景状态/版本。status 为空串则只动版本。切到「已确认」必须有版本号。"""
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    sc = proj.get_scenario(code)
    if not sc:
        raise HTTPException(404, f"场景 {code} 不存在")
    from db import SCENARIO_STATUSES
    status = req.status.strip()
    if status and status not in SCENARIO_STATUSES:
        raise HTTPException(400, f"非法状态：{status}（可选：{'/'.join(SCENARIO_STATUSES)}）")
    # 约束：切到「已确认」时，生效后的版本号不能为空
    if status == "已确认":
        effective = req.version if req.version is not None else sc["version"]
        if not (effective or "").strip():
            raise HTTPException(400, "切到「已确认」必须先填版本号（该场景归属的工具发布版本）")
    proj.set_scenario_status(code, status or None, req.version)
    # 确认成功后：后台异步（重新）生成该版本的 AI 总结——新版本首个确认/已有版本新增确认场景都会刷新
    if status == "已确认":
        ver = (req.version if req.version is not None else sc["version"]).strip()
        threading.Thread(target=_gen_version_summary, args=(ws_id, ver), daemon=True).start()
    return {"saved": True}


def _gen_version_summary(ws_id: int, version: str) -> None:
    """后台任务：汇总该版本下所有场景的变更历史，让 AI 生成一句话版本总结（release notes 风格）。"""
    try:
        proj = store.project(ws_id)
        codes = proj.list_scenarios_by_version(version)
        if not codes:
            return
        lines = []
        for c in codes:
            sc = proj.get_scenario(c)
            if not sc:
                continue
            lines.append(f"## 场景 {c}：{sc['title']}")
            for ch in proj.list_scenario_changes(c, limit=50):
                if ch["summary"]:
                    lines.append(f"- {ch['summary']}")
        prompt = (
            f"你是工具发布经理。下面是工具版本 {version} 包含的场景及其变更明细。"
            "请写一段 3-6 句的版本总结（release notes 风格）：这个版本主要交付了什么能力、覆盖哪些场景、有什么重要行为变化。"
            "用中文，面向阅读变更记录的人，不要逐条复述，要归纳。直接输出总结正文，不要标题和前缀。\n\n"
            + "\n".join(lines)
        )
        text, _ = ai.chat([{"role": "user", "content": prompt}])
        text = (text or "").strip()
        if text:
            proj.save_version_summary(version, text)
    except Exception:
        logging.getLogger("uvicorn.error").exception("生成版本总结失败 ws=%s version=%s", ws_id, version)


@app.get("/api/workspaces/{ws_id}/version-summary")
def get_version_summary(ws_id: int, version: str):
    """某工具版本的 AI 总结（可能为空：后台还没生成完）。"""
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    return {"version": version, "summary": store.project(ws_id).get_version_summary(version)}


@app.delete("/api/workspaces/{ws_id}/scenarios/{code}")
def delete_scenario(ws_id: int, code: str):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    if not proj.get_scenario(code):
        raise HTTPException(404, f"场景 {code} 不存在")
    proj.delete_scenario(code)
    return {"deleted": True}


class UiSpecRequest(BaseModel):
    spec: dict


def _project_with_scenario(ws_id: int, code: str):
    """取项目库并确认场景存在；失败抛 404。"""
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    if not proj.get_scenario(code):
        raise HTTPException(404, f"场景 {code} 不存在")
    return proj


@app.get("/api/workspaces/{ws_id}/scenarios/{code}/ui-spec")
def get_ui_spec(ws_id: int, code: str):
    proj = _project_with_scenario(ws_id, code)
    return proj.get_ui_spec(code) or {"spec": None}


@app.put("/api/workspaces/{ws_id}/scenarios/{code}/ui-spec")
def save_ui_spec(ws_id: int, code: str, req: UiSpecRequest):
    proj = _project_with_scenario(ws_id, code)
    proj.save_ui_spec(code, req.spec)
    return {"saved": True}


@app.get("/api/workspaces/{ws_id}/scenarios/{code}/process-spec")
def get_process_spec(ws_id: int, code: str):
    proj = _project_with_scenario(ws_id, code)
    return proj.get_process_spec(code) or {"spec": None}


@app.put("/api/workspaces/{ws_id}/scenarios/{code}/process-spec")
def save_process_spec(ws_id: int, code: str, req: UiSpecRequest):
    proj = _project_with_scenario(ws_id, code)
    proj.save_process_spec(code, req.spec)
    return {"saved": True}


# ---------------------------------------------------------- 角色管理

class RoleRequest(BaseModel):
    name: str
    abbr: str = ""
    core_work: str = ""
    permissions: str = ""


@app.post("/api/workspaces/{ws_id}/roles")
def create_role(ws_id: int, req: RoleRequest):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "角色名称不能为空")
    if any(r["name"] == name for r in proj.list_roles()):
        raise HTTPException(409, f"角色「{name}」已存在")
    role_id = proj.create_role(name, req.abbr.strip(), req.core_work, req.permissions)
    return {"id": role_id, "saved": True}


@app.put("/api/workspaces/{ws_id}/roles/{role_id}")
def update_role(ws_id: int, role_id: int, req: RoleRequest):
    if not req.name.strip():
        raise HTTPException(400, "角色名称不能为空")
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    if not any(r["id"] == role_id for r in proj.list_roles()):
        raise HTTPException(404, "角色不存在")
    proj.update_role(role_id, {"name": req.name.strip(), "abbr": req.abbr.strip(), "core_work": req.core_work, "permissions": req.permissions})
    return {"saved": True}


@app.delete("/api/workspaces/{ws_id}/roles/{role_id}")
def delete_role(ws_id: int, role_id: int):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    if not any(r["id"] == role_id for r in proj.list_roles()):
        raise HTTPException(404, "角色不存在")
    proj.delete_role(role_id)
    return {"deleted": True}


# ---------------------------------------------------------- 导出

import tempfile
from urllib.parse import quote

from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse


def _collect_project(ws_id: int):
    """取项目全部场景 + 过程拆解规格。"""
    ws = store.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    scenarios = []
    process_specs = {}
    for s in proj.list_scenarios():
        full = proj.get_scenario(s["code"])
        if full:
            scenarios.append(full)
            ps = proj.get_process_spec(s["code"])
            if ps and ps.get("spec"):
                process_specs[s["code"]] = ps["spec"]
    return ws, proj, scenarios, process_specs


def _pdf_response(html_doc: str, filename: str):
    """HTML → PDF 文件下载；无浏览器时回退为 HTML 预览页。"""
    tmp = Path(tempfile.mkdtemp()) / filename
    if html_to_pdf(html_doc, tmp):
        return FileResponse(tmp, media_type="application/pdf", filename=filename)
    return HTMLResponse(html_doc)  # 回退：用户自行 Ctrl+P


@app.get("/api/static/mermaid.min.js")
def serve_mermaid():
    """给导出的 HTML 提供 mermaid 渲染库（前端已装的依赖，直接复用）。"""
    path = BASE_DIR.parent / "frontend" / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"
    if not path.exists():
        raise HTTPException(404, "mermaid 未安装（frontend/npm install mermaid）")
    return FileResponse(path, media_type="application/javascript")


@app.get("/api/workspaces/{ws_id}/export/html")
def export_project_html(ws_id: int):
    """整项目导出为 HTML（浏览器打开后打印为 PDF）。"""
    ws, proj, scenarios, process_specs = _collect_project(ws_id)
    return HTMLResponse(render_project_html(ws, proj.list_roles(), scenarios, process_specs))


@app.get("/api/workspaces/{ws_id}/export/pdf")
def export_project_pdf(ws_id: int):
    """整项目直接下载 PDF。"""
    ws, proj, scenarios, process_specs = _collect_project(ws_id)
    html_doc = render_project_html(ws, proj.list_roles(), scenarios, process_specs)
    return _pdf_response(html_doc, f"{ws['name']}.pdf")


@app.get("/api/workspaces/{ws_id}/export/md")
def export_project_md(ws_id: int):
    """整项目导出为 Markdown（导出式单向同步：平台为唯一事实源，文档勿手改）。"""
    ws, proj, scenarios, _ = _collect_project(ws_id)
    md = render_markdown(ws, proj.list_roles(), scenarios)
    return PlainTextResponse(
        md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(ws['name'] + '.md')}"},
    )


@app.get("/api/workspaces/{ws_id}/scenarios/{code}/export/html")
def export_scenario_html(ws_id: int, code: str):
    """单场景导出为 HTML。"""
    ws = store.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    sc = proj.get_scenario(code)
    if not sc:
        raise HTTPException(404, f"场景 {code} 不存在")
    ps = proj.get_process_spec(code)
    return HTMLResponse(render_scenario_html(ws["name"], sc, ps["spec"] if ps else None))


@app.get("/api/workspaces/{ws_id}/scenarios/{code}/export/pdf")
def export_scenario_pdf(ws_id: int, code: str):
    """单场景直接下载 PDF。"""
    ws = store.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    sc = proj.get_scenario(code)
    if not sc:
        raise HTTPException(404, f"场景 {code} 不存在")
    ps = proj.get_process_spec(code)
    html_doc = render_scenario_html(ws["name"], sc, ps["spec"] if ps else None)
    return _pdf_response(html_doc, f"场景{code}-{sc['title']}.pdf")


# ---------------------------------------------------------- AI 对话

class AiConfigRequest(BaseModel):
    provider: str = "openai"   # openai（Kimi/DeepSeek/通义等）| anthropic（Claude）
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class AiChatRequest(BaseModel):
    ws_id: int
    scenario_code: str = ""
    view: str = ""           # browse | play | tool | 其他
    selection: str = ""      # 用户选中的文字
    messages: list[dict]     # 历史 [{role, content:str|list}]（claude-cli 模式忽略，靠 session_id）
    image: str = ""          # base64 截图（dataURL 或裸 base64）
    session_id: str = ""     # claude-cli 多轮续接


class AiApplyRequest(BaseModel):
    ws_id: int
    change: dict             # parse_changes 拆出的一条指令


@app.get("/api/ai/config")
def get_ai_config():
    cfg = ai.load_config()
    return {
        "configured": ai.configured(),
        "provider": cfg.get("provider", "openai"),
        "model": cfg.get("model", ""),
        "base_url": cfg.get("base_url", ""),
    }


@app.put("/api/ai/config")
def save_ai_config(req: AiConfigRequest):
    cfg = ai.load_config()
    if req.api_key:
        cfg["api_key"] = req.api_key
    cfg["provider"] = req.provider or "openai"
    cfg["base_url"] = req.base_url.strip()
    cfg["model"] = req.model.strip()
    ai.save_config(cfg)
    return {"saved": True, "configured": ai.configured()}


def _scenario_brief(ws_id: int, code: str) -> str:
    """把当前场景数据随对话发给 AI。整体序列化、不截断——AI 侧修改规则是"按字段整体替换"，
    它必须看到每个字段的完整旧值（哪怕是空），否则会在"完整新值"里写丢它看不到的部分。"""
    if not code:
        return ""
    proj = store.project(ws_id)
    sc = proj.get_scenario(code)
    if not sc:
        return ""
    lines = [f"【当前场景 {sc['code']}：{sc['title']}】",
             f"场景数据：{json.dumps(sc['data'], ensure_ascii=False)}"]
    ps = proj.get_process_spec(code)
    if ps and ps.get("spec"):
        lines.append(f"过程拆解 spec：{json.dumps(ps['spec'], ensure_ascii=False)}")
    us = proj.get_ui_spec(code)
    if us and us.get("spec"):
        lines.append(f"工具原型 spec：{json.dumps(us['spec'], ensure_ascii=False)}")
    return "\n".join(lines)


@app.post("/api/ai/chat")
def ai_chat(req: AiChatRequest):
    if not ai.configured():
        logging.getLogger("uvicorn.error").warning(
            "ai/chat 400: AI 未就绪（provider=%s, claude_cli=%s）",
            ai.load_config().get("provider"), ai._claude_cli_path())
        raise HTTPException(400, "AI 未就绪（claude-cli 需本机装 claude，其他模式需配 API key）")

    # 组装上下文：平台位置 + 全局约定 + 选中内容 + 场景数据
    ws = store.get_workspace(req.ws_id)
    ctx = [f"【平台位置】项目「{ws['name'] if ws else req.ws_id}」"]
    proj = store.project(req.ws_id)
    global_notes = proj.get_global_notes().strip()
    if global_notes:
        ctx.append(f"【项目全局约定】（跨场景的架构/外部系统约定，回答与提 change 时都必须遵守）\n{global_notes}")
    if req.scenario_code:
        ctx.append(f"场景 {req.scenario_code}，视图：{req.view or '未知'}")
    if req.selection:
        ctx.append(f"【用户选中的内容】\n{req.selection}")
    brief = _scenario_brief(req.ws_id, req.scenario_code)
    if brief:
        ctx.append(brief)

    # 上一条回复若含解析失败的 change，自动提醒 AI 重发（用户不用手动发现坏 JSON）
    invalid_summary = proj.last_invalid_change_summary()
    if invalid_summary:
        ctx.append(
            "【系统提醒】你上一轮生成的修改指令（change 块）JSON 解析失败，未能应用，用户也没有可点的「应用」按钮。"
            f"失败原因：{invalid_summary}。如果用户的意图仍需落库修改，请重新生成该 change 块，"
            "确保 ```change 内是完整合法的 JSON（字符串闭合、无截断、无多余内容）。"
        )

    # 历史消息 + 本轮（上下文注入到最新一条 user 消息）
    messages = [dict(m) for m in req.messages]
    user_content: list[dict] = []
    if req.image:
        b64 = req.image.split(",", 1)[-1]
        user_content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}})
    text = "\n\n".join(ctx)
    # 前端把用户实际问题放在 messages 最后一条
    if messages and messages[-1]["role"] == "user":
        last = messages.pop()
        text = f"{text}\n\n【用户问题】\n{last['content'] if isinstance(last['content'], str) else last['content']}"
    user_content.append({"type": "text", "text": text})
    messages.append({"role": "user", "content": user_content})

    try:
        reply, session_id = ai.chat(messages, session_id=req.session_id)
    except Exception as e:
        raise HTTPException(502, f"AI 调用失败：{e}")
    body, changes = parse_changes(reply)
    had_invalid = any(c.get("_invalid") for c in changes)

    # 落库聊天历史（本轮 user + assistant；assistant 行记录是否含坏 change）
    user_q = ""
    for m in reversed(req.messages):
        if m.get("role") == "user":
            c = m.get("content")
            user_q = c if isinstance(c, str) else str(c)
            break
    proj.save_chat_message(session_id or "", "user", user_q, selection=req.selection, image=req.image)
    proj.save_chat_message(session_id or "", "assistant", reply, changes=changes, had_invalid=had_invalid)

    return {"reply": body, "changes": changes, "session_id": session_id}


@app.get("/api/ai/history")
def ai_history(ws_id: int, limit: int = 200):
    """取当前项目的聊天历史（重建面板用）。"""
    proj = store.project(ws_id)
    return {"messages": proj.list_chat_messages(limit)}


@app.delete("/api/ai/history")
def ai_history_clear(ws_id: int):
    proj = store.project(ws_id)
    proj.clear_chat_messages()
    return {"cleared": True}


@app.post("/api/ai/apply")
def ai_apply(req: AiApplyRequest):
    """应用一条经用户确认的 AI 修改指令。"""
    c = req.change
    ws_id = req.ws_id
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    code = c.get("code", "")
    action = c.get("action", "")

    # 全局约定不属于任何场景：先处理，不走下面的场景校验
    if action == "update_global_notes":
        text = c.get("text")
        if not isinstance(text, str):
            raise HTTPException(400, "update_global_notes 缺少 text（全局约定完整新文本）")
        proj.save_global_notes(text)
        # 记一条项目级变更（scenario_code 用占位符，便于在变更历史里识别来源）
        proj.log_scenario_change(
            "__global__", c.get("summary", "AI 更新全局约定"), "",
            "", "ai_apply",
        )
        return {"applied": True, "action": action, "code": ""}

    sc = proj.get_scenario(code)
    if not sc:
        raise HTTPException(404, f"场景 {code} 不存在")

    if action == "update_scenario":
        data = sc["data"]
        patch = c.get("data_patch", {})
        allowed = {"value", "main_users", "not_covered", "triggers", "preconditions", "steps", "variants", "acceptance", "exceptions", "ui_material", "notes", "role_views"}
        for k, v in patch.items():
            if k in allowed:
                data[k] = v
        # 列级字段：patch 带 domain/title 时一起更新（它们是独立列，不在 data JSON 里）
        domain = patch["domain"] if "domain" in patch else sc.get("domain", "")
        title = patch.get("title", sc["title"])
        # pending 是独立列（不在 data JSON 里）：patch 带 pending 时整体替换，否则保留旧值
        pending = patch["pending"] if "pending" in patch else sc["pending"]
        proj.update_scenario(code, title, domain, data, pending)
        # 变更历史：AI 的 summary 即变更总结，detail 记录动了哪些字段
        proj.log_scenario_change(
            code, c.get("summary", "AI 修改场景"),
            json.dumps({"fields": sorted(patch.keys())}, ensure_ascii=False),
            sc.get("version", ""), "ai_apply",
        )
    elif action == "update_process_spec":
        if not isinstance(c.get("spec"), dict):
            raise HTTPException(400, "缺少 spec")
        proj.save_process_spec(code, c["spec"])
        proj.log_scenario_change(code, c.get("summary", "AI 更新过程拆解"), "", sc.get("version", ""), "ai_apply")
    elif action == "update_ui_spec":
        if not isinstance(c.get("spec"), dict):
            raise HTTPException(400, "缺少 spec")
        proj.save_ui_spec(code, c["spec"])
        proj.log_scenario_change(code, c.get("summary", "AI 更新工具原型"), "", sc.get("version", ""), "ai_apply")
    else:
        raise HTTPException(400, f"未知 action：{action}")
    return {"applied": True, "action": action, "code": code}


# ---------------------------------------------------------- 全局约定


class GlobalNotesRequest(BaseModel):
    text: str = ""


@app.get("/api/workspaces/{ws_id}/global-notes")
def get_global_notes(ws_id: int):
    """项目级全局约定（跨场景的架构规则/外部系统约定等），注入开发 prompt 和对话 AI 上下文。"""
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    return {"text": store.project(ws_id).get_global_notes()}


@app.put("/api/workspaces/{ws_id}/global-notes")
def put_global_notes(ws_id: int, req: GlobalNotesRequest):
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    store.project(ws_id).save_global_notes(req.text)
    return {"saved": True}


# ---------------------------------------------------------- 后台工具开发

def _system_label(ws: dict) -> str:
    """从 workspace.settings 取工具显示名（GLink 等），空则回退项目名。"""
    s = ws.get("settings") or {}
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except Exception:
            s = {}
    return (s.get("system_label") or "").strip() or ws.get("name", "")


def _existing_block(work_dir: Path) -> str:
    """增量开发时给 AI 的现有代码概览：文件树 + 关键文件内容。"""
    if not work_dir.exists():
        return "（目录为空，全新开发）"
    files = [p for p in sorted(work_dir.rglob("*"))
             if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts and "__pycache__" not in p.parts]
    if not files:
        return "（目录为空，全新开发）"
    lines = ["已存在的文件（在其基础上增量修改，不要推倒重来）："]
    for p in files[:60]:
        lines.append(f"  {p.relative_to(work_dir)}")
    # 附 README 内容（最能说明现状），截断防爆
    readme = work_dir / "README.md"
    if readme.exists():
        content = readme.read_text(encoding="utf-8", errors="replace")[:3000]
        lines.append(f"\n现有 README.md 内容：\n{content}")
    return "\n".join(lines)


class DevelopRequest(BaseModel):
    codes: list[str]           # 本次要实现的场景号
    version: str = ""          # 本次开发归属的工具发布版本（成功后标到场景上）


class DevInputRequest(BaseModel):
    text: str                  # 用户对 [NEEDS_INPUT] 问题的回复


@app.get("/api/workspaces/{ws_id}/develop/target")
def develop_target(ws_id: int):
    """开发目标概览：工具名/目录/已有文件/可开发场景（含哪些已有 ui_spec）。"""
    ws = store.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "工作域不存在")
    proj = store.project(ws_id)
    label = _system_label(ws)
    work_dir = dev.tool_dir(ws_id, label)
    scenarios = []
    for s in proj.list_scenarios():
        us = proj.get_ui_spec(s["code"])
        scenarios.append({
            "code": s["code"], "title": s["title"], "domain": s["domain"],
            "has_ui_spec": bool(us and us.get("spec")),
            "status": s["status"], "version": s["version"],
        })
    files = []
    if work_dir.exists():
        files = [str(p.relative_to(work_dir)) for p in sorted(work_dir.rglob("*"))
                 if p.is_file() and ".git" not in p.parts and p.name != ".dev-log.md"]
    return {
        "tool_name": label,
        "work_dir": str(work_dir),
        "exists": work_dir.exists() and any(files),
        "files": files,
        "scenarios": scenarios,
        "ai_ready": ai.configured(),
    }


@app.post("/api/workspaces/{ws_id}/develop")
def develop_start(ws_id: int, req: DevelopRequest):
    """启动后台开发任务：组装场景+原型 prompt，起 claude agent 在 data/projects/ws-<id>/tools/<tool>/ 自主开发。"""
    ws = store.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "工作域不存在")
    if not req.codes:
        raise HTTPException(400, "请至少选择一个场景")
    cli = ai._claude_cli_path()
    if not cli:
        raise HTTPException(400, "未找到 claude CLI（后台开发依赖本机 claude）")
    proj = store.project(ws_id)
    label = _system_label(ws)
    work_dir = dev.tool_dir(ws_id, label)

    sc_lines, us_lines = [], []
    for code in req.codes:
        sc = proj.get_scenario(code)
        if not sc:
            raise HTTPException(404, f"场景 {code} 不存在")
        if sc["status"] != "已确认":
            raise HTTPException(400, f"场景 {code} 当前状态是「{sc['status']}」，只有「已确认」的场景才能开发")
        sc_lines.append(f"\n## 场景 {sc['code']}：{sc['title']}\n{json.dumps(sc['data'], ensure_ascii=False)}")
        us = proj.get_ui_spec(code)
        if us and us.get("spec"):
            us_lines.append(f"\n## 场景 {code} 的界面规格\n{json.dumps(us['spec'], ensure_ascii=False)}")
    if not us_lines:
        raise HTTPException(400, "所选场景都还没有工具原型（ui_spec），请先在工具原型里确认界面再开发")

    cfg = ai.load_config()
    global_notes = proj.get_global_notes().strip()
    prompt = PROMPT_TEMPLATE.format(
        tool_name=label,
        work_dir=str(work_dir),
        global_block=global_notes or "（无；本项目尚未填写全局约定，可在「🛠 开发工具」面板顶部的「📜 全局约定」里维护）",
        scenarios_block="".join(sc_lines),
        ui_specs_block="".join(us_lines) or "（无）",
        existing_block=_existing_block(work_dir),
    )
    try:
        task = dev.start(ws_id, label, req.codes, prompt, cli, cfg.get("model", ""))
        task.version = req.version.strip()  # 开发成功后标到场景上
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"started": True, "work_dir": str(work_dir), "status": task.status}


@app.post("/api/workspaces/{ws_id}/develop/mark-developed")
def develop_mark(ws_id: int):
    """开发任务完成后由前端调用：把本次场景标为「已开发」并记录版本。幂等。"""
    task = dev.get(ws_id)
    if not task or task.status != "done":
        return {"marked": False, "reason": "无已完成任务"}
    proj = store.project(ws_id)
    ver = getattr(task, "version", "")
    proj.mark_scenarios_developed(task.codes, ver)
    for code in task.codes:
        proj.log_scenario_change(code, f"工具开发完成，标记「已开发」{('（版本 ' + ver + '）') if ver else ''}",
                                 "", ver, "develop_mark")
    return {"marked": True, "codes": task.codes, "version": ver}


@app.get("/api/workspaces/{ws_id}/develop/status")
def develop_status(ws_id: int):
    task = dev.get(ws_id)
    if not task:
        return {"status": "idle", "log_tail": [], "files": [], "codes": []}
    return task.snapshot()


@app.post("/api/workspaces/{ws_id}/develop/input")
def develop_input(ws_id: int, req: DevInputRequest):
    task = dev.get(ws_id)
    if not task:
        raise HTTPException(404, "没有开发任务")
    try:
        task.answer(req.text)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"continued": True, "status": task.status}


@app.post("/api/workspaces/{ws_id}/develop/resume")
def develop_resume(ws_id: int):
    """error（网络/进程中断）后续开发：重启 claude 进程，重发上下文，从中断处继续。"""
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    try:
        task = dev.resume(ws_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"resumed": True, "status": task.status}


class DevFeedbackRequest(BaseModel):
    text: str = ""               # 手写的反馈内容
    include_findings: bool = False  # 同时汇总所有「待反馈」的实测记录


@app.post("/api/workspaces/{ws_id}/develop/feedback")
def develop_feedback(ws_id: int, req: DevFeedbackRequest):
    """done/error 后反馈修正：把用户反馈（可附带实测记录汇总）发给开发 agent 修正。"""
    if not store.get_workspace(ws_id):
        raise HTTPException(404, "工作域不存在")
    parts = []
    if req.text.strip():
        parts.append(req.text.strip())
    fed_ids: list[int] = []
    if req.include_findings:
        proj = store.project(ws_id)
        opens = proj.open_findings()
        if opens:
            lines = ["", "【真实环境实测记录的不一致点】"]
            for f in opens:
                lines.append(f"- 场景 {f['scenario_code']}：{f['text']}")
                fed_ids.append(f["id"])
            parts.append("\n".join(lines))
    if not parts:
        raise HTTPException(400, "反馈内容为空（写点内容，或勾选汇总实测记录）")
    try:
        task = dev.feedback(ws_id, "\n\n".join(parts))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    if fed_ids:
        store.project(ws_id).mark_findings_fed(fed_ids)
    return {"started": True, "status": task.status, "findings_fed": len(fed_ids)}


@app.post("/api/workspaces/{ws_id}/develop/stop")
def develop_stop(ws_id: int):
    task = dev.get(ws_id)
    if not task or not task.proc:
        return {"stopped": False}
    try:
        task.proc.kill()
    except Exception:
        pass
    task.status = "idle"
    task._log("【已停止】用户终止了开发进程")
    return {"stopped": True}


# 生产模式：托管前端构建产物
dist = BASE_DIR.parent / "frontend" / "dist"
if dist.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=dist, html=True), name="static")
