import { useRef, useEffect, useCallback } from 'react';
import { Bot } from 'lucide-react';
import type { Message } from '../../types';
import { renderMarkdown } from '../../utils/markdown';

interface MessageBubbleProps {
  message: Message;
}

function formatTime(ts: string): string {
  const date = new Date(ts);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * StreamingContent: Renders streaming markdown content using a ref-based
 * approach. During streaming, we accumulate raw text in a ref and use
 * requestAnimationFrame to batch DOM updates. This avoids React re-renders
 * on every chunk, giving smooth word-by-word output like ChatGPT.
 *
 * When not streaming, we render the final markdown via useMemo.
 */
function StreamingContent({ message }: { message: Message }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const rawTextRef = useRef<string>(message.content);
  const renderTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isStreamingRef = useRef(message.streaming);

  // Keep refs in sync
  isStreamingRef.current = message.streaming;

  // Throttle markdown rendering to ~10fps during streaming.
  // Re-rendering the full markdown on every animation frame (60fps) causes jank
  // because marked.parse() + innerHTML replacement gets expensive as text grows.
  // At 100ms intervals, we batch ~5 tokens per render — looks smooth to the eye.
  const scheduleRender = useCallback(() => {
    if (renderTimerRef.current) return; // Already scheduled
    renderTimerRef.current = setTimeout(() => {
      renderTimerRef.current = null;
      if (contentRef.current) {
        contentRef.current.innerHTML = renderMarkdown(rawTextRef.current);
      }
    }, 100);
  }, []);

  // Expose a method for the parent to append text (called from ChatPage)
  // We store it on the DOM element as a custom property.
  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;

    // Attach the append method to the element
    (el as any).__appendDelta = (delta: string) => {
      rawTextRef.current += delta;
      scheduleRender();
    };

    // Attach a replace method for snapshot events
    (el as any).__setContent = (text: string) => {
      rawTextRef.current = text;
      scheduleRender();
    };

    return () => {
      if (renderTimerRef.current) {
        clearTimeout(renderTimerRef.current);
        renderTimerRef.current = null;
      }
    };
  }, [scheduleRender]);

  // When streaming ends or for non-streaming messages, do a final render
  useEffect(() => {
    if (!message.streaming && contentRef.current) {
      rawTextRef.current = message.content;
      contentRef.current.innerHTML = renderMarkdown(message.content);
    }
  }, [message.streaming, message.content]);

  // Initial render
  useEffect(() => {
    if (contentRef.current && message.content) {
      contentRef.current.innerHTML = renderMarkdown(message.content);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div
        ref={contentRef}
        className="message-content text-sm text-foreground/80 leading-relaxed"
        data-streaming-content={message.id}
      />
      {message.streaming && (
        <span className="inline-block w-0.5 h-4 bg-muted-foreground ml-0.5 animate-pulse align-middle" />
      )}
    </div>
  );
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user';

  return (
    <div className="animate-fade-in">
      {isUser ? (
        <div className="flex justify-end">
          <div className="max-w-[80%]">
            <div className="flex items-center justify-end gap-2 mb-1.5">
              <span className="text-[11px] text-muted-foreground">
                {formatTime(message.timestamp)}
              </span>
            </div>
            <div className="rounded-2xl rounded-br-md bg-secondary px-4 py-2.5">
              <p className="text-sm text-foreground whitespace-pre-wrap leading-relaxed">
                {message.content}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-card mt-0.5">
            <Bot className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
          <div className="flex-1 min-w-0 max-w-[85%]">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-[11px] font-medium text-foreground/70">
                Nimrod
              </span>
              <span className="text-[11px] text-muted-foreground">
                {formatTime(message.timestamp)}
              </span>
            </div>
            <StreamingContent message={message} />
          </div>
        </div>
      )}
    </div>
  );
}
