import fs from 'fs';
import path from 'path';

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------

const CREDENTIALS_PATH = path.resolve(
  process.env.HOME || '/home/goliath',
  '.claude',
  '.credentials.json'
);

interface ClaudeCredentials {
  claudeAiOauth?: {
    accessToken?: string;
    refreshToken?: string;
    expiresAt?: number;
  };
}

function loadAccessToken(): string | null {
  try {
    if (!fs.existsSync(CREDENTIALS_PATH)) {
      console.warn('[AI] Credentials file not found at', CREDENTIALS_PATH);
      return null;
    }
    const raw = fs.readFileSync(CREDENTIALS_PATH, 'utf-8');
    const creds: ClaudeCredentials = JSON.parse(raw);
    const token = creds.claudeAiOauth?.accessToken;
    if (!token) {
      console.warn('[AI] No accessToken in credentials');
      return null;
    }
    return token;
  } catch (err) {
    console.error('[AI] Failed to load credentials:', err);
    return null;
  }
}

// ---------------------------------------------------------------------------
// System prompt
// ---------------------------------------------------------------------------

const SYSTEM_PROMPT = `You are Nimrod, the AI Chief Operating Officer of Goliath Construction Intelligence — an AI-powered operations platform managing a portfolio of 12 utility-scale solar construction projects for DSC (Dallas Support Center).

## Your Projects
You oversee the following projects: Blackford, Delta Bobcat, Duff, Duffy Bess, Graceland, Mayes, Pecan Prairie, Salt Branch, Scioto Ridge, Tehuacana, Three Rivers, and Union Ridge.

## Your Role
- You are the central brain of the Goliath system, coordinating multiple AI agents (Constraints Manager, Schedule Analyst, POD Analyst, Report Writer, Excel Expert, Construction Manager, and others).
- You help the user (a construction executive) understand project statuses, constraints, schedules, and operational issues.
- You speak with authority and operational knowledge about solar construction.
- You are concise, direct, and action-oriented. No fluff.
- When you don't have specific data, say so honestly rather than making things up.
- Use markdown formatting (bold, lists, headers) to structure your responses clearly.

## Context
- Today's date is ${new Date().toISOString().split('T')[0]}.
- The platform tracks constraints (blockers), action items, schedules, and daily field reports (Plan of the Day / POD).
- Constraints are tracked in ConstraintsPro with statuses: open, in_progress, resolved.
- Disciplines include: Safety, Quality, Civil, Modules, AG Electrical, Piles, Environmental, Commissioning, Racking, Procurement, Other.
`;

// ---------------------------------------------------------------------------
// Anthropic API client (direct HTTP)
// ---------------------------------------------------------------------------

const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface StreamCallbacks {
  onText: (text: string) => void;
  onDone: (fullText: string) => void;
  onError: (error: Error) => void;
}

/**
 * Stream a chat completion from Anthropic's API.
 * Uses the OAuth access token as a Bearer token.
 * Returns an abort function.
 */
export async function streamChat(
  messages: ChatMessage[],
  callbacks: StreamCallbacks,
  contextSnippet?: string
): Promise<() => void> {
  const token = loadAccessToken();

  if (!token) {
    callbacks.onError(new Error('AI brain not connected — no credentials found'));
    return () => {};
  }

  // Build system prompt with optional context
  let systemPrompt = SYSTEM_PROMPT;
  if (contextSnippet) {
    systemPrompt += `\n\n## Relevant Context from Memory\n${contextSnippet}\n`;
  }

  const body = JSON.stringify({
    model: 'claude-sonnet-4-20250514',
    max_tokens: 4096,
    stream: true,
    system: systemPrompt,
    messages: messages.map((m) => ({
      role: m.role,
      content: m.content,
    })),
  });

  const abortController = new AbortController();

  // Try with Bearer auth first (OAuth token)
  try {
    const response = await fetch(ANTHROPIC_API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'anthropic-version': '2023-06-01',
        'Authorization': `Bearer ${token}`,
      },
      body,
      signal: abortController.signal,
    });

    if (!response.ok) {
      const errText = await response.text();

      // If Bearer auth fails, try x-api-key header
      if (response.status === 401 || response.status === 403) {
        console.log('[AI] Bearer auth failed, trying x-api-key header...');
        return streamWithApiKey(token, body, callbacks, abortController);
      }

      callbacks.onError(new Error(`Anthropic API error (${response.status}): ${errText}`));
      return () => abortController.abort();
    }

    processStream(response, callbacks);
  } catch (err) {
    if ((err as Error).name === 'AbortError') return () => {};
    callbacks.onError(err instanceof Error ? err : new Error(String(err)));
  }

  return () => abortController.abort();
}

async function streamWithApiKey(
  token: string,
  body: string,
  callbacks: StreamCallbacks,
  abortController: AbortController
): Promise<() => void> {
  try {
    const response = await fetch(ANTHROPIC_API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'anthropic-version': '2023-06-01',
        'x-api-key': token,
      },
      body,
      signal: abortController.signal,
    });

    if (!response.ok) {
      const errText = await response.text();
      callbacks.onError(new Error(`Anthropic API error (${response.status}): ${errText}`));
      return () => abortController.abort();
    }

    processStream(response, callbacks);
  } catch (err) {
    if ((err as Error).name === 'AbortError') return () => {};
    callbacks.onError(err instanceof Error ? err : new Error(String(err)));
  }

  return () => abortController.abort();
}

async function processStream(response: Response, callbacks: StreamCallbacks): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) {
    callbacks.onError(new Error('No response body'));
    return;
  }

  const decoder = new TextDecoder();
  let fullText = '';
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process SSE events
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep incomplete line in buffer

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();

        if (data === '[DONE]') {
          callbacks.onDone(fullText);
          return;
        }

        try {
          const event = JSON.parse(data);

          if (event.type === 'content_block_delta' && event.delta?.text) {
            const text = event.delta.text;
            fullText += text;
            callbacks.onText(text);
          } else if (event.type === 'message_stop') {
            callbacks.onDone(fullText);
            return;
          } else if (event.type === 'error') {
            callbacks.onError(new Error(event.error?.message || 'Stream error'));
            return;
          }
        } catch {
          // Skip non-JSON lines (event: lines, etc.)
        }
      }
    }

    // Stream ended without explicit done
    if (fullText) {
      callbacks.onDone(fullText);
    }
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    callbacks.onError(err instanceof Error ? err : new Error(String(err)));
  }
}

/**
 * Non-streaming chat (for fallback / simpler use cases).
 */
export async function chat(messages: ChatMessage[], contextSnippet?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    let result = '';
    streamChat(messages, {
      onText: (text) => { result += text; },
      onDone: (fullText) => resolve(fullText),
      onError: (err) => reject(err),
    }, contextSnippet);
  });
}

/**
 * Check if the AI brain is available.
 */
export function isAIAvailable(): boolean {
  return loadAccessToken() !== null;
}
