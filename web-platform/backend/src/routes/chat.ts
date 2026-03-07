import { Router, Request, Response } from 'express';
import { v4 as uuidv4 } from 'uuid';
import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import multer from 'multer';
import type { WebSocket } from 'ws';

export const chatRouter = Router();

// ---------------------------------------------------------------------------
// File upload configuration for chat attachments
// ---------------------------------------------------------------------------

const CHAT_UPLOADS_DIR = '/opt/goliath/uploads/chat';

// Ensure uploads directory exists
if (!fs.existsSync(CHAT_UPLOADS_DIR)) {
  fs.mkdirSync(CHAT_UPLOADS_DIR, { recursive: true });
}

const chatUpload = multer({
  storage: multer.diskStorage({
    destination: CHAT_UPLOADS_DIR,
    filename: (_req, file, cb) => {
      const timestamp = Date.now();
      const safeName = file.originalname.replace(/[^a-zA-Z0-9._-]/g, '_');
      cb(null, `${timestamp}_${safeName}`);
    },
  }),
  limits: { fileSize: 20 * 1024 * 1024 }, // 20 MB
  fileFilter: (_req, file, cb) => {
    const allowed = [
      'image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/webp',
      'application/pdf',
    ];
    cb(null, allowed.includes(file.mimetype));
  },
});

// ---------------------------------------------------------------------------
// Telegram notification on agent completion
// ---------------------------------------------------------------------------

const TG_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
const TG_CHAT_ID = process.env.REPORT_CHAT_ID || '';

async function notifyTelegram(title: string, preview: string): Promise<void> {
  if (!TG_BOT_TOKEN || !TG_CHAT_ID) return;
  try {
    const text = `✅ <b>Agent task complete!</b>\n\n<b>Chat:</b> ${title}\n<b>Preview:</b> <code>${preview.slice(0, 200)}</code>\n\nOpen the GUI to see the full response.`;
    await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: TG_CHAT_ID, text, parse_mode: 'HTML' }),
    });
  } catch (err) {
    console.error('[telegram-notify]', err);
  }
}

// ---------------------------------------------------------------------------
// Session management — maps our web session IDs to Claude CLI session IDs
// ---------------------------------------------------------------------------

interface ChatSession {
  id: string;                // our web session ID
  cliSessionId: string | null; // the Claude CLI session ID (from stream-json init)
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: SessionMessage[];
  activeProcess: ChildProcess | null;
}

interface SessionMessageAttachment {
  type: 'image' | 'pdf';
  filename: string;
  originalName: string;
  mimeType: string;
  path: string;        // disk path
  url: string;         // web-accessible URL
}

interface SessionMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  attachment?: SessionMessageAttachment;
}

const sessions = new Map<string, ChatSession>();

// Nimrod system prompt for the Claude CLI
const NIMROD_SYSTEM_PROMPT = `You are Nimrod, the AI Chief Operating Officer of Goliath Construction Intelligence — an AI-powered operations platform managing a portfolio of 12 utility-scale solar construction projects for DSC (Dallas Support Center).

## Your Projects
You oversee: Blackford, Delta Bobcat, Duff, Duffy Bess, Graceland, Mayes, Pecan Prairie, Salt Branch, Scioto Ridge, Tehuacana, Three Rivers, and Union Ridge.

## Your Role
- Central brain of the Goliath system coordinating multiple AI agents
- Help construction executives understand project statuses, constraints, schedules, and operational issues
- Speak with authority about solar construction operations
- Be concise, direct, and action-oriented. No fluff.
- When you lack specific data, say so honestly
- Use markdown formatting (bold, lists, headers) for clarity

## Context
- Today's date is ${new Date().toISOString().split('T')[0]}
- The platform tracks constraints (blockers), action items, schedules, and daily field reports (Plan of the Day / POD)
- Constraints tracked in ConstraintsPro with statuses: open, in_progress, resolved
- Disciplines: Safety, Quality, Civil, Modules, AG Electrical, Piles, Environmental, Commissioning, Racking, Procurement, Other

## Important
- You have access to the Goliath codebase and project files at /opt/goliath
- You can read project data, constraint files, reports, transcripts, and other operational data
- Use your tools to look up real data when asked about specific projects or constraints
- Do NOT make up data — always check the files first`;

// Path to Claude CLI
const CLAUDE_BIN = '/usr/bin/claude';

