import { Router, Request, Response } from 'express';
import path from 'path';
import fs from 'fs';
import { execFile } from 'child_process';
import { getGoliathRoot, getPodProductionDb } from '../services/database';

export const productionRouter = Router();

// ─── Project registry (mirrors config.py) ─────────────────────────────────
const PROJECTS: Record<string, { name: string; number: number }> = {
  'union-ridge':   { name: 'Union Ridge',   number: 1 },
  'duff':          { name: 'Duff',           number: 2 },
  'salt-branch':   { name: 'Salt Branch',    number: 3 },
  'blackford':     { name: 'Blackford',      number: 4 },
  'delta-bobcat':  { name: 'Delta Bobcat',   number: 5 },
  'tehuacana':     { name: 'Tehuacana',      number: 6 },
  'three-rivers':  { name: 'Three Rivers',   number: 7 },
  'scioto-ridge':  { name: 'Scioto Ridge',   number: 8 },
  'mayes':         { name: 'Mayes',          number: 9 },
  'graceland':     { name: 'Graceland',      number: 10 },
  'pecan-prairie': { name: 'Pecan Prairie',  number: 11 },
  'duffy-bess':    { name: 'Duffy BESS',     number: 12 },
};

// ─── Cache ─────────────────────────────────────────────────────────────────
const CACHE_TTL_MS = 60_000; // 60 seconds
let cache: { data: object; expires: number; mtime: number } | null = null;

// ─── Date helpers ──────────────────────────────────────────────────────────

/**
 * Format a Date as "YYYY-MM-DD" using local time (NOT UTC).
 *
 * IMPORTANT: Never use `d.toISOString().slice(0, 10)` for local-date strings.
 * toISOString() converts to UTC, which shifts the date forward after 6 PM CST
 * (or 5 PM CDT), causing off-by-one errors.
 */
