import { Router, Request, Response } from 'express';
import { v4 as uuidv4 } from 'uuid';
import { spawn, ChildProcess } from 'child_process';

export const appBuilderRouter = Router();

// ---------------------------------------------------------------------------
// Session management for App Builder chat
// ---------------------------------------------------------------------------

interface BuilderSession {
  id: string;
  cliSessionId: string | null;
  intent: 'goliath-feature' | 'new-app';
  backend: string | null;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: BuilderMessage[];
  activeProcess: ChildProcess | null;
}

interface BuilderMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
}

const builderSessions = new Map<string, BuilderSession>();

// Path to Claude CLI
const CLAUDE_BIN = '/usr/bin/claude';
const CLAUDE_CWD = '/opt/goliath';

// ---------------------------------------------------------------------------
// System prompt for App Builder DevOps agent
// ---------------------------------------------------------------------------

function getDevOpsSystemPrompt(intent: string, backend: string | null): string {
  const today = new Date().toISOString().split('T')[0];

  const basePrompt = `You are the DevOps / App Builder Agent for Goliath, a solar construction portfolio management system.

## Your Role
You build applications and features on demand. You are an expert full-stack developer and DevOps engineer.
You write clean, production-ready code. You explain what you're building as you go.

## Context
- Today's date is ${today}
- The Goliath platform lives at /opt/goliath
- The web platform (React + Express) is at /opt/goliath/web-platform
- You have full access to the filesystem and can create, edit, and deploy code

## Communication Style
- Be direct and technical. Show your work.
- When you start building, describe what you're creating step by step
- Use markdown formatting for clarity (code blocks, headers, lists)
- When you create files, mention the path clearly
- When you finish a task, summarize what was built and what the next steps are`;

  if (intent === 'new-app') {
    const backendNote = backend === 'postgres'
      ? 'The app uses a Postgres sidecar container (docker-compose). Connection is injected as DATABASE_URL.'
      : backend === 'convex-cloud'
      ? 'The app connects to Convex Cloud (convex.dev). Generate schema + server functions. Will need a Convex API key.'
      : backend === 'convex-self-hosted'
      ? 'The app uses a self-hosted Convex backend running as a Docker container alongside the app.'
      : 'No specific backend selected.';

    return `${basePrompt}

## Build Mode: NEW APP (Container Mode)
You are building a standalone application that runs in its own Docker container.

### Backend
${backendNote}

### Deployment
- Apps are deployed as Docker containers on the Goliath VPS
- Each app gets its own isolated runtime, database, and URL
- Apps are managed via docker-compose in /opt/goliath/apps/<app-name>/
- Traefik reverse proxy handles routing: <app-name>.goliath.localhost
- The app NEVER touches the Goliath codebase

### Your Process
1. Ask clarifying questions if the user's requirements are vague
2. Design the architecture (tech stack, file structure, key components)
3. Generate all necessary files (Dockerfile, docker-compose.yml, app code, etc.)
4. Provide deployment instructions
5. Report BUILD_COMPLETE when done

### Tech Preferences
- Frontend: React + Vite + Tailwind CSS (unless user specifies otherwise)
- Backend: Node.js/Express or Python/FastAPI (match the use case)
- Always include a Dockerfile and docker-compose.yml
- Always include a .env.example with required variables`;
  }

  // Goliath Feature (Patch Mode)
  return `${basePrompt}

## Build Mode: GOLIATH FEATURE (Patch Mode)
You are modifying the Goliath platform itself — adding pages, components, API routes, or agents.

### Goliath Architecture
- Frontend: React 19 + Vite + Tailwind CSS + shadcn/ui at /opt/goliath/web-platform/frontend/
- Backend: Express.js + TypeScript at /opt/goliath/web-platform/backend/
- Telegram Bot: Python at /opt/goliath/telegram-bot/
- Agents: Defined in /opt/goliath/telegram-bot/bot/agents/definitions.py

### Key Files
- Routes registered in /opt/goliath/web-platform/backend/src/index.ts
- Pages registered in /opt/goliath/web-platform/frontend/src/App.tsx
- Theme system uses CSS custom properties (var(--theme-*))
- All pages use JetBrains Mono font, brutaloid style

### Your Process
1. Understand the existing codebase patterns
2. Implement the feature following existing conventions
3. Explain what you changed and why
4. Note if a restart or rebuild is required
5. Report BUILD_COMPLETE when done

### Rules
- Follow the existing code style (TypeScript, consistent patterns)
- Use the existing theme system (CSS vars, not hardcoded colors)
- Don't break existing functionality
- Changes to .py files require a bot restart
- Changes to frontend require a Vite rebuild`;
}

