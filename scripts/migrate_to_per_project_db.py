# 一次性迁移：把单库 data/platform.db 拆成 注册表 + 每项目独立库 data/projects/ws-<id>.db
# 用法：.venv\Scripts\python.exe -I scripts\migrate_to_per_project_db.py
# 原 platform.db 会备份为 platform.db.bak（已存在则不覆盖），迁移后可手动删除。
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OLD_DB = DATA / "platform.db"
BACKUP = DATA / "platform.db.bak"
PROJECTS = DATA / "projects"

sys.path.insert(0, str(ROOT / "backend"))
from db import PROJECT_SCHEMA, REGISTRY_SCHEMA  # noqa: E402


def main():
    if not OLD_DB.exists():
        print("platform.db 不存在，无需迁移")
        return
    if PROJECTS.exists() and any(PROJECTS.glob("ws-*.db")):
        print("projects/ 下已有项目库，疑似已迁移过，退出")
        return

    src = sqlite3.connect(OLD_DB)
    src.row_factory = sqlite3.Row

    workspaces = src.execute("SELECT * FROM workspace ORDER BY id").fetchall()
    if not workspaces:
        print("platform.db 中没有项目，无需迁移")
        return

    PROJECTS.mkdir(parents=True, exist_ok=True)

    # 1. 备份原库
    if not BACKUP.exists():
        shutil.copy2(OLD_DB, BACKUP)
        print(f"已备份原库 -> {BACKUP.name}")

    # 2. 每个项目导出到独立库
    for w in workspaces:
        ws_id = w["id"]
        dst_path = PROJECTS / f"ws-{ws_id}.db"
        dst = sqlite3.connect(dst_path)
        dst.executescript(PROJECT_SCHEMA)

        roles = src.execute("SELECT name, abbr, core_work, permissions, sort FROM role WHERE workspace_id = ? ORDER BY id", (ws_id,)).fetchall()
        dst.executemany("INSERT INTO role(name, abbr, core_work, permissions, sort) VALUES (?,?,?,?,?)", [tuple(r) for r in roles])

        scenarios = src.execute("SELECT code, title, domain, data, pending, origin, edited FROM scenario WHERE workspace_id = ? ORDER BY id", (ws_id,)).fetchall()
        dst.executemany("INSERT INTO scenario(code, title, domain, data, pending, origin, edited) VALUES (?,?,?,?,?,?,?)", [tuple(s) for s in scenarios])

        reports = src.execute("SELECT created_at, warnings FROM import_report WHERE workspace_id = ? ORDER BY id", (ws_id,)).fetchall()
        dst.executemany("INSERT INTO import_report(created_at, warnings) VALUES (?,?)", [tuple(r) for r in reports])

        ui = src.execute("SELECT scenario_code, spec, updated_at FROM ui_spec WHERE workspace_id = ?", (ws_id,)).fetchall()
        dst.executemany("INSERT INTO ui_spec(scenario_code, spec, updated_at) VALUES (?,?,?)", [tuple(r) for r in ui])

        ps = src.execute("SELECT scenario_code, spec, updated_at FROM process_spec WHERE workspace_id = ?", (ws_id,)).fetchall()
        dst.executemany("INSERT INTO process_spec(scenario_code, spec, updated_at) VALUES (?,?,?)", [tuple(r) for r in ps])

        dst.commit()
        dst.close()
        print(f"项目 {ws_id}「{w['name']}」: {len(roles)} 角色 / {len(scenarios)} 场景 / {len(ui)} ui_spec / {len(ps)} process_spec -> {dst_path.name}")

    # 3. 重写 platform.db 为纯注册表
    src.close()
    reg_path = DATA / "platform.db.new"
    reg = sqlite3.connect(reg_path)
    reg.executescript(REGISTRY_SCHEMA)
    reg.executemany(
        "INSERT INTO workspace(id, name, description, source_path, created_at) VALUES (?,?,?,?,?)",
        [(w["id"], w["name"], w["description"], w["source_path"], w["created_at"]) for w in workspaces],
    )
    reg.commit()
    reg.close()
    shutil.move(reg_path, OLD_DB)
    print(f"platform.db 已重写为纯注册表（{len(workspaces)} 个项目）")
    print("迁移完成。确认平台运行正常后可删除 platform.db.bak")


if __name__ == "__main__":
    main()