function localDateStr(d: Date = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// ─── Helpers ───────────────────────────────────────────────────────────────

interface PodFile {
  date: string;
  filename: string;
  size: number;
  mtime: number;
}

/** Scan a project's pod/ directory for dated PDF files. */
function scanPodFiles(projectsDir: string, projectKey: string): PodFile[] {
  const podDir = path.join(projectsDir, projectKey, 'pod');
  if (!fs.existsSync(podDir) || !fs.statSync(podDir).isDirectory()) {
    return [];
  }

  const results: PodFile[] = [];
  const dateRegex = /^(\d{4}-\d{2}-\d{2})_/;

  let entries: string[];
  try {
    entries = fs.readdirSync(podDir);
  } catch {
    return [];
  }

  for (const name of entries) {
    if (name.startsWith('.')) continue;
    if (!name.toLowerCase().endsWith('.pdf')) continue;

    const match = name.match(dateRegex);
    if (!match) continue;

    try {
      const fullPath = path.join(podDir, name);
      const st = fs.statSync(fullPath);
      if (!st.isFile()) continue;

      results.push({
        date: match[1],
        filename: name,
        size: st.size,
        mtime: st.mtimeMs,
      });
    } catch {
      continue;
    }
  }

  results.sort((a, b) => b.date.localeCompare(a.date)); // newest first
  return results;
}

/** Get the latest mtime across all project pod/ directories (for cache busting). */
function getPodDirsMtime(projectsDir: string): number {
  let latest = 0;
  for (const key of Object.keys(PROJECTS)) {
    const podDir = path.join(projectsDir, key, 'pod');
    try {
      const st = fs.statSync(podDir);
      if (st.mtimeMs > latest) latest = st.mtimeMs;
    } catch {
      // directory doesn't exist
    }
  }
  return latest;
}

interface DailyCount {
  date: string;
  count: number;
}

interface ProjectTrend {
  key: string;
  name: string;
  number: number;
  today: number;
  yesterday: number;
  delta_units: number;
  delta_pct: number;
  seven_day_total: number;
  all_time_total: number;
  trend: 'up' | 'down' | 'flat' | 'none';
  latest_pod_date: string | null;
  days_since_last_pod: number | null;
  daily: DailyCount[];
}

/** Build the full production trends response. */
function buildProductionTrends(projectsDir: string) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  // Build 7-day date range (including today)
  const dateRange: string[] = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    dateRange.push(localDateStr(d));
  }

  const projectsData: ProjectTrend[] = [];

  const sortedKeys = Object.entries(PROJECTS)
    .sort(([, a], [, b]) => a.number - b.number)
    .map(([key]) => key);

  for (const key of sortedKeys) {
    const info = PROJECTS[key];
    const podFiles = scanPodFiles(projectsDir, key);

    // Build date → file count map
    const dateCounts: Record<string, number> = {};
    for (const pf of podFiles) {
      dateCounts[pf.date] = (dateCounts[pf.date] || 0) + 1;
    }

    // Daily data for 7-day window
    const daily: DailyCount[] = dateRange.map(d => ({
      date: d,
      count: dateCounts[d] || 0,
    }));

    const todayStr = dateRange[dateRange.length - 1];
    const yesterdayStr = dateRange[dateRange.length - 2];
    const todayCount = dateCounts[todayStr] || 0;
    const yesterdayCount = dateCounts[yesterdayStr] || 0;

    // Delta
    const deltaUnits = todayCount - yesterdayCount;
    let deltaPct: number;
    if (yesterdayCount > 0) {
      deltaPct = Math.round(((deltaUnits / yesterdayCount) * 100) * 10) / 10;
    } else if (todayCount > 0) {
      deltaPct = 100.0;
    } else {
      deltaPct = 0.0;
    }

    // 7-day total
    const sevenDayTotal = daily.reduce((s, d) => s + d.count, 0);

    // Trend: compare first 4 days vs last 3 days
    let trend: 'up' | 'down' | 'flat' | 'none';
    if (sevenDayTotal === 0) {
      trend = 'none';
    } else {
      const firstHalf = daily.slice(0, 4).reduce((s, d) => s + d.count, 0);
      const secondHalf = daily.slice(4).reduce((s, d) => s + d.count, 0);
      if (secondHalf > firstHalf) trend = 'up';
      else if (secondHalf < firstHalf) trend = 'down';
      else trend = 'flat';
    }

    // Latest POD and days since
    const latestPod = podFiles.length > 0 ? podFiles[0].date : null;
    let daysSince: number | null = null;
    if (latestPod) {
      const latestDate = new Date(latestPod + 'T00:00:00');
      daysSince = Math.floor((today.getTime() - latestDate.getTime()) / 86_400_000);
    }

    projectsData.push({
      key,
      name: info.name,
      number: info.number,
      today: todayCount,
      yesterday: yesterdayCount,
      delta_units: deltaUnits,
      delta_pct: deltaPct,
      seven_day_total: sevenDayTotal,
      all_time_total: podFiles.length,
      trend,
      latest_pod_date: latestPod,
      days_since_last_pod: daysSince,
      daily,
    });
  }

  // Portfolio-wide summary
  const portfolioToday = projectsData.reduce((s, p) => s + p.today, 0);
  const portfolioYesterday = projectsData.reduce((s, p) => s + p.yesterday, 0);
  const portfolio7d = projectsData.reduce((s, p) => s + p.seven_day_total, 0);
  const projectsReportingToday = projectsData.filter(p => p.today > 0).length;
  const projectsWithData = projectsData.filter(p => p.all_time_total > 0).length;

  // Aggregate daily for portfolio chart
  const portfolioDaily: DailyCount[] = dateRange.map((d, i) => ({
    date: d,
    count: projectsData.reduce((s, p) => s + p.daily[i].count, 0),
  }));

  return {
    generated_at: new Date().toISOString(),
    date_range: { start: dateRange[0], end: dateRange[dateRange.length - 1] },
    portfolio: {
      today: portfolioToday,
      yesterday: portfolioYesterday,
      delta_units: portfolioToday - portfolioYesterday,
      seven_day_total: portfolio7d,
      projects_reporting_today: projectsReportingToday,
      total_projects: Object.keys(PROJECTS).length,
      projects_with_data: projectsWithData,
      daily: portfolioDaily,
    },
    projects: projectsData,
  };
}

// ─── Route ─────────────────────────────────────────────────────────────────

/**
 * GET /api/production/trends
 * Returns per-project POD activity data with 7-day trends, deltas, and portfolio summary.
 * Cached for 60s with automatic bust when a new POD file lands.
 */
productionRouter.get('/production/trends', (_req: Request, res: Response) => {
  try {
    const root = getGoliathRoot();
    const projectsDir = path.join(root, 'projects');
    const now = Date.now();

    // Check cache validity
    const currentMtime = getPodDirsMtime(projectsDir);
    const cacheValid = cache
      && cache.expires > now
      && currentMtime <= cache.mtime;

    if (cacheValid && cache) {
      res.json(cache.data);
      return;
    }

    // Rebuild
    const data = buildProductionTrends(projectsDir);
    cache = { data, expires: now + CACHE_TTL_MS, mtime: currentMtime };
    res.json(data);
  } catch (err) {
    console.error('[GET /api/production/trends]', err);
    res.status(500).json({ error: 'Failed to build production trends' });
  }
});

