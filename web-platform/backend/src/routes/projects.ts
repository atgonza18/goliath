import { Router, Request, Response } from 'express';
import path from 'path';
import fs from 'fs';
import { getMemoryDb, getGoliathRoot } from '../services/database';

export const projectsRouter = Router();

// Human-readable names for project keys
const PROJECT_NAMES: Record<string, string> = {
  'blackford': 'Blackford',
  'delta-bobcat': 'Delta Bobcat',
  'duff': 'Duff',
  'duffy-bess': 'Duffy Bess',
  'graceland': 'Graceland',
  'mayes': 'Mayes',
  'pecan-prairie': 'Pecan Prairie',
  'salt-branch': 'Salt Branch',
  'scioto-ridge': 'Scioto Ridge',
  'tehuacana': 'Tehuacana',
  'three-rivers': 'Three Rivers',
  'union-ridge': 'Union Ridge',
};

interface Contact {
  name: string;
  role: string;
  email?: string;
  phone?: string;
}

interface ConstraintRow {
  id: number;
  summary: string;
  detail: string | null;
  resolved: number;
  created_at: string;
  tags: string | null;
}

interface ActivityRow {
  id: number;
  category: string;
  summary: string;
  created_at: string;
}

/**
 * GET /api/projects
 * List all 12 projects with status and constraint counts
 */
projectsRouter.get('/projects', (_req: Request, res: Response) => {
  try {
    const root = getGoliathRoot();
    const projectsDir = path.join(root, 'projects');
    const memDb = getMemoryDb();

    const projects: Array<{
      key: string;
      name: string;
      status: string;
      constraintsCount: number;
      openItems: number;
      recentActivity: string;
    }> = [];

    // List project directories (skip hidden dirs like .stfolder)
    let dirs: string[];
    try {
      dirs = fs.readdirSync(projectsDir).filter(d => {
        return !d.startsWith('.') && fs.statSync(path.join(projectsDir, d)).isDirectory();
      });
    } catch {
      dirs = Object.keys(PROJECT_NAMES);
    }

    for (const key of dirs) {
      const name = PROJECT_NAMES[key] || key.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

      // Count constraint files in the constraints folder
      const constraintsDir = path.join(projectsDir, key, 'constraints');
      let fileConstraints = 0;
      try {
        fileConstraints = fs.readdirSync(constraintsDir).filter(f => !f.startsWith('.')).length;
      } catch {
        // no constraints folder or empty
      }

      // Count constraint-type memories from DB
      let dbConstraints = 0;
      try {
        const row = memDb.prepare(
          `SELECT COUNT(*) as cnt FROM memories WHERE project_key = ? AND category IN ('constraint','action_item') AND resolved = 0`
        ).get(key) as { cnt: number } | undefined;
        dbConstraints = row?.cnt || 0;
      } catch {
        // DB may not have this project
      }

      const constraintsCount = fileConstraints + dbConstraints;

      // Count open action items
      let openItems = 0;
      try {
        const row = memDb.prepare(
          `SELECT COUNT(*) as cnt FROM memories WHERE project_key = ? AND category = 'action_item' AND resolved = 0`
        ).get(key) as { cnt: number } | undefined;
        openItems = row?.cnt || 0;
      } catch {
        // ignore
      }

      // Get most recent activity
      let recentActivity = 'No recent activity';
      try {
        const row = memDb.prepare(
          `SELECT summary, created_at FROM memories WHERE project_key = ? ORDER BY created_at DESC LIMIT 1`
        ).get(key) as { summary: string; created_at: string } | undefined;
        if (row) {
          recentActivity = row.summary.slice(0, 120);
        }
      } catch {
        // ignore
      }

      // Determine status based on data availability
      let status: string = 'unknown';
      if (constraintsCount > 5) {
        status = 'at-risk';
      } else if (constraintsCount > 0 || openItems > 0) {
        status = 'on-track';
      }

      // Check for schedule files to influence status
      const scheduleDir = path.join(projectsDir, key, 'schedule');
      try {
        const schedFiles = fs.readdirSync(scheduleDir).filter(f => !f.startsWith('.'));
        if (schedFiles.length > 0 && status === 'unknown') {
          status = 'on-track';
        }
      } catch {
        // no schedule
      }

      projects.push({
        key,
        name,
        status,
        constraintsCount,
        openItems,
        recentActivity,
      });
    }

    // Sort alphabetically by name
    projects.sort((a, b) => a.name.localeCompare(b.name));
    res.json(projects);
  } catch (err) {
    console.error('[GET /api/projects]', err);
    res.status(500).json({ error: 'Failed to list projects' });
  }
});

