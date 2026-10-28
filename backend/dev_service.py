"""后台工具开发：确认原型与文档后，起 claude agent 在 tools/<tool>/ 里自主开发真正的工具。

- 每个工作域一条任务（DevTask），后台线程驱动 claude CLI（stream-json 协议）。
- 产物落在项目根 tools/<tool_slug>/，与平台代码同级，整个文件夹可独立发布。
- AI 需要用户决策时输出 `[NEEDS_INPUT] 问题`，任务转入 waiting_input；
  用户回复后作为新的 user 消息发回同一进程继续（保持会话上下文）。
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

NEEDS_INPUT_RE = re.compile(r"^\s*\[NEEDS_INPUT\]\s*(.+)$", re.MULTILINE)

PROMPT_TEMPLATE = """你是「{tool_name}」工具的开发工程师。这个工具的需求已在场景平台里确认（场景文档 + 工具原型界面规格），现在请你把它开发成**可独立发布、可真正运行的工具**。

# 工作目录（你的唯一操作范围）
{work_dir}

所有代码、README、配置都写在这个目录里；不要碰目录外的任何文件。整个文件夹会被独立打包发布，保持自包含（依赖写进 README / requirements.txt / package.json）。

# 本次要实现的场景（功能清单）
{scenarios_block}

# 场景对应的工具原型界面规格（ui_spec）
{ui_specs_block}

# 已有代码（增量开发）
{existing_block}

# 开发要求
1. 通读上面所有场景与界面规格，理解工具要做什么；场景是功能清单，ui_spec 是界面/交互契约。
2. 选择最合适的形态（CLI / Python 包 / 前端小应用等），以能真正跑起来为准；把可运行的最小闭环先做出来。
3. 代码写进工作目录，写好 README（如何安装、如何运行、如何发布）。
4. 完成后自检：把关键命令/入口真正跑一遍验证（如有可执行入口）。
5. 开发过程的重要决策、未完成项、待确认问题，追加到 `.dev-log.md`。

# 硬约束：ui_spec 对照清单（用户确认的界面契约，不可静默偏离）
ui_spec 是用户在原型工具里逐屏确认过的设计，你的实现必须逐条对齐。开发完成后，**必须**在 `.dev-log.md`（或 README）里输出「ui_spec 对照清单」，格式为逐屏逐条核对表：

| ui_spec 项（屏/字段/校验/规则） | 状态 | 实现位置或说明 |
|---|---|---|
| 例：屏2 P8 校验（3位大写、不含 I/O） | ✅ 已实现 | glink/core/naming.py validate_p8 |
| 例：屏3 冲突时提示"打开现有仓库" | ⚠️ 偏差 | CLI 形态下改为直接报错并退出，原因：… |

状态只能是三种：`✅ 已实现` / `⚠️ 偏差（必须写明原因）` / `❌ 未实现（必须写明原因与后续计划）`。

**两条死规定**：
- **不许静默偏离**：凡是与用户确认的 ui_spec 不一致的地方，要么在对照清单里显式标注 `⚠️ 偏差` 并写清原因，要么用 `[NEEDS_INPUT]` 先问用户。绝不允许改了设计却不在清单里体现。
- **对照清单必须完整覆盖** ui_spec 的每一屏、每个字段、每条校验规则和 confirm/run 阶段的行为约定，一条不落。用户拿这张清单逐条验收，清单不全等于开发未完成。

# 与用户的沟通
- 开发中遇到**必须由用户拍板**的问题（技术选型冲突、需求歧义、缺失信息），不要猜，在回复末尾单独一行输出：
  `[NEEDS_INPUT] 你的问题（一句话说清要用户决定什么）`
- 输出该标记后停止，等用户回复。除此之外不要中途停下问问题，能自主决定的直接做。

现在开始。先简述你的开发计划（形态选型 + 文件结构），然后动手实现。
"""

CONTINUE_TEMPLATE = """用户回复了你提出的问题：

{reply}