// ─── Dashboard cache ────────────────────────────────────────────────────────
const DASHBOARD_CACHE_TTL_MS = 60_000;
let dashboardCache: { data: object; expires: number } | null = null;

// DB row shape — uses actual column names (no aliasing)
interface PodRow {
  project_key: string;
  report_date: string;
  activity_category: string;
  activity_name: string;
  qty_to_date: number | null;
  qty_last_workday: number | null;
  qty_completed_yesterday: number | null;
  total_qty: number | null;
  unit: string | null;
  pct_complete: number | null;
  today_location: string | null;
  notes: string | null;
}

/**
 * GET /api/production/dashboard
 * Returns production summary per project matching the frontend ProjectProductionSummary type.
 * Query: ?days=30 (default 30)
 */
productionRouter.get('/production/dashboard', (req: Request, res: Response) => {
  try {
    const days = Math.min(Math.max(parseInt(req.query.days as string) || 30, 1), 365);
    const now = Date.now();

    if (dashboardCache && dashboardCache.expires > now) {
      res.json(dashboardCache.data);
      return;
    }

    const db = getPodProductionDb();
    const cutoffDate = localDateStr(new Date(Date.now() - days * 86_400_000));
    const todayStr = localDateStr();

    // Get all production data in date range — use real column names
    const rows = db.prepare(`
      SELECT project_key, report_date, activity_category, activity_name,
             qty_to_date, qty_last_workday, qty_completed_yesterday,
             total_qty, unit, pct_complete, today_location, notes
      FROM pod_production
      WHERE report_date >= ?
      ORDER BY report_date DESC, project_key, activity_category, activity_name
    `).all(cutoffDate) as PodRow[];

    // Group rows by project, then find latest date per project
    const projectMap = new Map<string, { rows: PodRow[]; latestDate: string | null }>();

    for (const row of rows) {
      if (!projectMap.has(row.project_key)) {
        projectMap.set(row.project_key, { rows: [], latestDate: null });
      }
      const p = projectMap.get(row.project_key)!;
      p.rows.push(row);
      if (!p.latestDate || row.report_date > p.latestDate) {
        p.latestDate = row.report_date;
      }
    }

    // Build project summaries matching ProjectProductionSummary
    const projects: object[] = [];
    let activeSites = 0;
    let projectsWithData = 0;

    const sortedKeys = Object.entries(PROJECTS)
      .sort(([, a], [, b]) => a.number - b.number)
      .map(([key]) => key);

    for (const key of sortedKeys) {
      const info = PROJECTS[key];
      const projData = projectMap.get(key);
      const hasData = !!projData && projData.rows.length > 0;

      if (hasData) projectsWithData++;
      if (projData?.latestDate === todayStr) activeSites++;

      // Get latest-date activities grouped by category
      const latestRows = projData
        ? projData.rows.filter(r => r.report_date === projData.latestDate)
        : [];

      // Build category summary
      const catMap = new Map<string, { count: number; pcts: number[] }>();
      const allPcts: number[] = [];

      for (const row of latestRows) {
        const cat = row.activity_category || 'General';
        if (!catMap.has(cat)) catMap.set(cat, { count: 0, pcts: [] });
        const c = catMap.get(cat)!;
        c.count++;
        if (row.pct_complete != null) {
          c.pcts.push(row.pct_complete);
          allPcts.push(row.pct_complete);
        }
      }

      const categoriesSummary = Array.from(catMap.entries()).map(([category, data]) => ({
        category,
        activity_count: data.count,
        avg_pct_complete: data.pcts.length > 0
          ? Math.round((data.pcts.reduce((s, v) => s + v, 0) / data.pcts.length) * 10) / 10
          : null,
      }));

      const overallProgress = allPcts.length > 0
        ? Math.round((allPcts.reduce((s, v) => s + v, 0) / allPcts.length) * 10) / 10
        : null;

      projects.push({
        key,
        name: info.name,
        number: info.number,
        latest_date: projData?.latestDate || null,
        has_data: hasData,
        activity_count: latestRows.length,
        category_count: catMap.size,
        categories_summary: categoriesSummary,
        overall_progress: overallProgress,
      });
    }

    const response = {
      generated_at: new Date().toISOString(),
      portfolio: {
        active_sites: activeSites,
        total_projects: Object.keys(PROJECTS).length,
        projects_with_data: projectsWithData,
      },
      projects,
    };

    dashboardCache = { data: response, expires: now + DASHBOARD_CACHE_TTL_MS };
    res.json(response);
  } catch (err) {
    console.error('[GET /api/production/dashboard]', err);
    res.status(500).json({ error: 'Failed to build production dashboard' });
  }
});

