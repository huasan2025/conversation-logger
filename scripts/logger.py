#!/usr/bin/env python3
"""
Claude Code Conversation Logger
将 Claude Code 会话自动保存为 Obsidian vault 中的 Markdown 文件。

用法:
  python3 logger.py session-start    (SessionStart hook 调用)
  python3 logger.py user-prompt      (UserPromptSubmit hook 调用)
  python3 logger.py stop             (Stop hook 调用)
  python3 logger.py replay <transcript_path> <output_path>  (历史转换)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

VAULT_PATH = os.path.expanduser("~/Documents/HuaSan-LifeOS")
CONVERSATIONS_DIR = os.path.join(VAULT_PATH, "06-Conversations")
STATE_DIR = os.path.expanduser("~/.claude/scripts/conversation-logger/state")

# 只捕获这几种工具的操作摘要（MCP 通过前缀 mcp__ 动态匹配）
TRACKED_TOOLS = {"Write", "Skill"}


# ── 解析 ──────────────────────────────────────────────────────────────────────

def parse_transcript(transcript_path: str) -> list:
    """
    解析 JSONL transcript，返回对话轮次列表。
    每个轮次: (role: str, text: str, tool_summaries: list[str])

    关键规则：
    - JSONL 中 text entry 和 tool_use entry 永远是独立的行。
    - text entry 后面紧跟 tool_use entry = "引导句"（bridge text），跳过。
      例："先看文件里实际有什么内容：" → 下一条是 Bash → 跳过。
    - text entry 后面是 user entry（工具结果）或新的 user 真实输入 = 真正的回复，保留。
    """
    # 先读入所有行（需要前瞻判断）
    raw_entries = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw_entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    turns = []
    for i, entry in enumerate(raw_entries):
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        msg = entry.get("message", {})
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "user":
            # 真实用户输入的 content 是 string。
            # list 类型 = tool_result 或 Skill/system 注入 → 跳过。
            # 以 "<" 开头的 string = 命令注入（/exit、local-command 等）→ 跳过。
            if isinstance(content, str) and content.strip() and not content.lstrip().startswith("<"):
                turns.append(("user", content, []))

        elif role == "assistant":
            text, tools = _extract_assistant_content(content)

            # 跳过 thinking-only 轮次（text 空且无工具摘要）
            if not text.strip() and not tools:
                continue

            # 前瞻：如果这条 entry 只有文字（无工具摘要），且下一条非空 entry
            # 是 assistant tool_use，则这是引导句，跳过。
            if text.strip() and not tools:
                next_role, next_has_tool = _peek_next_entry(raw_entries, i + 1)
                if next_role == "assistant" and next_has_tool:
                    continue  # bridge text，跳过

            turns.append(("assistant", text, tools))

    return turns


def _peek_next_entry(raw_entries: list, start: int) -> tuple:
    """
    从 start 向后找第一条有实质内容的 assistant 或 user entry，
    返回 (role, has_tool_use)。找不到返回 (None, False)。
    """
    for entry in raw_entries[start:]:
        t = entry.get("type")
        if t not in ("user", "assistant"):
            continue
        msg = entry.get("message", {})
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "assistant":
            if isinstance(content, list):
                has_tool = any(b.get("type") == "tool_use" for b in content)
                has_text = any(b.get("type") == "text" for b in content)
                # 跳过 thinking-only entry，继续找
                if not has_tool and not has_text:
                    continue
                return ("assistant", has_tool)
            return ("assistant", False)
        elif role == "user":
            return ("user", False)
    return (None, False)


def _extract_text(content) -> str:
    """从 string 或 content block 列表中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content if block.get("type") == "text"]
        return "\n".join(parts)
    return ""


def _extract_assistant_content(content) -> tuple:
    """
    从 assistant content 中提取文本和工具摘要。
    返回 (text: str, tool_summaries: list[str])
    """
    if isinstance(content, str):
        return content, []

    text_parts = []
    tool_summaries = []

    for block in content:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            summary = _make_tool_summary(block)
            if summary:
                tool_summaries.append(summary)

    return "\n".join(text_parts), tool_summaries


def _make_tool_summary(block: dict) -> str:
    """将 tool_use block 转换为一行摘要，不在跟踪范围内的工具返回空字符串。"""
    name = block.get("name", "")
    inp = block.get("input", {})

    if name == "Write":
        file_path = inp.get("file_path", "?")
        return f"📝 新建文件: `{file_path}`"

    if name == "Skill":
        skill_name = inp.get("skill", "?")
        args = inp.get("args", "")
        display = skill_name + (f" {args}" if args else "")
        return f"🎯 Skill: `{display}`"

    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        display = f"{parts[1]}/{parts[2]}" if len(parts) >= 3 else name
        return f"🔌 MCP: `{display}`"

    return ""


