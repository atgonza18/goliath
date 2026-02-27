# GOLIATH — TODO

## Completed ✅

### Security
- [x] Lock down bot access with `ALLOWED_CHAT_IDS` in `.env` — configured with user's chat ID
- [x] Configure `REPORT_CHAT_ID` in `.env` for morning report delivery

### Features Shipped
- [x] Activity log system — `/logs` command
- [x] Voice memo toggle — `/voice on|off`
- [x] Conversation context — ConversationStore injects last 20 turns
- [x] ConstraintsPro MCP integration — live constraint queries via Convex database
- [x] Folder organizer agent — scans for dupes, misplaced files, stray scripts
- [x] Email pipeline (inbound) — IMAP poller → queue → Nimrod draft → Telegram approval
- [x] Email pipeline (outbound) — approved drafts route through PA relay → MasTec address
- [x] Email draft quality — substantive data-driven drafts with soul.md voice injection
- [x] Email deduplication — external_message_id + sender/subject/time checks prevent approval loops
- [x] Email formatting — HTML emails with proper paragraphs, bullets, spacing
- [x] Draft preamble stripping — _extract_clean_draft() removes Nimrod's internal commentary
- [x] Full draft visibility in Telegram — shows up to 3,600 chars (was 300) before approval
- [x] soul.md — user voice and communication profile for consistent tone across all outputs
- [x] Scioto Ridge folder cleanup — removed duplicate Blackford files, consolidated scripts

### Resolved Issues (Historical)
- [x] Nested Claude session error — cleared CLAUDECODE env var in subprocesses
- [x] Budget exceeded error — removed --max-budget-usd flag (Claude Max plan)
- [x] Nimrod timeout on analysis — runs without tool access, forces delegation
- [x] --allowedTools "" breaking prompt — omit flag entirely
- [x] Telegram formatting — switched to HTML parse_mode with fallback

---

## In Progress 🔧

### Email Pipeline Polish
- [ ] End-to-end re-test with real email (all 5 fixes verified with simulated test — need production validation)
- [ ] Monitor for edge cases: CC'd emails, reply chains, attachments

### Power Automate Flows
- [ ] Flow #3: Post-meeting transcript analysis — extract constraints, action items, commitments from call transcripts
- [ ] Teams calendar integration — daily sync of meetings, attendees, roles, contact info

---

## TODO 📋

### Data Population
- [ ] Upload schedules for remaining projects (have: Blackford, Duff, Pecan Prairie)
- [ ] Collect and upload POD data from all 12 project sites
- [ ] Populate constraints folders for all projects
- [ ] Populate project-directory folders (contacts, org charts)
- [ ] Populate project-details subfolders (engineering, materials, location, budget)

### Cron Jobs
- [ ] Set up actual crontab entries for daily_scan.py (6 PM CT) and morning_report.py (8 AM CT)
- [ ] Test end-to-end cron flow (scan → report file → Telegram delivery)

### Codebase Maintenance
- [ ] Codespaces reference cleanup — 12 files identified, break into phases
- [ ] Commit all pending changes to git (28 files — see commit plan below)

---

## Restart Cheatsheet
```bash
# Restart the bot (Hetzner — primary)
systemctl restart goliath-bot

# Or manually:
bash /opt/goliath/telegram-bot/start.sh

# Check logs
journalctl -u goliath-bot -f              # systemd logs (Hetzner)
tail -f /opt/goliath/telegram-bot/bot.log  # file-based logs

# Check if bot is running
ps aux | grep bot.main | grep -v grep

# Kill stuck Claude processes
pkill -f "claude --print"
```
