# GOLIATH — TODO

## Security
- [ ] Lock down bot access with `ALLOWED_CHAT_IDS` in `.env` — grab chat IDs from bot logs (`tail bot.log | grep chat_id`) and add yours + supervisor's ID. Restart bot after.

## Data Population
- [ ] Upload schedules for remaining projects (have: Blackford, Duff, Pecan Prairie; Scioto Ridge has wrong files)
- [ ] Fix Scioto Ridge schedule folder — currently contains Blackford files (needs re-upload)
- [ ] Collect and upload POD data from all 12 project sites
- [ ] Populate constraints folders for all projects
- [ ] Populate project-directory folders (contacts, org charts)
- [ ] Populate project-details subfolders (engineering, materials, location, budget)

## Cron Jobs
- [ ] Set up actual crontab entries for `daily_scan.py` (6 PM CT) and `morning_report.py` (8 AM CT)
- [ ] Configure `REPORT_CHAT_ID` in `.env` for morning report delivery
- [ ] Test end-to-end cron flow (scan → report file → Telegram delivery)

## Features to Add
- [x] Activity log system — `/logs` command to see what agents ran, how long, success/failure per query
- [x] Voice memo toggle — `/voice on|off` to make voice memos optional
- [x] Conversation context — already implemented (ConversationStore injects last 20 turns into Nimrod's prompt)

## Known Issues Encountered (Resolved)

### 1. Nested Claude session error
**Symptom:** `Claude Code cannot be launched inside another Claude Code session`
**Cause:** Bot runs inside a Claude Code Codespace which sets `CLAUDECODE` env var. Subprocess inherits it.
**Fix:** `claude_runner.py` clears `CLAUDECODE` from subprocess environment before spawning.
**File:** `telegram-bot/bot/services/claude_runner.py`

### 2. Budget exceeded error ($0.50 cap)
**Symptom:** `Exceeded USD budget (0.5)` on complex queries
**Cause:** `--max-budget-usd 0.50` flag was set as a safety guard. Not needed on Claude Max plan.
**Fix:** Removed the flag entirely from the Claude CLI command.
**File:** `telegram-bot/bot/services/claude_runner.py`

### 3. Nimrod doing analysis instead of delegating (5-min timeout)
**Symptom:** Nimrod times out on project analysis queries (schedule, constraints, etc.)
**Cause:** Nimrod had `--dangerously-skip-permissions` which gave him tool access. He read PDFs himself instead of delegating to subagents.
**Fix:** Nimrod's routing and synthesis passes now run WITHOUT `--dangerously-skip-permissions` (no tool access). Only subagents get full permissions. This forces Nimrod to delegate via SUBAGENT_REQUEST blocks.
**Files:** `telegram-bot/bot/agents/runner.py` (added `no_tools` param), `telegram-bot/bot/agents/orchestrator.py` (Pass 1 and Pass 2 use `no_tools=True`)

### 4. --allowedTools "" breaking prompt parsing
**Symptom:** `Input must be provided either through stdin or as a prompt argument when using --print`
**Cause:** `--allowedTools ""` was eating the next argument. Empty string not valid for this flag.
**Fix:** Replaced with simply omitting `--dangerously-skip-permissions` instead. In `--print` mode without that flag, Claude has no tool access by default.
**File:** `telegram-bot/bot/agents/runner.py`

### 5. Telegram messages not rendering formatting
**Symptom:** Raw markdown (`**bold**`, `# headers`) showing as plain text in Telegram
**Cause:** Messages sent without `parse_mode`. Nimrod was outputting Markdown but Telegram needs explicit parse mode.
**Fix:** Switched to HTML formatting in Nimrod's prompt + `parse_mode="HTML"` with fallback to plain text.
**Files:** `telegram-bot/bot/agents/definitions.py` (Nimrod prompt), `telegram-bot/bot/handlers/orchestration.py`

## Restart Cheatsheet
```bash
# Restart the bot
bash /workspaces/goliath/telegram-bot/start.sh

# Check logs
tail -f /workspaces/goliath/telegram-bot/bot.log

# Check if bot is running
ps aux | grep bot.main | grep -v grep

# Kill stuck Claude processes
pkill -f "claude --print"
```
