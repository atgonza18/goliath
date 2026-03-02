import { Router, Request, Response } from 'express';

export const constraintsRouter = Router();

// ---------------------------------------------------------------------------
// Convex HTTP client setup
// ---------------------------------------------------------------------------

const CONVEX_URL = process.env.CONVEX_URL || 'https://charming-cuttlefish-923.convex.cloud';

/**
 * Low-level helper: call a Convex query function via the HTTP API.
 * Convex exposes /api/query, /api/mutation, /api/action endpoints.
 */
async function convexQuery(fnPath: string, args: Record<string, unknown> = {}): Promise<unknown> {
  // Strip undefined values
  const cleanArgs: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(args)) {
    if (v !== undefined) cleanArgs[k] = v;
  }

  const response = await fetch(`${CONVEX_URL}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      path: fnPath,
      args: cleanArgs,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Convex query ${fnPath} failed (${response.status}): ${text}`);
  }

  const result = await response.json();

  // Convex HTTP API returns { status: "success", value: ... } or { status: "error", errorMessage: ... }
  if (result.status === 'error') {
    throw new Error(result.errorMessage || 'Convex query error');
  }

  return result.value;
}

// Cache for projects list (refreshed every 5 minutes)
let projectsCache: Array<{ _id: string; name: string; code?: string }> | null = null;
let projectsCacheTime = 0;
const CACHE_TTL = 5 * 60 * 1000;

async function getProjects(): Promise<Array<{ _id: string; name: string; code?: string }>> {
  const now = Date.now();
  if (projectsCache && now - projectsCacheTime < CACHE_TTL) {
    return projectsCache;
  }
  const projects = await convexQuery('projects:list', {}) as Array<{ _id: string; name: string; code?: string }>;
  projectsCache = projects;
  projectsCacheTime = now;
  return projects;
}

async function findProjectByName(name: string): Promise<string | undefined> {
  const projects = await getProjects();
  const lower = name.toLowerCase().replace(/[\s-]+/g, '');
  const match = projects.find(p => {
    const pLower = p.name.toLowerCase().replace(/[\s-]+/g, '');
    const cLower = (p.code || '').toLowerCase().replace(/[\s-]+/g, '');
    return pLower === lower || cLower === lower || pLower.includes(lower) || lower.includes(pLower);
  });
  return match?._id;
}

