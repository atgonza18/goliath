import { Router, Request, Response } from 'express';
import path from 'path';
import fs from 'fs';
import multer from 'multer';

export const filesRouter = Router();

// Base root that we never allow traversal above
const PROJECTS_ROOT = '/opt/goliath/projects';

/**
 * Resolve and validate a user-supplied path so it never escapes PROJECTS_ROOT.
 * Returns the absolute path on success, or null if the path is invalid.
 */
function safePath(userPath: string | undefined): string | null {
  const relative = (userPath || '').replace(/\\/g, '/');

  // Block obvious traversal attempts
  if (relative.includes('..')) return null;

  const resolved = path.resolve(PROJECTS_ROOT, relative);

  // Must be within or equal to PROJECTS_ROOT
  if (!resolved.startsWith(PROJECTS_ROOT)) return null;

  return resolved;
}

// ── Multer setup for uploads ──────────────────────────────────────────────
const storage = multer.diskStorage({
  destination(_req, _file, cb) {
    const targetDir = safePath((_req as Request).body?.path);
    if (!targetDir) {
      cb(new Error('Invalid upload path'), '');
      return;
    }
    // Ensure the target directory exists
    fs.mkdirSync(targetDir, { recursive: true });
    cb(null, targetDir);
  },
  filename(_req, file, cb) {
    // Keep the original filename — sanitise to be safe
    const clean = file.originalname.replace(/[^a-zA-Z0-9._\-() ]/g, '_');
    cb(null, clean);
  },
});
const upload = multer({ storage, limits: { fileSize: 100 * 1024 * 1024 } }); // 100 MB

// ── Helpers ───────────────────────────────────────────────────────────────
function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

interface FileItem {
  name: string;
  type: 'file' | 'directory';
  size: number;
  sizeFormatted: string;
  modified: string;
  extension: string;
  path: string;
}

// ──────────────────────────────────────────────────────────────────────────
// GET /api/files?path=  — List directory contents
// ──────────────────────────────────────────────────────────────────────────
filesRouter.get('/files', (req: Request, res: Response) => {
  try {
    const userPath = (req.query.path as string) || '';
    const dir = safePath(userPath);

    if (!dir) {
      res.status(400).json({ error: 'Invalid path' });
      return;
    }

    if (!fs.existsSync(dir)) {
      res.status(404).json({ error: 'Directory not found' });
      return;
    }

    const stat = fs.statSync(dir);
    if (!stat.isDirectory()) {
      res.status(400).json({ error: 'Path is not a directory' });
      return;
    }

    const entries = fs.readdirSync(dir);
    const items: FileItem[] = [];

    for (const name of entries) {
      // Skip hidden files/folders
      if (name.startsWith('.')) continue;

      const fullPath = path.join(dir, name);
      let entryStat: fs.Stats;
      try {
        entryStat = fs.statSync(fullPath);
      } catch {
        continue; // skip broken symlinks etc.
      }

      const relativePath = path.relative(PROJECTS_ROOT, fullPath);
      const isDir = entryStat.isDirectory();

      items.push({
        name,
        type: isDir ? 'directory' : 'file',
        size: isDir ? 0 : entryStat.size,
        sizeFormatted: isDir ? '--' : humanSize(entryStat.size),
        modified: entryStat.mtime.toISOString(),
        extension: isDir ? '' : path.extname(name).replace('.', '').toLowerCase(),
        path: relativePath,
      });
    }

    // Sort: directories first, then alphabetically within each group
    items.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });

    res.json(items);
  } catch (err) {
    console.error('[GET /api/files]', err);
    res.status(500).json({ error: 'Failed to list directory' });
  }
});

// ──────────────────────────────────────────────────────────────────────────
// GET /api/files/download?path=  — Download a single file
// ──────────────────────────────────────────────────────────────────────────
filesRouter.get('/files/download', (req: Request, res: Response) => {
  try {
    const userPath = req.query.path as string;
    if (!userPath) {
      res.status(400).json({ error: 'path query parameter is required' });
      return;
    }

    const filePath = safePath(userPath);
    if (!filePath) {
      res.status(400).json({ error: 'Invalid path' });
      return;
    }

    if (!fs.existsSync(filePath)) {
      res.status(404).json({ error: 'File not found' });
      return;
    }

    const stat = fs.statSync(filePath);
    if (!stat.isFile()) {
      res.status(400).json({ error: 'Path is not a file' });
      return;
    }

    const filename = path.basename(filePath);
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    res.setHeader('Content-Length', stat.size);

    const stream = fs.createReadStream(filePath);
    stream.pipe(res);
  } catch (err) {
    console.error('[GET /api/files/download]', err);
    res.status(500).json({ error: 'Failed to download file' });
  }
});

