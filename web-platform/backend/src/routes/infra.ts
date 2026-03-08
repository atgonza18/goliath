import { Router } from 'express';
import fs from 'fs';
import path from 'path';

export const infraRouter = Router();

const PHASE1_STATUS_PATH = path.resolve(__dirname, '../../../../infrastructure/phase1-status.json');

/**
 * GET /api/infra/phase1-status
 *
 * Returns the Phase 1 infrastructure status from the status file
 * written by the setup-phase1.sh script. If the file doesn't exist,
 * returns a "not configured" response.
 */
infraRouter.get('/infra/phase1-status', (_req, res) => {
  try {
    if (!fs.existsSync(PHASE1_STATUS_PATH)) {
      res.status(404).json({
        phase: 1,
        docker_installed: false,
        compose_installed: false,
        traefik_running: false,
        hello_world_running: false,
        domain: null,
        server_ip: null,
        hello_world_url: null,
        traefik_dashboard_url: null,
        setup_completed_at: null,
        message: 'Phase 1 infrastructure has not been set up yet. Run setup-phase1.sh to configure.',
      });
      return;
    }

    const raw = fs.readFileSync(PHASE1_STATUS_PATH, 'utf-8');
    const data = JSON.parse(raw);
    res.json(data);
  } catch (err) {
    console.error('[infra] Failed to read phase1-status.json:', err);
    res.status(500).json({ error: 'Failed to read infrastructure status' });
  }
});