// ---------------------------------------------------------------------------
// Stream state management
// ---------------------------------------------------------------------------

interface BuilderStreamState {
  text: string;
  done: boolean;
  streaming: boolean;
  started: boolean;
  pendingMessage: string;
  pendingSession: BuilderSession;
  onDelta?: (delta: string) => void;
  onDone?: () => void;
  abortFn?: () => void;
}

const activeBuilderStreams = new Map<string, BuilderStreamState>();

function spawnBuilderClaude(
  message: string,
  session: BuilderSession,
  streamId: string,
): void {
  const stream = activeBuilderStreams.get(streamId);
  if (!stream) return;

  const systemPrompt = getDevOpsSystemPrompt(session.intent, session.backend);

  const args = [
    '-p', message,
    '--output-format', 'stream-json',
    '--include-partial-messages',
    '--verbose',
    '--strict-mcp-config',
    '--dangerously-skip-permissions',
  ];

  if (session.cliSessionId) {
    args.push('--resume', session.cliSessionId);
  } else {
    args.push('--system-prompt', systemPrompt);
  }

  // Clean environment
  const env: Record<string, string> = {};
  for (const [key, val] of Object.entries(process.env)) {
    if (
      val !== undefined &&
      !key.startsWith('CLAUDECODE') &&
      !key.startsWith('CLAUDE_CODE') &&
      !key.startsWith('CLAUDE_AGENT')
    ) {
      env[key] = val;
    }
  }
  env['PATH'] = '/usr/bin:/usr/local/bin:/bin:/usr/sbin:/sbin';
  env['TERM'] = 'dumb';
  env['HOME'] = process.env.HOME || '/home/goliath';
  env['CLAUDECODE'] = '';
  env['CLAUDE_AGENT_SDK_VERSION'] = '';
  env['CLAUDE_CODE_ENTRYPOINT'] = '';

  const child = spawn(CLAUDE_BIN, args, {
    cwd: CLAUDE_CWD,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  session.activeProcess = child;

  let buffer = '';
  let fullText = '';
  let gotSessionId = false;

  child.stdout.on('data', (data: Buffer) => {
    buffer += data.toString();
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const event = JSON.parse(trimmed);

        // Extract CLI session ID
        if (event.type === 'system' && event.subtype === 'init' && event.session_id) {
          if (!gotSessionId) {
            session.cliSessionId = event.session_id;
            gotSessionId = true;
          }
        }

        // Text delta
        if (event.type === 'stream_event' && event.event) {
          const inner = event.event;
          if (
            inner.type === 'content_block_delta' &&
            inner.delta?.type === 'text_delta' &&
            inner.delta.text
          ) {
            const delta = inner.delta.text;
            fullText += delta;
            stream.text = fullText;
            if (stream.onDelta) {
              stream.onDelta(delta);
            }
          }
        }

        // Fallback: assistant message
        if (event.type === 'assistant' && event.message?.content) {
          for (const block of event.message.content) {
            if (block.type === 'text' && block.text) {
              if (!fullText) {
                fullText = block.text;
                stream.text = fullText;
                if (stream.onDelta) {
                  stream.onDelta(fullText);
                }
              }
            }
          }
        }

        // Result — done
        if (event.type === 'result') {
          if (event.result && typeof event.result === 'string') {
            fullText = event.result;
            stream.text = fullText;
          }
          stream.done = true;
          stream.streaming = false;
          if (stream.onDone) stream.onDone();

          session.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: fullText,
            timestamp: new Date().toISOString(),
          });
          session.updatedAt = new Date().toISOString();
          session.activeProcess = null;
        }
      } catch {
        // Skip non-JSON
      }
    }
  });

  child.stderr.on('data', (data: Buffer) => {
    console.log('[app-builder stderr]', data.toString().trim());
  });

  // 10-minute timeout for builds (longer than chat)
  const processTimeout = setTimeout(() => {
    if (!stream.done) {
      console.log('[app-builder] Process timed out after 10 minutes, killing');
      try { child.kill('SIGTERM'); } catch {}
    }
  }, 600000);

  child.on('error', (err) => {
    clearTimeout(processTimeout);
    console.error('[app-builder spawn error]', err.message);
    const fallback = `**Error:** Failed to start Claude CLI: ${err.message}`;
    stream.text = fallback;
    stream.done = true;
    stream.streaming = false;
    if (stream.onDone) stream.onDone();

    session.messages.push({
      id: uuidv4(),
      role: 'assistant',
      content: fallback,
      timestamp: new Date().toISOString(),
    });
    session.activeProcess = null;
  });

  child.on('close', (code) => {
    clearTimeout(processTimeout);
    console.log(`[app-builder] Process exited with code ${code}`);

    if (buffer.trim()) {
      try {
        const event = JSON.parse(buffer.trim());
        if (event.type === 'result' && event.result) {
          fullText = event.result;
          stream.text = fullText;
        }
      } catch {}
    }

    if (!stream.done) {
      if (fullText) {
        stream.text = fullText;
      } else if (code !== 0) {
        stream.text = `**Error:** Build agent exited with code ${code}. Please try again.`;
      } else {
        stream.text = stream.text || 'No response received. Please try again.';
      }
      stream.done = true;
      stream.streaming = false;
      if (stream.onDone) stream.onDone();

      if (!session.messages.find(m => m.content === stream.text && m.role === 'assistant')) {
        session.messages.push({
          id: uuidv4(),
          role: 'assistant',
          content: stream.text,
          timestamp: new Date().toISOString(),
        });
      }
      session.activeProcess = null;
    }
  });

  stream.abortFn = () => {
    try { child.kill('SIGTERM'); } catch {}
  };

  // Auto-clean after 15 minutes
  setTimeout(() => {
    activeBuilderStreams.delete(streamId);
  }, 900000);
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/**
 * POST /api/app-builder/chat — send a message to the App Builder DevOps agent
 * Body: { sessionId?, backend?, intent?, message }
 */
