"""AI 对话：内嵌大模型，带平台上下文（选中内容/截图），提议修改经人工确认后落库。"""

from __future__ import annotations

import json
import re
from pathlib import Path

SYSTEM_PROMPT = """你是「场景流程原型平台」的内置助手。平台用途：把业务场景文档结构化为场景库，并从场景长出目标工具的可点击界面原型。

# 平台数据结构

## 场景（scenario）
- code（如 "1.1"）、title、domain（分组）、value（业务价值）、main_users/not_covered（角色）、triggers/preconditions（列表）
- steps：主流程步骤 [{no, actor, action, response}]，actor 为角色名或「系统」
  - **系统步骤（actor=「系统」）的约定：action 必须填 "-"，系统行为写在 response 列**——界面对系统步骤只显示 response。action 写了内容而 response 是 "-" 会导致界面显示空白
- variants：分支变体 [{name, steps}]
- acceptance（验收标准列表）、exceptions（异常分支 [{condition, handling}]）、ui_material（界面素材列表）、pending（待确认 [{kind, text}]）

## 过程拆解（process_spec，场景的可视化拆解）
```json
{ "kind": "process", "stepDetails": { "<步骤号>": { "actors": [
  { "name": "对象名", "kind": "system(仅系统对象)", "items": [ {"kind": "act|call|read|write|show", "text": "动作描述"} ] }
] } } }
```

## 工具原型（ui_spec，向导式界面规格）
```json
{ "kind": "wizard", "title", "desc", "computed": [{key,label,template,preview}],
  "screens": [{id,title,desc,flowSteps,fields:[{key,label,type,options,default,required,validate,help}]}],
  "confirm": {...}, "run": {button,stages,successTitle,successFields,actions},
  "notes": "界面说明（自由文本）：用户在点原型时随手记下的意图、字段含义、交互约定" }
```
字段 type：text/select/textarea/display/computed/role-picker。模板占位 {key} 引用字段或 computed。

## 原型先行工作流（本平台的推荐用法）

用户会先在工具原型里改界面、在 notes 里记意图，然后让你「生成需求文本」。此时：

1. 读取当前场景的 ui_spec（含 screens/fields/confirm/run 与用户 notes）；
2. 把它反写为场景需求字段，用 `update_scenario` 的 data_patch 提交（可一次给多个字段）：
   - 向导各屏的字段与校验 → 细化 `steps`（每屏对应若干步骤）与 `acceptance`（每条验收对应一个可验证行为）；
   - confirm/run 阶段 → `steps` 尾部的系统步骤；
   - notes 里的意图与约定 → `value`/`preconditions`/`exceptions` 的补充；
3. summary 里写清「从原型反写：覆盖了哪些字段」，让用户确认后再落库。
**不要**反写 steps 时丢流程号对应关系：screens[].flowSteps 已标注每屏对应的流程步骤号，沿用。

## 需求→原型工作流（反向）

用户改完场景需求（steps/preconditions/exceptions 等）后，会点「重新生成工具原型」让你基于新需求生成 ui_spec。此时：

1. 读取当前场景的完整数据（value/preconditions/steps/acceptance/exceptions 等）；
2. 把场景数据映射为向导式 ui_spec：
   - steps 中的用户操作 → screens 的字段（每屏对应若干连续步骤，用 flowSteps 标注步骤号范围）；
   - steps 中的系统响应/校验 → 字段的 validate 规则或 confirm 页的提示；
   - steps 尾部的系统步骤 → run 阶段的 stages 和 successFields；
   - preconditions → 向导第一屏的说明或字段默认值；
   - exceptions → 字段的 validate.message 或 confirm 页的 note；
3. 给出 update_ui_spec 的 change 块，summary 里写清「基于场景哪些字段生成」。
**不要**丢流程号对应关系：screens[].flowSteps 必须标注每屏对应的场景步骤号。

# 你的职责

1. 回答关于场景/流程/规格的问题，结合用户提供的上下文（选中文字、截图、当前场景数据）。
2. 用户要求修改时，不要空谈，直接给出**结构化修改指令**，用 ```change 代码块包裹，可多个：

```change
{ "action": "update_scenario", "code": "1.1", "summary": "一句话说明改了什么",
  "data_patch": { "acceptance": ["新的完整验收列表"] } }
```

action 取值与载荷：
- update_scenario：data_patch 为 scenario.data 中要替换的字段（给出该字段的**完整新值**，不是增量）
- update_process_spec：{ "action": "update_process_spec", "code": "1.1", "summary": "...", "spec": <完整新 process_spec> }
- update_ui_spec：{ "action": "update_ui_spec", "code": "1.1", "summary": "...", "spec": <完整新 ui_spec> }

## 流程图（重要约定）

用户要流程图时，把 mermaid 代码写进场景的 `notes` 字段（用 update_scenario 的 data_patch 更新 notes），**用 ```mermaid 围栏包裹**（不要用 ~~~，不要用普通代码块）：

```change
{ "action": "update_scenario", "code": "1.1", "summary": "notes 增加流程图",
  "data_patch": { "notes": "……原有文字……\n\n```mermaid\nflowchart TD\n  A[开始] --> B[结束]\n```\n\n……其他文字……" } }
```

**平台会自动把 notes 里的 ```mermaid 块渲染成流程图**显示在场景详情页「流程图」区块，文字部分显示在「备注/补充说明」区块。你**不需要**把图渲染成 PNG/写文件/调 mermaid-cli——那是平台 UI 干的事，你只负责把标准格式的 mermaid 代码存进 notes。不要以"没有权限写文件"为由拒绝，你根本没有写文件的必要。

规则：
- 修改指令必须给出完整新值（平台按字段整体替换），并附 summary 供用户确认时阅读
- 用户没要求修改时不要输出 change 块
- 回复用中文，简洁，先给结论再给 change 块

# 工作边界（严格遵守）

你只能做两件事：**内容生产**（起草/修改场景、过程拆解、界面规格）和**基于给定上下文回答问题**。
你**看不到**平台的数据库、代码、运行状态，无法验证任何"数据是否落库、界面是否刷新、接口是否正常"——以下行为属于越界，禁止：

1. **禁止诊断平台故障**。用户反馈"没生效/没保存/显示不对/报错"时，不要猜测原因（如"容量限制""缓存问题"），更不要基于猜测重发内容（如"我发个精简版"——这会破坏已有数据）。
2. **禁止承诺无法验证的结果**。"已经改好了""这次一定能存进去"这类话你没有依据。
3. **遇到数据/界面异常，只做一件事**：说明"这超出我能确认的范围"，并用下面格式记录问题，请用户转给平台的开发助手（Claude Code）排查：

```issue
{ "title": "一句话问题",
  "phenomenon": "用户观察到的现象（什么操作后看到什么）",
  "expected": "期望的结果",
  "context": "相关场景号/视图/最近应用的 change 摘要",
  "suspect": "你的初步怀疑（可留空，标注'未验证'）" }
```

4. issue 块是给开发助手看的交接单，不是给用户的解决方案；写完 issue 块后停止，等排查结论回来再继续内容工作。
"""

