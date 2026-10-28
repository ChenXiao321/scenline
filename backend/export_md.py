"""从平台数据导出 Markdown 场景文档（导出式单向同步）。

定位：平台数据是唯一事实源，本模块把当前库内容重新生成为一份完整 Markdown。
导出的文档带「勿手改」水印——要改内容请回平台编辑，再重新导出。

结构对齐《业务场景梳理》约定：文档标题 → 角色定义表 → 按分组（domain）分节的
场景（业务价值/主要用户/触发/前置/主流程表/变体/界面素材/验收/异常/流程图备注），
再附各场景的待确认小节。步骤号统一输出为数字（库里可能存的是字符串）。
"""

from __future__ import annotations

import time

WATERMARK = (
    "> ⚠️ **本文件由场景流程原型平台导出，请勿手工编辑。** "
    "要修改场景内容请在平台中编辑后重新导出（平台数据为唯一事实源）。"
)


def _num(no) -> str:
    """步骤号统一为整数文本（库里可能存 "1" / 1 / "1.5"）。"""
    try:
        f = float(no)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return str(no)


def _cell(text: str) -> str:
    """表格单元格转义：换行转 <br>，竖线转 \\|。"""
    return (text or "").replace("|", "\\|").replace("\n", "<br>")


def _bullet(lines: list[str], ordered: bool) -> list[str]:
    out = []
    for i, t in enumerate(lines, 1):
        t = (t or "").strip()
        if not t:
            continue
        out.append(f"{i}. {t}" if ordered else f"- {t}")
    return out


def _step_table(steps: list[dict]) -> list[str]:
    out = ["| 步骤 | 用户操作 | 系统响应 |", "|:--:|:---|:---|"]
    for s in steps:
        action = _cell(s.get("action", ""))
        response = _cell(s.get("response", ""))
        out.append(f"| {_num(s.get('no',''))} | {action} | {response} |")
    return out


def _scenario_md(sc: dict) -> list[str]:
    """生成单个场景的 Markdown 行。sc 为 get_scenario 的返回（含 data/pending）。"""
    d = sc["data"]
    L: list[str] = [f"#### 场景 {sc['code']}：{sc['title']}", ""]

    if d.get("value"):
        L += [f"> **业务价值**：{d['value']}", ""]
    if d.get("main_users"):
        L.append(f"**主要用户**：{'、'.join(d['main_users'])}  ")
    if d.get("not_covered"):
        L.append(f"**不覆盖**：{'、'.join(d['not_covered'])}")
    if d.get("main_users") or d.get("not_covered"):
        L.append("")
    if d.get("triggers"):
        L += ["**触发条件**：", *_bullet(d["triggers"], ordered=True), ""]
    if d.get("preconditions"):
        L += ["**前置条件**：", *_bullet(d["preconditions"], ordered=False), ""]
    if d.get("steps"):
        L += ["**主流程**：", "", *_step_table(d["steps"]), ""]

    for v in d.get("variants", []):
        L += [f"##### {v['name']}", ""]
        if v.get("steps"):
            L += [*_step_table(v["steps"]), ""]

    if d.get("ui_material"):
        L += ["**关键界面 / 交互**："]
        L += [t if t.startswith((" ", "\t", "-", "*")) else f"- {t}" for t in d["ui_material"]]
        L.append("")

    if d.get("acceptance"):
        L += ["**验收标准**：", *_bullet(d["acceptance"], ordered=False), ""]
    if d.get("exceptions"):
        L += ["**异常分支**：", "", "| 异常情况 | 系统行为 |", "|:---|:---|"]
        for e in d["exceptions"]:
            L.append(f"| {_cell(e.get('condition',''))} | {_cell(e.get('handling',''))} |")
        L.append("")

    if d.get("notes"):
        L += ["**流程图 / 补充说明**：", "", d["notes"].strip(), ""]

    return L


def render_markdown(ws: dict, roles: list[dict], scenarios: list[dict]) -> str:
    """整项目 → 完整 Markdown 文档字符串。"""
    L: list[str] = [
        f"# {ws['name']}",
        "",
        WATERMARK,
        "",
        f"> 导出于 {time.strftime('%Y-%m-%d %H:%M')}；场景 {len(scenarios)} 个，角色 {len(roles)} 个。",
        "",
        "## 角色定义",
        "",
        "| 角色 | 英文/缩写 | 核心工作 | 权限 |",
        "|:---|:---|:---|:---|",
    ]
    for r in roles:
        L.append(f"| {_cell(r['name'])} | {_cell(r.get('abbr',''))} | {_cell(r.get('core_work',''))} | {_cell(r.get('permissions',''))} |")
    L.append("")

    # 按 domain 分节，domain 内按编号数值排序
    def code_key(sc):
        try:
            return (0, float(sc["code"]))
        except (TypeError, ValueError):
            return (1, sc["code"])

    domains: dict[str, list[dict]] = {}
    order: list[str] = []
    for sc in scenarios:
        dom = sc.get("domain") or "未分组"
        if dom not in domains:
            domains[dom] = []
            order.append(dom)
        domains[dom].append(sc)

    for dom in order:
        L += [f"## {dom}", ""]
        for sc in sorted(domains[dom], key=code_key):
            L += _scenario_md(sc)
        # 该分节内各场景的待确认小节
        for sc in sorted(domains[dom], key=code_key):
            pend = sc.get("pending") or []
            if pend:
                L += [f"#### 场景 {sc['code']} 待确认 / 待办", ""]
                for p in pend:
                    mark = {"待确认": "🔶", "待办": "📝", "已确认": "✅"}.get(p.get("kind"), "•")
                    L.append(f"- {mark} {p.get('text','')}")
                L.append("")

    return "\n".join(L).rstrip() + "\n"