/**
 * GET /api/projects/:key
 * Get detailed project info: contacts, constraints, recent activities
 */
projectsRouter.get('/projects/:key', (req: Request, res: Response) => {
  try {
    const key = req.params.key as string;
    const root = getGoliathRoot();
    const projectDir = path.join(root, 'projects', key);

    if (!fs.existsSync(projectDir)) {
      res.status(404).json({ error: `Project '${key}' not found` });
      return;
    }

    const memDb = getMemoryDb();
    const name = PROJECT_NAMES[key] || key.replace(/-/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());

    // --- Contacts ---
    const contacts: Contact[] = [];
    const contactsFile = path.join(root, 'contacts', `${key}.json`);
    try {
      if (fs.existsSync(contactsFile)) {
        const data = JSON.parse(fs.readFileSync(contactsFile, 'utf-8'));
        if (data.contacts && Array.isArray(data.contacts)) {
          for (const c of data.contacts) {
            contacts.push({
              name: c.name || 'Unknown',
              role: c.role || 'Unknown',
              email: c.email || undefined,
              phone: c.phone || undefined,
            });
          }
        }
      }
    } catch {
      // no contacts file
    }

    // --- Constraints from memory DB ---
    const constraints: Array<{
      id: string;
      description: string;
      status: string;
      priority: string;
      dateLogged: string;
      dueDate?: string;
    }> = [];

    try {
      const rows = memDb.prepare(
        `SELECT id, summary, detail, resolved, created_at, tags
         FROM memories
         WHERE project_key = ? AND category IN ('constraint', 'action_item')
         ORDER BY created_at DESC
         LIMIT 50`
      ).all(key) as ConstraintRow[];

      for (const row of rows) {
        const tags = row.tags || '';
        let priority: string = 'medium';
        if (tags.includes('critical') || tags.includes('high-priority')) {
          priority = 'high';
        } else if (tags.includes('low')) {
          priority = 'low';
        }

        constraints.push({
          id: String(row.id),
          description: row.summary,
          status: row.resolved ? 'resolved' : 'open',
          priority,
          dateLogged: row.created_at,
          dueDate: undefined,
        });
      }
    } catch {
      // ignore
    }

    // --- Recent activities from memory ---
    const recentActivities: Array<{
      id: string;
      type: string;
      summary: string;
      timestamp: string;
    }> = [];

    try {
      const rows = memDb.prepare(
        `SELECT id, category, summary, created_at
         FROM memories
         WHERE project_key = ?
         ORDER BY created_at DESC
         LIMIT 20`
      ).all(key) as ActivityRow[];

      for (const row of rows) {
        recentActivities.push({
          id: String(row.id),
          type: row.category,
          summary: row.summary,
          timestamp: row.created_at,
        });
      }
    } catch {
      // ignore
    }

    // --- Counts ---
    const constraintsCount = constraints.filter(c => c.status === 'open').length;
    const openItems = constraints.filter(c => c.status === 'open').length;

    let status: string = 'unknown';
    if (constraintsCount > 5) status = 'at-risk';
    else if (constraintsCount > 0) status = 'on-track';

    // Check for schedule files
    const scheduleDir = path.join(projectDir, 'schedule');
    try {
      const schedFiles = fs.readdirSync(scheduleDir).filter(f => !f.startsWith('.'));
      if (schedFiles.length > 0 && status === 'unknown') status = 'on-track';
    } catch {
      // ignore
    }

    let recentActivity = 'No recent activity';
    if (recentActivities.length > 0) {
      recentActivity = recentActivities[0].summary.slice(0, 120);
    }

    res.json({
      key,
      name,
      status,
      constraintsCount,
      openItems,
      recentActivity,
      contacts,
      constraints,
      recentActivities,
    });
  } catch (err) {
    console.error('[GET /api/projects/:key]', err);
    res.status(500).json({ error: 'Failed to get project detail' });
  }
});