appBuilderRouter.post('/app-builder/chat', (req: Request, res: Response) => {
  try {
    const { sessionId, backend, intent, message } = req.body as {
      sessionId?: string;
      backend?: string;
      intent?: string;
      message?: string;
    };

    if (!message || typeof message !== 'string' || !message.trim()) {
      res.status(400).json({ error: 'message is required' });
      return;
    }

    const resolvedIntent = (intent === 'goliath-feature' ? 'goliath-feature' : 'new-app') as 'goliath-feature' | 'new-app';

    // Create or get session
    let session: BuilderSession;
    if (sessionId && builderSessions.has(sessionId)) {
      session = builderSessions.get(sessionId)!;
    } else {
      const newId = sessionId || uuidv4();
      session = {
        id: newId,
        cliSessionId: null,
        intent: resolvedIntent,
        backend: backend || null,
        title: message.slice(0, 80) + (message.length > 80 ? '...' : ''),
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        messages: [],
        activeProcess: null,
      };
      builderSessions.set(newId, session);
    }

    // Update title from first message
    if (session.messages.length === 0) {
      session.title = message.slice(0, 80) + (message.length > 80 ? '...' : '');
    }

    // Store user message
    const userMsg: BuilderMessage = {
      id: uuidv4(),
      role: 'user',
      content: message.trim(),
      timestamp: new Date().toISOString(),
    };
    session.messages.push(userMsg);
    session.updatedAt = new Date().toISOString();

    // Set up SSE stream (deferred spawn)
    const streamId = uuidv4();
    activeBuilderStreams.set(streamId, {
      text: '',
      done: false,
      streaming: true,
      started: false,
      pendingMessage: message.trim(),
      pendingSession: session,
    });

    res.json({
      id: userMsg.id,
      sessionId: session.id,
      streamUrl: `/api/app-builder/stream/${streamId}`,
    });
  } catch (err) {
    console.error('[POST /api/app-builder/chat]', err);
    res.status(500).json({ error: 'Failed to process message' });
  }
});

