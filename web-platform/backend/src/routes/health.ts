import { Router } from 'express';

export const healthRouter = Router();

const startTime = Date.now();

healthRouter.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    uptime: Math.floor((Date.now() - startTime) / 1000),
    version: '1.0.0',
  });
});
