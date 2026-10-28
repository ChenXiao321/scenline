"""通用场景文档解析器。

解析《业务场景梳理》风格的 Markdown 文档为结构化数据。
只识别约定结构（角色定义表、**主流程** 步骤表、**验收标准**、**异常分支**、
待确认小节、##### 分支 X 小节等），不绑定任何具体业务词汇。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# 系统行为者标记：主流程表中"用户操作"列出现这些值时，该步骤属于系统
SYSTEM_MARKERS = {"-", "—", "（系统）", "无", ""}


@dataclass
class Role:
    name: str
    abbr: str = ""
    core_work: str = ""
    permissions: str = ""


@dataclass
class Step:
    no: str
    actor: str  # 角色名，或 "系统"
    action: str  # 用户操作
    response: str = ""  # 系统响应
    raw_actor: str = ""  # 原文（调试/核对用）


@dataclass
class Variant:
    """场景内的一个流程变体（##### 分支 A / 前置流程 / 不一致处理 等小节）。"""

    name: str
    steps: list[Step] = field(default_factory=list)


@dataclass
class ExceptionBranch:
    condition: str
    handling: str


@dataclass
class Scenario:
    code: str  # 如 "1.1"、"3.3b"
    title: str
    domain: str = ""  # 所属分组标题（文档中间章节，如 "2.1 项目与仓库初始化"）
    value: str = ""  # 业务价值
    main_users: list[str] = field(default_factory=list)
    not_covered: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)  # 主流程
    variants: list[Variant] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    exceptions: list[ExceptionBranch] = field(default_factory=list)
    ui_material: list[str] = field(default_factory=list)  # **关键界面 / 交互** 小节原文（保留缩进）
    notes: str = ""  # 其余正文（流程图、说明块等）原样保留


@dataclass
class PendingItem:
    kind: str  # "待确认" | "待办" | "其他"
    text: str


@dataclass
class PendingSection:
    title: str  # 如 "场景 1.1 待确认 / 待办"
    scenario_code: str  # 关联的场景编号（从标题解析）
    items: list[PendingItem] = field(default_factory=list)


@dataclass
class Document:
    title: str
    roles: list[Role] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    pending: list[PendingSection] = field(default_factory=list)


# ---------------------------------------------------------------- 行级工具

_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}")


def is_table_row(line: str) -> bool:
    return line.strip().startswith("|")


def is_table_sep(line: str) -> bool:
    s = line.strip().strip("|")
    return bool(s) and all(set(cell.strip()) <= set(":- ") and "-" in cell for cell in s.split("|") if cell.strip())


