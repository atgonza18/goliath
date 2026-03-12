#!/usr/bin/env node

/**
 * Recall.ai MCP Server — Dispatch and manage meeting bots via Recall.ai API.
 *
 * Tools:
 *   recall_dispatch_bot   — Send a bot to join a Teams/Zoom/Meet meeting
 *   recall_stop_bot       — Remove a bot from an active meeting
 *   recall_get_bot_status — Check the current status of a dispatched bot
 *   recall_list_bots      — List recent bots (last 50)
 *
 * Reads RECALL_API_KEY and RECALL_API_BASE_URL from environment.
 * Designed to run as an MCP stdio server alongside ConstraintsPro.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import dotenv from "dotenv";

// ============================================================================
// Configuration
// ============================================================================

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Load secrets from the Goliath .env file (never committed to git)
// DOTENV_CONFIG_QUIET suppresses the noisy v17 banner on stderr
const GOLIATH_ENV = path.resolve(__dirname, "../.env");
if (fs.existsSync(GOLIATH_ENV)) {
  dotenv.config({ path: GOLIATH_ENV, debug: false });
}

const RECALL_API_KEY = process.env.RECALL_API_KEY || "";
const RECALL_API_BASE_URL = (
  process.env.RECALL_API_BASE_URL || "https://us-west-2.recall.ai"
).replace(/\/+$/, "");
const RECALL_BOT_NAME = process.env.RECALL_BOT_NAME || "Aaron Gonzalez";

if (!RECALL_API_KEY) {
  console.error(
    "WARNING: RECALL_API_KEY not set — Recall.ai tools will return errors"
  );
}

// ============================================================================
// Recall.ai API Helpers
// ============================================================================

function headers() {
  return {
    Authorization: `Token ${RECALL_API_KEY}`,
    "Content-Type": "application/json",
  };
}

function success(result) {
  return {
    content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
  };
}

function error(message) {
  return {
    content: [{ type: "text", text: `Error: ${message}` }],
    isError: true,
  };
}

/**
 * Make an authenticated request to the Recall.ai API.
 */
async function recallFetch(method, endpoint, body = null) {
  if (!RECALL_API_KEY) {
    throw new Error(
      "RECALL_API_KEY is not configured. Cannot call Recall.ai API."
    );
  }

  const url = `${RECALL_API_BASE_URL}${endpoint}`;
  const opts = {
    method,
    headers: headers(),
  };
  if (body) {
    opts.body = JSON.stringify(body);
  }

  const resp = await fetch(url, opts);
  const text = await resp.text();

  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { raw: text };
  }

  if (!resp.ok) {
    const detail = data?.detail || data?.error || text || `HTTP ${resp.status}`;
    throw new Error(`Recall API ${resp.status}: ${detail}`);
  }

  return data;
}

// ============================================================================
// Tool Definitions
// ============================================================================

const TOOLS = [
  {
    name: "recall_dispatch_bot",
    description:
      "Send a Recall.ai meeting bot to join a Teams, Zoom, or Google Meet call. " +
      "The bot will join, record, and transcribe the meeting. " +
      "Returns the bot ID for tracking. Use this when the user says " +
      '"get the bot in", "join the call", "send the bot", or shares a meeting URL.',
    inputSchema: {
      type: "object",
      properties: {
        meeting_url: {
          type: "string",
          description:
            "The full meeting URL (Teams, Zoom, or Google Meet link)",
        },
        bot_name: {
          type: "string",
          description:
            "Display name for the bot in the meeting (default: Aaron Gonzalez)",
        },
      },
      required: ["meeting_url"],
    },
  },
  {
    name: "recall_stop_bot",
    description:
      "Remove a Recall.ai bot from an active meeting call. " +
      "The bot will leave the call immediately. Use this when the user says " +
      '"kill the bot", "remove the bot", "stop recording", or "leave the call".',
    inputSchema: {
      type: "object",
      properties: {
        bot_id: {
          type: "string",
          description:
            "The bot ID returned from recall_dispatch_bot, or from recall_list_bots",
        },
      },
      required: ["bot_id"],
    },
  },
  {
    name: "recall_get_bot_status",
    description:
      "Check the current status of a Recall.ai meeting bot. " +
      "Returns status, meeting info, and recording details.",
    inputSchema: {
      type: "object",
      properties: {
        bot_id: {
          type: "string",
          description: "The bot ID to check status for",
        },
      },
      required: ["bot_id"],
    },
  },
  {
    name: "recall_list_bots",
    description:
      "List recent Recall.ai bots (last 50). Shows bot IDs, statuses, " +
      "and meeting URLs. Useful for finding a bot ID to stop or check on.",
    inputSchema: {
      type: "object",
      properties: {},
      required: [],
    },
  },
];

