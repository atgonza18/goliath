import {
  Folder,
  File,
  FileText,
  FileSpreadsheet,
  FileImage,
  Download,
  Eye,
  MoreVertical,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { FileItem } from '../../types';

interface FileRowProps {
  item: FileItem;
  onNavigate: (path: string) => void;
  onDownload: (path: string) => void;
  onPreview?: () => void;
}

const ICON_MAP: Record<string, typeof File> = {
  pdf: FileText,
  doc: FileText,
  docx: FileText,
  txt: FileText,
  md: FileText,
  rtf: FileText,
  log: FileText,
  csv: FileSpreadsheet,
  xls: FileSpreadsheet,
  xlsx: FileSpreadsheet,
  xlsm: FileSpreadsheet,
  png: FileImage,
  jpg: FileImage,
  jpeg: FileImage,
  gif: FileImage,
  svg: FileImage,
  webp: FileImage,
  bmp: FileImage,
};

function getFileIcon(item: FileItem) {
  if (item.type === 'directory') {
    return <Folder className="h-4 w-4 text-emerald-500" />;
  }
  const IconComponent = ICON_MAP[item.extension] || File;
  return <IconComponent className="h-4 w-4 text-muted-foreground" />;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));

  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days}d ago`;

  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  });
}

export function FileRow({ item, onNavigate, onDownload, onPreview }: FileRowProps) {
  const handleClick = () => {
    if (item.type === 'directory') {
      onNavigate(item.path);
    } else {
      onDownload(item.path);
    }
  };

  return (
    <div className="group flex items-center gap-3 px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-accent/30 transition-colors">
      {/* Clickable area: icon + name */}
      <button
        onClick={handleClick}
        className="flex items-center gap-3 flex-1 min-w-0 text-left"
      >
        <div className="shrink-0">{getFileIcon(item)}</div>
        <span className="text-sm text-foreground truncate">
          {item.name}
        </span>
      </button>

      {/* Size — hidden on mobile */}
      <span className="hidden md:block text-xs text-muted-foreground w-20 text-right shrink-0">
        {item.type === 'directory' ? '--' : item.sizeFormatted}
      </span>

      {/* Modified — hidden on mobile */}
      <span className="hidden md:block text-xs text-muted-foreground w-24 text-right shrink-0">
        {formatDate(item.modified)}
      </span>

      {/* Actions */}
      <div className="shrink-0 w-8">
        {item.type === 'file' && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
              >
                <MoreVertical className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              {onPreview && (
                <DropdownMenuItem onClick={onPreview}>
                  <Eye className="h-3.5 w-3.5 mr-2" />
                  Preview
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onDownload(item.path)}>
                <Download className="h-3.5 w-3.5 mr-2" />
                Download
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    </div>
  );
}
