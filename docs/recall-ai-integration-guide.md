# Recall AI Integration — Step-by-Step Guide
**Created: 2026-02-28**
**Updated: 2026-02-28 — Switched to email-based approach (Option A)**
**Status: PHASE 1 IN PROGRESS**

---

## Architecture Decision: Email-Based Integration (Option A)

**Problem:** Recall AI's Calendar V2 API requires an Azure app registration in
Microsoft Entra. MasTec IT will not approve Azure app registrations for tools
they haven't vetted. Calendar V1 dashboard connection shows `connected: false`
for Microsoft — V1 is deprecated.

**Solution:** Use Goliath's existing email poller to detect meeting invites
(which already arrive via the Power Automate relay). Meeting invite emails
contain everything we need — Teams URLs, attendee lists, meeting times, and
project names in the subject line. No calendar API, no Azure app, no IT.

**Flow:**
```
Outlook Calendar Invite → Power Automate → Gmail → Goliath Email Poller
    → Detect Teams URL + invite signals
    → Extract meeting time, attendees, project mapping
    → Schedule Recall.ai bot (via API, using just the URL)
    → Update project contact lists
    → Bot joins meeting → Transcript captured → Intelligence extracted
```

---

## PHASE 0: Foundation (DONE ✅)

### Step 0: Seed Contact Lists from Known Data
- [x] Created contact JSON files for 7 projects + portfolio-wide
- [x] Seeded with known contacts from email history and user confirmation
- [x] Stored at `/opt/goliath/contacts/{project-key}.json`
- [x] DSC team (Aaron, Tyler, Joshua) in `_portfolio-wide.json` only

---

## PHASE 1: Email-Based Meeting Detection & Bot Scheduling (IN PROGRESS)

### Step 1: Recall AI API Key ✅
- [x] API key already configured in `.env` as `RECALL_API_KEY`
- [x] Recall service fully operational (tested with manual `/join` commands)

### Step 2: Meeting Invite Detection (BUILT ✅)
- [x] Created `meeting_invite_handler.py` — detects meeting invites in emails
- [x] Detection signals: Teams URL + invite body patterns ("Join Microsoft Teams Meeting", etc.)
- [x] Filters out calendar responses (Accepted/Declined/Tentative/Cancelled)
- [x] Supports both email body parsing AND `.ics` attachment parsing
- [x] Added `.ics`/`.vcs` to allowed extensions in email poller
- [x] Added `meeting_invite` classification to email poller routing

### Step 3: Meeting Info Extraction (BUILT ✅)
- [x] Extracts Teams URL from email body or `.ics` attachment
- [x] Parses meeting start time from "When:" lines or ICS DTSTART
- [x] Extracts attendees from ICS ATTENDEE fields, email CC, or sender
- [x] Maps meeting title to project using `match_project_key()` (same rules as schedule/POD)

### Step 4: Auto-Bot Scheduling (BUILT ✅)
- [x] When meeting is in the future: schedules Recall bot to join 1 min before start
- [x] When meeting is NOW or recent: joins immediately
- [x] When meeting is >2 hours old: skips (too late)
- [x] Uses existing `recall_service.send_bot_to_meeting()` — no new API code needed

### Step 5: Contact Harvesting from Invites (BUILT ✅)
- [x] Extracts attendee names + emails from meeting invites
- [x] Auto-adds new contacts to the matched project's contact JSON
- [x] Dedup: skips if email already exists in roster
- [x] Updates names if we have a better one (e.g., ICS has full name)
- [x] Skips DSC team members (Aaron, Tyler, Joshua) — they're portfolio-wide only
- [x] Tags new contacts with `source: "meeting_invite"`

### Step 6: Telegram Notifications (BUILT ✅)
- [x] Notifies user when meeting invite is detected
- [x] Shows: project name, meeting title, time, bot status, contacts added
- [x] Example: "📅 Meeting invite detected — Mayes. 🤖 Bot scheduled. ✅ 3 new contacts added"

### REMAINING — Needs Testing
- [ ] Test with a real meeting invite email
- [ ] Verify Power Automate forwards calendar invites (not just regular emails)
- [ ] Confirm attendee extraction works with real Outlook invite format
- [ ] Test scheduled bot join (future meeting)

---

## PHASE 2: Auto-Join Calls & Transcript Capture