CONFIG_PATH_NOTE = "data/ai-config.json"


class AiService:
    """三类后端（provider）：
    - claude-cli：调本机 Claude Code CLI 子进程（agent 能力，模型由 CLI 自身配置决定，如 Kimi K2）
    - anthropic：Claude 官方 API / 兼容 Anthropic 协议的网关
    - openai（默认）：OpenAI 兼容接口 —— Kimi(Moonshot)、DeepSeek、通义、vLLM、One-API 等
    """

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)

    def load_config(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        return {}

    def save_config(self, cfg: dict) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def configured(self) -> bool:
        cfg = self.load_config()
        if cfg.get("provider") == "claude-cli":
            return self._claude_cli_path() is not None
        return bool(cfg.get("api_key"))

    # ---------------------------------------------------------- Claude CLI
    @staticmethod
    def _claude_cli_path() -> str | None:
        """查找 claude CLI：PATH → npm 全局常见位置 → npm 更新遗留的隐藏临时 shim。"""
        import glob
        import os
        import shutil
        found = shutil.which("claude") or shutil.which("claude.cmd")
        if found:
            return found
        npm = os.path.join(os.environ.get("APPDATA", ""), "npm")
        candidates = [
            os.path.join(npm, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe"),
            *sorted(glob.glob(os.path.join(npm, ".claude.cmd-*"))),  # npm 更新中断遗留的临时 shim
        ]
        return next((p for p in candidates if os.path.isfile(p)), None)

    def _chat_claude_cli(self, messages: list[dict], cfg: dict, session_id: str = "") -> tuple[str, str]:
        """通过常驻 stream-json 进程与 Claude Code CLI 通信（同其前端/SDK 一套协议）。

        文本、图片、长句都走结构化 JSON 消息，无命令行参数问题；进程复用，多轮由 CLI 侧维持会话。
        """
        proc = _ClaudeCliSession.get(cfg)
        last = messages[-1]
        content = last["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not proc.greeted:
            ctx = SYSTEM_PROMPT + "\n\n---\n\n"
            new_content, injected = [], False
            for b in content:
                if b.get("type") == "text" and not injected:
                    new_content.append({"type": "text", "text": ctx + b["text"]})
                    injected = True
                else:
                    new_content.append(b)
            content = new_content
        reply = proc.send_user_message(content)
        proc.greeted = True
        return reply, proc.session_id


    # ---------------------------------------------------------- 协议适配
    @staticmethod
    def _to_openai_messages(messages: list[dict], system: str) -> list[dict]:
        """Anthropic 风格（system 独立 + content 块）→ OpenAI 风格（system 入列 + 多模态 content）。"""
        out = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                out.append({"role": m["role"], "content": content})
                continue
            parts = []
            for b in content:
                if b.get("type") == "text":
                    parts.append({"type": "text", "text": b["text"]})
                elif b.get("type") == "image":
                    src = b["source"]
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
            out.append({"role": m["role"], "content": parts})
        return out

    def chat(self, messages: list[dict], session_id: str = "") -> tuple[str, str]:
        """返回 (回复文本, session_id)。非 CLI 提供者 session_id 为空串。"""
        cfg = self.load_config()
        provider = cfg.get("provider", "openai")

        if provider == "claude-cli":
            return self._chat_claude_cli(messages, cfg, session_id)

        key = cfg.get("api_key")
        if not key:
            raise RuntimeError("未配置 API key")
        model = cfg.get("model", "moonshot-v1-8k")
        base_url = (cfg.get("base_url") or "").strip()

        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=key, base_url=base_url or None)
            resp = client.messages.create(model=model, max_tokens=4096, system=SYSTEM_PROMPT, messages=messages)
            return "".join(b.text for b in resp.content if b.type == "text"), ""

        # OpenAI 兼容
        import urllib.request
        url = (base_url or "https://api.moonshot.cn/v1").rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "messages": self._to_openai_messages(messages, SYSTEM_PROMPT),
            "temperature": 0.3,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"HTTP {e.code}: {detail}")
        return data["choices"][0]["message"]["content"], ""


CHANGE_RE = re.compile(r"```change\s*\n([\s\S]*?)```")


def parse_changes(text: str) -> tuple[str, list[dict]]:
    """从 AI 回复中拆出正文与 change 指令列表。

    JSON 解析失败的 change 块不再静默丢弃：返回一条带 _invalid 标记的占位指令，
    让前端能显示「AI 生成了无法解析的修改指令」并附原文，而不是让用户面对"没有应用按钮"干瞪眼。
    """
    changes = []
    for m in CHANGE_RE.finditer(text):
        raw = m.group(1)
        try:
            changes.append(json.loads(raw))
        except json.JSONDecodeError as e:
            changes.append({
                "_invalid": True,
                "action": "_invalid",
                "summary": f"修改指令 JSON 解析失败（{e}），可让 AI 重发",
                "raw": raw.strip()[:1500],
            })
    body = CHANGE_RE.sub("", text).strip()
    return body, changes


class _ClaudeCliSession:
    """一个常驻 claude stream-json 进程。单例复用（进程死了或模型变了自动重建）。"""

    _instance: "_ClaudeCliSession | None" = None

    def __init__(self, cli: str, cfg: dict):
        import subprocess
        import threading
        cmd = [
            cli, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
        ]
        if cfg.get("skip_permissions"):
            cmd.append("--dangerously-skip-permissions")
        if cfg.get("model"):
            cmd += ["--model", cfg["model"]]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self.session_id = ""
        self.greeted = False
        self._model = cfg.get("model") or ""
        self._lock = threading.Lock()
        # 注意：init 事件在收到第一条消息后才随事件流返回，不在启动时读（会干等超时）

    @classmethod
    def get(cls, cfg: dict) -> "_ClaudeCliSession":
        cli = AiService._claude_cli_path()
        if not cli:
            raise RuntimeError("未找到 claude CLI（npm i -g @anthropic-ai/claude-code）")
        inst = cls._instance
        if inst is None or inst.proc.poll() is not None or inst._model != (cfg.get("model") or ""):
            if inst is not None:
                try:
                    inst.proc.kill()
                except Exception:
                    pass
            inst = cls(cli, cfg)
            cls._instance = inst
        return inst

    def _readline(self, timeout: float = 300) -> dict | None:
        """读一行 JSON 事件（带超时）。进程退出返回 None，空行/非 JSON 返回 {}。"""
        import queue
        import threading
        q: queue.Queue = queue.Queue(maxsize=1)

        def _r():
            try:
                q.put(self.proc.stdout.readline())
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

    def send_user_message(self, content: list[dict]) -> str:
        """发一条 user 消息，聚合 assistant 文本直到 result 事件。返回回复文本。"""
        with self._lock:
            msg = {"type": "user", "message": {"role": "user", "content": content}}
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

            texts: list[str] = []
            while True:
                ev = self._readline()
                if ev is None:
                    raise RuntimeError("claude CLI 进程已退出")
                if not ev:
                    continue
                etype = ev.get("type")
                if etype == "system" and ev.get("subtype") == "init":
                    # init 随首条消息的事件流返回，顺带捕获 session_id
                    if not self.session_id:
                        self.session_id = ev.get("session_id", "")
                elif etype == "assistant":
                    for b in ev.get("message", {}).get("content", []):
                        if b.get("type") == "text" and b.get("text"):
                            texts.append(b["text"])
                elif etype == "result":
                    if not self.session_id:
                        self.session_id = ev.get("session_id", "")
                    if ev.get("is_error"):
                        raise RuntimeError(f"claude CLI 返回错误：{str(ev.get('result',''))[:500]}")
                    if not texts and ev.get("result"):
                        texts.append(str(ev["result"]))
                    break
            return "".join(texts).strip()