/**
 * GET /api/production/dashboard/:projectKey
 * Returns detailed production data for a single project — categories with activities.
 * Matches the frontend ProjectProductionDetail / PodCategory / PodActivity types.
 */
productionRouter.get('/production/dashboard/:projectKey', (req: Request, res: Response) => {
  try {
    const projectKey = req.params.projectKey as string;

    if (!PROJECTS[projectKey]) {
      res.status(404).json({ error: `Unknown project key: ${projectKey}` });
      return;
    }

    const info = PROJECTS[projectKey];
    const db = getPodProductionDb();

    // Find the latest report date for this project
    const latestRow = db.prepare(`
      SELECT MAX(report_date) as latest_date
      FROM pod_production
      WHERE project_key = ?
    `).get(projectKey) as { latest_date: string | null } | undefined;

    const latestDate = latestRow?.latest_date || null;

    if (!latestDate) {
      res.json({
        key: projectKey,
        name: info.name,
        number: info.number,
        latest_date: null,
        categories: [],
      });
      return;
    }

    // Get all activities for latest date, grouped by category
    const actRows = db.prepare(`
      SELECT activity_category, activity_name,
             qty_to_date, qty_last_workday, qty_completed_yesterday,
             total_qty, unit, pct_complete, today_location, notes
      FROM pod_production
      WHERE project_key = ? AND report_date = ?
      ORDER BY activity_category, activity_name
    `).all(projectKey, latestDate) as Array<{
      activity_category: string;
      activity_name: string;
      qty_to_date: number | null;
      qty_last_workday: number | null;
      qty_completed_yesterday: number | null;
      total_qty: number | null;
      unit: string | null;
      pct_complete: number | null;
      today_location: string | null;
      notes: string | null;
    }>;

    // Group into categories
    const catMap = new Map<string, Array<{
      activity_name: string;
      qty_to_date: number | null;
      qty_last_workday: number | null;
      qty_completed_yesterday: number;
      total_qty: number | null;
      unit: string | null;
      pct_complete: number | null;
      today_location: string | null;
      notes: string | null;
    }>>();

    for (const row of actRows) {
      const cat = row.activity_category || 'General';
      if (!catMap.has(cat)) catMap.set(cat, []);
      catMap.get(cat)!.push({
        activity_name: row.activity_name,
        qty_to_date: row.qty_to_date,
        qty_last_workday: row.qty_last_workday,
        qty_completed_yesterday: row.qty_completed_yesterday ?? 0,
        total_qty: row.total_qty,
        unit: row.unit,
        pct_complete: row.pct_complete,
        today_location: row.today_location,
        notes: row.notes,
      });
    }

    const categories = Array.from(catMap.entries()).map(([category, activities]) => ({
      category,
      activities,
    }));

    res.json({
      key: projectKey,
      name: info.name,
      number: info.number,
      latest_date: latestDate,
      categories,
    });
  } catch (err) {
    console.error('[GET /api/production/dashboard/:projectKey]', err);
    res.status(500).json({ error: 'Failed to load project production detail' });
  }
});

/**
 * POST /api/production/extract
 * Triggers the POD extraction script (extract_pod_data.py) asynchronously.
 */
let extractionRunning = false;

productionRouter.post('/production/extract', (_req: Request, res: Response) => {
  if (extractionRunning) {
    res.json({ status: 'already_running', message: 'Extraction is already in progress' });
    return;
  }

  const root = getGoliathRoot();
  const scriptPath = path.join(root, 'scripts', 'extract_pod_data.py');

  if (!fs.existsSync(scriptPath)) {
    res.status(500).json({ status: 'error', message: 'Extraction script not found' });
    return;
  }

  extractionRunning = true;

  // Run extraction asynchronously — don't wait for completion
  const pythonPath = path.join(root, 'venv', 'bin', 'python3');
  const python = fs.existsSync(pythonPath) ? pythonPath : 'python3';

  execFile(python, [scriptPath], {
    cwd: root,
    timeout: 600_000, // 10 min max
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  }, (err, _stdout, stderr) => {
    extractionRunning = false;
    // Clear dashboard cache so next request picks up new data
    dashboardCache = null;
    if (err) {
      console.error('[POST /api/production/extract] Script error:', stderr?.slice(0, 500));
    }
  });

  res.json({ status: 'started', message: 'Extraction started in background' });
});

