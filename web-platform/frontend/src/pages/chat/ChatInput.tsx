import { useState, useRef, useCallback } from 'react';
import { ArrowUp } from 'lucide-react';

interface ChatInputProps {
  onSend: (message: string) => void;
  disabled: boolean;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [message, setMessage] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = useCallback(() => {
    const trimmed = message.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setMessage('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [message, disabled, onSend]);

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

  const canSend = message.trim().length > 0 && !disabled;

  return (
    <div className="px-4 pb-5 pt-2 shrink-0">
      <div className="max-w-[720px] mx-auto">
        <div className="flex items-end gap-3">
          <textarea
            ref={textareaRef}
            value={message}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="ASK ANYTHING..."
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
          ENTER TO SEND &middot; SHIFT+ENTER FOR NEW LINE
        </p>
      </div>
    </div>
  );
}
