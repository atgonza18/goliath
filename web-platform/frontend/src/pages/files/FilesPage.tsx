import { useState, useCallback, useRef } from 'react';
import {
  Upload,
  FolderPlus,
  FolderOpen,
  FolderUp,
  X,
  ChevronRight,
  ChevronDown,
  Folder,
} from 'lucide-react';
import { api } from '../../api/client';
import type { FileItem } from '../../types';
import { useApi } from '../../hooks/useApi';
import { PageHeader } from '../../components/common/PageHeader';
import { ListSkeleton } from '../../components/common/LoadingSpinner';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Breadcrumbs } from './Breadcrumbs';
import { FileRow } from './FileRow';
import { UploadDialog } from './UploadDialog';

// ---- Tree Sidebar ----

interface TreeNodeProps {
  name: string;
  path: string;
  currentPath: string;
  onNavigate: (path: string) => void;
  depth: number;
}

function TreeNode({ name, path, currentPath, onNavigate, depth }: TreeNodeProps) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<FileItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const isActive = currentPath === path;

  const handleToggle = async () => {
    if (!loaded) {
      try {
        const items = await api.getFiles(path || undefined);
        setChildren(items.filter(i => i.type === 'directory'));
        setLoaded(true);
      } catch {
        setChildren([]);
        setLoaded(true);
      }
    }
    setExpanded(!expanded);
    onNavigate(path);
  };

  return (
    <div>
      <button
        onClick={handleToggle}
        className={`flex items-center gap-1 w-full text-left px-2 py-1 rounded text-xs transition-colors ${
          isActive
            ? 'bg-amber-500/10 text-amber-400'
            : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        <Folder className="h-3 w-3 shrink-0 text-amber-500/60" />
        <span className="truncate">{name}</span>
      </button>
      {expanded && children.length > 0 && (
        <div>
          {children.map(child => (
            <TreeNode
              key={child.path}
              name={child.name}
              path={child.path}
              currentPath={currentPath}
              onNavigate={onNavigate}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TreeSidebar({
  rootItems,
  currentPath,
  onNavigate,
}: {
  rootItems: FileItem[] | null;
  currentPath: string;
  onNavigate: (path: string) => void;
}) {
  const dirs = rootItems?.filter(i => i.type === 'directory') || [];

  return (
    <div className="w-56 min-w-[14rem] border-r border-border shrink-0 hidden lg:block">
      <div className="px-3 py-2 border-b border-border">
        <button
          onClick={() => onNavigate('')}
          className={`flex items-center gap-1.5 w-full text-left px-2 py-1 rounded text-xs font-medium transition-colors ${
            currentPath === ''
              ? 'bg-amber-500/10 text-amber-400'
              : 'text-muted-foreground hover:text-foreground'
          }`}
        >
          <FolderOpen className="h-3.5 w-3.5" />
          goliath/
        </button>
      </div>
      <ScrollArea className="h-[calc(100%-40px)]">
        <div className="p-1">
          {dirs.map(item => (
            <TreeNode
              key={item.path}
              name={item.name}
              path={item.path}
              currentPath={currentPath}
              onNavigate={onNavigate}
              depth={0}
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

// ---- Preview Panel ----

function PreviewPanel({
  filePath,
  onClose,
}: {
  filePath: string;
  onClose: () => void;
}) {
  const { data, loading, error } = useApi(
    () => api.previewFile(filePath),
    [filePath]
  );

  return (
    <div className="w-96 min-w-[24rem] border-l border-border shrink-0 flex flex-col hidden xl:flex">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <span className="text-xs font-medium text-foreground truncate">
          {data?.name || filePath.split('/').pop()}
        </span>
        <Button variant="ghost" size="icon-xs" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <ScrollArea className="flex-1">
        {loading ? (
          <div className="p-4">
            <ListSkeleton count={10} />
          </div>
        ) : error ? (
          <div className="p-4 text-xs text-muted-foreground">{error}</div>
        ) : data ? (
          <pre className="p-4 text-[11px] leading-relaxed font-mono text-foreground/80 whitespace-pre-wrap break-words">
            {data.content}
            {data.truncated && (
              <span className="text-muted-foreground/50 italic block mt-2">
                (truncated at 100KB — {(data.size / 1024).toFixed(0)}KB total)
              </span>
            )}
          </pre>
        ) : null}
      </ScrollArea>
    </div>
  );
}

// ---- Main Page ----

const TEXT_EXTENSIONS = new Set([
  'txt', 'md', 'json', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',
  'py', 'js', 'ts', 'tsx', 'jsx', 'html', 'css', 'scss', 'less',
  'sh', 'bash', 'zsh', 'fish', 'sql', 'graphql', 'prisma',
  'rs', 'go', 'java', 'kt', 'c', 'cpp', 'h', 'hpp', 'cs',
  'rb', 'php', 'swift', 'r', 'lua', 'vim', 'log', 'csv', 'env',
  'xml', 'svg', 'dockerfile', 'makefile', 'gitignore', 'editorconfig',
]);

export function FilesPage() {
  const [currentPath, setCurrentPath] = useState('');
  const [uploadOpen, setUploadOpen] = useState(false);
  const [mkdirOpen, setMkdirOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [creating, setCreating] = useState(false);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [folderUploading, setFolderUploading] = useState(false);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const {
    data: files,
    loading,
    error,
    refetch,
  } = useApi<FileItem[]>(() => api.getFiles(currentPath || undefined), [currentPath]);

  // Root level items for tree sidebar
  const {
    data: rootItems,
  } = useApi<FileItem[]>(() => api.getFiles(), []);

  const navigate = useCallback((path: string) => {
    setCurrentPath(path);
    setPreviewPath(null);
  }, []);

  const handleDownload = useCallback((path: string) => {
    api.downloadFile(path);
  }, []);

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return;
    setCreating(true);
    try {
      const dirPath = currentPath
        ? `${currentPath}/${newFolderName.trim()}`
        : newFolderName.trim();
      await api.createDirectory(dirPath);
      setNewFolderName('');
      setMkdirOpen(false);
      refetch();
    } catch (err) {
      console.error('Failed to create folder:', err);
    } finally {
      setCreating(false);
    }
  };

  const handleFolderUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;
    setFolderUploading(true);
    try {
      await api.uploadFolder(currentPath || '', Array.from(fileList));
      refetch();
    } catch (err) {
      console.error('Folder upload failed:', err);
    } finally {
      setFolderUploading(false);
      if (folderInputRef.current) {
        folderInputRef.current.value = '';
      }
    }
  };

  const itemCount = files?.length ?? 0;
  const dirCount = files?.filter(f => f.type === 'directory').length ?? 0;
  const fileCount = itemCount - dirCount;

  const subtitle = loading
    ? 'Loading...'
    : `${dirCount} folder${dirCount !== 1 ? 's' : ''}, ${fileCount} file${fileCount !== 1 ? 's' : ''}`;

  return (
    <div className="flex flex-col h-full min-h-0">
      <PageHeader
        title="Files"
        subtitle={subtitle}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={() => folderInputRef.current?.click()}
              disabled={folderUploading}
            >
              <FolderUp className="h-3.5 w-3.5 mr-1.5" />
              <span className="hidden sm:inline">
                {folderUploading ? 'Uploading...' : 'Upload Folder'}
              </span>
            </Button>
            <input
              ref={folderInputRef}
              type="file"
              // @ts-expect-error webkitdirectory is non-standard
              webkitdirectory=""
              directory=""
              multiple
              onChange={handleFolderUpload}
              className="hidden"
            />
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={() => setMkdirOpen(true)}
            >
              <FolderPlus className="h-3.5 w-3.5 mr-1.5" />
              <span className="hidden sm:inline">New Folder</span>
            </Button>
            <Button
              size="sm"
              className="h-8 text-xs bg-amber-600 hover:bg-amber-700 text-white"
              onClick={() => setUploadOpen(true)}
            >
              <Upload className="h-3.5 w-3.5 mr-1.5" />
              <span className="hidden sm:inline">Upload</span>
            </Button>
          </div>
        }
      />

      <div className="flex flex-1 min-h-0">
        {/* Tree sidebar */}
        <TreeSidebar
          rootItems={rootItems}
          currentPath={currentPath}
          onNavigate={navigate}
        />

        {/* Main content */}
        <div className="flex-1 flex flex-col min-w-0 min-h-0">
          {/* Breadcrumb bar */}
          <div className="flex items-center px-6 py-2 border-b border-border">
            <Breadcrumbs currentPath={currentPath} onNavigate={navigate} />
          </div>

          {/* Column headers */}
          {!loading && !error && files && files.length > 0 && (
            <div className="hidden md:flex items-center gap-3 px-4 py-1.5 border-b border-border text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
              <div className="flex-1 pl-11">Name</div>
              <div className="w-20 text-right">Size</div>
              <div className="w-24 text-right">Modified</div>
              <div className="w-8" />
            </div>
          )}

          {/* Content */}
          <div className="flex-1 overflow-y-auto min-h-0" data-scroll-container>
            {loading ? (
              <div className="p-6 max-w-4xl">
                <ListSkeleton count={8} />
              </div>
            ) : error ? (
              <div className="p-6">
                <ErrorState message={error} onRetry={refetch} />
              </div>
            ) : !files || files.length === 0 ? (
              <EmptyState
                icon={<FolderOpen className="h-5 w-5" />}
                title="This folder is empty"
                description="Upload files or create a subfolder to get started."
                action={
                  <Button
                    size="sm"
                    className="bg-amber-600 hover:bg-amber-700 text-white"
                    onClick={() => setUploadOpen(true)}
                  >
                    <Upload className="h-3.5 w-3.5 mr-1.5" />
                    Upload Files
                  </Button>
                }
              />
            ) : (
              <div className="border-b border-border">
                {files.map(item => (
                  <FileRow
                    key={item.path}
                    item={item}
                    onNavigate={navigate}
                    onDownload={handleDownload}
                    onPreview={
                      item.type === 'file' && TEXT_EXTENSIONS.has(item.extension.toLowerCase())
                        ? () => setPreviewPath(item.path)
                        : undefined
                    }
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Preview panel */}
        {previewPath && (
          <PreviewPanel
            filePath={previewPath}
            onClose={() => setPreviewPath(null)}
          />
        )}
      </div>

      {/* Upload Dialog */}
      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        currentPath={currentPath}
        onUploadComplete={refetch}
      />

      {/* New Folder Dialog */}
      <Dialog open={mkdirOpen} onOpenChange={setMkdirOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>New Folder</DialogTitle>
            <DialogDescription>
              Create a new folder in {currentPath || '/opt/goliath/'}
            </DialogDescription>
          </DialogHeader>
          <Input
            placeholder="Folder name"
            value={newFolderName}
            onChange={e => setNewFolderName(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') handleCreateFolder();
            }}
            autoFocus
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setMkdirOpen(false);
                setNewFolderName('');
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleCreateFolder}
              disabled={!newFolderName.trim() || creating}
              className="bg-amber-600 hover:bg-amber-700 text-white"
            >
              {creating ? 'Creating...' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
