import { Router, Request, Response } from 'express';
import { getChatDb } from '../services/database';

export const conversationsRouter = Router();

/**
 * GET /api/conversations
 * Returns list of conversations with last message preview
 */
conversationsRouter.get('/conversations', (_req: Request, res: Response) => {
  try {
    const db = getChatDb();

    const rows = db.prepare(`
      SELECT
        c.id,
        c.title,
        c.updated_at as timestamp,
        (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as lastMessage,
        (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as messageCount
      FROM conversations c
      ORDER BY c.updated_at DESC
      LIMIT 50
    `).all() as Array<{
      id: string;
      title: string;
      timestamp: string;
      lastMessage: string | null;
      messageCount: number;
    }>;

    const conversations = rows.map(row => ({
      id: row.id,
      title: row.title || 'Untitled',
      lastMessage: row.lastMessage || '',
      timestamp: row.timestamp,
      messageCount: row.messageCount,
    }));

    res.json(conversations);
  } catch (err) {
    console.error('[GET /api/conversations]', err);
    res.status(500).json({ error: 'Failed to list conversations' });
  }
});

/**
 * GET /api/conversations/:id
 * Returns all messages in a conversation
 */
conversationsRouter.get('/conversations/:id', (req: Request, res: Response) => {
  try {
    const db = getChatDb();
    const { id } = req.params;

    // Check conversation exists
    const conv = db.prepare('SELECT id FROM conversations WHERE id = ?').get(id);
    if (!conv) {
      res.status(404).json({ error: 'Conversation not found' });
      return;
    }

    const rows = db.prepare(`
      SELECT id, role, content, created_at as timestamp
      FROM messages
      WHERE conversation_id = ?
      ORDER BY created_at ASC
    `).all(id) as Array<{
      id: number;
      role: string;
      content: string;
      timestamp: string;
    }>;

    const messages = rows.map(row => ({
      id: String(row.id),
      role: row.role as 'user' | 'assistant',
      content: row.content,
      timestamp: row.timestamp,
    }));

    res.json(messages);
  } catch (err) {
    console.error('[GET /api/conversations/:id]', err);
    res.status(500).json({ error: 'Failed to get conversation' });
  }
});
