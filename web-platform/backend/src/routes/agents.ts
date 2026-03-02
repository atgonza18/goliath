import { Router, Request, Response } from 'express';
import { getMemoryDb } from '../services/database';

export const agentsRouter = Router();

// Static agent definitions — mirrors the Goliath agent ecosystem
const AGENT_DEFINITIONS: Array<{
  name: string;
  role: string;
  description: string;
}> = [
  {
    name: 'schedule_analyst',
    role: 'Schedule Analyst',
    description: 'Analyzes project schedules, critical path items, milestone tracking, and schedule pressure points across the solar portfolio.',
  },
  {
    name: 'constraints_manager',
    role: 'Constraints Manager',
    description: 'Tracks and manages project constraints, monitors resolution status, and escalates overdue items across all 12 projects.',
  },
  {
    name: 'pod_analyst',
    role: 'POD Analyst',
    description: 'Processes Plan of the Day reports, extracts key updates, tracks daily production metrics and safety observations.',
  },
  {
    name: 'report_writer',
    role: 'Report Writer',
    description: 'Generates formatted reports including constraint summaries, project status updates, and executive briefings.',
  },
  {
    name: 'excel_expert',
    role: 'Excel Expert',
    description: 'Reads and analyzes Excel spreadsheets including schedules, budgets, and constraint logs. Extracts structured data from complex workbooks.',
  },
  {
    name: 'construction_manager',
    role: 'Construction Manager',
    description: 'Provides construction domain expertise for solar EPC projects — scope, means & methods, subcontractor coordination, and field operations.',
  },
  {
    name: 'scheduling_expert',
    role: 'Scheduling Expert',
    description: 'Deep expertise in P6/MS Project schedule analysis, critical path method, resource loading, and earned value metrics.',
  },
  {
    name: 'cost_analyst',
    role: 'Cost Analyst',
    description: 'Analyzes project budgets, cost variances, change orders, and financial forecasting for solar construction projects.',
  },
  {
    name: 'devops',
    role: 'DevOps Engineer',
    description: 'Manages infrastructure, deployments, and system health for the Goliath platform. Handles cron jobs, databases, and API integrations.',
  },
  {
    name: 'researcher',
    role: 'Researcher',
    description: 'Performs deep research tasks — vendor lookups, code compliance, weather analysis, and industry benchmarking for solar construction.',
  },
  {
    name: 'folder_organizer',
    role: 'Folder Organizer',
    description: 'Manages the project file system structure, organizes incoming documents, and maintains the portfolio data hierarchy.',
  },
  {
    name: 'transcript_processor',
    role: 'Transcript Processor',
    description: 'Processes meeting transcripts from Recall.ai — extracts summaries, action items, decisions, constraints, and follow-ups.',
  },
];

/**
 * GET /api/agents
 * Return the list of available agents with their status
 */
agentsRouter.get('/agents', (_req: Request, res: Response) => {
  try {
    const memDb = getMemoryDb();

    const agents = AGENT_DEFINITIONS.map(agent => {
      let lastActive: string | undefined;
      let tasksCompleted = 0;

      // Try to find activity for this agent in the activity log or memories
      try {
        // Check activity_log for subagent dispatches
        const row = memDb.prepare(
          `SELECT created_at FROM activity_log
           WHERE subagents_dispatched LIKE ?
           ORDER BY created_at DESC LIMIT 1`
        ).get(`%${agent.name}%`) as { created_at: string } | undefined;

        if (row) {
          lastActive = row.created_at;
        }
      } catch {
        // activity_log may not exist or have different schema
      }

      // Count tasks (memories created by this agent or dispatches involving it)
      try {
        const row = memDb.prepare(
          `SELECT COUNT(*) as cnt FROM memories WHERE source = ?`
        ).get(agent.name) as { cnt: number } | undefined;
        tasksCompleted = row?.cnt || 0;
      } catch {
        // ignore
      }

      return {
        name: agent.name,
        role: agent.role,
        description: agent.description,
        status: 'active' as const,
        lastActive,
        tasksCompleted,
      };
    });

    res.json(agents);
  } catch (err) {
    console.error('[GET /api/agents]', err);
    res.status(500).json({ error: 'Failed to list agents' });
  }
});
