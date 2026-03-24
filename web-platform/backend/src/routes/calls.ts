import { Router, Request, Response } from 'express';
import { getMemoryDb, getChatDb } from '../services/database';
import fs from 'fs';
import path from 'path';

export const callsRouter = Router();

// ---------------------------------------------------------------------------
// Schema: call_reviews + call_review_constraints tables in chat.db
// (writable from the web backend, separate from read-only memory.db)
// ---------------------------------------------------------------------------

function ensureCallReviewTables(): void {
  const db = getChatDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS call_reviews (
      id TEXT PRIMARY KEY,
      bot_id TEXT NOT NULL,
      meeting_url TEXT,
      meeting_title TEXT,
      project_key TEXT,
      participants TEXT,
      duration_minutes INTEGER DEFAULT 0,
      summary TEXT,
      action_items TEXT,
      decisions TEXT,
      transcript_file TEXT,
      status TEXT DEFAULT 'pending_review' CHECK(status IN ('pending_review', 'reviewed', 'dismissed')),
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
      reviewed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS call_review_constraints (
      id TEXT PRIMARY KEY,
      review_id TEXT NOT NULL REFERENCES call_reviews(id) ON DELETE CASCADE,
      description TEXT NOT NULL,
      discipline TEXT DEFAULT 'Other',
      priority TEXT DEFAULT 'medium' CHECK(priority IN ('low', 'medium', 'high')),
      owner TEXT,
      due_date TEXT,
      category TEXT DEFAULT 'NEW' CHECK(category IN ('NEW', 'UPDATE', 'CLOSE', 'SKIP')),
      current_status TEXT,
      existing_constraint_id TEXT,
      action_status TEXT DEFAULT 'pending' CHECK(action_status IN ('pending', 'approved', 'rejected', 'pushed')),
      pushed_at TEXT,
      push_result TEXT,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
    );

    CREATE INDEX IF NOT EXISTS idx_crc_review ON call_review_constraints(review_id);
    CREATE INDEX IF NOT EXISTS idx_crc_status ON call_review_constraints(action_status);
  `);
}

// Initialize tables on module load
let tablesInitialized = false;
function initTables() {
  if (!tablesInitialized) {
    ensureCallReviewTables();
    tablesInitialized = true;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateId(): string {
  return `cr_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

interface RecallBotRow {
  bot_id: string;
  meeting_url: string;
  bot_name: string;
  status: string;
  transcript_text: string | null;
  transcript_file: string | null;
  created_at: string;
  completed_at: string | null;
  error: string | null;
  chat_id: number;
}

function parseParticipants(text: string): string[] {
  if (!text) return [];
  // Extract unique speaker names from transcript format "[HH:MM:SS] Name:"
  const speakers = new Set<string>();
  const regex = /\[[\d:]+\]\s+(.+?):/g;
  let match;
  while ((match = regex.exec(text)) !== null) {
    const name = match[1].trim();
    if (name && !name.includes('note taker')) {
      speakers.add(name);
    }
  }
  return Array.from(speakers);
}

function estimateDuration(text: string): number {
  if (!text) return 0;
  // Find the last timestamp in the transcript
  const timestamps = text.match(/\[(\d{2}):(\d{2}):(\d{2})\]/g);
  if (!timestamps || timestamps.length === 0) return 0;
  const last = timestamps[timestamps.length - 1];
  const parts = last.match(/(\d{2}):(\d{2}):(\d{2})/);
  if (!parts) return 0;
  return parseInt(parts[1]) * 60 + parseInt(parts[2]);
}

function readTranscriptSummary(transcriptFile: string): {
  summary: string;
  actionItems: string;
  decisions: string;
  constraints: Array<{
    description: string;
    discipline: string;
    priority: string;
    owner: string;
    category: string;
  }>;
} | null {
  // Look for a processed markdown summary in the project transcripts folder
  // Pattern: the transcript processor saves .md files alongside the raw .txt
  if (!transcriptFile) return null;

  const baseName = path.basename(transcriptFile, '.txt');
  const possiblePaths = [
    transcriptFile.replace('.txt', '-summary.md'),
    transcriptFile.replace('.txt', '.md'),
  ];

  // Also check project transcript folders
  const projectsDir = '/opt/goliath/projects';
  try {
    if (fs.existsSync(projectsDir)) {
      const projects = fs.readdirSync(projectsDir);
      for (const proj of projects) {
        const transcriptDir = path.join(projectsDir, proj, 'transcripts');
        if (fs.existsSync(transcriptDir)) {
          const files = fs.readdirSync(transcriptDir);
          for (const f of files) {
            if (f.endsWith('.md') && f.includes(baseName.slice(0, 10))) {
              possiblePaths.push(path.join(transcriptDir, f));
            }
          }
        }
      }
    }
  } catch { /* ignore */ }

  for (const p of possiblePaths) {
    try {
      if (fs.existsSync(p)) {
        const content = fs.readFileSync(p, 'utf-8');
        return parseTranscriptMarkdown(content);
      }
    } catch { /* ignore */ }
  }

  return null;
}

function parseTranscriptMarkdown(content: string): {
  summary: string;
  actionItems: string;
  decisions: string;
  constraints: Array<{
    description: string;
    discipline: string;
    priority: string;
    owner: string;
    category: string;
  }>;
} {
  const sections = {
    summary: '',
    actionItems: '',
    decisions: '',
    constraints: [] as Array<{
      description: string;
      discipline: string;
      priority: string;
      owner: string;
      category: string;
    }>,
  };

  // Extract summary (first section or text before first heading)
  const summaryMatch = content.match(/(?:##?\s*(?:Summary|Meeting Summary|Overview))\s*\n([\s\S]*?)(?=\n##?\s|\n---|\$)/i);
  if (summaryMatch) {
    sections.summary = summaryMatch[1].trim();
  } else {
    // Take first 500 chars as summary
    sections.summary = content.slice(0, 500).trim();
  }

  // Extract action items section
  const actionMatch = content.match(/(?:##?\s*(?:Action Items|Tasks|Commitments))\s*\n([\s\S]*?)(?=\n##?\s|\n---|\$)/i);
  if (actionMatch) {
    sections.actionItems = actionMatch[1].trim();
  }

  // Extract decisions section
  const decisionMatch = content.match(/(?:##?\s*(?:Decisions|Key Decisions))\s*\n([\s\S]*?)(?=\n##?\s|\n---|\$)/i);
  if (decisionMatch) {
    sections.decisions = decisionMatch[1].trim();
  }

  // Extract constraints
  const constraintMatch = content.match(/(?:##?\s*(?:Constraints|Constraints Discussed))\s*\n([\s\S]*?)(?=\n##?\s|\n---|\$)/i);
  if (constraintMatch) {
    const constraintText = constraintMatch[1];
    // Parse bullet points as constraints
    const bullets = constraintText.match(/[-*]\s+(.+)/g);
    if (bullets) {
      for (const bullet of bullets) {
        const text = bullet.replace(/^[-*]\s+/, '').trim();
        if (text.length > 10) {
          // Try to extract priority and owner from the text
          let priority = 'medium';
          let owner = '';
          let discipline = 'Other';

          if (/high|critical|urgent/i.test(text)) priority = 'high';
          if (/low|minor/i.test(text)) priority = 'low';

          const ownerMatch = text.match(/(?:owner|assigned to|responsible):\s*(.+?)(?:\.|,|$)/i);
          if (ownerMatch) owner = ownerMatch[1].trim();

          // Detect discipline
          if (/safety/i.test(text)) discipline = 'Safety';
          else if (/civil|grading|drainage/i.test(text)) discipline = 'Civil';
          else if (/electrical|AG\s*E/i.test(text)) discipline = 'AG Electrical';
          else if (/procurement|subcontract|PO|vendor/i.test(text)) discipline = 'Procurement';
          else if (/module/i.test(text)) discipline = 'Modules';
          else if (/rack/i.test(text)) discipline = 'Racking';
          else if (/pile/i.test(text)) discipline = 'Piles';
          else if (/environmental|permit/i.test(text)) discipline = 'Environmental';
          else if (/commission/i.test(text)) discipline = 'Commissioning';
          else if (/quality/i.test(text)) discipline = 'Quality';

          sections.constraints.push({
            description: text,
            discipline,
            priority,
            owner,
            category: 'NEW',
          });
        }
      }
    }
  }

  return sections;
}

// ---------------------------------------------------------------------------
// API Routes
// ---------------------------------------------------------------------------

/**
 * GET /api/calls
 * List all calls (from recall_bots table + any existing reviews)
 */
callsRouter.get('/calls', (req: Request, res: Response) => {
  try {
    initTables();
    const memDb = getMemoryDb();
    const chatDb = getChatDb();

    // Get all recall bots
    const bots = memDb.prepare(`
      SELECT bot_id, meeting_url, bot_name, status, transcript_text,
             transcript_file, created_at, completed_at, error, chat_id
      FROM recall_bots
      ORDER BY created_at DESC
      LIMIT 50
    `).all() as RecallBotRow[];

    // Get existing reviews
    const reviews = chatDb.prepare(`
      SELECT id, bot_id, meeting_title, project_key, participants,
             duration_minutes, status, created_at, reviewed_at
      FROM call_reviews
      ORDER BY created_at DESC
    `).all() as any[];

    const reviewMap = new Map<string, any>();
    for (const r of reviews) {
      reviewMap.set(r.bot_id, r);
    }

    const calls = bots.map(bot => {
      const review = reviewMap.get(bot.bot_id);
      const participants = bot.transcript_text
        ? parseParticipants(bot.transcript_text)
        : [];
      const duration = bot.transcript_text
        ? estimateDuration(bot.transcript_text)
        : 0;

      return {
        bot_id: bot.bot_id,
        meeting_url: bot.meeting_url,
        bot_name: bot.bot_name,
        status: bot.status,
        has_transcript: !!bot.transcript_text || !!bot.transcript_file,
        transcript_file: bot.transcript_file,
        participants,
        participant_count: participants.length,
        duration_minutes: duration,
        created_at: bot.created_at,
        completed_at: bot.completed_at,
        error: bot.error,
        review_id: review?.id || null,
        review_status: review?.status || null,
        meeting_title: review?.meeting_title || null,
        project_key: review?.project_key || null,
      };
    });

    res.json(calls);
  } catch (err) {
    console.error('[GET /api/calls]', err);
    res.status(500).json({ error: 'Failed to list calls' });
  }
});

/**
 * GET /api/calls/:botId
 * Get full call detail with transcript analysis and constraints
 */
callsRouter.get('/calls/:botId', (req: Request, res: Response) => {
  try {
    initTables();
    const { botId } = req.params;
    const memDb = getMemoryDb();
    const chatDb = getChatDb();

    // Get bot info
    const bot = memDb.prepare(`
      SELECT bot_id, meeting_url, bot_name, status, transcript_text,
             transcript_file, created_at, completed_at, error, chat_id
      FROM recall_bots
      WHERE bot_id = ?
    `).get(botId) as RecallBotRow | undefined;

    if (!bot) {
      res.status(404).json({ error: 'Call not found' });
      return;
    }

    const participants = bot.transcript_text
      ? parseParticipants(bot.transcript_text)
      : [];
    const duration = bot.transcript_text
      ? estimateDuration(bot.transcript_text)
      : 0;

    // Check if review exists
    let review = chatDb.prepare(`
      SELECT * FROM call_reviews WHERE bot_id = ?
    `).get(botId) as any;

    // If no review exists, auto-create one from transcript analysis
    if (!review && bot.transcript_file) {
      const analysis = readTranscriptSummary(bot.transcript_file);
      const reviewId = generateId();

      chatDb.prepare(`
        INSERT INTO call_reviews (id, bot_id, meeting_url, meeting_title, project_key,
          participants, duration_minutes, summary, action_items, decisions, transcript_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        reviewId,
        bot.bot_id,
        bot.meeting_url,
        null,  // meeting_title — extracted below
        null,  // project_key
        JSON.stringify(participants),
        duration,
        analysis?.summary || '',
        analysis?.actionItems || '',
        analysis?.decisions || '',
        bot.transcript_file,
      );

      // Auto-create constraint rows if analysis found any
      if (analysis?.constraints && analysis.constraints.length > 0) {
        const insertConstraint = chatDb.prepare(`
          INSERT INTO call_review_constraints (id, review_id, description, discipline,
            priority, owner, category, action_status)
          VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        `);

        for (const c of analysis.constraints) {
          insertConstraint.run(
            generateId(),
            reviewId,
            c.description,
            c.discipline,
            c.priority,
            c.owner || null,
            c.category,
          );
        }
      }

      review = chatDb.prepare(`
        SELECT * FROM call_reviews WHERE id = ?
      `).get(reviewId);
    }

    // Get constraints for this review
    let constraints: any[] = [];
    if (review) {
      constraints = chatDb.prepare(`
        SELECT * FROM call_review_constraints
        WHERE review_id = ?
        ORDER BY created_at ASC
      `).all(review.id) as any[];
    }

    // Read raw transcript (first 10000 chars for preview)
    let transcriptPreview = '';
    if (bot.transcript_file) {
      try {
        if (fs.existsSync(bot.transcript_file)) {
          const fullTranscript = fs.readFileSync(bot.transcript_file, 'utf-8');
          transcriptPreview = fullTranscript.slice(0, 10000);
        }
      } catch { /* ignore */ }
    }

    res.json({
      bot_id: bot.bot_id,
      meeting_url: bot.meeting_url,
      bot_name: bot.bot_name,
      status: bot.status,
      created_at: bot.created_at,
      completed_at: bot.completed_at,
      error: bot.error,
      participants,
      participant_count: participants.length,
      duration_minutes: duration,
      review: review ? {
        id: review.id,
        meeting_title: review.meeting_title,
        project_key: review.project_key,
        summary: review.summary,
        action_items: review.action_items,
        decisions: review.decisions,
        status: review.status,
        created_at: review.created_at,
        reviewed_at: review.reviewed_at,
      } : null,
      constraints: constraints.map(c => ({
        id: c.id,
        description: c.description,
        discipline: c.discipline,
        priority: c.priority,
        owner: c.owner,
        due_date: c.due_date,
        category: c.category,
        current_status: c.current_status,
        existing_constraint_id: c.existing_constraint_id,
        action_status: c.action_status,
        pushed_at: c.pushed_at,
        push_result: c.push_result,
      })),
      transcript_preview: transcriptPreview,
    });
  } catch (err) {
    console.error('[GET /api/calls/:botId]', err);
    res.status(500).json({ error: 'Failed to get call detail' });
  }
});

/**
 * PATCH /api/calls/:botId/review
 * Update review metadata (title, project_key)
 */
callsRouter.patch('/calls/:botId/review', (req: Request, res: Response) => {
  try {
    initTables();
    const { botId } = req.params;
    const { meeting_title, project_key } = req.body;
    const chatDb = getChatDb();

    const review = chatDb.prepare(`
      SELECT id FROM call_reviews WHERE bot_id = ?
    `).get(botId) as any;

    if (!review) {
      res.status(404).json({ error: 'Review not found' });
      return;
    }

    if (meeting_title !== undefined) {
      chatDb.prepare('UPDATE call_reviews SET meeting_title = ? WHERE id = ?').run(meeting_title, review.id);
    }
    if (project_key !== undefined) {
      chatDb.prepare('UPDATE call_reviews SET project_key = ? WHERE id = ?').run(project_key, review.id);
    }

    res.json({ success: true });
  } catch (err) {
    console.error('[PATCH /api/calls/:botId/review]', err);
    res.status(500).json({ error: 'Failed to update review' });
  }
});

/**
 * POST /api/calls/constraints/:constraintId/approve
 * Mark a constraint as approved
 */
callsRouter.post('/calls/constraints/:constraintId/approve', (req: Request, res: Response) => {
  try {
    initTables();
    const { constraintId } = req.params;
    const chatDb = getChatDb();

    chatDb.prepare(`
      UPDATE call_review_constraints
      SET action_status = 'approved'
      WHERE id = ? AND action_status = 'pending'
    `).run(constraintId);

    res.json({ success: true });
  } catch (err) {
    console.error('[POST approve constraint]', err);
    res.status(500).json({ error: 'Failed to approve constraint' });
  }
});

/**
 * POST /api/calls/constraints/:constraintId/reject
 * Mark a constraint as rejected
 */
callsRouter.post('/calls/constraints/:constraintId/reject', (req: Request, res: Response) => {
  try {
    initTables();
    const { constraintId } = req.params;
    const chatDb = getChatDb();

    chatDb.prepare(`
      UPDATE call_review_constraints
      SET action_status = 'rejected'
      WHERE id = ? AND action_status = 'pending'
    `).run(constraintId);

    res.json({ success: true });
  } catch (err) {
    console.error('[POST reject constraint]', err);
    res.status(500).json({ error: 'Failed to reject constraint' });
  }
});

/**
 * POST /api/calls/constraints/:constraintId/update-category
 * Update the category (NEW, UPDATE, CLOSE, SKIP) for a constraint
 */
callsRouter.post('/calls/constraints/:constraintId/update-category', (req: Request, res: Response) => {
  try {
    initTables();
    const { constraintId } = req.params;
    const { category } = req.body;
    const chatDb = getChatDb();

    if (!['NEW', 'UPDATE', 'CLOSE', 'SKIP'].includes(category)) {
      res.status(400).json({ error: 'Invalid category' });
      return;
    }

    chatDb.prepare(`
      UPDATE call_review_constraints SET category = ? WHERE id = ?
    `).run(category, constraintId);

    res.json({ success: true });
  } catch (err) {
    console.error('[POST update-category]', err);
    res.status(500).json({ error: 'Failed to update category' });
  }
});

/**
 * POST /api/calls/:botId/approve-all
 * Bulk approve all pending constraints for a call
 */
callsRouter.post('/calls/:botId/approve-all', (req: Request, res: Response) => {
  try {
    initTables();
    const { botId } = req.params;
    const chatDb = getChatDb();

    const review = chatDb.prepare(`
      SELECT id FROM call_reviews WHERE bot_id = ?
    `).get(botId) as any;

    if (!review) {
      res.status(404).json({ error: 'Review not found' });
      return;
    }

    const result = chatDb.prepare(`
      UPDATE call_review_constraints
      SET action_status = 'approved'
      WHERE review_id = ? AND action_status = 'pending' AND category != 'SKIP'
    `).run(review.id);

    // Mark review as reviewed
    chatDb.prepare(`
      UPDATE call_reviews SET status = 'reviewed', reviewed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
      WHERE id = ?
    `).run(review.id);

    res.json({ success: true, approved_count: result.changes });
  } catch (err) {
    console.error('[POST approve-all]', err);
    res.status(500).json({ error: 'Failed to bulk approve' });
  }
});

/**
 * POST /api/calls/constraints/:constraintId/push
 * Push an approved constraint to ConstraintsPro (marks as pushed with timestamp)
 */
callsRouter.post('/calls/constraints/:constraintId/push', (req: Request, res: Response) => {
  try {
    initTables();
    const { constraintId } = req.params;
    const chatDb = getChatDb();

    const constraint = chatDb.prepare(`
      SELECT * FROM call_review_constraints WHERE id = ?
    `).get(constraintId) as any;

    if (!constraint) {
      res.status(404).json({ error: 'Constraint not found' });
      return;
    }

    if (constraint.action_status !== 'approved') {
      res.status(400).json({ error: 'Constraint must be approved before pushing' });
      return;
    }

    // Mark as pushed (the actual ConstraintsPro API call would be handled
    // by the Telegram bot's agent system or a separate worker)
    chatDb.prepare(`
      UPDATE call_review_constraints
      SET action_status = 'pushed',
          pushed_at = strftime('%Y-%m-%dT%H:%M:%S','now'),
          push_result = 'queued'
      WHERE id = ?
    `).run(constraintId);

    res.json({
      success: true,
      message: 'Constraint queued for push to ConstraintsPro',
    });
  } catch (err) {
    console.error('[POST push constraint]', err);
    res.status(500).json({ error: 'Failed to push constraint' });
  }
});