// Working directory for Claude CLI — use /opt/goliath so the CLI has full access to project files.
// The --strict-mcp-config flag prevents loading MCP servers from project settings (which caused hangs).
const CLAUDE_CWD = '/opt/goliath';

// ---------------------------------------------------------------------------
// Helper: Run claude CLI and stream results
// ---------------------------------------------------------------------------

interface StreamState {
  text: string;           // Full accumulated text (for late-joining clients)
  done: boolean;
  streaming: boolean;
  started: boolean;       // Whether Claude CLI has been spawned
  pendingMessage: string; // Message to send when SSE connects
  pendingSession: ChatSession; // Session reference for deferred spawn
  onDelta?: (delta: string) => void;  // Send ONLY the new delta (not accumulated)
  onDone?: () => void;
  abortFn?: () => void;
}

const activeStreams = new Map<string, StreamState>();

function spawnClaude(
  message: string,
  session: ChatSession,
  streamId: string,
): void {
  const stream = activeStreams.get(streamId);
  if (!stream) return;

  const args = [
    '-p', message,
    '--output-format', 'stream-json',
    '--include-partial-messages', // Emit partial message chunks as they arrive (real streaming)
    '--verbose',
    '--strict-mcp-config', // Don't load MCP servers from project settings (prevents hang)
    '--dangerously-skip-permissions', // Allow file reads without interactive approval (stdin is ignored)
  ];

  // Resume existing session or start new with system prompt
  if (session.cliSessionId) {
    args.push('--resume', session.cliSessionId);
  } else {
    // Only set system prompt on first message (new session)
    args.push('--system-prompt', NIMROD_SYSTEM_PROMPT);
  }

  // Spawn the process with a clean environment (no CLAUDECODE vars)
  const env: Record<string, string> = {};
  // Copy necessary env vars but exclude CLAUDECODE-related ones
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
  // Override PATH to ensure claude is found
  env['PATH'] = '/usr/bin:/usr/local/bin:/bin:/usr/sbin:/sbin';
  env['TERM'] = 'dumb';
  env['HOME'] = process.env.HOME || '/home/goliath';
  // Explicitly clear these to prevent "nested session" detection
  env['CLAUDECODE'] = '';
  env['CLAUDE_AGENT_SDK_VERSION'] = '';
  env['CLAUDE_CODE_ENTRYPOINT'] = '';

  const child = spawn(CLAUDE_BIN, args, {
    cwd: CLAUDE_CWD,
    env,
    stdio: ['ignore', 'pipe', 'pipe'], // stdin ignored — prompt comes via -p flag
  });

  session.activeProcess = child;

  let buffer = '';
  let fullText = '';
  let gotSessionId = false;

  child.stdout.on('data', (data: Buffer) => {
    buffer += data.toString();

    // Process complete JSON lines
    const lines = buffer.split('\n');
    buffer = lines.pop() || ''; // Keep incomplete line

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const event = JSON.parse(trimmed);

        // Extract CLI session ID from init event
        if (event.type === 'system' && event.subtype === 'init' && event.session_id) {
          if (!gotSessionId) {
            session.cliSessionId = event.session_id;
            gotSessionId = true;
          }
        }

        // Handle streaming deltas — these are the real-time text chunks
        if (event.type === 'stream_event' && event.event) {
          const inner = event.event;

          // Text delta — append to accumulated text and stream ONLY the delta to client
          if (
            inner.type === 'content_block_delta' &&
            inner.delta?.type === 'text_delta' &&
            inner.delta.text
          ) {
            const delta = inner.delta.text;
            fullText += delta;
            stream.text = fullText;
            if (stream.onDelta) {
              stream.onDelta(delta); // Send ONLY the new delta (not accumulated text)
            }
          }
        }

        // Extract text from assistant message (full snapshot — use as fallback/sync)
        if (event.type === 'assistant' && event.message?.content) {
          for (const block of event.message.content) {
            if (block.type === 'text' && block.text) {
              // Only use as fallback if we haven't received streaming deltas yet
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

        // Handle result event (marks completion)
        if (event.type === 'result') {
          if (event.result && typeof event.result === 'string') {
            fullText = event.result;
            stream.text = fullText;
          }
          stream.done = true;
          stream.streaming = false;
          if (stream.onDone) stream.onDone();

          // Store message in session
          session.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: fullText,
            timestamp: new Date().toISOString(),
          });
          session.updatedAt = new Date().toISOString();
          session.activeProcess = null;

          // Notify via Telegram
          notifyTelegram(session.title, fullText.slice(0, 200));
        }
      } catch {
        // Skip non-JSON lines
      }
    }
  });

  let stderrBuffer = '';
  child.stderr.on('data', (data: Buffer) => {
    stderrBuffer += data.toString();
    console.log('[claude-cli stderr]', data.toString().trim());
  });

  // Kill the process after 5 minutes to prevent zombies
  const processTimeout = setTimeout(() => {
    if (!stream.done) {
      console.log('[claude-cli] Process timed out after 5 minutes, killing');
      try {
        child.kill('SIGTERM');
      } catch {
        // ignore
      }
    }
  }, 300000);

  child.on('error', (err) => {
    clearTimeout(processTimeout);
    console.error('[claude-cli spawn error]', err.message);
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

    // Notify via Telegram (error)
    notifyTelegram(session.title, '❌ ' + fallback);
  });

  child.on('close', (code) => {
    clearTimeout(processTimeout);
    console.log(`[claude-cli] Process exited with code ${code}`);

    // Process any remaining buffer
    if (buffer.trim()) {
      try {
        const event = JSON.parse(buffer.trim());
        if (event.type === 'result' && event.result) {
          fullText = event.result;
          stream.text = fullText;
        }
      } catch {
        // ignore
      }
    }

    if (!stream.done) {
      // Process exited without a result event
      if (fullText) {
        stream.text = fullText;
      } else if (code !== 0) {
        const errInfo = stderrBuffer.trim() ? ` (${stderrBuffer.trim().slice(0, 200)})` : '';
        stream.text = `**Error:** Claude CLI exited with code ${code}.${errInfo} The AI may be temporarily unavailable — please try again in a moment.`;
      } else {
        stream.text = stream.text || 'No response received from Claude CLI. Please try again.';
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

      // Notify via Telegram
      notifyTelegram(session.title, stream.text.slice(0, 200));
    }
  });

  // Store abort function
  stream.abortFn = () => {
    try {
      child.kill('SIGTERM');
    } catch {
      // ignore
    }
  };

  // Auto-clean stream after 10 minutes
  setTimeout(() => {
    activeStreams.delete(streamId);
  }, 600000);
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/**
 * POST /api/chat/sessions — create a new chat session
 */
chatRouter.post('/chat/sessions', (_req: Request, res: Response) => {
  try {
    const sessionId = uuidv4();
    const session: ChatSession = {
      id: sessionId,
      cliSessionId: null,
      title: 'New Chat',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      messages: [],
      activeProcess: null,
    };
    sessions.set(sessionId, session);

    res.json({
      id: sessionId,
      title: session.title,
      createdAt: session.createdAt,
    });
  } catch (err) {
    console.error('[POST /api/chat/sessions]', err);
    res.status(500).json({ error: 'Failed to create session' });
  }
});

/**
 * GET /api/chat/sessions — list active sessions
 */
chatRouter.get('/chat/sessions', (_req: Request, res: Response) => {
  try {
    const list = Array.from(sessions.values()).map((s) => ({
      id: s.id,
      title: s.title,
      createdAt: s.createdAt,
      updatedAt: s.updatedAt,
      messageCount: s.messages.length,
      lastMessage: s.messages.length > 0
        ? s.messages[s.messages.length - 1].content.slice(0, 100)
        : '',
    }));

    // Sort by most recently updated
    list.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

    res.json(list);
  } catch (err) {
    console.error('[GET /api/chat/sessions]', err);
    res.status(500).json({ error: 'Failed to list sessions' });
  }
});

/**
 * GET /api/chat/sessions/:id — get session details with messages
 */
chatRouter.get('/chat/sessions/:id', (req: Request, res: Response) => {
  try {
    const id = req.params.id as string;
    const session = sessions.get(id);
    if (!session) {
      res.status(404).json({ error: 'Session not found' });
      return;
    }

    res.json({
      id: session.id,
      title: session.title,
      createdAt: session.createdAt,
      updatedAt: session.updatedAt,
      messages: session.messages.map(m => ({
        ...m,
        attachment: m.attachment ? {
          type: m.attachment.type,
          filename: m.attachment.filename,
          originalName: m.attachment.originalName,
          url: m.attachment.url,
          mimeType: m.attachment.mimeType,
        } : undefined,
      })),
    });
  } catch (err) {
    console.error('[GET /api/chat/sessions/:id]', err);
    res.status(500).json({ error: 'Failed to get session' });
  }
});

/**
 * DELETE /api/chat/sessions/:id — end a session, kill any active process
 */
chatRouter.delete('/chat/sessions/:id', (req: Request, res: Response) => {
  try {
    const id = req.params.id as string;
    const session = sessions.get(id);
    if (!session) {
      res.status(404).json({ error: 'Session not found' });
      return;
    }

    // Kill active process if any
    if (session.activeProcess) {
      try {
        session.activeProcess.kill('SIGTERM');
      } catch {
        // ignore
      }
    }

    sessions.delete(id);
    res.json({ success: true });
  } catch (err) {
    console.error('[DELETE /api/chat/sessions/:id]', err);
    res.status(500).json({ error: 'Failed to delete session' });
  }
});

/**
 * POST /api/chat/message — send a message to a session, returns SSE stream URL
 * Body: { sessionId, message }
 */
chatRouter.post('/chat/message', (req: Request, res: Response) => {
  try {
    const { sessionId, message } = req.body as {
      sessionId?: string;
      message?: string;
    };

    if (!message || typeof message !== 'string' || !message.trim()) {
      res.status(400).json({ error: 'message is required' });
      return;
    }

    // Auto-create session if not provided
    let session: ChatSession;
    if (sessionId && sessions.has(sessionId)) {
      session = sessions.get(sessionId)!;
    } else {
      const newId = sessionId || uuidv4();
      session = {
        id: newId,
        cliSessionId: null,
        title: message.slice(0, 80) + (message.length > 80 ? '...' : ''),
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        messages: [],
        activeProcess: null,
      };
      sessions.set(newId, session);
    }

    // Update title from first message if still default
    if (session.title === 'New Chat' && session.messages.length === 0) {
      session.title = message.slice(0, 80) + (message.length > 80 ? '...' : '');
    }

    // Store user message
    const userMsg: SessionMessage = {
      id: uuidv4(),
      role: 'user',
      content: message.trim(),
      timestamp: new Date().toISOString(),
    };
    session.messages.push(userMsg);
    session.updatedAt = new Date().toISOString();

    // Set up SSE stream — DON'T spawn Claude CLI yet.
    // Defer until the SSE connection is established to avoid the race condition
    // where deltas arrive before the client is listening (causing big snapshot dumps
    // instead of smooth token-by-token streaming).
    const streamId = uuidv4();
    activeStreams.set(streamId, {
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
      streamUrl: `/api/chat/stream/${streamId}`,
    });
  } catch (err) {
    console.error('[POST /api/chat/message]', err);
    res.status(500).json({ error: 'Failed to process message' });
  }
});

/**
 * GET /api/chat/stream/:streamId — SSE endpoint for streaming responses
 */
chatRouter.get('/chat/stream/:streamId', (req: Request, res: Response) => {
  const streamId = req.params.streamId as string;
  const stream = activeStreams.get(streamId);

  // Disable any response buffering so SSE events are sent immediately
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  // Flush headers immediately to establish the SSE connection
  res.flushHeaders();

  // Disable Nagle's algorithm — send every write immediately, no batching
  if (res.socket) {
    res.socket.setNoDelay(true);
    res.socket.setTimeout(0);
  }

  // Write a 2KB padding comment to force proxy buffers (Cloudflare, nginx) to flush.
  // Many reverse proxies buffer the first 1-4KB before starting to stream.
  res.write(`: proxy-buffer-pad ${'_'.repeat(2048)}\n\n`);

  // Helper to write SSE data and flush immediately (prevents Node.js buffering)
  const sseWrite = (data: string) => {
    try {
      const ok = res.write(data);
      // Force flush via multiple strategies:
      // 1. compression middleware adds flush()
      if (typeof (res as any).flush === 'function') {
        (res as any).flush();
      }
      // 2. If the write buffer is full, the socket needs draining — but for SSE
      //    with small payloads this should rarely happen. The key thing is that
      //    setNoDelay(true) above ensures the TCP layer sends immediately.
      if (!ok && res.socket && !res.socket.destroyed) {
        // Back-pressure: the kernel buffer is full. For SSE this is unusual,
        // but if it happens, the 'drain' event will resume writes automatically.
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

  // If the stream is still active (CLI is running)
  if (stream.streaming) {
    // Send initial heartbeat immediately so the client knows the connection is alive
    sseWrite(': heartbeat\n\n');

    // Send heartbeat every 5 seconds to keep SSE connection alive
    // (Cloudflare tunnels and browsers may time out idle SSE connections)
    const heartbeat = setInterval(() => {
      sseWrite(': heartbeat\n\n');
    }, 5000);

    // Send any accumulated text so far (for late-joining clients, send as 'snapshot')
    if (stream.text) {
      sseWrite(`data: ${JSON.stringify({ type: 'snapshot', text: stream.text })}\n\n`);
    }

    // Wire up live streaming — send ONLY the new delta text as it arrives
    stream.onDelta = (delta: string) => {
      try {
        sseWrite(`data: ${JSON.stringify({ type: 'delta', text: delta })}\n\n`);
      } catch {
        // Client disconnected
        clearInterval(heartbeat);
      }
    };

    stream.onDone = () => {
      clearInterval(heartbeat);
      try {
        sseWrite(`data: [DONE]\n\n`);
        res.end();
      } catch {
        // Client disconnected
      }
      activeStreams.delete(streamId);
    };

    // NOW spawn Claude CLI — callbacks are set up first, so every delta
    // streams instantly to the client. No more race condition where deltas
    // accumulate before the SSE connection is ready.
    if (!stream.started) {
      stream.started = true;
      spawnClaude(stream.pendingMessage, stream.pendingSession, streamId);
    }

    // Clean up on client disconnect
    req.on('close', () => {
      clearInterval(heartbeat);
      stream.onDelta = undefined;
      stream.onDone = undefined;
      // Don't abort — let the CLI finish
    });
    return;
  }

  // Stream is already done — send the full text as a snapshot
  if (stream.text) {
    sseWrite(`data: ${JSON.stringify({ type: 'snapshot', text: stream.text })}\n\n`);
  }
  sseWrite(`data: [DONE]\n\n`);
  res.end();
  activeStreams.delete(streamId);
});

// ---------------------------------------------------------------------------
// Serve uploaded chat attachments
// ---------------------------------------------------------------------------

chatRouter.get('/chat/uploads/:filename', (req: Request, res: Response) => {
  const filename = req.params.filename as string;
  // Prevent directory traversal
  if (filename.includes('..') || filename.includes('/') || filename.includes('\\')) {
    res.status(400).json({ error: 'Invalid filename' });
    return;
  }
  const filePath = path.join(CHAT_UPLOADS_DIR, filename);
  if (!fs.existsSync(filePath)) {
    res.status(404).json({ error: 'File not found' });
    return;
  }
  // Determine MIME type
  const ext = path.extname(filename).toLowerCase();
  const mimeTypes: Record<string, string> = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.pdf': 'application/pdf',
  };
  const contentType = mimeTypes[ext] || 'application/octet-stream';
  res.setHeader('Content-Type', contentType);
  res.setHeader('Cache-Control', 'public, max-age=86400'); // 1 day
  res.sendFile(filePath);
});

// ---------------------------------------------------------------------------
// Legacy route — keep the old POST /api/chat working for backward compat
// Now also handles multipart/form-data for file attachments
// ---------------------------------------------------------------------------

chatRouter.post('/chat', chatUpload.single('file'), async (req: Request, res: Response) => {
  try {
    // Support both JSON (Content-Type: application/json) and FormData (multipart)
    const message = (req.body?.message as string) || '';
    const conversationId = (req.body?.conversationId || req.body?.conversation_id) as string | undefined;

    // Build attachment metadata if a file was uploaded
    let attachment: SessionMessageAttachment | undefined;
    let augmentedMessage = message.trim();
    const uploadedFile = req.file;

    if (uploadedFile) {
      const isImage = uploadedFile.mimetype.startsWith('image/');
      attachment = {
        type: isImage ? 'image' : 'pdf',
        filename: uploadedFile.filename,
        originalName: uploadedFile.originalname,
        mimeType: uploadedFile.mimetype,
        path: uploadedFile.path,
        url: `/api/chat/uploads/${uploadedFile.filename}`,
      };

      // Augment the prompt so Claude knows about the attachment
      if (isImage) {
        augmentedMessage = `${augmentedMessage}\n\n[The user has attached an image file. You can view it using the Read tool at: ${uploadedFile.path}]`.trim();
      } else {
        augmentedMessage = `${augmentedMessage}\n\n[The user has attached a PDF file. You can read it using the Read tool at: ${uploadedFile.path}]`.trim();
      }
    }

    if (!augmentedMessage) {
      res.status(400).json({ error: 'message is required' });
      return;
    }

    // Redirect to the new session-based flow
    let session: ChatSession;
    if (conversationId && sessions.has(conversationId)) {
      session = sessions.get(conversationId)!;
    } else {
      const newId = conversationId || uuidv4();
      const titleSource = message.trim() || (uploadedFile ? `[Attached: ${uploadedFile.originalname}]` : 'New Chat');
      session = {
        id: newId,
        cliSessionId: null,
        title: titleSource.slice(0, 80) + (titleSource.length > 80 ? '...' : ''),
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        messages: [],
        activeProcess: null,
      };
      sessions.set(newId, session);
    }

    if (session.title === 'New Chat' && session.messages.length === 0) {
      const titleSource = message.trim() || (uploadedFile ? `[Attached: ${uploadedFile.originalname}]` : 'New Chat');
      session.title = titleSource.slice(0, 80) + (titleSource.length > 80 ? '...' : '');
    }

    const userMsg: SessionMessage = {
      id: uuidv4(),
      role: 'user',
      content: message.trim(),
      timestamp: new Date().toISOString(),
      attachment,
    };
    session.messages.push(userMsg);
    session.updatedAt = new Date().toISOString();

    const streamId = uuidv4();
    activeStreams.set(streamId, {
      text: '',
      done: false,
      streaming: true,
      started: false,
      pendingMessage: augmentedMessage,
      pendingSession: session,
    });

    res.json({
      id: userMsg.id,
      message: '',
      conversationId: session.id,
      streamUrl: `/api/chat/stream/${streamId}`,
      // Send attachment info back so frontend can update the message URL
      attachment: attachment ? {
        type: attachment.type,
        filename: attachment.filename,
        originalName: attachment.originalName,
        url: attachment.url,
        mimeType: attachment.mimeType,
      } : undefined,
    });
  } catch (err) {
    console.error('[POST /api/chat]', err);
    res.status(500).json({ error: 'Failed to process message' });
  }
});

// ---------------------------------------------------------------------------
// WebSocket handler — real-time streaming that bypasses Cloudflare buffering
// ---------------------------------------------------------------------------

/**
 * Handle a WebSocket connection for chat streaming.
 * Protocol:
 *   Client sends:  { type: "chat", sessionId: string, message: string }
 *   Server sends:  { type: "session", sessionId: string }        — session created/confirmed
 *                  { type: "delta", text: string }                — streaming text chunk
 *                  { type: "done" }                               — response complete
 *                  { type: "error", text: string }                — error occurred
 */
export function handleChatWebSocket(ws: WebSocket): void {
  let alive = true;

  ws.on('close', () => {
    alive = false;
    console.log('[WS] Connection closed');
  });

  ws.on('error', (err) => {
    alive = false;
    console.error('[WS] Error:', err.message);
  });

  const wsSend = (data: object) => {
    if (alive && ws.readyState === 1 /* OPEN */) {
      try {
        ws.send(JSON.stringify(data));
      } catch {
        // Client disconnected
      }
    }
  };

  ws.on('message', (raw) => {
    let msg: any;
    try {
      msg = JSON.parse(raw.toString());
    } catch {
      wsSend({ type: 'error', text: 'Invalid JSON' });
      return;
    }

    if (msg.type === 'chat') {
      handleChatMessage(msg.sessionId, msg.message, wsSend);
    } else if (msg.type === 'ping') {
      wsSend({ type: 'pong' });
    }
  });

  function handleChatMessage(
    sessionId: string | undefined,
    message: string,
    send: (data: object) => void,
  ): void {
    if (!message || typeof message !== 'string' || !message.trim()) {
      send({ type: 'error', text: 'message is required' });
      return;
    }

    // Create or get session
    let session: ChatSession;
    if (sessionId && sessions.has(sessionId)) {
      session = sessions.get(sessionId)!;
    } else {
      const newId = sessionId || uuidv4();
      session = {
        id: newId,
        cliSessionId: null,
        title: message.slice(0, 80) + (message.length > 80 ? '...' : ''),
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        messages: [],
        activeProcess: null,
      };
      sessions.set(newId, session);
    }

    if (session.title === 'New Chat' && session.messages.length === 0) {
      session.title = message.slice(0, 80) + (message.length > 80 ? '...' : '');
    }

    // Store user message
    const userMsg: SessionMessage = {
      id: uuidv4(),
      role: 'user',
      content: message.trim(),
      timestamp: new Date().toISOString(),
    };
    session.messages.push(userMsg);
    session.updatedAt = new Date().toISOString();

    // Tell client which session we're using
    send({ type: 'session', sessionId: session.id });

    // Spawn Claude CLI and stream directly over WebSocket
    spawnClaudeWS(message.trim(), session, send);
  }
}

/**
 * Spawn Claude CLI and send deltas directly via WebSocket send function.
 * No SSE, no HTTP response buffering, no proxy interference.
 */
function spawnClaudeWS(
  message: string,
  session: ChatSession,
  send: (data: object) => void,
): void {
  const args = [
    '-p', message,
    '--output-format', 'stream-json',
    '--include-partial-messages', // CRITICAL: emit token-by-token deltas, not just final message
    '--verbose',
    '--strict-mcp-config',
    '--dangerously-skip-permissions',
  ];

  if (session.cliSessionId) {
    args.push('--resume', session.cliSessionId);
  } else {
    args.push('--system-prompt', NIMROD_SYSTEM_PROMPT);
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
  let done = false;

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

        // Streaming text delta — send immediately over WebSocket
        if (event.type === 'stream_event' && event.event) {
          const inner = event.event;
          if (
            inner.type === 'content_block_delta' &&
            inner.delta?.type === 'text_delta' &&
            inner.delta.text
          ) {
            const delta = inner.delta.text;
            fullText += delta;
            send({ type: 'delta', text: delta });
          }
        }

        // Fallback: full assistant message (only if no streaming deltas received)
        if (event.type === 'assistant' && event.message?.content) {
          for (const block of event.message.content) {
            if (block.type === 'text' && block.text) {
              if (!fullText) {
                fullText = block.text;
                send({ type: 'delta', text: fullText });
              }
            }
          }
        }

        // Result — done
        if (event.type === 'result') {
          if (event.result && typeof event.result === 'string') {
            fullText = event.result;
          }
          done = true;
          send({ type: 'done' });
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
    console.log('[claude-cli-ws stderr]', data.toString().trim());
  });

  const processTimeout = setTimeout(() => {
    if (!done) {
      console.log('[claude-cli-ws] Process timed out after 5 minutes');
      try { child.kill('SIGTERM'); } catch {}
    }
  }, 300000);

  child.on('error', (err) => {
    clearTimeout(processTimeout);
    console.error('[claude-cli-ws spawn error]', err.message);
    if (!done) {
      done = true;
      send({ type: 'error', text: `Failed to start Claude CLI: ${err.message}` });
      session.activeProcess = null;
    }
  });

  child.on('close', (code) => {
    clearTimeout(processTimeout);
    console.log(`[claude-cli-ws] Process exited with code ${code}`);

    // Process remaining buffer
    if (buffer.trim()) {
      try {
        const event = JSON.parse(buffer.trim());
        if (event.type === 'result' && event.result) {
          fullText = event.result;
        }
      } catch {}
    }

    if (!done) {
      done = true;
      if (!fullText && code !== 0) {
        send({ type: 'error', text: `Claude CLI exited with code ${code}` });
      } else {
        send({ type: 'done' });
        if (fullText) {
          session.messages.push({
            id: uuidv4(),
            role: 'assistant',
            content: fullText,
            timestamp: new Date().toISOString(),
          });
        }
      }
      session.activeProcess = null;
    }
  });
}

// ---------------------------------------------------------------------------
// Cleanup on process exit
// ---------------------------------------------------------------------------

function cleanupSessions() {
  for (const [, session] of sessions) {
    if (session.activeProcess) {
      try {
        session.activeProcess.kill('SIGTERM');
      } catch {
        // ignore
      }
    }
  }
  sessions.clear();
}

process.on('SIGTERM', cleanupSessions);
process.on('SIGINT', cleanupSessions);
process.on('exit', cleanupSessions);
