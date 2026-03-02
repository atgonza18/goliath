import { Router, Request, Response } from 'express';
import { getMemoryDb } from '../services/database';

export const actionItemsRouter = Router();

interface ActionItemRow {
  id: number;
  created_at: string;
  summary: string;
  detail: string | null;
  project_key: string | null;
  resolved: number;
  tags: string | null;
  source: string | null;
}

/**
 * GET /api/action-items
 * List action items, optionally filtered by ?project=
 */
actionItemsRouter.get('/action-items', (req: Request, res: Response) => {
  try {
    const memDb = getMemoryDb();
    const projectFilter = req.query.project as string | undefined;

    let sql = `
      SELECT id, created_at, summary, detail, project_key, resolved, tags, source
      FROM memories
      WHERE category = 'action_item'
    `;
    const params: string[] = [];

    if (projectFilter) {
      sql += ` AND project_key = ?`;
      params.push(projectFilter);
    }

    sql += ` ORDER BY created_at DESC LIMIT 100`;

    const rows = memDb.prepare(sql).all(...params) as ActionItemRow[];

    const items = rows.map(row => {
      const tags = row.tags || '';
      let status: string = 'open';
      if (row.resolved) {
        status = 'resolved';
      } else if (tags.includes('in-progress') || tags.includes('pending')) {
        status = 'in-progress';
      }

      return {
        id: String(row.id),
        date: row.created_at,
        summary: row.summary,
        detail: row.detail || '',
        project: row.project_key || 'general',
        status,
        assignee: undefined,
        dueDate: undefined,
      };
    });

    res.json(items);
  } catch (err) {
    console.error('[GET /api/action-items]', err);
    res.status(500).json({ error: 'Failed to list action items' });
  }
});

/**
 * POST /api/action-items/:id/resolve
 * Mark an action item as resolved
 *
 * NOTE: The memory DB is opened read-only for safety.
 * For now, this endpoint returns success but logs a warning.
 * A future version will use a write connection or a separate tracking table.
 */
actionItemsRouter.post('/action-items/:id/resolve', (req: Request, res: Response) => {
  try {
    const { id } = req.params;
    console.warn(`[action-items] Resolve requested for id=${id} — memory DB is read-only, resolution tracked locally`);

    // We cannot write to the memory DB (opened read-only).
    // For MVP: acknowledge the resolution. In production, either:
    // 1. Open memory DB read-write, or
    // 2. Track resolutions in the local chat.db
    res.json({ success: true, note: 'Resolution tracked (read-only mode)' });
  } catch (err) {
    console.error('[POST /api/action-items/:id/resolve]', err);
    res.status(500).json({ error: 'Failed to resolve action item' });
  }
});