/**
 * GET /api/app-builder/stream/:streamId — SSE for streaming build responses
 */
appBuilderRouter.get('/app-builder/stream/:streamId', (req: Request, res: Response) => {
  const streamId = req.params.streamId as string;
  const stream = activeBuilderStreams.get(streamId);

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  if (res.socket) {
    res.socket.setNoDelay(true);
    res.socket.setTimeout(0);
  }

  // Proxy buffer pad
  res.write(`: proxy-buffer-pad ${'_'.repeat(2048)}\n\n`);

  const sseWrite = (data: string) => {
    try {
      res.write(data);
      if (typeof (res as any).flush === 'function') {
        (res as any).flush();
      }
    } catch {
      // Client disconnected
    }
  };

  if (!stream) {
    sseWrite(`data: ${JSON.stringify({ type: 'error', text: 'Stream not found.' })}\n\n`);
    sseWrite(`data: [DONE]\n\n`);
    res.end();
    return;
  }

  if (stream.streaming) {
    sseWrite(': heartbeat\n\n');

    const heartbeat = setInterval(() => {
      sseWrite(': heartbeat\n\n');
    }, 5000);

    if (stream.text) {
      sseWrite(`data: ${JSON.stringify({ type: 'snapshot', text: stream.text })}\n\n`);
    }

    stream.onDelta = (delta: string) => {
      try {
        sseWrite(`data: ${JSON.stringify({ type: 'delta', text: delta })}\n\n`);
      } catch {
        clearInterval(heartbeat);
      }
    };

    stream.onDone = () => {
      clearInterval(heartbeat);
      try {
        sseWrite(`data: [DONE]\n\n`);
        res.end();
      } catch {}
      activeBuilderStreams.delete(streamId);
    };

    // Spawn Claude CLI after SSE callbacks are wired
    if (!stream.started) {
      stream.started = true;
      spawnBuilderClaude(stream.pendingMessage, stream.pendingSession, streamId);
    }

    req.on('close', () => {
      clearInterval(heartbeat);
      stream.onDelta = undefined;
      stream.onDone = undefined;
    });
    return;
  }

  // Already done
  if (stream.text) {
    sseWrite(`data: ${JSON.stringify({ type: 'snapshot', text: stream.text })}\n\n`);
  }
  sseWrite(`data: [DONE]\n\n`);
  res.end();
  activeBuilderStreams.delete(streamId);
});

/**
 * DELETE /api/app-builder/sessions/:id — kill a builder session
 */
appBuilderRouter.delete('/app-builder/sessions/:id', (req: Request, res: Response) => {
  const id = req.params.id as string;
  const session = builderSessions.get(id);
  if (!session) {
    res.status(404).json({ error: 'Session not found' });
    return;
  }
  if (session.activeProcess) {
    try { session.activeProcess.kill('SIGTERM'); } catch {}
  }
  builderSessions.delete(id);
  res.json({ success: true });
});

// Cleanup on exit
function cleanupBuilderSessions() {
  builderSessions.forEach((session) => {
    if (session.activeProcess) {
      try { session.activeProcess.kill('SIGTERM'); } catch {}
    }
  });
  builderSessions.clear();
}

process.on('SIGTERM', cleanupBuilderSessions);
process.on('SIGINT', cleanupBuilderSessions);
process.on('exit', cleanupBuilderSessions);
