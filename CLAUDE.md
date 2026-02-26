# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Goliath is an autonomous multi-agent AI system for solar construction portfolio management. It uses a Telegram bot interface, Claude AI agents, and persistent SQLite memory to monitor 12 solar projects across schedule, constraints, and production (POD) dimensions.

## Commands

### Run the Telegram Bot
```bash
# Start/restart (also used by Codespace auto-start)
bash /workspaces/goliath/telegram-bot/start.sh

# Run interactively (see live logs)
cd /workspaces/goliath/telegram-bot && python -m bot.main

# Stop
kill $(cat /workspaces/goliath/telegram-bot/bot.pid)
```

### Install Dependencies
```bash
pip install -r /workspaces/goliath/telegram-bot/requirements.txt
```

### Run Cron Jobs Manually
```bash
python /workspaces/goliath/cron-jobs/daily_scan.py
python /workspaces/goliath/cron-jobs/morning_report.py
```

### Debugging
```bash
tail -f /workspaces/goliath/telegram-bot/bot.log
ps aux | grep bot.main | grep -v grep
pkill -f "claude --print"  # kill stuck subagent processes
```

There is no test suite, linter, or build step.

## Architecture

### Two-Pass Agent Orchestration

The system uses a two-pass flow where **Nimrod** (the COO agent) orchestrates 5 specialist subagents:

1. **Routing pass**: Nimrod receives user message + memory context, runs WITHOUT tool access, outputs `SUBAGENT_REQUEST` and `MEMORY_SAVE` blocks
2. **Subagent execution**: Requested subagents run in parallel (max 3 concurrent) WITH full file access (`--dangerously-skip-permissions`)
3. **Synthesis pass**: Nimrod receives subagent results, synthesizes a response (again no tool access)
4. **Voice memo**: Response is converted to speech via Edge TTS and sent alongside text

All agents (including Nimrod) run with `--dangerously-skip-permissions` for full file access. Nimrod can handle simple file ops directly but delegates complex analysis to specialist subagents.

### Key Files

- `Claude.md` (note: lowercase 'laude') — Agent system prompt injected into all Claude CLI calls. **Do not rename or merge with this file.**
- `telegram-bot/bot/agents/definitions.py` — All 6 agent system prompts and configurations
- `telegram-bot/bot/agents/orchestrator.py` — Two-pass orchestration engine, subagent dispatch, memory injection
- `telegram-bot/bot/agents/runner.py` — Claude CLI subprocess runner (`claude --print`)
- `telegram-bot/bot/handlers/orchestration.py` — Telegram message → orchestrator bridge
- `telegram-bot/bot/memory/store.py` — SQLite FTS5 memory system
- `telegram-bot/bot/config.py` — All paths, project registry, env var loading

### Tech Stack

- Python 3.9+, async/await throughout
- `python-telegram-bot` 21.x for Telegram interface
- Claude CLI (`claude --print`) for stateless single-shot AI calls
- SQLite with FTS5 for persistent memory
- Edge TTS for voice memos (free, no API key)
- `openpyxl` + `pandas` for Excel/data manipulation

### Data Flow

All agent calls are stateless — continuity comes from SQLite memory injection, not conversation history. Memory is injected as: 10 recent memories + 10 search-matched + open action items. Only Nimrod writes to memory; subagents receive memories as read-only context.

### Environment

- Runs in GitHub Codespaces; auto-starts via `.devcontainer/postStartCommand`
- `.env` contains `TELEGRAM_BOT_TOKEN` and optional `ALLOWED_CHAT_IDS` / `REPORT_CHAT_ID`
- All hardcoded paths assume `/workspaces/goliath/` root
- Telegram messages use HTML formatting (`<b>`, `<i>`, `<code>`), not Markdown

### Resolved Pitfalls

- Clear `CLAUDECODE` env var in subprocesses to avoid nested session errors
- Don't use `--max-budget-usd` flag (causes budget exceeded errors)
- Don't pass `--allowedTools ""` — omit the flag entirely
- Telegram parse mode must be HTML, not Markdown