/**
 * GET /api/production/project/:projectKey/trend
 * Returns cumulative actual vs. planned (baseline) production data for a single project.
 * Queries pod_production grouped by report_date.
 */
productionRouter.get('/production/project/:projectKey/trend', (req: Request, res: Response) => {
  try {
    const projectKey = req.params.projectKey as string;

    if (!PROJECTS[projectKey]) {
      res.status(404).json({ error: `Unknown project key: ${projectKey}` });
      return;
    }

    const db = getPodProductionDb();

    // Query real column names: qty_completed_yesterday (daily) and qty_to_date (cumulative)
    const rows = db.prepare(`
      SELECT report_date,
             SUM(qty_completed_yesterday) as daily_qty,
             SUM(qty_to_date)             as cumulative_qty
      FROM pod_production
      WHERE project_key = ?
      GROUP BY report_date
      ORDER BY report_date ASC
    `).all(projectKey) as Array<{ report_date: string; daily_qty: number | null; cumulative_qty: number | null }>;

    const dates: string[] = [];
    const actuals_daily: number[] = [];
    const planned_daily: number[] = [];
    const actuals_cumulative: number[] = [];
    const planned_cumulative: number[] = [];

    let prevCumulative = 0;

    for (const row of rows) {
      const cumulative = row.cumulative_qty ?? 0;
      // Prefer explicit daily qty; fall back to cumulative delta when daily is zero
      let daily = row.daily_qty ?? 0;
      if (daily === 0 && cumulative > prevCumulative) {
        daily = cumulative - prevCumulative;
      }

      dates.push(row.report_date);
      actuals_daily.push(daily);
      actuals_cumulative.push(cumulative);
      planned_daily.push(0);       // no baseline schedule in schema
      planned_cumulative.push(0);

      prevCumulative = cumulative;
    }

    const hasBaseline = false; // real schema has no planned/required_rate column

    res.json({
      project_key: projectKey,
      has_baseline: hasBaseline,
      dates,
      actuals_daily,
      actuals_cumulative,
      planned_daily,
      planned_cumulative,
    });
  } catch (err) {
    console.error('[GET /api/production/project/:projectKey/trend]', err);
    res.status(500).json({ error: 'Failed to build project production trend' });
  }
});

/**
 * GET /api/production/extraction-status
 * Returns extraction pipeline health for monitoring.
 */
productionRouter.get('/production/extraction-status', (_req: Request, res: Response) => {
  try {
    const db = getPodProductionDb();

    const totals = db.prepare(`
      SELECT status, COUNT(*) as count FROM pod_extraction_log GROUP BY status
    `).all() as Array<{ status: string; count: number }>;

    const recent = db.prepare(`
      SELECT source_file, project_key, report_date, status, error_message, extracted_at
      FROM pod_extraction_log
      ORDER BY extracted_at DESC
      LIMIT 20
    `).all();

    const lastExtraction = db.prepare(`
      SELECT MAX(extracted_at) as last FROM pod_extraction_log WHERE status = 'success'
    `).get() as { last: string | null } | undefined;

    const totalActivities = db.prepare(`
      SELECT COUNT(*) as count FROM pod_production
    `).get() as { count: number };

    const statusMap: Record<string, number> = {};
    for (const t of totals) {
      statusMap[t.status] = t.count;
    }

    res.json({
      generated_at: new Date().toISOString(),
      summary: {
        total_files_processed: totals.reduce((s, t) => s + t.count, 0),
        success: statusMap['success'] || 0,
        failed: statusMap['failed'] || 0,
        corrupted: statusMap['corrupted'] || 0,
        total_activities_extracted: totalActivities.count,
        last_successful_extraction: lastExtraction?.last || null,
      },
      recent_extractions: recent,
    });
  } catch (err) {
    console.error('[GET /api/production/extraction-status]', err);
    res.status(500).json({ error: 'Failed to get extraction status' });
  }
});
