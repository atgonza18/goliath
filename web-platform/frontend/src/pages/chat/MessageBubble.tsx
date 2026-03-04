import { useRef, useEffect, useCallback, useState } from 'react';
import { ChevronDown, Coins } from 'lucide-react';
import type { Message } from '../../types';
import { renderMarkdown } from '../../utils/markdown';
import { cn } from '@/lib/utils';

interface MessageBubbleProps {
  message: Message;
}

function formatTime(ts: string): string {
  const date = new Date(ts);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function StreamingContent({ message }: { message: Message }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const rawTextRef = useRef<string>(message.content);
  const renderTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isStreamingRef = useRef(message.streaming);

  isStreamingRef.current = message.streaming;

  const scheduleRender = useCallback(() => {
    if (renderTimerRef.current) return;
    renderTimerRef.current = setTimeout(() => {
      renderTimerRef.current = null;
      if (contentRef.current) {
        contentRef.current.innerHTML = renderMarkdown(rawTextRef.current);
      }
    }, 100);
  }, []);

  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;

    (el as any).__appendDelta = (delta: string) => {
      rawTextRef.current += delta;
      scheduleRender();
    };

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

  useEffect(() => {
    if (!message.streaming && contentRef.current) {
      rawTextRef.current = message.content;
      contentRef.current.innerHTML = renderMarkdown(message.content);
    }
  }, [message.streaming, message.content]);

  useEffect(() => {
    if (contentRef.current && message.content) {
      contentRef.current.innerHTML = renderMarkdown(message.content);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div
        ref={contentRef}
        className="message-content text-[13px] text-foreground/85 leading-relaxed"
        data-streaming-content={message.id}
      />
      {message.streaming && (
        <span className="inline-block w-[2px] h-[14px] bg-zinc-400 ml-0.5 animate-pulse align-middle rounded-full" />
      )}
    </div>
  );
}

function AgentBadge({ message }: { message: Message }) {
  const [expanded, setExpanded] = useState(false);
  const subagents = message.metadata?.subagents;
  if (!subagents || subagents.length === 0) return null;

  const successCount = subagents.filter((s) => s.success).length;
  const totalDuration = subagents.reduce((sum, s) => sum + (s.duration || 0), 0);

  return (
    <div className="mt-2.5">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-[11px] text-zinc-600 hover:text-zinc-400 transition-colors"
      >
        <div className="h-1 w-1 rounded-full bg-zinc-600" />
        <span>
          {successCount}/{subagents.length} agents &middot; {(totalDuration / 1000).toFixed(1)}s
        </span>
        <ChevronDown
          className={`h-3 w-3 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      {expanded && (
        <div className="mt-1.5 space-y-0.5 animate-expand pl-3 border-l border-zinc-800">
          {subagents.map((agent, i) => (
            <div
              key={`${agent.agent}-${i}`}
              className="flex items-center gap-2 text-[11px] py-0.5"
            >
              <span
                className={cn(
                  'w-1 h-1 rounded-full',
                  agent.success ? 'bg-emerald-500/60' : 'bg-red-400/60'
                )}
              />
              <span className="text-zinc-500">
                {agent.agent.replace(/_/g, ' ')}
              </span>
              <span className="text-zinc-700 ml-auto font-mono">
                {agent.duration != null ? `${(agent.duration / 1000).toFixed(1)}s` : ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TokenBadge({ message }: { message: Message }) {
  const tokens = message.metadata?.token_summary;
  if (!tokens) return null;

  return (
    <div className="group relative inline-flex items-center gap-1 text-[10px] text-zinc-700 ml-2 cursor-default">
      <Coins className="h-2.5 w-2.5" />
      <span>{(tokens.total_tokens / 1000).toFixed(1)}k</span>
      <div className="absolute bottom-full left-0 mb-1 hidden group-hover:block z-10">
        <div className="bg-zinc-900 border border-zinc-800 rounded-md px-2.5 py-1.5 text-[10px] shadow-xl whitespace-nowrap text-zinc-400">
          <div>Input: {tokens.total_input.toLocaleString()}</div>
          <div>Output: {tokens.total_output.toLocaleString()}</div>
          <div>Cost: ${tokens.total_cost_usd.toFixed(4)}</div>
        </div>
      </div>
    </div>
  );
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user';

  return (
    <div className="animate-fade-in">
      {isUser ? (
        /* User message — right-aligned, minimal */
        <div className="flex justify-end">
          <div className="max-w-[75%]">
            <div className="rounded-2xl rounded-br-sm bg-zinc-800/80 px-4 py-2.5">
              <p className="text-[13px] text-zinc-200 whitespace-pre-wrap leading-relaxed">
                {message.content}
              </p>
            </div>
            <div className="flex justify-end mt-1">
              <span className="text-[10px] text-zinc-700">
                {formatTime(message.timestamp)}
              </span>
            </div>
          </div>
        </div>
      ) : (
        /* Assistant message — left-aligned, clean */
        <div className="flex gap-3">
          {/* Avatar */}
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-800 mt-0.5">
            <span className="text-[10px] font-semibold text-zinc-400">N</span>
          </div>

          <div className="flex-1 min-w-0 max-w-[85%]">
            {/* Meta row */}
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[11px] font-medium text-zinc-500">
                Nimrod
              </span>
              <span className="text-[10px] text-zinc-700">
                {formatTime(message.timestamp)}
              </span>
              <TokenBadge message={message} />
            </div>

            <StreamingContent message={message} />
            <AgentBadge message={message} />
          </div>
        </div>
      )}
    </div>
  );
}