def split_row(line: str) -> list[str]:
    """拆分 Markdown 表格行为单元格，处理单元格内转义的 \\|。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # 转义 \| -> 占位符，拆分后还原
    s = s.replace("\\|", "\x00")
    return [c.replace("\x00", "|").strip() for c in s.split("|")]


def strip_md(text: str) -> str:
    """去掉粗体/链接等行内标记，用于角色名匹配。"""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    t = re.sub(r"`(.+?)`", r"\1", t)
    return t.strip()


def heading_level(line: str) -> int:
    m = re.match(r"^(#{1,6})\s", line)
    return len(m.group(1)) if m else 0


# ---------------------------------------------------------------- 主解析

def parse_document(path: str | Path) -> Document:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    doc = Document(title="")
    i = 0
    n = len(lines)
    current_domain = ""
    scenario: Scenario | None = None
    pending: PendingSection | None = None
    in_role_section = False

    while i < n:
        line = lines[i]
        lvl = heading_level(line)

        if lvl:
            title_text = line.lstrip("#").strip()
            # 文档标题
            if lvl == 1 and not doc.title:
                doc.title = title_text
                i += 1
                continue
            # 角色定义章节
            if "角色定义" in title_text:
                in_role_section = True
                scenario = None
                pending = None
                i += 1
                continue
            if in_role_section and lvl <= 2 and "角色定义" not in title_text:
                in_role_section = False
            # 场景标题：#### 场景 X.Y：标题
            m = re.match(r"^场景\s*([0-9]+\.[0-9a-zA-Z]+)\s*[：:]\s*(.+)$", title_text)
            if m:
                scenario = Scenario(code=m.group(1), title=m.group(2).strip(), domain=current_domain)
                doc.scenarios.append(scenario)
                pending = None
                i = parse_scenario_body(lines, i + 1, scenario)
                continue
            # 待确认小节：#### 场景 X.Y 待确认 / 待办
            m = re.match(r"^场景\s*([0-9]+\.[0-9a-zA-Z]+)\s*(待确认.*|待办.*)$", title_text)
            if m:
                pending = PendingSection(title=title_text, scenario_code=m.group(1))
                doc.pending.append(pending)
                scenario = None
                i = parse_pending_body(lines, i + 1, pending)
                continue
            # 中间章节 -> 作为后续场景的分组名
            if lvl in (2, 3) and not title_text.startswith("场景"):
                current_domain = title_text
                scenario = None
                pending = None
            i += 1
            continue

        # 角色表格行
        if in_role_section and is_table_row(line) and not is_table_sep(line):
            cells = split_row(line)
            if len(cells) >= 2 and cells[0] and strip_md(cells[0]) != "角色" and ":-" not in line:
                name = strip_md(cells[0])
                if name and not name.startswith("-"):
                    doc.roles.append(Role(
                        name=name,
                        abbr=strip_md(cells[1]) if len(cells) > 1 else "",
                        core_work=strip_md(cells[2]) if len(cells) > 2 else "",
                        permissions=strip_md(cells[3]) if len(cells) > 3 else "",
                    ))
            i += 1
            continue

        i += 1

    # 角色去重（主表与补充表可能有重复角色名，保留信息更全的）
    merged: dict[str, Role] = {}
    for r in doc.roles:
        if r.name not in merged or len(r.core_work) > len(merged[r.name].core_work):
            merged[r.name] = r
    doc.roles = list(merged.values())
    return doc


# ---------------------------------------------------------------- 场景体

_SECTION_LABELS = ("主流程", "前置流程", "验收标准", "异常分支", "触发条件", "前置条件")


def flush_notes(target: Scenario, buf: list[str]):
    text = "\n".join(buf).strip()
    if text:
        target.notes = (target.notes + "\n\n" + text).strip() if target.notes else text


def parse_scenario_body(lines: list[str], start: int, sc: Scenario) -> int:
    """解析一个场景的全部内容，直到下一个同级/更高级标题。返回下一个未消费行号。"""
    i = start
    n = len(lines)
    notes_buf: list[str] = []

    while i < n:
        line = lines[i]
        lvl = heading_level(line)
        if lvl and lvl <= 4:  # 场景结束
            break
        if lvl == 5:
            # ##### 分支 X / 前置流程 / 不一致处理 等 -> 变体小节
            flush_notes(sc, notes_buf)
            notes_buf = []
            var_name = line.lstrip("#").strip()
            variant = Variant(name=var_name)
            i = parse_variant_body(lines, i + 1, variant)
            if variant.steps or variant.name:
                sc.variants.append(variant)
            continue

        stripped = strip_md(line).rstrip("：:")

        # **主流程**：后跟步骤表
        if stripped in ("主流程", "前置流程") and is_table_row(_peek(lines, i)):
            steps, i = parse_step_table(lines, i + 1, sc)
            sc.steps.extend(steps)
            continue
        # **触发条件**：列表
        if stripped == "触发条件":
            sc.triggers, i = parse_bullet_list(lines, i + 1)
            continue
        if stripped == "前置条件":
            sc.preconditions, i = parse_bullet_list(lines, i + 1)
            continue
        if stripped == "验收标准":
            sc.acceptance, i = parse_bullet_list(lines, i + 1)
            continue
        if stripped == "异常分支":
            if is_table_row(_peek(lines, i)):
                sc.exceptions, i = parse_exception_table(lines, i + 1)
            else:
                items, i = parse_bullet_list(lines, i + 1)
                sc.exceptions.extend(ExceptionBranch(condition=t, handling="") for t in items)
            continue
        # **关键界面 / 交互**：界面素材列表（保留原始缩进与层级）
        if stripped.replace(" ", "").startswith("关键界面"):
            sc.ui_material, i = parse_raw_list(lines, i + 1)
            continue

        # **主要用户** / **不覆盖** 行
        if stripped.startswith("主要用户"):
            sc.main_users = _split_names(line.split("：", 1)[-1] if "：" in line else line)
            i += 1
            continue
        if stripped.startswith("不覆盖"):
            sc.not_covered = _split_names(line.split("：", 1)[-1] if "：" in line else line)
            i += 1
            continue
        # 业务价值引用块
        if line.startswith(">") and "业务价值" in line:
            sc.value = strip_md(line.lstrip("> ").replace("**业务价值**：", "").replace("**业务价值**:", ""))
            i += 1
            continue

        notes_buf.append(line)
        i += 1

    flush_notes(sc, notes_buf)
    return i


def parse_variant_body(lines: list[str], start: int, variant: Variant) -> int:
    """解析 ##### 小节体：主要关心其中的步骤表。返回下一个未消费行号。"""
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        if heading_level(line):
            break
        stripped = strip_md(line).rstrip("：:")
        if stripped in ("主流程", "前置流程", "步骤") and is_table_row(_peek(lines, i)):
            steps, i = parse_step_table(lines, i + 1, None)
            variant.steps.extend(steps)
            continue
        # 有的变体直接放表格，无 **主流程** 标签
        if is_table_row(line) and not is_table_sep(line):
            cells = split_row(line)
            if cells and cells[0].strip() in ("步骤", ":step"):
                steps, i = parse_step_table(lines, i, None)
                variant.steps.extend(steps)
                continue
        i += 1
    return i


