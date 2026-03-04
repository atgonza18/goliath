#!/usr/bin/env node
/**
 * smart-note-diff.mjs — Semantic Note Dedup for ConstraintsPro
 *
 * Replaces dumb keyword-overlap dedup with LLM-powered semantic comparison.
 * When a new note shares the same day as existing notes, this module uses
 * Claude to extract ONLY genuinely new information (names, dates, decisions,
 * action items, numbers) that are not already captured.
 *
 * Usage:
 *   import { smartNoteDiff } from "./lib/smart-note-diff.mjs";
 *   const delta = await smartNoteDiff(existingNotes, newNoteContent, constraintTitle);
 *   // delta is a string (new info to push) or null (nothing new)
 *
 * API Key discovery order:
 *   1. ANTHROPIC_API_KEY environment variable
 *   2. /opt/goliath/.env file
 *   3. /opt/goliath/telegram-bot/.env file
 *   4. Fallback: sentence-level local dedup (no LLM)
 */

import { readFileSync } from "fs";

// ---------------------------------------------------------------------------
// API key discovery
// ---------------------------------------------------------------------------

let _cachedApiKey = undefined;

function discoverAnthropicKey() {
  if (_cachedApiKey !== undefined) return _cachedApiKey;

  // 1. Environment variable
  if (process.env.ANTHROPIC_API_KEY) {
    _cachedApiKey = process.env.ANTHROPIC_API_KEY;
    console.log("[smart-note-diff] API key found in ANTHROPIC_API_KEY env var");
    return _cachedApiKey;
  }

  // 2. .env files
  const envPaths = [
    "/opt/goliath/.env",
    "/opt/goliath/telegram-bot/.env",
  ];

  for (const envPath of envPaths) {
    try {
      const contents = readFileSync(envPath, "utf-8");
      const match = contents.match(/^ANTHROPIC_API_KEY=(.+)$/m);
      if (match) {
        _cachedApiKey = match[1].trim();
        console.log(`[smart-note-diff] API key found in ${envPath}`);
        return _cachedApiKey;
      }
    } catch {
      // File not found or not readable — continue
    }
  }

  // 3. No key found
  _cachedApiKey = null;
  console.log("[smart-note-diff] No ANTHROPIC_API_KEY found — using local sentence-level dedup fallback");
  return _cachedApiKey;
}

// ---------------------------------------------------------------------------
// Note parsing
// ---------------------------------------------------------------------------

/**
 * Parse ConstraintsPro notes string into individual note entries.
 * Notes are formatted as "M/D: content" separated by newlines.
 * Multi-line notes are joined back to their date-prefixed header.
 */
function parseNotes(notesStr) {
  if (!notesStr) return [];

  const lines = notesStr.split("\n");
  const notes = [];
  let currentNote = null;

  for (const line of lines) {
    // Match date prefix: "3/2:", "12/15:", etc.
    const dateMatch = line.match(/^(\d{1,2}\/\d{1,2}):\s*(.*)/);
    if (dateMatch) {
      if (currentNote) notes.push(currentNote);
      currentNote = {
        dateStr: dateMatch[1],
        content: dateMatch[2],
        fullLine: line,
      };
    } else if (currentNote && line.trim()) {
      // Continuation of previous note
      currentNote.content += " " + line.trim();
      currentNote.fullLine += "\n" + line;
    }
  }
  if (currentNote) notes.push(currentNote);

  return notes;
}

/**
 * Get today's date in M/D format (no leading zeros), matching ConstraintsPro format.
 */
function getTodayMD() {
  const now = new Date();
  return `${now.getMonth() + 1}/${now.getDate()}`;
}

// ---------------------------------------------------------------------------
// Claude API call (direct HTTP — no SDK dependency)
// ---------------------------------------------------------------------------

const CLAUDE_API_URL = "https://api.anthropic.com/v1/messages";
const CLAUDE_MODEL = "claude-3-5-haiku-latest";

