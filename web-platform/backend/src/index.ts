import express from 'express';
import { createServer } from 'http';
import net from 'net';
import cors from 'cors';
import path from 'path';
import fs from 'fs';
import { WebSocketServer, WebSocket } from 'ws';
import { execSync, spawn } from 'child_process';

import { healthRouter } from './routes/health';
import { chatRouter, handleChatWebSocket } from './routes/chat';
import { conversationsRouter } from './routes/conversations';
import { projectsRouter } from './routes/projects';
import { actionItemsRouter } from './routes/actionItems';
import { agentsRouter } from './routes/agents';
import { memoriesRouter } from './routes/memories';
import { filesRouter } from './routes/files';
import { constraintsRouter } from './routes/constraints';
import { productionRouter } from './routes/production';
import { swarmRouter } from './routes/swarm';
import { infraRouter } from './routes/infra';
import { appBuilderRouter } from './routes/appBuilder';
import { callsRouter } from './routes/calls';
import { initDatabases } from './services/database';

const PORT = Number(process.env.PORT) || 80;
const FRONTEND_DIST = path.resolve(__dirname, '../../frontend/dist');

const app = express();

// --------------- Middleware ---------------
app.use(cors());
app.use(express.json());

// Request logging
app.use((req, res, next) => {
  const start = Date.now();
  res.on('finish', () => {
    const duration = Date.now() - start;
    // Skip logging static asset requests unless VERBOSE is set
    if (process.env.VERBOSE || !req.originalUrl.startsWith('/assets/')) {
      console.log(`${new Date().toISOString()} ${req.method} ${req.originalUrl} ${res.statusCode} ${duration}ms`);
    }
  });
  next();
});

// --------------- API routes ---------------
app.use('/api', healthRouter);
app.use('/api', chatRouter);
app.use('/api', conversationsRouter);
app.use('/api', projectsRouter);
app.use('/api', actionItemsRouter);
app.use('/api', agentsRouter);
app.use('/api', memoriesRouter);
app.use('/api', filesRouter);
app.use('/api', constraintsRouter);
app.use('/api', productionRouter);
app.use('/api', swarmRouter);
app.use('/api', infraRouter);
app.use('/api', appBuilderRouter);
app.use('/api', callsRouter);

// --------------- Browser (virtual desktop) API ---------------
const BROWSER_SCRIPT = '/opt/goliath/web-platform/browser/start-browser.sh';
const BROWSER_WS_PORT = 6080; // websockify port

app.get('/api/browser/status', (_req, res) => {
  try {
    const output = execSync(`bash ${BROWSER_SCRIPT} status`, { encoding: 'utf-8', timeout: 5000 });
    const running = output.includes('running');
    res.json({ running, details: output.trim() });
  } catch {
    res.json({ running: false, details: 'Browser stack not running' });
  }
});

