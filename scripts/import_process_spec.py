# 过程拆解规格导入脚本：把 specs/process-spec-<code>.json 写入项目库的 process_spec 表
# 用法：.venv\Scripts\python.exe -I scripts\import_process_spec.py <workspace_id> <code> [spec_path]
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def main():
    ws_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    code = sys.argv[2] if len(sys.argv) > 2 else "1.1"
    spec_path = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "specs" / f"process-spec-{code}.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert spec.get("kind") == "process", "spec.kind 必须是 process"
    db = ROOT / "data" / "projects" / f"ws-{ws_id}.db"
    if not db.exists():
        print(f"项目库不存在：{db}")
        sys.exit(1)
    with sqlite3.connect(db) as c:
        c.execute(
            """INSERT INTO process_spec(scenario_code, spec, updated_at) VALUES (?,?,?)
               ON CONFLICT(scenario_code) DO UPDATE SET spec = excluded.spec, updated_at = excluded.updated_at""",
            (code, json.dumps(spec, ensure_ascii=False), time.time()),
        )
    print(f"已导入 {spec_path.name} -> {db.name}: 场景 {code}")

if __name__ == "__main__":
    main()
