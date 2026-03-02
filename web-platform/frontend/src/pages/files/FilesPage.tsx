import { useState, useCallback } from 'react';
import { Upload, FolderPlus, FolderOpen } from 'lucide-react';
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
import { Breadcrumbs } from './Breadcrumbs';
import { FileRow } from './FileRow';
import { UploadDialog } from './UploadDialog';

export function FilesPage() {
  const [currentPath, setCurrentPath] = useState('');
  const [uploadOpen, setUploadOpen] = useState(false);
  const [mkdirOpen, setMkdirOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [creating, setCreating] = useState(false);

  const {
    data: files,
    loading,
    error,
    refetch,
  } = useApi<FileItem[]>(() => api.getFiles(currentPath || undefined), [currentPath]);

  const navigate = useCallback((path: string) => {
    setCurrentPath(path);
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

  const itemCount = files?.length ?? 0;
  const dirCount = files?.filter((f) => f.type === 'directory').length ?? 0;
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
              onClick={() => setMkdirOpen(true)}
            >
              <FolderPlus className="h-3.5 w-3.5 mr-1.5" />
              <span className="hidden sm:inline">New Folder</span>
            </Button>
            <Button
              size="sm"
              className="h-8 text-xs bg-emerald-600 hover:bg-emerald-700 text-white"
              onClick={() => setUploadOpen(true)}
            >
              <Upload className="h-3.5 w-3.5 mr-1.5" />
              <span className="hidden sm:inline">Upload</span>
            </Button>
          </div>
        }
      />

      {/* Breadcrumb bar */}
      <div className="flex items-center px-6 py-2 border-b border-border">
        <Breadcrumbs currentPath={currentPath} onNavigate={navigate} />
      </div>

      {/* Column headers -- hidden on mobile */}
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
                className="bg-emerald-600 hover:bg-emerald-700 text-white"
                onClick={() => setUploadOpen(true)}
              >
                <Upload className="h-3.5 w-3.5 mr-1.5" />
                Upload Files
              </Button>
            }
          />
        ) : (
          <div className="border-b border-border">
            {files.map((item) => (
              <FileRow
                key={item.path}
                item={item}
                onNavigate={navigate}
                onDownload={handleDownload}
              />
            ))}
          </div>
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
              Create a new folder in {currentPath || 'projects root'}
            </DialogDescription>
          </DialogHeader>
          <Input
            placeholder="Folder name"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyDown={(e) => {
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
              className="bg-emerald-600 hover:bg-emerald-700 text-white"
            >
              {creating ? 'Creating...' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