// ============================================================================
// Tool Handler
// ============================================================================

async function handleTool(name, args) {
  switch (name) {
    // ---- Dispatch Bot ----
    case "recall_dispatch_bot": {
      try {
        const meetingUrl = args.meeting_url;
        if (!meetingUrl) {
          return error("meeting_url is required");
        }

        const botName = args.bot_name || RECALL_BOT_NAME;

        const payload = {
          meeting_url: meetingUrl,
          bot_name: botName,
          recording_config: {
            transcript: {
              provider: {
                recallai_streaming: {
                  language_code: "en_us",
                  mode: "prioritize_accuracy",
                },
              },
            },
          },
        };

        const data = await recallFetch("POST", "/api/v1/bot/", payload);

        return success({
          bot_id: data.id,
          status: "dispatched",
          bot_name: botName,
          meeting_url: meetingUrl,
          message: `Bot "${botName}" dispatched to meeting. It will join shortly.`,
        });
      } catch (e) {
        return error(`Failed to dispatch bot: ${e.message}`);
      }
    }

    // ---- Stop Bot (Leave Call) ----
    case "recall_stop_bot": {
      try {
        const botId = args.bot_id;
        if (!botId) {
          return error("bot_id is required");
        }

        const data = await recallFetch(
          "POST",
          `/api/v1/bot/${botId}/leave_call/`
        );

        return success({
          bot_id: botId,
          status: "leaving",
          message: `Bot ${botId} instructed to leave the call. It will disconnect shortly.`,
          response: data,
        });
      } catch (e) {
        return error(`Failed to stop bot: ${e.message}`);
      }
    }

    // ---- Get Bot Status ----
    case "recall_get_bot_status": {
      try {
        const botId = args.bot_id;
        if (!botId) {
          return error("bot_id is required");
        }

        const data = await recallFetch("GET", `/api/v1/bot/${botId}/`);

        // Extract the latest status from status_changes
        const statusChanges = data.status_changes || [];
        const latestStatus =
          statusChanges.length > 0
            ? statusChanges[statusChanges.length - 1]
            : { code: "unknown" };

        return success({
          bot_id: data.id,
          current_status: latestStatus.code,
          status_sub_code: latestStatus.sub_code || null,
          bot_name: data.bot_name,
          meeting_url: data.meeting_url,
          created_at: data.created_at,
          status_history: statusChanges.map((s) => ({
            code: s.code,
            sub_code: s.sub_code,
            created_at: s.created_at,
          })),
          recordings: data.recordings || [],
        });
      } catch (e) {
        return error(`Failed to get bot status: ${e.message}`);
      }
    }

    // ---- List Recent Bots ----
    case "recall_list_bots": {
      try {
        const data = await recallFetch("GET", "/api/v1/bot/?ordering=-created_at");

        const bots = (data.results || []).map((bot) => {
          const statusChanges = bot.status_changes || [];
          const latestStatus =
            statusChanges.length > 0
              ? statusChanges[statusChanges.length - 1]
              : { code: "unknown" };

          return {
            bot_id: bot.id,
            current_status: latestStatus.code,
            bot_name: bot.bot_name,
            meeting_url: bot.meeting_url,
            created_at: bot.created_at,
          };
        });

        return success({
          count: data.count || bots.length,
          bots,
        });
      } catch (e) {
        return error(`Failed to list bots: ${e.message}`);
      }
    }

    default:
      return error(`Unknown tool: ${name}`);
  }
}

// ============================================================================
// Server Setup
// ============================================================================

const server = new Server(
  { name: "recall", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: TOOLS,
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  return handleTool(name, (args ?? {}));
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error(
    `Recall.ai MCP server running (${TOOLS.length} tools available)`
  );
}

main().catch(console.error);
