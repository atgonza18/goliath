import { useState, useRef, useCallback } from 'react';
import { ArrowUp } from 'lucide-react';
import { cn } from '@/lib/utils';

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
    target.style.height = Math.min(target.scrollHeight, 160) + 'px';
  };

  const canSend = message.trim().length > 0 && !disabled;

  return (
    <div className="px-4 pb-4 pt-2 shrink-0">
      <div className="max-w-[720px] mx-auto">
        <div className="relative">
          <textarea
            ref={textareaRef}
            value={message}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything..."
            disabled={disabled}
            rows={1}
            className={cn(
              'w-full resize-none rounded-xl border border-zinc-800 bg-zinc-900/50 pl-4 pr-12 py-3 text-[13px] text-foreground',
              'placeholder:text-zinc-600',
              'focus:outline-none focus:border-zinc-600',
              'transition-colors duration-150',
              'disabled:opacity-40 disabled:cursor-not-allowed'
            )}
            style={{ minHeight: '48px', maxHeight: '160px' }}
          />
          <button
            onClick={handleSubmit}
            disabled={!canSend}
            className={cn(
              'absolute right-2 bottom-2 h-8 w-8 rounded-lg flex items-center justify-center transition-all duration-150',
              canSend
                ? 'bg-zinc-200 text-zinc-900 hover:bg-white'
                : 'bg-zinc-800 text-zinc-600 cursor-not-allowed'
            )}
          >
            <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
          </button>
        </div>
        <p className="text-[10px] text-zinc-700 text-center mt-1.5">
          Enter to send &middot; Shift+Enter for new line
        </p>
      </div>
    </div>
  );
}
