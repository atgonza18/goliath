import { Router, Request, Response } from 'express';
import { getMemoryDb } from '../services/database';

export const memoriesRouter = Router();

interface MemoryRow {
  id: number;
  summary: string;
  detail: string | null;
  category: string;
  project_key: string | null;
  created_at: string;
  tags: string | null;
}

/**
 * GET /api/memories/search?q=query
 * Full-text search across the memory database
 */
memoriesRouter.get('/memories/search', (req: Request, res: Response) => {
  try {
    const query = req.query.q as string | undefined;

    if (!query || !query.trim()) {
      res.json({ results: [] });
      return;
    }

    const memDb = getMemoryDb();

    // Try FTS search first (fast and ranked), fall back to LIKE if FTS table doesn't exist
    let rows: MemoryRow[];
    try {
      // Use FTS5 match with rank
      rows = memDb.prepare(`
        SELECT m.id, m.summary, m.detail, m.category, m.project_key, m.created_at, m.tags,
               rank
        FROM memories_fts fts
        JOIN memories m ON m.id = fts.rowid
        WHERE memories_fts MATCH ?
        ORDER BY rank
        LIMIT 30
      `).all(query.trim()) as (MemoryRow & { rank: number })[];
    } catch {
      // FTS not available, fall back to LIKE
      const pattern = `%${query.trim()}%`;
      rows = memDb.prepare(`
        SELECT id, summary, detail, category, project_key, created_at, tags
        FROM memories
        WHERE summary LIKE ? OR detail LIKE ?
        ORDER BY created_at DESC
        LIMIT 30
      `).all(pattern, pattern) as MemoryRow[];
    }

    const results = rows.map((row, idx) => ({
      content: `[${row.category}] ${row.summary}${row.detail ? '\n' + row.detail : ''}`,
      score: 1 - (idx * 0.02), // approximate score from ordering
      id: row.id,
      category: row.category,
      project: row.project_key || null,
      date: row.created_at,
      tags: row.tags || null,
    }));

    res.json({ results });
  } catch (err) {
    console.error('[GET /api/memories/search]', err);
    res.status(500).json({ error: 'Failed to search memories' });
  }
});
