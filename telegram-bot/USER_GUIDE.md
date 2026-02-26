# GOLIATH Telegram Bot — User Guide

## Quick Start (5 steps)

### Step 1: Get your bot token
You already did this with @BotFather on Telegram. You should have a token that looks like:
```
7123456789:AAH1bGciOiJIUzI1NiIsInR5cCI6...
```

### Step 2: Add your token to the config
Open the `.env` file in the repo root:
```bash
# In the Codespace terminal:
code /workspaces/goliath/.env
```

Replace `your-token-here` with your actual token:
```
TELEGRAM_BOT_TOKEN=7123456789:AAH1bGciOiJIUzI1NiIsInR5cCI6...
```

Save the file.

### Step 3: Start the bot
```bash
cd /workspaces/goliath/telegram-bot
python -m bot.main
```

You should see:
```
2026-02-24 ... [INFO] bot.main: Starting GOLIATH Telegram Bot...
2026-02-24 ... [INFO] bot.main: Bot is polling. Send /start in Telegram to begin.
```

### Step 4: Open Telegram and find your bot
1. Open Telegram on your phone or desktop
2. Search for your bot by the username you set with @BotFather
3. Tap **Start** or type `/start`
4. You should see the welcome message

### Step 5: Try it out
Send these commands to verify everything works:
- `/start` — Welcome message
- `/help` — List of all commands
- `/status` — See all 12 projects
- `/project union-ridge` — Detail for one project
- Type any question in plain English — Claude will analyze it

---

## All Commands

| Command | What it does | Example |
|---------|-------------|---------|
| `/start` | Welcome message and capabilities overview | `/start` |
| `/help` | List all available commands | `/help` |
| `/status` | Portfolio overview — all 12 projects with file counts | `/status` |
| `/project <name>` | Detail for one project, listing all files per subfolder | `/project union-ridge` |
| `/files <project> [subfolder]` | List files in a project folder | `/files duff constraints` |
| `/read <project> <path>` | Read a specific file's contents | `/read duff constraints/tracker.md` |
| _(plain text)_ | Ask Claude to analyze anything — reads/writes project files | "What constraints are blocking Three Rivers?" |

### Project name shortcuts
You can use the folder key or the full name:
- `/project union-ridge` or `/project union ridge` — both work
- `/project duff` — exact match
- `/project delta` — will fuzzy-match to `delta-bobcat`

---

## Folder Keys Reference

| Project | Folder key (use in commands) |
|---------|-----|
| Union Ridge | `union-ridge` |
| Duff | `duff` |
| Salt Branch | `salt-branch` |
| Blackford | `blackford` |
| Delta Bobcat | `delta-bobcat` |
| Tehuacana | `tehuacana` |
| Three Rivers | `three-rivers` |
| Scioto Ridge | `scioto-ridge` |
| Mayes | `mayes` |
| Graceland | `graceland` |
| Pecan Prairie | `pecan-prairie` |
| Duffy BESS | `duffy-bess` |

---

## Running the Bot

### Run in the foreground (see live logs)
```bash
cd /workspaces/goliath/telegram-bot
python -m bot.main
```
Press `Ctrl+C` to stop.

### Run in the background
```bash
cd /workspaces/goliath/telegram-bot
nohup python -m bot.main > bot.log 2>&1 &
echo $! > bot.pid
echo "Bot started with PID $(cat bot.pid)"
```

### Stop the background bot
```bash
kill $(cat /workspaces/goliath/telegram-bot/bot.pid)
```

### Check if the bot is running
```bash
ps aux | grep "bot.main" | grep -v grep
```

### View logs
```bash
tail -f /workspaces/goliath/telegram-bot/bot.log
```

---

## Security: Restrict Who Can Use Your Bot

By default, anyone who finds your bot on Telegram can send it commands. To lock it down:

### Find your Telegram chat ID
1. Start the bot and send `/start`
2. Check the bot logs — look for a line like:
   ```
   INFO bot.handlers.basic: /start from chat_id=123456789
   ```
3. That number is your chat ID

### Set the whitelist
Edit `/workspaces/goliath/.env` and add:
```
ALLOWED_CHAT_IDS=123456789
```

For multiple users, separate with commas:
```
ALLOWED_CHAT_IDS=123456789,987654321
```

Restart the bot for this to take effect.

---

## Claude AI Integration

When you send a plain text message (not a `/command`), the bot routes your question to Claude, which:
- Has access to all project files in `/workspaces/goliath/projects/`
- Knows the DSC role and all 12 projects (via Claude.md)
- Can read and write files on your behalf
- Responds with analysis formatted for Telegram

### Example questions you can ask:
- "What files do we have for Union Ridge?"
- "Summarize the constraints for all projects"
- "Create a constraints tracker for Three Rivers"
- "Compare the POD data between Duff and Salt Branch"
- "What schedule risks should I bring up in this week's meeting?"

### Limits
- Each Claude analysis has a 5-minute timeout
- Max 3 concurrent Claude analyses (additional requests queue)
- Cost capped at $0.50 per question
- Telegram messages max out at 4096 chars (long responses get split into multiple messages)

---

## Troubleshooting

### "TELEGRAM_BOT_TOKEN is not set"
You haven't added your token yet. Edit `/workspaces/goliath/.env` and paste your BotFather token.

### Bot starts but doesn't respond in Telegram
1. Make sure you're messaging the right bot (check the username)
2. If you set `ALLOWED_CHAT_IDS`, make sure your chat ID is included
3. Check the logs: `tail -f /workspaces/goliath/telegram-bot/bot.log`

### "Claude CLI not found"
The `claude` command-line tool needs to be installed and in your PATH. In the Codespace terminal, verify with:
```bash
which claude
```

### Bot stops when Codespace sleeps
GitHub Codespaces go idle after inactivity. The bot will stop when this happens. Options:
- Keep the Codespace active (interact with it periodically)
- Use the `gh codespace` CLI to wake it up remotely
- Consider moving to a persistent VM for production use

### Messages show weird formatting
The bot uses Markdown v1 for formatting. If Claude's response contains unsupported markdown characters, you may see raw formatting. This is cosmetic and doesn't affect functionality.