# ---------------------------------------------------------------- 表格/列表

def _peek(lines: list[str], i: int) -> str:
    """向后跳过空行，返回下一行非空内容。"""
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    return lines[j] if j < len(lines) else ""


def parse_step_table(lines: list[str], start: int, sc: Scenario | None) -> tuple[list[Step], int]:
    """从表头行开始解析 | 步骤 | 用户操作 | 系统响应 | 表。返回 (步骤, 下一行号)。"""
    steps: list[Step] = []
    i = start
    n = len(lines)
    # 表头
    header = split_row(lines[i])
    if header and "步骤" in header[0]:
        i += 1
        if i < n and is_table_sep(lines[i]):
            i += 1
    # 容错：表头前有空行或分隔行紧贴的情况
    while i < n and not is_table_row(lines[i]):
        if not lines[i].strip():
            i += 1
        else:
            break
    while i < n and is_table_row(lines[i]):
        if is_table_sep(lines[i]):  # 容错：跳过误入的分隔行
            i += 1
            continue
        cells = split_row(lines[i])
        if cells and cells[0].strip() == "步骤":  # 容错：跳过误入的表头行
            i += 1
            continue
        if len(cells) >= 3:
            no, action, response = cells[0], cells[1], cells[2]
            actor = "系统" if action.strip() in SYSTEM_MARKERS else ""
            steps.append(Step(no=no.strip(), actor=actor, action=action, response=response))
        elif len(cells) == 2:  # 容错：两列当作 步骤+动作
            steps.append(Step(no=cells[0].strip(), actor="", action=cells[1]))
        i += 1
    return steps, i


def parse_exception_table(lines: list[str], start: int) -> tuple[list[ExceptionBranch], int]:
    items: list[ExceptionBranch] = []
    i = start
    n = len(lines)
    i += 1  # 表头
    if i < n and is_table_sep(lines[i]):
        i += 1
    while i < n and is_table_row(lines[i]):
        cells = split_row(lines[i])
        if len(cells) >= 2:
            items.append(ExceptionBranch(condition=cells[0], handling=cells[1]))
        i += 1
    return items, i


def parse_bullet_list(lines: list[str], start: int) -> tuple[list[str], int]:
    """解析有序/无序列表，允许引用块与缩进续行，遇到非列表的非空行停止。"""
    items: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            # 空行：如果后面还是列表则续接，否则结束
            nxt = _peek(lines, i)
            if nxt and re.match(r"^(\s*)([-*+]|\d+[.、)])", nxt):
                i += 1
                continue
            i += 1
            break
        if s.startswith(">") and not line.startswith((" ", "\t")):
            break  # 顶层引用块是新内容（工具链配置示例等），不属于列表
        if is_table_row(s) and not is_table_sep(s):
            break  # 独立表格（如待确认小节的序号表）是新内容
        m = re.match(r"^(\s*)(?:[-*+]|\d+[.、)])\s+(.*)$", line)
        if m:
            items.append(strip_md(m.group(2)))
            i += 1
            continue
        # 缩进续行（含嵌套表格/代码行）视为列表的一部分
        if line.startswith((" ", "\t")):
            items.append(strip_md(s.lstrip("> ")))
            i += 1
            continue
        break
    return items, i


def parse_raw_list(lines: list[str], start: int) -> tuple[list[str], int]:
    """解析列表并保留原始行（含缩进与 `-` 标记），用于界面素材这类需要保留层级的场景。"""
    items: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            # 空行：后面还是列表则续接，否则结束
            nxt = _peek(lines, i)
            if nxt and re.match(r"^(\s*)([-*+]|\d+[.、)])", nxt):
                i += 1
                continue
            i += 1
            break
        if re.match(r"^(\s*)([-*+]|\d+[.、)])\s+", line):
            items.append(strip_md(line.rstrip()))
            i += 1
            continue
        if line.startswith((" ", "\t")):
            items.append(strip_md(line.rstrip()))  # 缩进续行保留
            i += 1
            continue
        break
    return items, i