// Map from project key (slug) to Convex project name
const KEY_TO_NAME: Record<string, string> = {
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

// ---------------------------------------------------------------------------
// GET /api/constraints
// List constraints. ?project=slug&status=open|in_progress|resolved&priority=high|medium|low
// ---------------------------------------------------------------------------
constraintsRouter.get('/constraints', async (req: Request, res: Response) => {
  try {
    const { project, status, priority } = req.query as {
      project?: string;
      status?: string;
      priority?: string;
    };

    let constraints: any[];

    if (project) {
      // Resolve project slug to Convex project ID
      const projectName = KEY_TO_NAME[project] || project;
      const projectId = await findProjectByName(projectName);
      if (!projectId) {
        res.json([]);
        return;
      }
      constraints = (await convexQuery('constraints:listByProject', { projectId })) as any[];
    } else {
      // Get all projects and fetch constraints for each
      const projects = await getProjects();
      constraints = [];
      // Use the DSC dashboard which gives us all constraints grouped
      try {
        const dashboard = (await convexQuery('constraints:getDscDashboard', {})) as any;
        if (dashboard && dashboard.leads) {
          for (const lead of dashboard.leads) {
            if (lead.constraints) {
              constraints.push(...lead.constraints);
            }
          }
        }
        if (dashboard && dashboard.unclaimed) {
          constraints.push(...dashboard.unclaimed);
        }
      } catch {
        // Fallback: fetch per-project
        for (const p of projects) {
          try {
            const pConstraints = (await convexQuery('constraints:listByProject', { projectId: p._id })) as any[];
            constraints.push(...pConstraints);
          } catch {
            // skip project on error
          }
        }
      }
    }

    // Apply filters
    if (status) {
      constraints = constraints.filter((c: any) => c.status === status);
    }
    if (priority) {
      constraints = constraints.filter((c: any) => c.priority === priority);
    }

    // Normalize response shape
    const result = constraints.map((c: any) => ({
      id: c._id,
      description: c.description || '',
      discipline: c.discipline || 'Other',
      status: c.status || 'open',
      priority: c.priority || 'medium',
      owner: c.owner || null,
      projectId: c.projectId || null,
      projectName: c.projectName || null,
      dscLead: c.dscLeadName || null,
      dueDate: c.dueDate ? new Date(c.dueDate).toISOString() : null,
      createdAt: c._creationTime ? new Date(c._creationTime).toISOString() : null,
      notes: c.notes || '',
    }));

    res.json(result);
  } catch (err) {
    console.error('[GET /api/constraints]', err);
    res.status(500).json({ error: 'Failed to fetch constraints' });
  }
});

// ---------------------------------------------------------------------------
// GET /api/constraints/stats
// Dashboard stats: total, by status, by project, aging
// Note: this must come BEFORE /api/constraints/:id
// ---------------------------------------------------------------------------
constraintsRouter.get('/constraints/stats', async (_req: Request, res: Response) => {
  try {
    const projects = await getProjects();

    // Gather all constraints
    let allConstraints: any[] = [];
    try {
      const dashboard = (await convexQuery('constraints:getDscDashboard', {})) as any;
      if (dashboard && dashboard.leads) {
        for (const lead of dashboard.leads) {
          if (lead.constraints) {
            allConstraints.push(...lead.constraints);
          }
        }
      }
      if (dashboard && dashboard.unclaimed) {
        allConstraints.push(...dashboard.unclaimed);
      }
    } catch {
      for (const p of projects) {
        try {
          const pc = (await convexQuery('constraints:listByProject', { projectId: p._id })) as any[];
          allConstraints.push(...pc);
        } catch {
          // skip
        }
      }
    }

    // Compute stats
    const total = allConstraints.length;
    const byStatus: Record<string, number> = {};
    const byPriority: Record<string, number> = {};
    const byProject: Record<string, number> = {};
    let overdue = 0;
    const now = Date.now();

    for (const c of allConstraints) {
      const st = c.status || 'open';
      byStatus[st] = (byStatus[st] || 0) + 1;

      const pr = c.priority || 'medium';
      byPriority[pr] = (byPriority[pr] || 0) + 1;

      const pName = c.projectName || 'Unknown';
      byProject[pName] = (byProject[pName] || 0) + 1;

      if (c.dueDate && c.status !== 'resolved' && c.dueDate < now) {
        overdue++;
      }
    }

    // Aging: how many constraints are > 7, > 14, > 30 days old
    const aging = { over7d: 0, over14d: 0, over30d: 0 };
    for (const c of allConstraints) {
      if (c.status === 'resolved') continue;
      const created = c._creationTime || 0;
      const ageMs = now - created;
      if (ageMs > 30 * 86400000) aging.over30d++;
      else if (ageMs > 14 * 86400000) aging.over14d++;
      else if (ageMs > 7 * 86400000) aging.over7d++;
    }

    res.json({
      total,
      byStatus,
      byPriority,
      byProject,
      overdue,
      aging,
    });
  } catch (err) {
    console.error('[GET /api/constraints/stats]', err);
    res.status(500).json({ error: 'Failed to compute constraint stats' });
  }
});

// ---------------------------------------------------------------------------
// GET /api/constraints/:id
// Get single constraint with full notes and activity
// ---------------------------------------------------------------------------
constraintsRouter.get('/constraints/:id', async (req: Request, res: Response) => {
  try {
    const constraintId = req.params.id;
    const constraint = (await convexQuery('constraints:getWithNotes', { constraintId })) as any;

    if (!constraint) {
      res.status(404).json({ error: 'Constraint not found' });
      return;
    }

    // Also fetch activity history
    let activity: any[] = [];
    try {
      activity = (await convexQuery('constraints:getActivityHistory', { constraintId })) as any[];
    } catch {
      // activity may not be available
    }

    res.json({
      id: constraint._id,
      description: constraint.description || '',
      discipline: constraint.discipline || 'Other',
      status: constraint.status || 'open',
      priority: constraint.priority || 'medium',
      owner: constraint.owner || null,
      projectId: constraint.projectId || null,
      projectName: constraint.projectName || null,
      dscLead: constraint.dscLeadName || null,
      dueDate: constraint.dueDate ? new Date(constraint.dueDate).toISOString() : null,
      createdAt: constraint._creationTime ? new Date(constraint._creationTime).toISOString() : null,
      notes: constraint.notes || '',
      activity: (activity || []).map((a: any) => ({
        id: a._id,
        type: a.type || a.action || '',
        detail: a.detail || a.description || '',
        timestamp: a._creationTime ? new Date(a._creationTime).toISOString() : '',
        user: a.userName || '',
      })),
    });
  } catch (err) {
    console.error('[GET /api/constraints/:id]', err);
    res.status(500).json({ error: 'Failed to fetch constraint' });
  }
});

// ---------------------------------------------------------------------------
// GET /api/constraints/by-project/:projectKey
// Convenience endpoint: get constraints for a project by slug key
// ---------------------------------------------------------------------------
constraintsRouter.get('/constraints/by-project/:projectKey', async (req: Request, res: Response) => {
  try {
    const key = req.params.projectKey as string;
    const projectName = KEY_TO_NAME[key] || key;
    const projectId = await findProjectByName(projectName);

    if (!projectId) {
      res.json([]);
      return;
    }

    const constraints = (await convexQuery('constraints:listByProject', { projectId })) as any[];

    const result = constraints.map((c: any) => ({
      id: c._id,
      description: c.description || '',
      discipline: c.discipline || 'Other',
      status: c.status || 'open',
      priority: c.priority || 'medium',
      owner: c.owner || null,
      projectName: c.projectName || null,
      dscLead: c.dscLeadName || null,
      dueDate: c.dueDate ? new Date(c.dueDate).toISOString() : null,
      createdAt: c._creationTime ? new Date(c._creationTime).toISOString() : null,
      notes: c.notes || '',
    }));

    res.json(result);
  } catch (err) {
    console.error('[GET /api/constraints/by-project/:projectKey]', err);
    res.status(500).json({ error: 'Failed to fetch project constraints' });
  }
});
