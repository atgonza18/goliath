import { Router, Request, Response } from 'express';
import path from 'path';
import fs from 'fs';
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

/**
 * GET /api/production/dashboard
 * Returns real production data extracted from POD PDFs — activities, quantities, rates.
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

    // Get all production data in date range
    const rows = db.prepare(`
      SELECT project_key, report_date, activity, quantity_today, unit,
             percent_complete, actual_rate, required_rate, blocks, notes
      FROM pod_production
      WHERE report_date >= ?
      ORDER BY report_date DESC, project_key, activity
    `).all(cutoffDate) as Array<{
      project_key: string; report_date: string; activity: string;
      quantity_today: number | null; unit: string | null;
      percent_complete: number | null; actual_rate: number | null;
      required_rate: number | null; blocks: string | null; notes: string | null;
    }>;

    // Build per-project data
    const projectMap = new Map<string, {
      activities: Array<{
        activity: string; quantity_today: number | null; unit: string | null;
        percent_complete: number | null; actual_rate: number | null;
        required_rate: number | null; blocks: string | null; notes: string | null;
        report_date: string;
      }>;
      dailySeries: Map<string, Map<string, number>>;
      latestDate: string | null;
    }>();

    for (const row of rows) {
      if (!projectMap.has(row.project_key)) {
        projectMap.set(row.project_key, {
          activities: [],
          dailySeries: new Map(),
          latestDate: null,
        });
      }
      const proj = projectMap.get(row.project_key)!;

      // Track latest date
      if (!proj.latestDate || row.report_date > proj.latestDate) {
        proj.latestDate = row.report_date;
      }

      // Add to activities (latest date only for the summary)
      if (row.report_date === proj.latestDate || proj.activities.length === 0) {
        proj.activities.push({
          activity: row.activity,
          quantity_today: row.quantity_today,
          unit: row.unit,
          percent_complete: row.percent_complete,
          actual_rate: row.actual_rate,
          required_rate: row.required_rate,
          blocks: row.blocks,
          notes: row.notes,
          report_date: row.report_date,
        });
      }

      // Daily series: date -> activity -> quantity
      if (!proj.dailySeries.has(row.report_date)) {
        proj.dailySeries.set(row.report_date, new Map());
      }
      const dayMap = proj.dailySeries.get(row.report_date)!;
      dayMap.set(row.activity, (dayMap.get(row.activity) || 0) + (row.quantity_today || 0));
    }

    // Build project response
    const projects: object[] = [];
    const portfolioDailySeries = new Map<string, Map<string, number>>();
    let activeSites = 0;
    const todayTotals = new Map<string, { quantity: number; unit: string }>();

    const sortedKeys = Object.entries(PROJECTS)
      .sort(([, a], [, b]) => a.number - b.number)
      .map(([key]) => key);

    for (const key of sortedKeys) {
      const info = PROJECTS[key];
      const projData = projectMap.get(key);
      const hasData = !!projData && projData.activities.length > 0;

      // Filter activities to only latest date for this project
      let latestActivities: Array<{
        activity: string; quantity_today: number | null; unit: string | null;
        percent_complete: number | null; actual_rate: number | null;
        required_rate: number | null; blocks: string | null; notes: string | null;
        report_date: string;
      }> = [];
      if (projData && projData.latestDate) {
        latestActivities = projData.activities.filter(
          a => a.report_date === projData.latestDate
        );
      }

      // Check if project reported today
      if (projData?.latestDate === todayStr) {
        activeSites++;
      }

      // Aggregate today's totals for portfolio
      if (projData) {
        const todayData = projData.dailySeries.get(todayStr);
        if (todayData) {
          for (const [act, qty] of todayData) {
            const existing = todayTotals.get(act);
            if (existing) {
              existing.quantity += qty;
            } else {
              // Find unit from activities
              const actData = projData.activities.find(a => a.activity === act);
              todayTotals.set(act, { quantity: qty, unit: actData?.unit || 'units' });
            }
          }
        }
      }

      // Build daily series for project
      const dailySeries: Array<{ date: string; activities: Record<string, number>; total: number }> = [];
      if (projData) {
        for (const [date, actMap] of Array.from(projData.dailySeries.entries()).sort()) {
          const activities: Record<string, number> = {};
          let total = 0;
          for (const [act, qty] of actMap) {
            activities[act] = qty;
            total += qty;

            // Also aggregate into portfolio daily
            if (!portfolioDailySeries.has(date)) {
              portfolioDailySeries.set(date, new Map());
            }
            const pDay = portfolioDailySeries.get(date)!;
            pDay.set(act, (pDay.get(act) || 0) + qty);
          }
          dailySeries.push({ date, activities, total });
        }
      }

      projects.push({
        key,
        name: info.name,
        number: info.number,
        latest_date: projData?.latestDate || null,
        has_data: hasData,
        activities: latestActivities.map(a => ({
          activity: a.activity,
          quantity_today: a.quantity_today,
          unit: a.unit,
          percent_complete: a.percent_complete,
          actual_rate: a.actual_rate,
          required_rate: a.required_rate,
          blocks: a.blocks,
          notes: a.notes,
        })),
        daily_series: dailySeries,
      });
    }

    // Portfolio daily series
    const portfolioDaily = Array.from(portfolioDailySeries.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, actMap]) => {
        const activities: Record<string, number> = {};
        let total = 0;
        for (const [act, qty] of actMap) {
          activities[act] = qty;
          total += qty;
        }
        return { date, activities, total };
      });

    // Today totals as object
    const todayTotalsObj: Record<string, { quantity: number; unit: string }> = {};
    for (const [act, data] of todayTotals) {
      todayTotalsObj[act] = data;
    }

    const response = {
      generated_at: new Date().toISOString(),
      portfolio: {
        active_sites: activeSites,
        total_projects: Object.keys(PROJECTS).length,
        today_totals: todayTotalsObj,
        daily_series: portfolioDaily,
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
