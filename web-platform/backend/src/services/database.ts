import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';

// Paths
const GOLIATH_ROOT = path.resolve(__dirname, '../../../../');
const TELEGRAM_DATA = path.join(GOLIATH_ROOT, 'telegram-bot', 'data');
const MEMORY_DB_PATH = path.join(TELEGRAM_DATA, 'memory.db');
const FOLLOWUP_DB_PATH = path.join(TELEGRAM_DATA, 'followup.db');
const LOCAL_DB_DIR = path.join(__dirname, '../../data');
const CHAT_DB_PATH = path.join(LOCAL_DB_DIR, 'chat.db');

let memoryDb: Database.Database;
let followupDb: Database.Database;
let chatDb: Database.Database;

/**
 * Open the existing Goliath memory database (read-only for safety)
 * and create the local chat database for web conversations.
 */
export function initDatabases(): void {
  // Ensure local data directory exists
  if (!fs.existsSync(LOCAL_DB_DIR)) {
    fs.mkdirSync(LOCAL_DB_DIR, { recursive: true });
  }

  // Open memory DB (read-only — we don't want the web API mutating it)
  if (fs.existsSync(MEMORY_DB_PATH)) {
    memoryDb = new Database(MEMORY_DB_PATH, { readonly: true });
    // Note: do NOT set journal_mode on read-only databases — WAL requires write access
    console.log(`Memory DB opened (read-only): ${MEMORY_DB_PATH}`);
  } else {
    console.warn(`Memory DB not found at ${MEMORY_DB_PATH} — memory endpoints will return empty results`);
    // Create an in-memory fallback
    memoryDb = new Database(':memory:');
    memoryDb.exec(`
      CREATE TABLE memories (
        id INTEGER PRIMARY KEY, created_at TEXT, category TEXT,
        project_key TEXT, summary TEXT, detail TEXT, source TEXT,
        tags TEXT, resolved INTEGER DEFAULT 0
      );
    `);
  }

  // Open follow-up DB (read-only)
  if (fs.existsSync(FOLLOWUP_DB_PATH)) {
    followupDb = new Database(FOLLOWUP_DB_PATH, { readonly: true });
    // Note: do NOT set journal_mode on read-only databases — WAL requires write access
    console.log(`Follow-up DB opened (read-only): ${FOLLOWUP_DB_PATH}`);
  } else {
    console.warn(`Follow-up DB not found at ${FOLLOWUP_DB_PATH}`);
    followupDb = new Database(':memory:');
    followupDb.exec(`
      CREATE TABLE follow_ups (
        id INTEGER PRIMARY KEY, constraint_id TEXT, project_key TEXT,
        owner TEXT, commitment TEXT, committed_date TEXT, follow_up_date TEXT,
        status TEXT DEFAULT 'pending', reminder_sent INTEGER DEFAULT 0,
        created_at TEXT
      );
    `);
  }

  // Create / open the local chat DB (read-write)
  chatDb = new Database(CHAT_DB_PATH);
  chatDb.pragma('journal_mode = WAL');

  chatDb.exec(`
    CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY,
      title TEXT,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
      updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
    );

    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      conversation_id TEXT NOT NULL REFERENCES conversations(id),
      role TEXT NOT NULL CHECK(role IN ('user','assistant')),
      content TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
    );

    CREATE INDEX IF NOT EXISTS idx_msg_conv
      ON messages(conversation_id, created_at);
  `);

  console.log(`Chat DB opened: ${CHAT_DB_PATH}`);
}

export function getMemoryDb(): Database.Database {
  return memoryDb;
}

export function getFollowupDb(): Database.Database {
  return followupDb;
}

export function getChatDb(): Database.Database {
  return chatDb;
}

export function getGoliathRoot(): string {
  return GOLIATH_ROOT;
}