请根据这个回复继续开发。如还有必须用户决策的问题，同样用 `[NEEDS_INPUT] 问题` 标记；否则继续把工具做完。"""


def _slugify(name: str) -> str:
    """工具名 → 目录名：小写、非字母数字转 -，去掉多余分隔。空则给空串由调用方兜底。"""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s


def _read_event(proc, timeout: float = 600) -> dict | None:
    """读一行 stream-json 事件（带超时）。进程退出 None，空行/非 JSON {}。"""
    q: queue.Queue = queue.Queue(maxsize=1)

    def _r():
        try:
            q.put(proc.stdout.readline())
        except Exception:
            q.put("")

    threading.Thread(target=_r, daemon=True).start()
    try:
        line = q.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError("claude CLI 响应超时")
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {}


class DevTask:
    """一个工作域的后台开发任务：常驻一个 claude 进程，可多轮（初开发 → 用户答问 → 继续）。"""

    def __init__(self, ws_id: int, work_dir: Path, cli: str, model: str = ""):
        self.ws_id = ws_id
        self.work_dir = work_dir
        self.cli = cli
        self.model = model
        self.status = "idle"          # idle | running | waiting_input | done | error
        self.question = ""            # waiting_input 时的问题
        self.summary = ""             # done 时的收尾说明
        self.error = ""
        self.codes: list[str] = []
        self.log_lines: list[str] = []  # 展示用日志（assistant 文本增量 + 状态行）
        self.started_at = 0.0
        self.finished_at = 0.0
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------- 进程
    def _spawn(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.cli, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if self.model:
            cmd += ["--model", self.model]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(self.work_dir),
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )

    def _send(self, text: str) -> None:
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _log(self, line: str) -> None:
        with self._lock:
            self.log_lines.append(line)
            if len(self.log_lines) > 2000:
                self.log_lines = self.log_lines[-2000:]

    def _append_dev_log(self, text: str) -> None:
        try:
            p = self.work_dir / ".dev-log.md"
            with p.open("a", encoding="utf-8") as f:
                f.write(text.rstrip() + "\n")
        except Exception:
            pass

    # ---------------------------------------------------------- 主循环
    def _pump(self, prompt: str) -> None:
        """发一条 user 消息并消费事件流直到 result；按内容更新状态。返回是否还能继续。"""
        self._send(prompt)
        texts: list[str] = []
        while True:
            ev = _read_event(self.proc)
            if ev is None:
                self.status = "error"
                self.error = "claude 进程已退出"
                self._log("【错误】claude 进程已退出")
                return False
            if not ev:
                continue
            etype = ev.get("type")
            if etype == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "text" and b.get("text"):
                        texts.append(b["text"])
                        self._log(b["text"])
                    elif b.get("type") == "tool_use":
                        name = b.get("name", "tool")
                        inp = b.get("input", {})
                        hint = inp.get("file_path") or inp.get("command") or inp.get("path") or ""
                        self._log(f"  ▸ {name} {str(hint)[:120]}")
            elif etype == "result":
                if ev.get("is_error"):
                    self.status = "error"
                    self.error = str(ev.get("result", ""))[:500]
                    self._log(f"【错误】{self.error}")
                    return False
                if not texts and ev.get("result"):
                    texts.append(str(ev["result"]))
                    self._log(str(ev["result"]))
                break
        reply = "\n".join(texts).strip()
        self._append_dev_log(f"\n\n---\n**[开发轮次 {time.strftime('%H:%M:%S')}]**\n\n{reply}")
        m = NEEDS_INPUT_RE.search(reply)
        if m:
            self.status = "waiting_input"
            self.question = m.group(1).strip()
            self._log(f"\n【待确认】{self.question}")
        else:
            self.status = "done"
            self.summary = reply[-800:]
            self._log("\n【完成】本轮开发结束")
        self.finished_at = time.time()
        return True

    def _run(self, prompt: str) -> None:
        try:
            self._spawn()
            self._pump(prompt)
        except Exception as e:
            self.status = "error"
            self.error = str(e)[:500]
            self._log(f"【错误】{self.error}")
            self.finished_at = time.time()

    # ---------------------------------------------------------- 对外
    def start(self, codes: list[str], prompt: str) -> None:
        self.codes = codes
        self.status = "running"
        self.error = ""
        self.question = ""
        self.summary = ""
        self.log_lines = []
        self.started_at = time.time()
        self.finished_at = 0.0
        self._append_dev_log(f"\n# 开发启动 {time.strftime('%Y-%m-%d %H:%M:%S')}，场景：{', '.join(codes)}")
        self._thread = threading.Thread(target=self._run, args=(prompt,), daemon=True)
        self._thread.start()

    def answer(self, text: str) -> None:
        """waiting_input 状态下把用户回复发回同一进程继续。"""
        if self.status != "waiting_input" or not self.proc or self.proc.poll() is not None:
            raise RuntimeError("当前不在待确认状态")
        self.status = "running"
        self._log(f"\n【你的回复】{text}")
        self._append_dev_log(f"\n**[用户回复 {time.strftime('%H:%M:%S')}]** {text}")
        prompt = CONTINUE_TEMPLATE.format(reply=text)
        self._thread = threading.Thread(target=self._continue, args=(prompt,), daemon=True)
        self._thread.start()

    def _continue(self, prompt: str) -> None:
        try:
            self._pump(prompt)
        except Exception as e:
            self.status = "error"
            self.error = str(e)[:500]
            self._log(f"【错误】{self.error}")
            self.finished_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            log_tail = list(self.log_lines[-400:])
        files = []
        if self.work_dir.exists():
            for p in sorted(self.work_dir.rglob("*")):
                if p.is_file() and ".git" not in p.parts and p.name != ".dev-log.md":
                    files.append(str(p.relative_to(self.work_dir)))
        return {
            "status": self.status,
            "codes": self.codes,
            "question": self.question,
            "summary": self.summary,
            "error": self.error,
            "log_tail": log_tail,
            "files": files,
            "work_dir": str(self.work_dir),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class DevManager:
    """按工作域管理 DevTask（同域复用，便于增量开发与多轮答问）。"""

    def __init__(self, tools_root: Path):
        self.tools_root = Path(tools_root)
        self._tasks: dict[int, DevTask] = {}
        self._lock = threading.Lock()

    def tool_dir(self, ws_id: int, system_label: str) -> Path:
        slug = _slugify(system_label) or f"tool-{ws_id}"
        return self.tools_root / slug

    def get(self, ws_id: int) -> DevTask | None:
        return self._tasks.get(ws_id)

    def start(self, ws_id: int, system_label: str, codes: list[str], prompt: str, cli: str, model: str = "") -> DevTask:
        with self._lock:
            old = self._tasks.get(ws_id)
            if old and old.status in ("running", "waiting_input") and old.proc and old.proc.poll() is None:
                raise RuntimeError("已有开发任务在进行中（可先回复待确认问题或等其结束）")
            task = DevTask(ws_id, self.tool_dir(ws_id, system_label), cli, model)
            self._tasks[ws_id] = task
        task.start(codes, prompt)
        return task
