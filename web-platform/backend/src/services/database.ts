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
const POD_PRODUCTION_DB_PATH = path.join(LOCAL_DB_DIR, 'pod_production.db');

let memoryDb: Database.Database;
let followupDb: Database.Database;
let chatDb: Database.Database;
let podProductionDb: Database.Database;

/**
 * Open the existing Goliath memory database (read-only for safety)
 * and create the local chat database for web conversations.
 */
export function initDatabases(): void {
  // Ensure local data directory exists
  if (!fs.existsSync(LOCAL_DB_DIR)) {
    fs.mkdirSync(LOCAL_DB_DIR, { recursive: true });
  }

  // Open memory DB — NOT readonly because SQLite WAL mode requires write access
  // to the SHM index file to see the latest data. Read-only connections only see
  // data from the last WAL checkpoint, which can be arbitrarily stale.
  // Safety: all queries in the web backend are SELECTs only.
  if (fs.existsSync(MEMORY_DB_PATH)) {
    memoryDb = new Database(MEMORY_DB_PATH);
    memoryDb.pragma('journal_mode = WAL');
    console.log(`Memory DB opened: ${MEMORY_DB_PATH}`);
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

  // Open pod production DB (read-only — written by Python extraction scripts)
  if (fs.existsSync(POD_PRODUCTION_DB_PATH)) {
    podProductionDb = new Database(POD_PRODUCTION_DB_PATH, { readonly: true });
    console.log(`Pod Production DB opened (read-only): ${POD_PRODUCTION_DB_PATH}`);
  } else {
    console.warn(`Pod Production DB not found at ${POD_PRODUCTION_DB_PATH} — production dashboard will return empty results`);
    podProductionDb = new Database(':memory:');
    podProductionDb.exec(`
      CREATE TABLE pod_production (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_key TEXT NOT NULL, report_date TEXT NOT NULL,
        activity_category TEXT NOT NULL DEFAULT 'General',
        activity_name TEXT NOT NULL,
        qty_to_date REAL, qty_last_workday REAL,
        qty_completed_yesterday REAL DEFAULT 0,
        total_qty REAL, unit TEXT, pct_complete REAL,
        today_location TEXT, notes TEXT,
        extracted_at TEXT NOT NULL, source_file TEXT NOT NULL,
        UNIQUE(project_key, report_date, activity_category, activity_name)
      );
      CREATE TABLE pod_extraction_log (
        id INTEGER PRIMARY KEY, source_file TEXT, project_key TEXT,
        report_date TEXT, status TEXT, error_message TEXT,
        activities_count INTEGER DEFAULT 0, extracted_at TEXT
      );
    `);
  }
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

export function getPodProductionDb(): Database.Database {
  return podProductionDb;
}

export function getGoliathRoot(): string {
  return GOLIATH_ROOT;
}
