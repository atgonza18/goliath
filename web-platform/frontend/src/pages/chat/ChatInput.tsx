import { useState, useRef, useCallback } from 'react';
import { ArrowUp, Paperclip, X, FileText } from 'lucide-react';

const ACCEPTED_TYPES = 'image/png,image/jpeg,image/jpg,image/gif,image/webp,application/pdf';
const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20 MB
const MAX_FILES = 10;

interface ChatInputProps {
  onSend: (message: string, files?: File[]) => void;
  disabled: boolean;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [message, setMessage] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [previewUrls, setPreviewUrls] = useState<Map<File, string>>(new Map());
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = useCallback(() => {
    const trimmed = message.trim();
    if ((!trimmed && attachedFiles.length === 0) || disabled) return;
    onSend(trimmed, attachedFiles.length > 0 ? attachedFiles : undefined);
    setMessage('');
    // Revoke all preview URLs
    previewUrls.forEach((url) => URL.revokeObjectURL(url));
    setPreviewUrls(new Map());
    setAttachedFiles([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [message, attachedFiles, disabled, onSend, previewUrls]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setMessage(e.target.value);
    const target = e.target;
    target.style.height = 'auto';
    target.style.height = Math.min(Math.max(target.scrollHeight, 44), 160) + 'px';
  };

  const handleAttachClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = e.target.files;
    if (!selectedFiles || selectedFiles.length === 0) return;

    const newFiles: File[] = [];
    const newPreviews = new Map(previewUrls);
    let rejected = 0;

    for (let i = 0; i < selectedFiles.length; i++) {
      const file = selectedFiles[i];

      // Check total file count limit
      if (attachedFiles.length + newFiles.length >= MAX_FILES) {
        rejected += selectedFiles.length - i;
        break;
      }

      // Check file size
      if (file.size > MAX_FILE_SIZE) {
        rejected++;
        continue;
      }

      // Check for duplicates (same name + size)
      const isDuplicate = attachedFiles.some(
        (f) => f.name === file.name && f.size === file.size
      ) || newFiles.some(
        (f) => f.name === file.name && f.size === file.size
      );
      if (isDuplicate) continue;

      newFiles.push(file);

      // Generate preview for images
      if (file.type.startsWith('image/')) {
        newPreviews.set(file, URL.createObjectURL(file));
      }
    }

    if (rejected > 0) {
      alert(`${rejected} file(s) were skipped (too large or limit of ${MAX_FILES} reached).`);
    }

    if (newFiles.length > 0) {
      setAttachedFiles((prev) => [...prev, ...newFiles]);
      setPreviewUrls(newPreviews);
    }

    // Reset file input so selecting the same file again works
    e.target.value = '';
  };

  const handleRemoveFile = (fileToRemove: File) => {
    setAttachedFiles((prev) => prev.filter((f) => f !== fileToRemove));
    setPreviewUrls((prev) => {
      const next = new Map(prev);
      const url = next.get(fileToRemove);
      if (url) {
        URL.revokeObjectURL(url);
        next.delete(fileToRemove);
      }
      return next;
    });
  };

  const hasFiles = attachedFiles.length > 0;
  const canSend = (message.trim().length > 0 || hasFiles) && !disabled;

  return (
    <div className="px-4 pb-5 pt-2 shrink-0">
      <div className="max-w-[720px] mx-auto">
        {/* Attachment previews */}
        {hasFiles && (
          <div className="mb-2 flex flex-col gap-1.5 animate-fade-in">
            {attachedFiles.map((file, idx) => {
              const isPdf = file.type === 'application/pdf';
              const preview = previewUrls.get(file) || null;

              return (
                <div
                  key={`${file.name}-${file.size}-${idx}`}
                  className="flex items-center gap-3 px-3 py-2"
                  style={{
                    background: 'var(--card)',
                    border: '2px solid var(--theme-border)',
                    borderRadius: '3px',
                  }}
                >
                  {isPdf ? (
                    <div
                      className="flex items-center justify-center shrink-0"
                      style={{
                        width: '40px',
                        height: '40px',
                        background: 'var(--theme-bg-tertiary)',
                        border: '2px solid var(--chart-1)',
                        borderRadius: '2px',
                      }}
                    >
                      <FileText className="h-5 w-5" style={{ color: 'var(--chart-1)' }} />
                    </div>
                  ) : preview ? (
                    <img
                      src={preview}
                      alt="Preview"
                      className="shrink-0 object-cover"
                      style={{
                        width: '40px',
                        height: '40px',
                        borderRadius: '2px',
                        border: '2px solid var(--chart-2)',
                      }}
                    />
                  ) : null}
                  <div className="flex-1 min-w-0">
                    <p
                      className="text-[11px] font-bold tracking-wider truncate"
                      style={{ color: 'var(--foreground)' }}
                    >
                      {file.name}
                    </p>
                    <p
                      className="text-[10px] tracking-wider"
                      style={{ color: 'var(--theme-text-dim)' }}
                    >
                      {(file.size / 1024).toFixed(0)} KB &middot; {isPdf ? 'PDF' : 'IMAGE'}
                    </p>
                  </div>
                  <button
                    onClick={() => handleRemoveFile(file)}
                    className="shrink-0 flex items-center justify-center transition-colors duration-100"
                    style={{
                      width: '24px',
                      height: '24px',
                      color: 'var(--theme-text-dim)',
                      background: 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = 'var(--destructive)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = 'var(--theme-text-dim)';
                    }}
                    title="Remove attachment"
                  >
                    <X className="h-4 w-4" strokeWidth={3} />
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {/* Input row */}
        <div className="flex items-end gap-3">
          {/* Hidden file input — multiple enabled */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_TYPES}
            multiple
            onChange={handleFileSelect}
            className="hidden"
          />

          {/* Attach button */}
          <button
            onClick={handleAttachClick}
            disabled={disabled}
            className="shrink-0 flex items-center justify-center transition-all duration-100 relative"
            style={{
              width: '44px',
              height: '44px',
              background: hasFiles ? 'var(--chart-2)' : 'var(--theme-bg-tertiary)',
              border: hasFiles ? '2px solid var(--chart-2)' : '2px solid var(--theme-border)',
              borderRadius: '3px',
              color: hasFiles ? 'var(--primary-foreground)' : 'var(--theme-text-dim)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              opacity: disabled ? 0.5 : 1,
            }}
            onMouseEnter={(e) => {
              if (!disabled && !hasFiles) {
                e.currentTarget.style.borderColor = 'var(--chart-2)';
                e.currentTarget.style.color = 'var(--chart-2)';
              }
            }}
            onMouseLeave={(e) => {
              if (!disabled && !hasFiles) {
                e.currentTarget.style.borderColor = 'var(--theme-border)';
                e.currentTarget.style.color = 'var(--theme-text-dim)';
              }
            }}
            title="Attach images or PDFs"
          >
            <Paperclip className="h-5 w-5" strokeWidth={2.5} />
            {/* File count badge */}
            {attachedFiles.length > 1 && (
              <span
                className="absolute -top-1.5 -right-1.5 flex items-center justify-center text-[9px] font-bold"
                style={{
                  width: '18px',
                  height: '18px',
                  borderRadius: '9px',
                  background: 'var(--theme-accent)',
                  color: 'var(--primary-foreground)',
                }}
              >
                {attachedFiles.length}
              </span>
            )}
          </button>

          <textarea
            ref={textareaRef}
            value={message}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={hasFiles ? 'Add a message (optional)...' : 'ASK ANYTHING...'}
            disabled={disabled}
            rows={1}
            className="flex-1 min-w-0 resize-none pl-4 pr-4 py-3 text-[13px] focus:outline-none transition-colors duration-100"
            style={{
              minHeight: '44px',
              maxHeight: '160px',
              background: 'var(--card)',
              border: '2px solid var(--theme-border)',
              borderRadius: '3px',
              color: 'var(--foreground)',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = 'var(--chart-2)';
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = 'var(--theme-border)';
            }}
          />
          <button
            onClick={handleSubmit}
            disabled={!canSend}
            className="shrink-0 flex items-center justify-center transition-all duration-100"
            style={{
              width: '44px',
              height: '44px',
              background: canSend ? 'var(--theme-accent)' : 'var(--theme-bg-tertiary)',
              border: canSend ? '2px solid var(--theme-accent)' : '2px solid var(--theme-border)',
              borderRadius: '3px',
              color: canSend ? 'var(--primary-foreground)' : 'var(--theme-border)',
              cursor: canSend ? 'pointer' : 'not-allowed',
            }}
          >
            <ArrowUp className="h-5 w-5" strokeWidth={3} />
          </button>
        </div>
        <p className="text-[10px] text-center mt-2 font-bold tracking-widest" style={{ color: 'var(--theme-border)' }}>
          ENTER TO SEND &middot; SHIFT+ENTER FOR NEW LINE &middot; 📎 ATTACH IMAGES/PDFS
        </p>
      </div>
    </div>
  );
}