// ──────────────────────────────────────────────────────────────────────────
// POST /api/files/upload  — Upload file(s) via multipart form
// ──────────────────────────────────────────────────────────────────────────
filesRouter.post(
  '/files/upload',
  // We need the path field before multer runs (for destination). But multer
  // parses multipart; fields arrive alongside files. So we use multer's
  // `any()` and handle the rest manually.
  upload.array('files', 20),
  (req: Request, res: Response) => {
    try {
      const files = req.files as Express.Multer.File[] | undefined;
      if (!files || files.length === 0) {
        res.status(400).json({ error: 'No files uploaded' });
        return;
      }

      const result = files.map((f) => ({
        name: f.filename,
        size: f.size,
        sizeFormatted: humanSize(f.size),
        path: path.relative(PROJECTS_ROOT, f.path),
      }));

      res.json({ uploaded: result });
    } catch (err) {
      console.error('[POST /api/files/upload]', err);
      res.status(500).json({ error: 'Failed to upload files' });
    }
  }
);

// ──────────────────────────────────────────────────────────────────────────
// POST /api/files/mkdir  — Create a directory
// ──────────────────────────────────────────────────────────────────────────
// ──────────────────────────────────────────────────────────────────────────
// GET /api/files/preview?path=  — Return text content for preview
// ──────────────────────────────────────────────────────────────────────────
filesRouter.get('/files/preview', (req: Request, res: Response) => {
  try {
    const userPath = req.query.path as string;
    if (!userPath) {
      res.status(400).json({ error: 'path query parameter is required' });
      return;
    }

    const filePath = safePath(userPath);
    if (!filePath) {
      res.status(400).json({ error: 'Invalid path' });
      return;
    }

    if (!fs.existsSync(filePath)) {
      res.status(404).json({ error: 'File not found' });
      return;
    }

    const stat = fs.statSync(filePath);
    if (!stat.isFile()) {
      res.status(400).json({ error: 'Path is not a file' });
      return;
    }

    const MAX_PREVIEW_SIZE = 100 * 1024; // 100KB
    const size = stat.size;
    const truncated = size > MAX_PREVIEW_SIZE;

    const readSize = Math.min(size, MAX_PREVIEW_SIZE);
    const buffer = Buffer.alloc(readSize);
    const fd = fs.openSync(filePath, 'r');
    fs.readSync(fd, buffer, 0, readSize, 0);
    fs.closeSync(fd);

    const content = buffer.toString('utf-8');
    const name = path.basename(filePath);

    res.json({
      content,
      size,
      truncated,
      name,
      path: path.relative(PROJECTS_ROOT, filePath),
    });
  } catch (err) {
    console.error('[GET /api/files/preview]', err);
    res.status(500).json({ error: 'Failed to preview file' });
  }
});

// ──────────────────────────────────────────────────────────────────────────
// GET /api/files/serve?path=  — Serve file inline with correct MIME type
// ──────────────────────────────────────────────────────────────────────────
filesRouter.get('/files/serve', (req: Request, res: Response) => {
  try {
    const userPath = req.query.path as string;
    if (!userPath) {
      res.status(400).json({ error: 'path query parameter is required' });
      return;
    }

    const filePath = safePath(userPath);
    if (!filePath) {
      res.status(400).json({ error: 'Invalid path' });
      return;
    }

    if (!fs.existsSync(filePath)) {
      res.status(404).json({ error: 'File not found' });
      return;
    }

    const stat = fs.statSync(filePath);
    if (!stat.isFile()) {
      res.status(400).json({ error: 'Path is not a file' });
      return;
    }

    // Serve inline (browser renders rather than downloads)
    const filename = path.basename(filePath);
    res.setHeader('Content-Disposition', `inline; filename="${filename}"`);
    res.sendFile(filePath);
  } catch (err) {
    console.error('[GET /api/files/serve]', err);
    res.status(500).json({ error: 'Failed to serve file' });
  }
});

// ──────────────────────────────────────────────────────────────────────────
// POST /api/files/mkdir  — Create a directory
// ──────────────────────────────────────────────────────────────────────────
filesRouter.post('/files/mkdir', (req: Request, res: Response) => {
  try {
    const userPath = req.body?.path as string;
    if (!userPath) {
      res.status(400).json({ error: 'path is required' });
      return;
    }

    const dir = safePath(userPath);
    if (!dir) {
      res.status(400).json({ error: 'Invalid path' });
      return;
    }

    if (fs.existsSync(dir)) {
      res.status(409).json({ error: 'Directory already exists' });
      return;
    }

    fs.mkdirSync(dir, { recursive: true });
    res.json({ success: true, path: path.relative(PROJECTS_ROOT, dir) });
  } catch (err) {
    console.error('[POST /api/files/mkdir]', err);
    res.status(500).json({ error: 'Failed to create directory' });
  }
});
