---
name: conversation-logger
description: Install and configure the Claude Code Conversation Logger — a zero-token-cost hook system that automatically saves every Claude Code session to a Markdown file. Use this skill when the user wants to log their Claude Code conversations, save sessions to Obsidian or any Markdown vault, set up automatic conversation recording, or archive AI collaboration sessions for content creation. Trigger on phrases like "save my conversations", "log Claude sessions", "conversation logger", "auto-save to Obsidian", or "记录对话".
---

# Conversation Logger — Installation Skill

This skill installs a Claude Code hook system that automatically saves every session to a Markdown file. It uses **zero LLM tokens** — pure Python file I/O.

## What gets installed

| Component | Location | Purpose |
|---|---|---|
| `logger.py` | `~/.claude/scripts/conversation-logger/` | The hook script |
| Hook config | `~/.claude/settings.json` | Wires 3 hooks to the script |
| State dir | `~/.claude/scripts/conversation-logger/state/` | Session tracking (auto-created) |

## Installation flow

### Step 1 — Ask the user for their output directory

Ask:

> "Where should conversation files be saved? (e.g. `~/Documents/MyVault/Conversations`)"

Use their answer as `CONVERSATIONS_DIR`. If they don't specify, default to `~/Documents/Conversations`.

### Step 2 — Install logger.py

```bash
mkdir -p ~/.claude/scripts/conversation-logger/state
cp <skill-base-dir>/scripts/logger.py ~/.claude/scripts/conversation-logger/logger.py
chmod +x ~/.claude/scripts/conversation-logger/logger.py
```

Then update the `CONVERSATIONS_DIR` line in the installed script to match what the user provided:

```python
CONVERSATIONS_DIR = os.environ.get(
    "CLAUDE_CONVERSATIONS_DIR",
    os.path.expanduser("<user's chosen path>"),
)
```

Alternatively, tell the user they can set the environment variable instead of editing the file:
```bash
export CLAUDE_CONVERSATIONS_DIR="/path/to/their/folder"
```

### Step 3 — Add hooks to settings.json

Read `~/.claude/settings.json`. Add the three hooks below. If a `hooks` key already exists, merge carefully — don't overwrite existing entries in `Stop` or other events.

```json
"SessionStart": [
  {
    "matcher": "",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ~/.claude/scripts/conversation-logger/logger.py session-start",
        "timeout": 10
      }
    ]
  }
],
"UserPromptSubmit": [
  {
    "matcher": "",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ~/.claude/scripts/conversation-logger/logger.py user-prompt",
        "timeout": 10
      }
    ]
  }
],
"Stop": [
  {
    "matcher": "",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ~/.claude/scripts/conversation-logger/logger.py stop",
        "timeout": 30
      }
    ]
  }
]
```

After editing, validate:
```bash
python3 -c "import json; json.load(open('~/.claude/settings.json')); print('JSON valid')"
```

### Step 4 — Verify

Tell the user:

> "Installation complete. To verify: start a new Claude Code session, send a couple of messages, then `/exit`. Check `<CONVERSATIONS_DIR>` for a new `.md` file."

Also mention the replay command for converting historical sessions:
```bash
python3 ~/.claude/scripts/conversation-logger/logger.py replay \
  ~/.claude/projects/<project-dir>/<session-uuid>.jsonl \
  ~/path/to/output.md
```

## Troubleshooting

**MD file is empty** — Most likely cause: hooks aren't firing. Check `~/.claude/settings.json` has the correct structure and run `claude --version` to confirm hooks are supported.

**State file missing** — The script creates `~/.claude/scripts/conversation-logger/state/` automatically on first `SessionStart`. If it's missing, the Stop hook silently skips writing (by design, to avoid crashes).

**Wrong path** — Edit `CONVERSATIONS_DIR` in `~/.claude/scripts/conversation-logger/logger.py`, or set the `CLAUDE_CONVERSATIONS_DIR` environment variable.