### Step 7: Verify Bot Auto-Join via Email Pipeline
- [ ] Confirm bots join meetings successfully when scheduled from invite emails
- [ ] Monitor for edge cases (recurring meetings, all-day events, cancelled meetings)
- [ ] Add recurring meeting detection (only schedule bot for next occurrence)

### Step 8: Transcript Pipeline (Already Built)
- [x] Recall service fetches transcript when bot finishes
- [x] Saves to `/opt/goliath/transcripts/recall/`
- [x] Auto-queues for `transcript_processor` agent
- [x] Transcript processor extracts: summary, speakers, constraints, action items, decisions
- [ ] Verify transcript quality from email-scheduled bots matches manual `/join`

---

## PHASE 3: Email Intelligence Pipeline

### Step 9: Email-to-Constraint Scanner (Goliath Dev Work)
- [ ] Build email scanning module in email poller
- [ ] Cross-reference sender against contact lists in `/opt/goliath/contacts/`
- [ ] If sender matches a project contact → associate email with that project
- [ ] Scan email body for constraint signals (vendor names, delivery dates, material updates, sub status, RFI responses)
- [ ] Match detected signals against open constraints in ConstraintsPro
- [ ] Auto-push relevant details to constraint notes in ConstraintsPro
- [ ] Tag each auto-pushed note with source: "email from [sender] on [date]"

### Step 10: Resolution Detection
- [ ] Detect resolution signals in emails ("delivered", "approved", "permit in hand", "complete", etc.)
- [ ] Flag potential resolutions for Aaron's approval before closing constraints
- [ ] Telegram notification: "📌 [Project] — [Constraint] may be resolved based on email from [sender]. Approve close?"

---

## PHASE 4: Morning Report Follow-Up Section

### Step 11: Add "Suggested Follow-Ups" to Morning Report (Goliath Dev Work)
- [ ] Scan open constraints with aging analysis (days since last update, proximity to need-by date)
- [ ] Cross-reference with contact list to identify WHO to contact
- [ ] Generate copy-paste-ready email drafts per constraint owner
- [ ] Include in morning report PDF with sections:
  - Overdue follow-ups (need-by date passed)
  - Due this week
  - Stalled (no update in 7+ days)
- [ ] Each item shows: Project, Constraint, Contact Name, Email, Suggested Draft

---

## Dependencies & Status

| Item | Owner | Status |
|------|-------|--------|
| Recall AI API key | Aaron | ✅ Done |
| Contact lists seeded | Goliath | ✅ Done |
| Meeting invite detection | Goliath | ✅ Built |
| Bot auto-scheduling | Goliath | ✅ Built |
| Contact harvesting | Goliath | ✅ Built |
| Power Automate invite forwarding | Aaron | ⚠️ Needs verification |
| Email-to-constraint scanner | Goliath (dev) | Not started |
| Morning report follow-up section | Goliath (dev) | Not started |

---

## Project Title → Key Mapping Rules
Used by both meeting invite detection and all other email classification:
- "Mayes" → `mayes`
- "Delta" or "Bobcat" → `delta-bobcat`
- "Scioto" → `scioto-ridge`
- "Blackford" → `blackford`
- "Salt Branch" → `salt-branch`
- "Three Rivers" → `three-rivers`
- "Tehuacana" → `tehuacana`
- "Union Ridge" → `union-ridge`
- "Duff" → `duff`
- "Graceland" → `graceland`
- "Pecan" → `pecan-prairie`
- "Duffy" or "Bess" → `duffy-bess`

---

## Key Files
| Component | Path |
|-----------|------|
| Meeting Invite Handler | `telegram-bot/bot/services/meeting_invite_handler.py` |
| Email Poller (modified) | `telegram-bot/bot/services/email_poller.py` |
| Recall AI Service | `telegram-bot/bot/services/recall_service.py` |
| Webhook Server | `telegram-bot/bot/services/webhook_server.py` |
| Contact Lists | `contacts/{project-key}.json` |
| This Guide | `docs/recall-ai-integration-guide.md` |

---

## Notes
- DSC team members (Aaron, Tyler, Joshua) stay in portfolio-wide contacts only, never duplicated per project
- Azure app registration path is BLOCKED — IT won't approve. Email-based approach is the permanent solution.
- Recall AI docs: https://docs.recall.ai/
- No `icalendar` Python library needed — ICS parsing uses regex (lightweight, no new dependency)
