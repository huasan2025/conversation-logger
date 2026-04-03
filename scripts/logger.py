#!/usr/bin/env python3
"""
Claude Code Conversation Logger
Automatically saves Claude Code sessions as Markdown files.

Usage:
  python3 logger.py session-start    (SessionStart hook)
  python3 logger.py user-prompt      (UserPromptSubmit hook)
  python3 logger.py stop             (Stop hook)
  python3 logger.py replay <transcript_path> <output_path>  (batch replay)

Configuration:
  Set CLAUDE_CONVERSATIONS_DIR environment variable, or edit CONVERSATIONS_DIR below.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────
# Where to save conversation Markdown files.
# Override with: export CLAUDE_CONVERSATIONS_DIR="/path/to/your/folder"
CONVERSATIONS_DIR = os.environ.get(
    "CLAUDE_CONVERSATIONS_DIR",
    os.path.expanduser("~/Documents/Conversations"),
)

# Internal state directory (auto-created, no need to change)
STATE_DIR = os.path.expanduser("~/.claude/scripts/conversation-logger/state")

# Tool calls to surface in the conversation log.
# MCP tools (mcp__*) are always included automatically.
TRACKED_TOOLS = {"Write", "Skill"}


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_transcript(transcript_path: str) -> list:
    """
    Parse a JSONL transcript and return a list of conversation turns.
    Each turn: (role: str, text: str, tool_summaries: list[str])
    """
    turns = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")
            if entry_type not in ("user", "assistant"):
                continue

            msg = entry.get("message", {})
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "user":
                # Real user input is always a plain string.
                # List content = tool results or system injections → skip.
                # Strings starting with "<" = slash command side-effects → skip.
                if (
                    isinstance(content, str)
                    and content.strip()
                    and not content.lstrip().startswith("<")
                ):
                    turns.append(("user", content, []))
            elif role == "assistant":
                text, tools = _extract_assistant_content(content)
                # Skip thinking-only turns (no visible text, no tracked tools)
                if text.strip() or tools:
                    turns.append(("assistant", text, tools))

    return turns


def _extract_assistant_content(content) -> tuple:
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
    """Convert a tool_use block into a one-line summary. Returns '' if not tracked."""
    name = block.get("name", "")
    inp = block.get("input", {})

    if name == "Write":
        file_path = inp.get("file_path", "?")
        return f"📝 New file: `{file_path}`"

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


# ── Formatting ────────────────────────────────────────────────────────────────

def _make_turn_title(user_text: str) -> str:
    """Generate a short section title from the first line of user input (≤30 chars)."""
    first_line = user_text.strip().split("\n")[0]
    if len(first_line) > 30:
        return first_line[:29] + "…"
    return first_line


def format_user_turn(text: str, title: str = "") -> str:
    header = f"\n### {title}\n\n" if title else "\n"
    return f"{header}**User:**\n\n{text}\n\n---\n"


def format_assistant_turn(text: str, tool_summaries: list) -> str:
    parts = [f"\n**Assistant:**\n\n{text}"]
    if tool_summaries:
        parts.append("\n" + "\n".join(f"> {s}" for s in tool_summaries))
    parts.append("\n\n---\n")
    return "\n".join(parts)


# ── State management ──────────────────────────────────────────────────────────

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


# ── Hook handlers ─────────────────────────────────────────────────────────────

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
        f"# {now.strftime('%Y-%m-%d')} {project_name}\n\n"
    )

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(frontmatter)

    _write_state(session_id, {"md_path": md_path, "logged_assistant_turns": 0})


def handle_user_prompt(data: dict) -> None:
    session_id = data.get("session_id", "unknown")
    prompt = data.get("prompt", "")  # Note: field is "prompt", not "user_prompt"

    if not prompt.strip():
        return

    state = _read_state(session_id)
    if not state:
        return

    title = _make_turn_title(prompt)
    with open(state["md_path"], "a", encoding="utf-8") as f:
        f.write(format_user_turn(prompt, title))


def handle_stop(data: dict) -> None:
    session_id = data.get("session_id", "unknown")
    transcript_path = data.get("transcript_path", "")
    # last_assistant_message only contains text after the final tool call.
    # When the assistant writes text before tool calls, that text is lost.
    # Prefer extracting full text from the transcript; fall back to last_assistant_message.
    last_msg_fallback = data.get("last_assistant_message", "")

    state = _read_state(session_id)
    if not state:
        return

    full_text = ""
    tool_summaries = []

    if transcript_path and os.path.exists(transcript_path):
        turns = parse_transcript(transcript_path)
        assistant_turns = [(t, s) for r, t, s in turns if r == "assistant"]
        logged = state["logged_assistant_turns"]
        new_turns = assistant_turns[logged:]

        # Concatenate text from all new assistant turns (skip empty ones)
        texts = [t for t, _ in new_turns if t.strip()]
        full_text = "\n\n".join(texts)

        for _, tools in new_turns:
            tool_summaries.extend(tools)

        state["logged_assistant_turns"] = len(assistant_turns)

    # Fall back to last_assistant_message if transcript gave no text
    if not full_text.strip():
        full_text = last_msg_fallback

    if not full_text.strip():
        return

    with open(state["md_path"], "a", encoding="utf-8") as f:
        f.write(format_assistant_turn(full_text, tool_summaries))

    _write_state(session_id, state)


def handle_replay(transcript_path: str, output_path: str) -> None:
    """Convert a historical transcript to Markdown in one pass."""
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
            f"# {project_name} (replay)\n\n"
        )
        for role, text, tool_summaries in turns:
            if role == "user":
                f.write(format_user_turn(text, _make_turn_title(text)))
            elif role == "assistant":
                f.write(format_assistant_turn(text, tool_summaries))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    try:
        _main()
    except Exception:
        # Hooks must never crash the Claude Code session
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
    elif event == "user-prompt":
        handle_user_prompt(data)
    elif event == "stop":
        handle_stop(data)
    else:
        print(f"Unknown event: {event}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
