import { useState, useRef, useCallback } from 'react';
import { Upload, X, FileUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from '@/components/ui/sheet';
import { useIsMobile } from '@/hooks/use-mobile';
import { api } from '../../api/client';

interface UploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentPath: string;
  onUploadComplete: () => void;
}

export function UploadDialog({
  open,
  onOpenChange,
  currentPath,
  onUploadComplete,
}: UploadDialogProps) {
  const isMobile = useIsMobile();
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = Array.from(e.dataTransfer.files);
    setFiles((prev) => [...prev, ...dropped]);
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
    }
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleUpload = async () => {
    if (files.length === 0) return;
    setUploading(true);
    try {
      await api.uploadFiles(currentPath || '', files);
      setFiles([]);
      onOpenChange(false);
      onUploadComplete();
    } catch (err) {
      console.error('Upload failed:', err);
    } finally {
      setUploading(false);
    }
  };

  const handleClose = (isOpen: boolean) => {
    if (!uploading) {
      if (!isOpen) setFiles([]);
      onOpenChange(isOpen);
    }
  };

  const content = (
    <div className="space-y-4">
      {/* Drop zone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`
          flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed
          px-6 py-10 cursor-pointer transition-colors
          ${
            dragOver
              ? 'border-emerald-500 bg-emerald-500/5'
              : 'border-border hover:border-muted-foreground/50 hover:bg-accent/30'
          }
        `}
      >
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-card">
          <Upload className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-foreground">
            Drop files here or click to browse
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Max 100 MB per file
          </p>
        </div>
        <input
          ref={inputRef}
          type="file"
          multiple
          onChange={handleFileChange}
          className="hidden"
        />
      </div>

      {/* File list */}
      {files.length > 0 && (
        <div className="space-y-1.5 max-h-48 overflow-y-auto">
          {files.map((file, i) => (
            <div
              key={`${file.name}-${i}`}
              className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2"
            >
              <FileUp className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              <span className="text-sm text-foreground truncate flex-1">
                {file.name}
              </span>
              <span className="text-xs text-muted-foreground shrink-0">
                {(file.size / 1024).toFixed(0)} KB
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeFile(i);
                }}
                className="shrink-0 p-0.5 rounded hover:bg-accent transition-colors"
              >
                <X className="h-3 w-3 text-muted-foreground" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  const footer = (
    <>
      <Button
        variant="outline"
        onClick={() => handleClose(false)}
        disabled={uploading}
      >
        Cancel
      </Button>
      <Button
        onClick={handleUpload}
        disabled={files.length === 0 || uploading}
        className="bg-emerald-600 hover:bg-emerald-700 text-white"
      >
        {uploading ? 'Uploading...' : `Upload ${files.length > 0 ? `(${files.length})` : ''}`}
      </Button>
    </>
  );

  // On mobile, use a bottom sheet; on desktop use a dialog
  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={handleClose}>
        <SheetContent side="bottom" className="h-[85vh] rounded-t-xl">
          <SheetHeader>
            <SheetTitle>Upload Files</SheetTitle>
            <SheetDescription>
              Upload to {currentPath || 'projects root'}
            </SheetDescription>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto px-4">
            {content}
          </div>
          <SheetFooter className="flex-row gap-2">
            {footer}
          </SheetFooter>
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload Files</DialogTitle>
          <DialogDescription>
            Upload to {currentPath || 'projects root'}
          </DialogDescription>
        </DialogHeader>
        {content}
        <DialogFooter>
          {footer}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