const SYSTEM_PROMPT = `You are a construction project note analyst. Compare the existing notes with the new note and extract ONLY information that is genuinely new.

Be concise but don't drop any facts, names, dates, numbers, action items, or decisions.

"Genuinely new" means:
- Names of people, companies, or locations NOT mentioned in existing notes
- Dates, deadlines, or timeframes NOT already captured
- Decisions or status changes NOT already recorded
- Action items or commitments NOT already listed
- Numbers, quantities, or measurements NOT already present
- New context or explanations that materially change understanding

If the new note is essentially restating what the existing notes already cover (even if worded differently), respond with exactly: NO_NEW_INFO

If there IS new information, write a concise note containing ONLY the new facts. Do not repeat information already in the existing notes. Do not add any preamble or explanation — just output the delta note text, ready to be saved.`;

async function callClaude(existingSameDayNotes, newNoteContent, constraintTitle) {
  const apiKey = discoverAnthropicKey();
  if (!apiKey) return null; // Signal to use fallback

  const userMessage = `## Constraint: ${constraintTitle}

## Existing same-day notes (already in the database):
${existingSameDayNotes.map((n, i) => `[Note ${i + 1}]: ${n.content}`).join("\n")}

## New note to evaluate:
${newNoteContent}

Extract ONLY genuinely new information from the new note that is NOT already captured in the existing notes. If nothing is new, respond with exactly: NO_NEW_INFO`;

  try {
    const resp = await fetch(CLAUDE_API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: CLAUDE_MODEL,
        max_tokens: 1024,
        system: SYSTEM_PROMPT,
        messages: [{ role: "user", content: userMessage }],
      }),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      console.error(`[smart-note-diff] Claude API error (${resp.status}): ${errText}`);
      return null; // Fall back to local dedup
    }

    const data = await resp.json();
    const text = data.content?.[0]?.text?.trim();

    if (!text) {
      console.error("[smart-note-diff] Claude returned empty response");
      return null;
    }

    return text;
  } catch (err) {
    console.error(`[smart-note-diff] Claude API call failed: ${err.message}`);
    return null; // Fall back to local dedup
  }
}

// ---------------------------------------------------------------------------
// Local sentence-level dedup fallback (no LLM required)
// ---------------------------------------------------------------------------

/**
 * Normalize text for comparison: lowercase, strip punctuation, collapse whitespace.
 */
