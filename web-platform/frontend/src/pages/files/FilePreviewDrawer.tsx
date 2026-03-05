import { useState, useEffect, useRef, useCallback } from 'react';
import { X, Download, FileText, Table, Image, FileCode, AlertCircle, GripVertical } from 'lucide-react';
import { api } from '../../api/client';
import { useApi } from '../../hooks/useApi';
import { useIsMobile } from '../../hooks/use-mobile';
import { renderMarkdown } from '../../utils/markdown';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { ListSkeleton } from '../../components/common/LoadingSpinner';

// ── File type detection ──────────────────────────────────────────────────

type PreviewType = 'pdf' | 'markdown' | 'excel' | 'image' | 'text' | 'unsupported';

const MARKDOWN_EXT = new Set(['md', 'markdown']);
const EXCEL_EXT = new Set(['xls', 'xlsx', 'xlsm', 'csv']);
const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp']);
const TEXT_EXT = new Set([
  'txt', 'json', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',
  'py', 'js', 'ts', 'tsx', 'jsx', 'html', 'css', 'scss', 'less',
  'sh', 'bash', 'zsh', 'fish', 'sql', 'graphql', 'prisma',
  'rs', 'go', 'java', 'kt', 'c', 'cpp', 'h', 'hpp', 'cs',
  'rb', 'php', 'swift', 'r', 'lua', 'vim', 'log', 'env',
  'xml', 'svg', 'dockerfile', 'makefile', 'gitignore', 'editorconfig',
]);

function getPreviewType(extension: string): PreviewType {
  const ext = extension.toLowerCase();
  if (ext === 'pdf') return 'pdf';
  if (MARKDOWN_EXT.has(ext)) return 'markdown';
  if (EXCEL_EXT.has(ext)) return 'excel';
  if (IMAGE_EXT.has(ext)) return 'image';
  if (TEXT_EXT.has(ext)) return 'text';
  return 'unsupported';
}

/** All extensions the drawer can preview */
export const PREVIEWABLE_EXTENSIONS = new Set([
  'pdf', ...MARKDOWN_EXT, ...EXCEL_EXT, ...IMAGE_EXT, ...TEXT_EXT,
]);

// ── Type icon helper ─────────────────────────────────────────────────────

function PreviewTypeIcon({ type }: { type: PreviewType }) {
  switch (type) {
    case 'pdf':       return <FileText className="h-3.5 w-3.5 text-red-400" />;
    case 'markdown':  return <FileCode className="h-3.5 w-3.5 text-blue-400" />;
    case 'excel':     return <Table className="h-3.5 w-3.5 text-green-400" />;
    case 'image':     return <Image className="h-3.5 w-3.5 text-purple-400" />;
    case 'text':      return <FileCode className="h-3.5 w-3.5 text-muted-foreground" />;
    default:          return <AlertCircle className="h-3.5 w-3.5 text-muted-foreground" />;
  }
}

// ── Sub-previews ─────────────────────────────────────────────────────────

