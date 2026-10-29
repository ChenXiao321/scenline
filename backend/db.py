"""SQLite 存储层：项目注册表（platform.db）+ 每项目独立库（projects/ws-<id>.db）。

- RegistryStore：仅 workspace 表，记录项目元数据。
- ProjectStore：单个项目的角色/场景/规格/导入报告，一个项目一个文件。
- PlatformStore：组合两者，对 API 层提供统一入口。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

# 场景生命周期状态（四态）。内容/规格被修改时自动回退到「草稿」；「已开发」由开发工具成功后设置；「已验证」仅手动。
SCENARIO_STATUSES = ["草稿", "已确认", "已开发", "已验证"]
_EDITABLE_RESET = {"已确认", "已开发", "已验证"}  # 这些状态下改内容 → 自动回退草稿

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    source_path TEXT DEFAULT '',
    created_at REAL NOT NULL
);
"""

# 注册表列级迁移（已有库补列）
REGISTRY_MIGRATIONS = [
    "ALTER TABLE workspace ADD COLUMN settings TEXT NOT NULL DEFAULT '{}'",  # 项目级设置，如 system_label
]

PROJECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS role (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    abbr TEXT DEFAULT '',
    core_work TEXT DEFAULT '',
    permissions TEXT DEFAULT '',
    sort INTEGER DEFAULT 0,
    UNIQUE(name)
);
CREATE TABLE IF NOT EXISTS scenario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',   -- Scenario 序列化 JSON
    pending TEXT NOT NULL DEFAULT '[]', -- 关联的待确认/待办条目 JSON
    origin TEXT NOT NULL DEFAULT 'import',
    edited INTEGER NOT NULL DEFAULT 0,
    UNIQUE(code)
);
CREATE TABLE IF NOT EXISTS import_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    warnings TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS ui_spec (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_code TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL,
    UNIQUE(scenario_code)
);
CREATE TABLE IF NOT EXISTS process_spec (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_code TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL,
    UNIQUE(scenario_code)
);
CREATE TABLE IF NOT EXISTS chat_message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT DEFAULT '',
    role TEXT NOT NULL,          -- user | assistant
    content TEXT NOT NULL,       -- 用户文本 / AI 回复原文（含 ```change 块）
    selection TEXT DEFAULT '',
    image TEXT DEFAULT '',       -- 截图 dataURL
    changes TEXT DEFAULT '[]',   -- parse_changes 拆出的指令 JSON（仅 assistant 行有）
    created_at REAL NOT NULL
);
"""

# 项目库列级迁移（已有库补列）
PROJECT_MIGRATIONS = [
    "ALTER TABLE chat_message ADD COLUMN had_invalid INTEGER NOT NULL DEFAULT 0",  # assistant 回复是否含解析失败的 change
    "ALTER TABLE scenario ADD COLUMN status TEXT NOT NULL DEFAULT '草稿'",  # 草稿/已确认/已开发/已验证
    "ALTER TABLE scenario ADD COLUMN version TEXT NOT NULL DEFAULT ''",     # 归属的工具发布版本，如 v0.1.0
]

# 项目级键值表（全局约定等，key 单行）
PROJECT_KV_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

# 场景变更历史：谁在什么时间改了什么（AI 应用 change / 手工编辑 / 开发标记）
PROJECT_CHANGELOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenario_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_code TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',   -- 一句话总结（AI 生成 / 手工编辑由平台总结字段差异）
    detail TEXT NOT NULL DEFAULT '',    -- 字段级明细 JSON：{field: {before:..., after:...}} 或文本
    version TEXT NOT NULL DEFAULT '',   -- 变更时场景归属的工具版本快照
    origin TEXT NOT NULL DEFAULT '',    -- ai_apply | manual_edit | develop_mark | ai_prototype 等
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chg_code ON scenario_change_log(scenario_code, id);
"""

# 实测记录：真实环境试用工具时记的"与预期不一致"的点，可汇总反馈给开发 agent
PROJECT_FINDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS finding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_code TEXT NOT NULL,
    text TEXT NOT NULL,              -- 不一致点描述
    status TEXT NOT NULL DEFAULT 'open',  -- open（待反馈） | fed（已反馈给开发）
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_finding_code ON finding(scenario_code, id);
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


from contextlib import contextmanager


@contextmanager
def _open(db_path: str | Path):
    """连接上下文：提交并真正关闭（with sqlite3.connect 只提交不关闭）。"""
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class RegistryStore:
    """项目注册表：platform.db，仅存 workspace 表。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with _open(self.db_path) as c:
            c.executescript(REGISTRY_SCHEMA)
            for sql in REGISTRY_MIGRATIONS:
                try:
                    c.execute(sql)
                except Exception:
                    pass  # 列已存在

    def list_workspaces(self) -> list[dict]:
        with _open(self.db_path) as c:
            rows = c.execute("SELECT * FROM workspace ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_workspace(self, ws_id: int) -> dict | None:
        with _open(self.db_path) as c:
            row = c.execute("SELECT * FROM workspace WHERE id = ?", (ws_id,)).fetchone()
        return dict(row) if row else None

    def create_workspace(self, name: str, description: str = "", source_path: str = "") -> int:
        with _open(self.db_path) as c:
            cur = c.execute(
                "INSERT INTO workspace(name, description, source_path, created_at) VALUES (?,?,?,?)",
                (name, description, source_path, time.time()),
            )
            return cur.lastrowid

    def delete_workspace(self, ws_id: int) -> None:
        with _open(self.db_path) as c:
            c.execute("DELETE FROM workspace WHERE id = ?", (ws_id,))

    def update_workspace_settings(self, ws_id: int, patch: dict) -> dict:
        """合并写入 workspace.settings（JSON），返回合并后的完整 settings。"""
        import json as _json
        with _open(self.db_path) as c:
            row = c.execute("SELECT settings FROM workspace WHERE id = ?", (ws_id,)).fetchone()
            if not row:
                return {}
            cur = _json.loads(row["settings"] or "{}")
            cur.update(patch)
            c.execute("UPDATE workspace SET settings = ? WHERE id = ?", (_json.dumps(cur, ensure_ascii=False), ws_id))
        return cur


class ProjectStore:
    """单个项目的数据库：角色/场景/界面规格/过程拆解/导入报告。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with _open(self.db_path) as c:
            c.executescript(PROJECT_SCHEMA)
            c.executescript(PROJECT_KV_SCHEMA)
            c.executescript(PROJECT_CHANGELOG_SCHEMA)
            c.executescript(PROJECT_FINDINGS_SCHEMA)
            for sql in PROJECT_MIGRATIONS:
                try:
                    c.execute(sql)
                except Exception:
                    pass  # 列已存在

    def _conn(self):
        return _open(self.db_path)

    def close(self) -> None:
        """Windows 下删文件前必须确保无句柄占用；连接都是 with 短连接，这里仅做 GC 兜底。"""
        import gc
        gc.collect()

    # ---------------------------------------------------------- 角色
    def create_role(self, name: str, abbr: str = "", core_work: str = "", permissions: str = "") -> int:
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM role").fetchone()
            cur = c.execute(
                "INSERT INTO role(name, abbr, core_work, permissions, sort) VALUES (?,?,?,?,?)",
                (name, abbr, core_work, permissions, row["s"]),
            )
            return cur.lastrowid

    def update_role(self, role_id: int, fields: dict) -> None:
        cols = [k for k in ("name", "abbr", "core_work", "permissions") if k in fields]
        if not cols:
            return
        sql = "UPDATE role SET " + ", ".join(f"{k} = ?" for k in cols) + " WHERE id = ?"
        with self._conn() as c:
            c.execute(sql, [*(fields[k] for k in cols), role_id])

    def delete_role(self, role_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM role WHERE id = ?", (role_id,))

    def list_roles(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM role ORDER BY sort, id").fetchall()
        return [dict(r) for r in rows]

    def role_names(self) -> set[str]:
        with self._conn() as c:
            return {r["name"] for r in c.execute("SELECT name FROM role").fetchall()}

    # ---------------------------------------------------------- 导入
    def import_document(self, doc: dict, pending_by_scenario: dict[str, list], warnings: list[dict]) -> None:
        """重导：清空未编辑的导入场景后写入；平台编辑过/手工创建的场景保留并记告警。"""
        with self._conn() as c:
            existing = {r["name"] for r in c.execute("SELECT name FROM role").fetchall()}
            c.execute("DELETE FROM scenario WHERE origin = 'import' AND edited = 0")
            kept = {r["code"] for r in c.execute("SELECT code FROM scenario").fetchall()}
            next_sort = c.execute("SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM role").fetchone()["s"]
            for r in doc["roles"]:
                if r["name"] in existing:
                    continue  # 已有角色：视为平台定义，保留用户编辑
                c.execute(
                    "INSERT INTO role(name, abbr, core_work, permissions, sort) VALUES (?,?,?,?,?)",
                    (r["name"], r.get("abbr", ""), r.get("core_work", ""), r.get("permissions", ""), next_sort),
                )
                next_sort += 1
            for sc in doc["scenarios"]:
                if sc["code"] in kept:
                    warnings.append({"scenario": sc["code"], "step": "-", "reason": "场景已在平台编辑或为手工创建，导入已跳过（保留平台版本）", "action": ""})
                    continue
                c.execute(
                    "INSERT INTO scenario(code, title, domain, data, pending, origin, edited) VALUES (?,?,?,?,?,'import',0)",
                    (
                        sc["code"],
                        sc["title"],
                        sc.get("domain", ""),
                        json.dumps(sc, ensure_ascii=False),
                        json.dumps(pending_by_scenario.get(sc["code"], []), ensure_ascii=False),
                    ),
                )
            c.execute(
                "INSERT INTO import_report(created_at, warnings) VALUES (?,?)",
                (time.time(), json.dumps(warnings, ensure_ascii=False)),
            )

    # ---------------------------------------------------------- 场景编辑
    def update_scenario(self, code: str, title: str, domain: str, data: dict, pending: list) -> None:
        """更新场景内容。内容变更时，若当前状态是 已确认/已开发/已验证，自动回退为「草稿」。"""
        with self._conn() as c:
            cur = c.execute("SELECT status FROM scenario WHERE code = ?", (code,)).fetchone()
            status = cur["status"] if cur else "草稿"
            if status in _EDITABLE_RESET:
                status = "草稿"
            c.execute(
                "UPDATE scenario SET title = ?, domain = ?, data = ?, pending = ?, edited = 1, status = ? WHERE code = ?",
                (title, domain, json.dumps(data, ensure_ascii=False), json.dumps(pending, ensure_ascii=False), status, code),
            )

    def set_scenario_status(self, code: str, status: str = None, version: str = None) -> None:
        """设置场景状态/版本（None 表示不动该字段）。"""
        sets, vals = [], []
        if status is not None:
            if status not in SCENARIO_STATUSES:
                raise ValueError(f"非法状态：{status}")
            sets.append("status = ?"); vals.append(status)
        if version is not None:
            sets.append("version = ?"); vals.append(version)
        if not sets:
            return
        vals.append(code)
        with self._conn() as c:
            c.execute(f"UPDATE scenario SET {', '.join(sets)} WHERE code = ?", vals)

    def mark_scenarios_developed(self, codes: list[str], version: str = "") -> None:
        """开发工具成功后：把场景标为「已开发」，记录所属工具版本。"""
        with self._conn() as c:
            for code in codes:
                if version:
                    c.execute("UPDATE scenario SET status = '已开发', version = ? WHERE code = ?", (version, code))
                else:
                    c.execute("UPDATE scenario SET status = '已开发' WHERE code = ?", (code,))

    # ---------------------------------------------------------- 发布版本（v1/v2… 自动递增，开发完成时分配）
    def next_version(self) -> str:
        """分配下一个发布版本号并推进计数器。首次发版按库里最大旧版本号初始化（如旧版到 1.1 → 下一个为 v2）。"""
        with self._conn() as c:
            row = c.execute("SELECT value FROM project_kv WHERE key = 'next_version'").fetchone()
            if row:
                n = int(row["value"])
            else:
                n = 1
                for r in c.execute("SELECT DISTINCT version FROM scenario WHERE version != ''").fetchall():
                    for num in re.findall(r"\d+", r["version"]):
                        n = max(n, int(num) + 1)
            c.execute(
                "INSERT INTO project_kv(key, value) VALUES ('next_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(n + 1),),
            )
        return f"v{n}"

    def assign_changes_version(self, codes: list[str], version: str) -> int:
        """发版回填：把指定场景下无版本归属的变更记录（草稿期/确认期修改）归入该版本。返回回填条数。"""
        with self._conn() as c:
            cur = c.execute(
                f"UPDATE scenario_change_log SET version = ? WHERE version = '' "
                f"AND scenario_code IN ({','.join('?' * len(codes))})",
                (version, *codes),
            )
            return cur.rowcount

    def get_version_desc(self, version: str) -> str:
        """用户手填的本版本描述（发布时可选）。"""
        with self._conn() as c:
            row = c.execute("SELECT value FROM project_kv WHERE key = ?", (f"version_desc:{version}",)).fetchone()
        return row["value"] if row else ""

    def save_version_desc(self, version: str, text: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO project_kv(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"version_desc:{version}", text),
            )

    def peek_next_version(self) -> str:
        """预览下一个版本号（不推进计数器），给开发页展示用。"""
        with self._conn() as c:
            row = c.execute("SELECT value FROM project_kv WHERE key = 'next_version'").fetchone()
            if row:
                return f"v{row['value']}"
            n = 1
            for r in c.execute("SELECT DISTINCT version FROM scenario WHERE version != ''").fetchall():
                for num in re.findall(r"\d+", r["version"]):
                    n = max(n, int(num) + 1)
        return f"v{n}"

    def create_scenario(self, code: str, title: str, domain: str, data: dict, pending: list) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO scenario(code, title, domain, data, pending, origin, edited) VALUES (?,?,?,?,?,'manual',1)",
                (code, title, domain, json.dumps(data, ensure_ascii=False), json.dumps(pending, ensure_ascii=False)),
            )

    def delete_scenario(self, code: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM scenario WHERE code = ?", (code,))

    # ---------------------------------------------------------- 查询
    def list_scenarios(self) -> list[dict]:
        """场景树用的轻量列表（附步骤/变体/待办计数，不含 data 正文）。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, code, title, domain, data, pending, origin, edited, status, version FROM scenario ORDER BY CAST(code AS REAL), code"
            ).fetchall()
        out = []
        for r in rows:
            d = {"id": r["id"], "code": r["code"], "title": r["title"], "domain": r["domain"], "origin": r["origin"], "edited": r["edited"],
                 "status": r["status"], "version": r["version"]}
            data = json.loads(r["data"])
            d["step_count"] = len(data.get("steps", []))
            d["variant_count"] = len(data.get("variants", []))
            d["pending_count"] = len(json.loads(r["pending"]))
            out.append(d)
        return out

    def scenario_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM scenario").fetchone()["n"]

    def role_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM role").fetchone()["n"]

    def get_scenario(self, code: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM scenario WHERE code = ?", (code,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["data"] = json.loads(d["data"])
        d["pending"] = json.loads(d["pending"])
        return d

    def get_latest_warnings(self) -> list[dict]:
        with self._conn() as c:
            row = c.execute("SELECT warnings FROM import_report ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["warnings"]) if row else []

    # ---------------------------------------------------------- 界面规格
    def get_ui_spec(self, code: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT spec, updated_at FROM ui_spec WHERE scenario_code = ?", (code,)
            ).fetchone()
        if not row:
            return None
        return {"spec": json.loads(row["spec"]), "updated_at": row["updated_at"]}

    def save_ui_spec(self, code: str, spec: dict) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO ui_spec(scenario_code, spec, updated_at) VALUES (?,?,?)
                   ON CONFLICT(scenario_code) DO UPDATE SET spec = excluded.spec, updated_at = excluded.updated_at""",
                (code, json.dumps(spec, ensure_ascii=False), time.time()),
            )
            # 原型规格变更也算内容变更：已确认/已开发/已验证 → 回退草稿
            cur = c.execute("SELECT status FROM scenario WHERE code = ?", (code,)).fetchone()
            if cur and cur["status"] in _EDITABLE_RESET:
                c.execute("UPDATE scenario SET status = '草稿' WHERE code = ?", (code,))

    # ---------------------------------------------------------- 过程拆解规格
    def get_process_spec(self, code: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT spec, updated_at FROM process_spec WHERE scenario_code = ?", (code,)
            ).fetchone()
        if not row:
            return None
        return {"spec": json.loads(row["spec"]), "updated_at": row["updated_at"]}

    def save_process_spec(self, code: str, spec: dict) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO process_spec(scenario_code, spec, updated_at) VALUES (?,?,?)
                   ON CONFLICT(scenario_code) DO UPDATE SET spec = excluded.spec, updated_at = excluded.updated_at""",
                (code, json.dumps(spec, ensure_ascii=False), time.time()),
            )
    # ---------------------------------------------------------- 聊天历史
    def save_chat_message(self, session_id: str, role: str, content: str,
                          selection: str = "", image: str = "", changes: list | None = None,
                          had_invalid: bool = False) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO chat_message(session_id, role, content, selection, image, changes, had_invalid, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (session_id, role, content, selection, image,
                 json.dumps(changes or [], ensure_ascii=False), 1 if had_invalid else 0, time.time()),
            )

    def last_invalid_change_summary(self) -> str | None:
        """最近一条含解析失败 change 的 assistant 回复的错误摘要（用于下一轮自动提醒 AI 重发）。无则 None。"""
        with self._conn() as c:
            row = c.execute(
                "SELECT changes FROM chat_message WHERE role = 'assistant' AND had_invalid = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        try:
            for ch in json.loads(row["changes"]):
                if ch.get("_invalid"):
                    return ch.get("summary", "")
        except Exception:
            pass
        return None

    def list_chat_messages(self, limit: int = 200) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT session_id, role, content, selection, image, changes, created_at FROM chat_message ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in reversed(rows):
            d = dict(r)
            d["changes"] = json.loads(d["changes"])
            out.append(d)
        return out

    def clear_chat_messages(self) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM chat_message")

    # ---------------------------------------------------------- 全局约定
    def get_global_notes(self) -> str:
        """项目级全局约定（架构规则/外部系统约定/命名规范等），注入开发 prompt 与对话 AI 上下文。"""
        with self._conn() as c:
            row = c.execute("SELECT value FROM project_kv WHERE key = 'global_notes'").fetchone()
        return row["value"] if row else ""

    def save_global_notes(self, text: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO project_kv(key, value) VALUES ('global_notes', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (text,),
            )

    # ---------------------------------------------------------- 场景变更历史
    def log_scenario_change(self, code: str, summary: str, detail: str = "",
                            version: str = "", origin: str = "") -> None:
        """追加一条场景变更记录。version 由调用方给（当时场景的版本快照）。"""
        with self._conn() as c:
            c.execute(
                "INSERT INTO scenario_change_log(scenario_code, summary, detail, version, origin, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (code, summary, detail, version, origin, time.time()),
            )

    def list_scenario_changes(self, code: str, limit: int = 100, version: str = None) -> list[dict]:
        """某场景的变更历史，倒序（最新在前）。version 非 None 时只取该版本归属的记录。"""
        sql = ("SELECT id, summary, detail, version, origin, created_at FROM scenario_change_log "
               "WHERE scenario_code = ?")
        vals: list = [code]
        if version is not None:
            sql += " AND version = ?"
            vals.append(version)
        sql += " ORDER BY id DESC LIMIT ?"
        vals.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, vals).fetchall()
        return [dict(r) for r in rows]

    def scenario_version_of(self, code: str) -> str:
        """场景当前归属的工具版本（记变更历史时做快照用）。"""
        with self._conn() as c:
            row = c.execute("SELECT version FROM scenario WHERE code = ?", (code,)).fetchone()
        return row["version"] if row else ""

    # ---------------------------------------------------------- 版本总结
    def get_version_summary(self, version: str) -> str:
        """某工具版本的 AI 总结（确认场景时后台生成）。"""
        with self._conn() as c:
            row = c.execute("SELECT value FROM project_kv WHERE key = ?", (f"version_summary:{version}",)).fetchone()
        return row["value"] if row else ""

    def save_version_summary(self, version: str, text: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO project_kv(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"version_summary:{version}", text),
            )

    def list_scenarios_by_version(self, version: str) -> list[str]:
        """某版本号下的所有场景号（生成版本总结的上下文用）。"""
        with self._conn() as c:
            rows = c.execute("SELECT code FROM scenario WHERE version = ? ORDER BY CAST(code AS REAL), code", (version,)).fetchall()
        return [r["code"] for r in rows]

    # ---------------------------------------------------------- 开发任务元信息（持久化，重启后可恢复"上次开发过"）
    def save_dev_meta(self, codes: list[str], version: str = "", finished_at: float = 0.0) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO project_kv(key, value) VALUES ('dev_meta', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps({"codes": codes, "version": version, "finished_at": finished_at}, ensure_ascii=False),),
            )

    def get_dev_meta(self) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM project_kv WHERE key = 'dev_meta'").fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except Exception:
            return None

    # ---------------------------------------------------------- 实测记录
    def add_finding(self, code: str, text: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO finding(scenario_code, text, status, created_at) VALUES (?,?, 'open', ?)",
                (code, text, time.time()),
            )
            return cur.lastrowid

    def list_findings(self, code: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, text, status, created_at FROM finding WHERE scenario_code = ? ORDER BY id DESC",
                (code,),
            ).fetchall()
        return [dict(r) for r in rows]

    def open_findings(self) -> list[dict]:
        """所有待反馈的实测记录（汇总反馈给开发 agent 用）。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, scenario_code, text, status, created_at FROM finding WHERE status = 'open' ORDER BY scenario_code, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_findings_fed(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conn() as c:
            c.execute(
                f"UPDATE finding SET status = 'fed' WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )

    def delete_finding(self, finding_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM finding WHERE id = ?", (finding_id,))


class PlatformStore:
    """对 API 层的统一入口：注册表 + 按项目 lazy-load 的项目库。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir = self.data_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.registry = RegistryStore(self.data_dir / "platform.db")
        self._projects: dict[int, ProjectStore] = {}

    def project_db_path(self, ws_id: int) -> Path:
        return self.projects_dir / f"ws-{ws_id}.db"

    def project(self, ws_id: int) -> ProjectStore:
        """按 ws_id 取项目库（不存在文件时创建空库；调用方需先确认项目已注册）。"""
        if ws_id not in self._projects:
            if not self.registry.get_workspace(ws_id):
                raise KeyError(f"工作域 {ws_id} 不存在")
            self._projects[ws_id] = ProjectStore(self.project_db_path(ws_id))
        return self._projects[ws_id]

    # ---------------------------------------------------------- 工作域（组合注册表与项目库统计）
    def list_workspaces(self) -> list[dict]:
        out = []
        for w in self.registry.list_workspaces():
            path = self.project_db_path(w["id"])
            if path.exists():
                p = self.project(w["id"])
                w["scenario_count"] = p.scenario_count()
                w["role_count"] = p.role_count()
            else:
                w["scenario_count"] = 0
                w["role_count"] = 0
            out.append(w)
        return out

    def get_workspace(self, ws_id: int) -> dict | None:
        return self.registry.get_workspace(ws_id)

    def update_workspace_settings(self, ws_id: int, patch: dict) -> dict:
        return self.registry.update_workspace_settings(ws_id, patch)

    def create_workspace(self, name: str, description: str = "", source_path: str = "") -> int:
        ws_id = self.registry.create_workspace(name, description, source_path)
        ProjectStore(self.project_db_path(ws_id))  # 立即建库
        return ws_id

    def delete_workspace(self, ws_id: int) -> None:
        self.registry.delete_workspace(ws_id)
        proj = self._projects.pop(ws_id, None)
        if proj is not None:
            proj.close()
        path = self.project_db_path(ws_id)
        if path.exists():
            path.unlink()
