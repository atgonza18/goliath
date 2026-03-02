import { useState, useRef, useCallback } from 'react';
import { ArrowUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
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

  return (
    <div className="border-t border-border px-4 py-3 shrink-0">
      <div className="flex items-end gap-2 max-w-[720px] mx-auto">
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={message}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="Message Nimrod..."
            disabled={disabled}
            rows={1}
            className={cn(
              'w-full resize-none rounded-xl border border-input bg-card px-4 py-3 text-sm text-foreground',
              'placeholder:text-muted-foreground',
              'focus:outline-none focus:ring-2 focus:ring-ring/50 focus:border-ring',
              'transition-[border-color,box-shadow]',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
            style={{ minHeight: '48px', maxHeight: '160px' }}
          />
        </div>
        <Button
          onClick={handleSubmit}
          disabled={disabled || !message.trim()}
          size="icon"
          className="shrink-0 h-10 w-10 rounded-xl"
        >
          <ArrowUp className="h-4 w-4" />
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground/50 text-center mt-2">
        Enter to send, Shift+Enter for new line
      </p>
    </div>
  );
}
