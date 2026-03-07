import { Router, Request, Response } from 'express';
import fs from 'fs';
import path from 'path';

export const swarmRouter = Router();

const SWARM_STATE_PATH = path.resolve('/opt/goliath/data/swarm_state.json');

interface AgentStatus {
  status: 'running' | 'completed' | 'failed';
  duration_ms?: number;
}

interface SwarmState {
  status: 'idle' | 'active' | 'completed';
  swarm_id?: string;
  agents?: string[];
  count?: number;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  succeeded?: number;
  failed?: number;
  agent_status?: Record<string, AgentStatus>;
}

/**
 * GET /api/swarm/status — returns current swarm state
 *
 * The Python orchestrator writes swarm state to a shared JSON file.
 * This endpoint reads that file and returns the current state.
 *
 * Response:
 *   { status: 'idle' }
 *   { status: 'active', swarm_id, agents, count, started_at }
 *   { status: 'completed', swarm_id, agents, count, started_at, completed_at, duration_ms, succeeded, failed }
 */
swarmRouter.get('/swarm/status', (_req: Request, res: Response) => {
  try {
    if (!fs.existsSync(SWARM_STATE_PATH)) {
      res.json({ status: 'idle' } as SwarmState);
      return;
    }

    const raw = fs.readFileSync(SWARM_STATE_PATH, 'utf-8');
    const state: SwarmState = JSON.parse(raw);

    // Stale active state (> 15 minutes old) — treat as idle
    if (state.status === 'active' && state.started_at) {
      const age = Date.now() - new Date(state.started_at).getTime();
      if (age > 15 * 60 * 1000) {
        res.json({ status: 'idle' } as SwarmState);
        return;
      }
    }

    res.json(state);
  } catch (err) {
    // Corrupt file or read error — return idle
    console.error('[swarm/status] Error reading swarm state:', err);
    res.json({ status: 'idle' } as SwarmState);
  }
});