# ── 格式化 ────────────────────────────────────────────────────────────────────

def _make_turn_title(user_text: str) -> str:
    """从 user 消息首行截取简短标题（最多 30 字符）。"""
    first_line = user_text.strip().split("\n")[0]
    if len(first_line) > 30:
        return first_line[:29] + "…"
    return first_line


def format_user_turn(text: str, title: str = "") -> str:
    header = f"\n### {title}\n\n" if title else "\n"
    return f"{header}**你：**\n\n{text}\n\n---\n"


def format_assistant_turn(text: str, tool_summaries: list) -> str:
    parts = [f"\n**沉舟：**\n\n{text}"]
    if tool_summaries:
        parts.append("\n" + "\n".join(f"> {s}" for s in tool_summaries))
    parts.append("\n\n---\n")
    return "\n".join(parts)


# ── 状态管理 ──────────────────────────────────────────────────────────────────

def _state_path(session_id: str) -> str:
    return os.path.join(STATE_DIR, f"claude-session-{session_id}.json")


def _read_state(session_id: str):
    path = _state_path(session_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_state(session_id: str, state: dict) -> None:
    with open(_state_path(session_id), "w", encoding="utf-8") as f:
        json.dump(state, f)


# ── Hook 事件处理 ─────────────────────────────────────────────────────────────

def handle_session_start(data: dict) -> None:
    session_id = data.get("session_id", "unknown")
    cwd = data.get("cwd", "")
    project_name = Path(cwd).name if cwd else "unknown"

    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d-%H%M')}-{project_name}.md"
    md_path = os.path.join(CONVERSATIONS_DIR, filename)

    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

    frontmatter = (
        f"---\n"
        f"date: {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"project: {project_name}\n"
        f"session_id: {session_id}\n"
        f"type: conversation\n"
        f"tags: []\n"
        f"---\n\n"
        f"# {now.strftime('%Y-%m-%d')} {project_name} 对话记录\n\n"
    )

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(frontmatter)

    _write_state(session_id, {"md_path": md_path, "frontmatter": frontmatter})


def handle_stop(data: dict) -> None:
    session_id = data.get("session_id", "unknown")
    transcript_path = data.get("transcript_path", "")

    state = _read_state(session_id)
    if not state:
        return
    if not transcript_path or not os.path.exists(transcript_path):
        return

    turns = parse_transcript(transcript_path)
    if not turns:
        return

    # Rewrite the entire MD on each Stop — eliminates counter drift bugs
    with open(state["md_path"], "w", encoding="utf-8") as f:
        f.write(state["frontmatter"])
        for role, text, tool_summaries in turns:
            if role == "user":
                f.write(format_user_turn(text, _make_turn_title(text)))
            elif role == "assistant":
                if text.strip() or tool_summaries:
                    f.write(format_assistant_turn(text, tool_summaries))

    _write_state(session_id, state)


def handle_replay(transcript_path: str, output_path: str) -> None:
    """将历史 transcript 一次性转换为 Markdown。"""
    turns = parse_transcript(transcript_path)

    project_name = Path(output_path).stem
    now = datetime.now()

    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(
            f"---\n"
            f"date: {now.strftime('%Y-%m-%d %H:%M')}\n"
            f"project: {project_name}\n"
            f"type: conversation\n"
            f"tags: []\n"
            f"---\n\n"
            f"# {project_name} 对话记录（回放）\n\n"
        )
        for role, text, tool_summaries in turns:
            if role == "user":
                f.write(format_user_turn(text, _make_turn_title(text)))
            elif role == "assistant":
                f.write(format_assistant_turn(text, tool_summaries))


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        _main()
    except Exception:
        # hook 不能崩溃影响会话，静默退出
        sys.exit(0)


def _main() -> None:
    if len(sys.argv) < 2:
        print("Usage: logger.py <event> [args...]", file=sys.stderr)
        sys.exit(1)

    event = sys.argv[1]

    if event == "replay":
        if len(sys.argv) < 4:
            print("Usage: logger.py replay <transcript_path> <output_path>", file=sys.stderr)
            sys.exit(1)
        handle_replay(sys.argv[2], sys.argv[3])
        return

    raw = sys.stdin.read()
    if not raw.strip():
        return
    data = json.loads(raw)

    if event == "session-start":
        handle_session_start(data)
    elif event == "stop":
        handle_stop(data)
    else:
        print(f"Unknown event: {event}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