function normalize(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Extract meaningful tokens from text (words > 3 chars, excluding stop words).
 */
const STOP_WORDS = new Set([
  "that", "this", "with", "from", "have", "will", "been", "were", "they",
  "their", "about", "which", "when", "there", "what", "would", "could",
  "should", "than", "them", "then", "into", "also", "some", "very",
  "just", "more", "most", "much", "each", "make", "like", "over",
  "such", "only", "other", "after", "before", "still", "being", "does",
  "done", "going", "back", "well", "here", "need", "want", "take",
  "note", "constraint", "sync", "source", "meeting", "update",
]);

function extractTokens(text) {
  const normalized = normalize(text);
  return normalized
    .split(" ")
    .filter(w => w.length > 3 && !STOP_WORDS.has(w));
}

/**
 * Split text into semantic sentences/clauses.
 */
function splitSentences(text) {
  // Split on sentence-ending punctuation, semicolons, and " — " (em dash separators)
  return text
    .split(/[.;!?]|\s—\s/)
    .map(s => s.trim())
    .filter(s => s.length > 10); // Skip very short fragments
}

/**
 * Check if a sentence is "covered" by existing notes using token overlap.
 * A sentence is covered if >60% of its meaningful tokens appear in existing text.
 */
function isSentenceCovered(sentence, existingTokenSet) {
  const sentenceTokens = extractTokens(sentence);
  if (sentenceTokens.length === 0) return true; // Empty = nothing new

  let covered = 0;
  for (const token of sentenceTokens) {
    if (existingTokenSet.has(token)) covered++;
  }

  const coverage = covered / sentenceTokens.length;
  return coverage > 0.6;
}

/**
 * Local fallback: sentence-level dedup without LLM.
 * Splits the new note into sentences and keeps only those that are NOT
 * substantially covered by existing same-day notes.
 */
function localSentenceDedup(existingSameDayNotes, newNoteContent) {
  // Build a combined token set from all existing same-day notes
  const existingText = existingSameDayNotes.map(n => n.content).join(" ");
  const existingTokens = new Set(extractTokens(existingText));

  // Split new note into sentences and filter
  const sentences = splitSentences(newNoteContent);
  const newSentences = [];

  for (const sentence of sentences) {
    if (!isSentenceCovered(sentence, existingTokens)) {
      newSentences.push(sentence);
    }
  }

  if (newSentences.length === 0) return null;

  return newSentences.join(". ") + ".";
}

// ---------------------------------------------------------------------------
// Main export: smartNoteDiff
// ---------------------------------------------------------------------------

/**
 * Perform semantic diff between existing notes and a new note.
 *
 * @param {string} existingNotes - The full notes string from ConstraintsPro
 *   (M/D: format, newline-separated)
 * @param {string} newNoteContent - The new note we want to push
 * @param {string} constraintDescription - The constraint title (for context)
 * @returns {Promise<string|null>} - The delta note to push (only new info),
 *   or null if nothing new
 */
export async function smartNoteDiff(existingNotes, newNoteContent, constraintDescription) {
  const label = `[smart-note-diff] "${constraintDescription}"`;

  // Parse existing notes
  const allNotes = parseNotes(existingNotes);
  const todayStr = getTodayMD();

  // Find same-day notes
  const sameDayNotes = allNotes.filter(n => n.dateStr === todayStr);

  console.log(`${label}: ${allNotes.length} total notes, ${sameDayNotes.length} from today (${todayStr})`);

  // If no same-day notes exist, the full note is new — push it all
  if (sameDayNotes.length === 0) {
    console.log(`${label}: No same-day notes found -> PUSH FULL NOTE`);
    return newNoteContent;
  }

  // Same-day notes exist — need semantic comparison
  console.log(`${label}: Same-day notes found, performing semantic diff...`);
  for (const n of sameDayNotes) {
    console.log(`${label}:   Existing: "${n.content.substring(0, 80)}..."`);
  }

  // Try Claude API first
  const claudeResult = await callClaude(sameDayNotes, newNoteContent, constraintDescription);

  if (claudeResult !== null) {
    // Claude responded
    if (claudeResult === "NO_NEW_INFO") {
      console.log(`${label}: Claude says NO NEW INFO -> SKIP`);
      return null;
    }

    console.log(`${label}: Claude extracted delta (${claudeResult.length} chars)`);
    console.log(`${label}:   Delta preview: "${claudeResult.substring(0, 100)}..."`);
    return claudeResult;
  }

  // Fallback: local sentence-level dedup
  console.log(`${label}: Using local sentence-level dedup fallback`);
  const localResult = localSentenceDedup(sameDayNotes, newNoteContent);

  if (localResult === null) {
    console.log(`${label}: Local dedup says nothing new -> SKIP`);
    return null;
  }

  console.log(`${label}: Local dedup extracted delta (${localResult.length} chars)`);
  console.log(`${label}:   Delta preview: "${localResult.substring(0, 100)}..."`);
  return localResult;
}

// ---------------------------------------------------------------------------
// Standalone test mode
// ---------------------------------------------------------------------------

if (process.argv[1] && process.argv[1].endsWith("smart-note-diff.mjs") && process.argv.includes("--test")) {
  console.log("=== SMART NOTE DIFF — STANDALONE TEST ===\n");

  const testExistingNotes = [
    "3/2: Sal will follow up on this spot",
    "2/28: Parking remains an issue, looking at alternatives",
    "2/25: Initial parking constraint logged",
  ].join("\n");

  const testNewNote = `Constraint sync (Blackford Solar Constraints Meeting — 2026-03-02): Single biggest constraint — cannot add workforce without parking. Current lot candidate (Truck Parking Club, ~5mi out) contact unreachable. Copart sold the property, new owner unknown. Ross Hurlow's landowner liaison (LRE) says no farmland available across project footprint. IBEW contract requires graveled lot — grass-only is not viable per Scott Hunter. Scott cannot add module crews (currently 9K modules/day, needs 20 crews / ~200 workers). Actions: Salam follow up Truck Parking Club lead; Tim check fairgrounds.`;

  console.log("Existing notes:");
  console.log(testExistingNotes);
  console.log("\nNew note:");
  console.log(testNewNote);
  console.log("\n--- Running smartNoteDiff ---\n");

  const result = await smartNoteDiff(testExistingNotes, testNewNote, "Off-Site Parking Shortage");
  console.log("\n--- RESULT ---");
  if (result === null) {
    console.log("NULL — nothing new to push");
  } else {
    console.log(`Delta note (${result.length} chars):`);
    console.log(result);
  }
}