app.post('/api/browser/start', (_req, res) => {
  try {
    const child = spawn('bash', [BROWSER_SCRIPT, 'start'], { detached: true, stdio: 'ignore' });
    child.unref();
    // Give it a moment to start
    setTimeout(() => {
      try {
        const output = execSync(`bash ${BROWSER_SCRIPT} status`, { encoding: 'utf-8', timeout: 5000 });
        res.json({ ok: true, details: output.trim() });
      } catch {
        res.json({ ok: true, details: 'Starting...' });
      }
    }, 4000);
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post('/api/browser/stop', (_req, res) => {
  try {
    execSync(`bash ${BROWSER_SCRIPT} stop`, { encoding: 'utf-8', timeout: 5000 });
    res.json({ ok: true });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// --------------- noVNC static files ---------------
const NOVNC_DIR = '/opt/goliath/libs/usr/share/novnc';
app.use('/novnc', express.static(NOVNC_DIR));

// Serve a custom noVNC page that auto-connects to our VNC proxy
app.get('/novnc/auto.html', (_req, res) => {
  res.type('html').send(`<!DOCTYPE html>
<html><head>
<title>Browser</title>
<style>
  html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #1a1a1a; }
  #screen { width: 100%; height: 100%; }
  #status { position: fixed; top: 0; left: 0; right: 0; padding: 8px; text-align: center;
    font-family: sans-serif; font-size: 13px; color: #ccc; background: rgba(0,0,0,0.7); z-index: 10; }
  #status.hidden { display: none; }
</style>
</head><body>
<div id="status">Connecting to browser...</div>
<div id="screen"></div>
<script type="module">
  import RFB from './core/rfb.js';
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = proto + '//' + location.host + '/ws/vnc';
  const status = document.getElementById('status');
  try {
    const rfb = new RFB(document.getElementById('screen'), url, { wsProtocols: ['binary'] });
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.addEventListener('connect', () => { status.classList.add('hidden'); });
    rfb.addEventListener('disconnect', () => {
      status.textContent = 'Disconnected — click to reconnect';
      status.classList.remove('hidden');
      status.style.cursor = 'pointer';
      status.onclick = () => location.reload();
    });
  } catch(e) {
    status.textContent = 'Failed to connect: ' + e.message;
  }
</script>
</body></html>`);
});

// --------------- Static frontend ---------------
// Hashed assets get long-term immutable caching
app.use('/assets', express.static(path.join(FRONTEND_DIST, 'assets'), {
  maxAge: '1y',
  immutable: true,
}));

// Other static files with shorter cache
app.use(express.static(FRONTEND_DIST, {
  maxAge: '10m',
  index: false, // We handle index.html ourselves for SPA routing
}));

// SPA fallback — any non-API GET route serves index.html
// (but don't serve index.html for requests that look like missing files)
app.get('*', (_req, res) => {
  // Never serve SPA fallback for API routes — return 404 so bugs are visible
  if (_req.path.startsWith('/api/')) {
    res.status(404).json({ error: 'API route not found' });
    return;
  }
  const ext = path.extname(_req.path);
  if (ext && ext !== '.html') {
    // Request has a file extension (e.g. .js, .css, .png) but wasn't matched
    // by express.static — it's a genuinely missing file, not a SPA route
    res.status(404).json({ error: 'Not found' });
    return;
  }
  const indexPath = path.join(FRONTEND_DIST, 'index.html');
  res.sendFile(indexPath, (err) => {
    if (err) {
      res.status(404).json({ error: 'Frontend not found — run "npm run build" in frontend/' });
    }
  });
});

// --------------- Global error handler ---------------
app.use(
  (
    err: Error,
    _req: express.Request,
    res: express.Response,
    _next: express.NextFunction
  ) => {
    console.error('[ERROR]', err.message);
    res.status(500).json({ error: 'Internal server error', detail: err.message });
  }
);

// --------------- Start ---------------
async function main() {
  try {
    initDatabases();
    console.log('Databases initialized');
  } catch (err) {
    console.error('Failed to initialize databases:', err);
    process.exit(1);
  }

  // Create HTTP server (needed for WebSocket upgrade)
  const server = createServer(app);

  // WebSocket server for real-time streaming (bypasses Cloudflare tunnel buffering)
  const wss = new WebSocketServer({ noServer: true });

  // Handle WebSocket upgrade requests
  server.on('upgrade', (request, socket, head) => {
    const url = new URL(request.url || '/', `http://${request.headers.host}`);
    if (url.pathname === '/ws/chat') {
      wss.handleUpgrade(request, socket, head, (ws) => {
        wss.emit('connection', ws, request);
      });
    } else if (url.pathname === '/ws/vnc') {
      // Proxy WebSocket to websockify (VNC) on port 6080
      const target = net.createConnection(BROWSER_WS_PORT, '127.0.0.1', () => {
        // Forward the original HTTP upgrade request to websockify
        const rawReq = `GET / HTTP/1.1\r\n` +
          `Host: 127.0.0.1:${BROWSER_WS_PORT}\r\n` +
          `Upgrade: websocket\r\n` +
          `Connection: Upgrade\r\n` +
          `Sec-WebSocket-Key: ${request.headers['sec-websocket-key']}\r\n` +
          `Sec-WebSocket-Version: ${request.headers['sec-websocket-version']}\r\n` +
          (request.headers['sec-websocket-protocol'] ? `Sec-WebSocket-Protocol: ${request.headers['sec-websocket-protocol']}\r\n` : '') +
          `\r\n`;
        target.write(rawReq);
        // Once websockify responds, pipe everything bidirectionally
        target.pipe(socket);
        socket.pipe(target);
      });
      target.on('error', () => socket.destroy());
      socket.on('error', () => target.destroy());
    } else {
      socket.destroy();
    }
  });

  wss.on('connection', (ws) => {
    console.log('[WS] New WebSocket connection');
    handleChatWebSocket(ws);
  });

  server.listen(PORT, '0.0.0.0', () => {
    console.log('');
    console.log('='.repeat(60));
    console.log('  Goliath Web Platform');
    console.log('='.repeat(60));
    console.log(`  API + Frontend server on http://0.0.0.0:${PORT}`);
    console.log(`  WebSocket: ws://0.0.0.0:${PORT}/ws/chat`);
    console.log(`  Frontend dist: ${FRONTEND_DIST}`);
    if (fs.existsSync(FRONTEND_DIST)) {
      console.log(`  Frontend status: READY`);
    } else {
      console.log(`  Frontend status: NOT BUILT (run "npm run build" in frontend/)`);
    }
    console.log(`  Access: http://178.156.152.148:${PORT}`);
    console.log('='.repeat(60));
    console.log('');
  });
}

main();