def parse_pending_body(lines: list[str], start: int, pending: PendingSection) -> int:
    """待确认小节：优先解析 | 序号 | 事项 | 说明 | 表格，其次列表。"""
    i = start
    n = len(lines)
    while i < n and not lines[i].strip():
        i += 1
    if i < n and is_table_row(lines[i]):
        header = split_row(lines[i])
        i += 1
        if i < n and is_table_sep(lines[i]):
            i += 1
        # 找出"事项"列的位置（表头一般是 序号/事项/说明）
        item_col = 1
        for idx, h in enumerate(header):
            if "事项" in h:
                item_col = idx
                break
        while i < n and is_table_row(lines[i]):
            if not is_table_sep(lines[i]):
                cells = split_row(lines[i])
                if len(cells) > item_col:
                    text = cells[item_col]
                    kind = "待办" if "待办" in text[:6] else ("已确认" if "已确认" in text[:10] else "待确认")
                    pending.items.append(PendingItem(kind=kind, text=text))
            i += 1
        return i
    items, i = parse_bullet_list(lines, i)
    for text in items:
        kind = "待办" if "待办" in text[:6] else ("已确认" if "已确认" in text[:10] else "待确认")
        pending.items.append(PendingItem(kind=kind, text=text))
    return i


def _split_names(text: str) -> list[str]:
    parts = re.split(r"[、，,/]+", strip_md(text))
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------- 角色归属

ROLE_STOPWORDS = {"用户", "扩展", "系统", "-", "—", ""}


def resolve_actors(doc: Document) -> list[dict]:
    """根据角色库给每个步骤标注归属角色。返回识别告警列表。"""
    names = sorted((r.name for r in doc.roles), key=len, reverse=True)
    abbrs = {r.abbr: r.name for r in doc.roles if r.abbr}
    warnings: list[dict] = []

    def infer(step: Step, sc: Scenario) -> str:
        if step.actor == "系统":
            return "系统"
        text = step.action
        hits = [nm for nm in names if nm in text]
        if hits:
            return hits[0]
        for ab, nm in abbrs.items():
            if ab and ab in text:
                return nm
        if len(sc.main_users) == 1:
            return sc.main_users[0]
        if len(sc.main_users) > 1:
            warnings.append({"scenario": sc.code, "step": step.no, "reason": f"多主要用户{sc.main_users}，暂归第一角色，待人工指定", "action": text[:60]})
            return sc.main_users[0]
        warnings.append({"scenario": sc.code, "step": step.no, "reason": "未声明主要用户", "action": text[:60]})
        return "未指定"

    def infer_all(steps: list[Step], sc: Scenario) -> None:
        # 前后文继承：紧跟在已识别角色步骤后的未识别步骤，大概率还是同一角色在操作
        last_known: str | None = None
        for step in steps:
            actor = infer(step, sc)
            if last_known and actor in (sc.main_users[0] if sc.main_users else "未指定", "未指定") \
                    and len(sc.main_users) > 1 and step.actor != "系统":
                actor = last_known
                # 移除刚才 infer 产生的多余告警
                warnings[:] = [w for w in warnings if not (w["scenario"] == sc.code and w["step"] == step.no)]
            step.actor = actor
            if actor not in ("未指定", "系统"):
                last_known = actor

    for sc in doc.scenarios:
        infer_all(sc.steps, sc)
        for var in sc.variants:
            infer_all(var.steps, sc)
    return warnings


def to_dict(doc: Document) -> dict:
    return asdict(doc)


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1]
    doc = parse_document(path)
    warns = resolve_actors(doc)
    print(f"标题: {doc.title}")
    print(f"角色: {len(doc.roles)} 个 -> {[r.name for r in doc.roles]}")
    print(f"场景: {len(doc.scenarios)} 个")
    for sc in doc.scenarios:
        sys_n = sum(1 for s in sc.steps if s.actor == '系统')
        var_n = sum(len(v.steps) for v in sc.variants)
        print(f"  场景 {sc.code}: {sc.title} | 步骤 {len(sc.steps)}(系统{sys_n}) 变体 {len(sc.variants)}(+{var_n}步) 验收 {len(sc.acceptance)} 异常 {len(sc.exceptions)} 界面素材 {len(sc.ui_material)}")
    print(f"待确认小节: {len(doc.pending)} 个, 共 {sum(len(p.items) for p in doc.pending)} 条")
    print(f"归属告警: {len(warns)} 条")
    for w in warns[:10]:
        print(f"  [{w['scenario']} 步骤{w['step']}] {w['reason']}: {w['action']}")
