import { useState, useRef, useCallback } from 'react';
import { ArrowUp, Paperclip, X, FileText } from 'lucide-react';

const ACCEPTED_TYPES = 'image/png,image/jpeg,image/jpg,image/gif,image/webp,application/pdf';
const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20 MB

interface ChatInputProps {
  onSend: (message: string, file?: File) => void;
  disabled: boolean;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [message, setMessage] = useState('');
  const [attachedFile, setAttachedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = useCallback(() => {
    const trimmed = message.trim();
    if ((!trimmed && !attachedFile) || disabled) return;
    onSend(trimmed, attachedFile || undefined);
    setMessage('');
    setAttachedFile(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [message, attachedFile, disabled, onSend, previewUrl]);

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
    const file = e.target.files?.[0];
    if (!file) return;

    if (file.size > MAX_FILE_SIZE) {
      alert('File too large. Maximum size is 20 MB.');
      return;
    }

    setAttachedFile(file);

    // Generate preview for images
    if (file.type.startsWith('image/')) {
      const url = URL.createObjectURL(file);
      setPreviewUrl(url);
    } else {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }

    // Reset file input so selecting the same file again works
    e.target.value = '';
  };

  const handleRemoveAttachment = () => {
    setAttachedFile(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }
  };

  const canSend = (message.trim().length > 0 || attachedFile !== null) && !disabled;
  const isPdf = attachedFile?.type === 'application/pdf';

  return (
    <div className="px-4 pb-5 pt-2 shrink-0">
      <div className="max-w-[720px] mx-auto">
        {/* Attachment preview */}
        {attachedFile && (
          <div
            className="mb-2 flex items-center gap-3 px-3 py-2 animate-fade-in"
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
            ) : previewUrl ? (
              <img
                src={previewUrl}
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
                {attachedFile.name}
              </p>
              <p
                className="text-[10px] tracking-wider"
                style={{ color: 'var(--theme-text-dim)' }}
              >
                {(attachedFile.size / 1024).toFixed(0)} KB &middot; {isPdf ? 'PDF' : 'IMAGE'}
              </p>
            </div>
            <button
              onClick={handleRemoveAttachment}
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
        )}

        {/* Input row */}
        <div className="flex items-end gap-3">
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_TYPES}
            onChange={handleFileSelect}
            className="hidden"
          />

          {/* Attach button */}
          <button
            onClick={handleAttachClick}
            disabled={disabled}
            className="shrink-0 flex items-center justify-center transition-all duration-100"
            style={{
              width: '44px',
              height: '44px',
              background: attachedFile ? 'var(--chart-2)' : 'var(--theme-bg-tertiary)',
              border: attachedFile ? '2px solid var(--chart-2)' : '2px solid var(--theme-border)',
              borderRadius: '3px',
              color: attachedFile ? 'var(--primary-foreground)' : 'var(--theme-text-dim)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              opacity: disabled ? 0.5 : 1,
            }}
            onMouseEnter={(e) => {
              if (!disabled && !attachedFile) {
                e.currentTarget.style.borderColor = 'var(--chart-2)';
                e.currentTarget.style.color = 'var(--chart-2)';
              }
            }}
            onMouseLeave={(e) => {
              if (!disabled && !attachedFile) {
                e.currentTarget.style.borderColor = 'var(--theme-border)';
                e.currentTarget.style.color = 'var(--theme-text-dim)';
              }
            }}
            title="Attach image or PDF"
          >
            <Paperclip className="h-5 w-5" strokeWidth={2.5} />
          </button>

          <textarea
            ref={textareaRef}
            value={message}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={attachedFile ? 'Add a message (optional)...' : 'ASK ANYTHING...'}
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
          ENTER TO SEND &middot; SHIFT+ENTER FOR NEW LINE &middot; 📎 ATTACH IMAGE/PDF
        </p>
      </div>
    </div>
  );
}