function PdfPreview({ filePath }: { filePath: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Revoke any previous blob URL
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
    setBlobUrl(null);

    (async () => {
      try {
        const response = await fetch(api.getFileServeUrl(filePath));
        if (!response.ok) throw new Error(`Failed to fetch PDF (${response.status})`);

        // Detect SPA fallback or wrong content — server must return actual PDF bytes
        const ct = response.headers.get('content-type') || '';
        if (ct.includes('text/html')) {
          throw new Error('Server returned HTML instead of PDF — the file may not exist or the server needs a restart');
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);

        if (!cancelled) {
          blobUrlRef.current = url;
          setBlobUrl(url);
        } else {
          URL.revokeObjectURL(url);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load PDF');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [filePath]);

  if (loading) return <div className="p-4"><ListSkeleton count={6} /></div>;
  if (error) return <ErrorMsg message={error} />;
  if (!blobUrl) return null;

  // Use absolute positioning for bulletproof height — flex-1 on iframes
  // can collapse to 0px in some browser/layout combinations.
  return (
    <div className="flex-1 min-h-0 relative">
      <iframe
        src={blobUrl}
        className="absolute inset-0 w-full h-full border-0"
        title="PDF Preview"
      />
    </div>
  );
}

function MarkdownPreview({ filePath }: { filePath: string }) {
  const { data, loading, error } = useApi(
    () => api.previewFile(filePath),
    [filePath]
  );

  if (loading) return <div className="p-4"><ListSkeleton count={8} /></div>;
  if (error) return <ErrorMsg message={error} />;
  if (!data) return null;

  const html = renderMarkdown(data.content);

  return (
    <ScrollArea className="flex-1 min-h-0">
      <div className="p-5">
        <div
          className="prose prose-invert prose-sm max-w-none
            [&_h1]:text-lg [&_h1]:font-bold [&_h1]:text-foreground [&_h1]:border-b [&_h1]:border-border [&_h1]:pb-2 [&_h1]:mb-4
            [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-foreground [&_h2]:mt-6 [&_h2]:mb-3
            [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-foreground [&_h3]:mt-4 [&_h3]:mb-2
            [&_p]:text-sm [&_p]:text-foreground/80 [&_p]:leading-relaxed [&_p]:mb-3
            [&_ul]:text-sm [&_ul]:text-foreground/80 [&_ul]:mb-3
            [&_ol]:text-sm [&_ol]:text-foreground/80 [&_ol]:mb-3
            [&_li]:mb-1
            [&_code]:text-xs [&_code]:bg-accent/40 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:font-mono
            [&_pre]:bg-accent/30 [&_pre]:p-3 [&_pre]:rounded [&_pre]:overflow-x-auto [&_pre]:mb-3
            [&_pre_code]:bg-transparent [&_pre_code]:p-0
            [&_table]:text-xs [&_table]:w-full [&_table]:border-collapse
            [&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:bg-accent/30 [&_th]:text-left [&_th]:font-medium
            [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1
            [&_a]:text-amber-400 [&_a]:underline
            [&_blockquote]:border-l-2 [&_blockquote]:border-amber-500/50 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_blockquote]:italic
            [&_hr]:border-border [&_hr]:my-4
            [&_img]:max-w-full [&_img]:rounded"
          dangerouslySetInnerHTML={{ __html: html }}
        />
        {data.truncated && <TruncationNotice size={data.size} />}
      </div>
    </ScrollArea>
  );
}

function ExcelPreview({ filePath }: { filePath: string }) {
  const [sheets, setSheets] = useState<{ name: string; data: string[][] }[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const url = api.getFileServeUrl(filePath);
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Failed to fetch file (${response.status})`);

        // Detect SPA fallback — server returned HTML instead of spreadsheet bytes
        const ct = response.headers.get('content-type') || '';
        if (ct.includes('text/html')) {
          throw new Error('Server returned HTML instead of spreadsheet — the file may not exist or the server needs a restart');
        }

        const buffer = await response.arrayBuffer();

        // Dynamic import to keep xlsx out of the main bundle
        const XLSX = await import('xlsx');
        const wb = XLSX.read(new Uint8Array(buffer), { type: 'array' });

        const parsed = wb.SheetNames.map(name => {
          const ws = wb.Sheets[name];
          const rows = XLSX.utils.sheet_to_json<string[]>(ws, { header: 1 }) as string[][];
          // Limit to 200 rows for performance
          return { name, data: rows.slice(0, 200) };
        });

        if (!cancelled) {
          setSheets(parsed);
          setActiveSheet(0);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to parse spreadsheet');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [filePath]);

  if (loading) return <div className="p-4"><ListSkeleton count={8} /></div>;
  if (error) return <ErrorMsg message={error} />;
  if (sheets.length === 0) return <ErrorMsg message="No data found in spreadsheet" />;

  const sheet = sheets[activeSheet];

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Sheet tabs (if multiple) */}
      {sheets.length > 1 && (
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border overflow-x-auto shrink-0">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              onClick={() => setActiveSheet(i)}
              className={`px-2.5 py-1 text-[11px] rounded transition-colors whitespace-nowrap ${
                i === activeSheet
                  ? 'bg-amber-500/15 text-amber-400 font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
              }`}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}

      {/* Table */}
      <ScrollArea className="flex-1">
        <div className="p-3">
          <table className="w-full border-collapse text-[11px] font-mono">
            <tbody>
              {sheet.data.map((row, ri) => (
                <tr key={ri} className={ri === 0 ? 'bg-accent/30 font-semibold' : 'hover:bg-accent/10'}>
                  {/* Row number */}
                  <td className="px-1.5 py-1 border border-border text-muted-foreground/50 text-right w-8 select-none">
                    {ri + 1}
                  </td>
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className="px-2 py-1 border border-border text-foreground/80 max-w-[200px] truncate"
                      title={cell != null ? String(cell) : ''}
                    >
                      {cell != null ? String(cell) : ''}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {sheet.data.length >= 200 && (
            <p className="text-[10px] text-muted-foreground/50 italic mt-2 text-center">
              Showing first 200 rows
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

function ImagePreview({ filePath }: { filePath: string }) {
  const url = api.getFileServeUrl(filePath);
  return (
    <ScrollArea className="flex-1 min-h-0">
      <div className="flex items-center justify-center p-6">
        <img
          src={url}
          alt={filePath.split('/').pop() || 'Preview'}
          className="max-w-full max-h-[80vh] object-contain rounded"
          loading="lazy"
        />
      </div>
    </ScrollArea>
  );
}

function TextPreview({ filePath }: { filePath: string }) {
  const { data, loading, error } = useApi(
    () => api.previewFile(filePath),
    [filePath]
  );

  if (loading) return <div className="p-4"><ListSkeleton count={8} /></div>;
  if (error) return <ErrorMsg message={error} />;
  if (!data) return null;

  return (
    <ScrollArea className="flex-1 min-h-0">
      <pre className="p-4 text-[11px] leading-relaxed font-mono text-foreground/80 whitespace-pre-wrap break-words">
        {data.content}
        {data.truncated && <TruncationNotice size={data.size} />}
      </pre>
    </ScrollArea>
  );
}

function UnsupportedPreview({ fileName, onDownload }: { fileName: string; onDownload: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center flex-1 min-h-0 gap-4 p-8 text-center">
      <AlertCircle className="h-10 w-10 text-muted-foreground/40" />
      <div>
        <p className="text-sm text-foreground/80 font-medium mb-1">
          Preview not available
        </p>
        <p className="text-xs text-muted-foreground">
          <code className="bg-accent/40 px-1.5 py-0.5 rounded">{fileName}</code> can't be previewed in the browser.
        </p>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={onDownload}
        className="mt-2"
      >
        <Download className="h-3.5 w-3.5 mr-1.5" />
        Download instead
      </Button>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────

function ErrorMsg({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 p-4 text-xs text-red-400">
      <AlertCircle className="h-3.5 w-3.5 shrink-0" />
      {message}
    </div>
  );
}

function TruncationNotice({ size }: { size: number }) {
  return (
    <span className="text-muted-foreground/50 italic block mt-2">
      (truncated at 100KB — {(size / 1024).toFixed(0)}KB total)
    </span>
  );
}

// ── Main Drawer Component ────────────────────────────────────────────────

interface FilePreviewDrawerProps {
  filePath: string;
  onClose: () => void;
}

export function FilePreviewDrawer({ filePath, onClose }: FilePreviewDrawerProps) {
  const isMobile = useIsMobile();
  const extension = filePath.split('.').pop() || '';
  const previewType = getPreviewType(extension);
  const fileName = filePath.split('/').pop() || filePath;

  // ── Resizable width (desktop only) ──
  const [drawerWidth, setDrawerWidth] = useState(45); // percentage
  const dragging = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const startX = e.clientX;
    const startWidth = drawerWidth;

    const handleMove = (ev: MouseEvent) => {
      const vw = window.innerWidth;
      const delta = startX - ev.clientX;
      const deltaPct = (delta / vw) * 100;
      const next = Math.min(70, Math.max(25, startWidth + deltaPct));
      setDrawerWidth(next);
    };

    const handleUp = () => {
      dragging.current = false;
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
  }, [drawerWidth]);

  // ── Keyboard: Escape to close ──
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const handleDownload = useCallback(() => {
    api.downloadFile(filePath);
  }, [filePath]);

  // ── Drawer header (shared between mobile/desktop) ──
  const header = (
    <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border shrink-0 bg-background">
      {/* Resize handle (desktop only) */}
      {!isMobile && (
        <button
          onMouseDown={handleResizeStart}
          className="cursor-col-resize p-0.5 -ml-1 text-muted-foreground/30 hover:text-muted-foreground/60 transition-colors"
          title="Drag to resize"
        >
          <GripVertical className="h-4 w-4" />
        </button>
      )}

      <PreviewTypeIcon type={previewType} />
      <span className="text-xs font-medium text-foreground truncate flex-1">{fileName}</span>
      <Button variant="ghost" size="icon-xs" onClick={handleDownload} title="Download">
        <Download className="h-3.5 w-3.5" />
      </Button>
      <Button variant="ghost" size="icon-xs" onClick={onClose} title="Close preview">
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );

  // ── Preview content ──
  const content = (
    <div className="flex-1 min-h-0 flex flex-col">
      {previewType === 'pdf'      && <PdfPreview filePath={filePath} />}
      {previewType === 'markdown' && <MarkdownPreview filePath={filePath} />}
      {previewType === 'excel'    && <ExcelPreview filePath={filePath} />}
      {previewType === 'image'    && <ImagePreview filePath={filePath} />}
      {previewType === 'text'     && <TextPreview filePath={filePath} />}
      {previewType === 'unsupported' && (
        <UnsupportedPreview fileName={fileName} onDownload={handleDownload} />
      )}
    </div>
  );

  // ── Mobile: full-screen overlay ──
  if (isMobile) {
    return (
      <div className="fixed inset-0 z-50 flex">
        {/* Backdrop */}
        <div
          className="absolute inset-0 bg-black/60 animate-fade-in"
          onClick={onClose}
        />
        {/* Panel */}
        <div className="relative ml-auto w-full bg-background flex flex-col animate-drawer-slide-in">
          {header}
          {content}
        </div>
      </div>
    );
  }

  // ── Desktop: flex child side panel ──
  return (
    <div
      ref={containerRef}
      className="shrink-0 border-l border-border flex flex-col bg-background animate-drawer-slide-in overflow-hidden"
      style={{ width: `${drawerWidth}%`, minWidth: '360px', maxWidth: '70%' }}
    >
      {header}
      {content}
    </div>
  );
}
