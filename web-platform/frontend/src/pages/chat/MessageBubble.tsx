import { useRef, useEffect, useCallback, useState } from 'react';
import { ChevronDown, Coins, Zap, FileText } from 'lucide-react';
import type { Message } from '../../types';
import { renderMarkdown } from '../../utils/markdown';

interface MessageBubbleProps {
  message: Message;
}

function formatTime(ts: string): string {
  const date = new Date(ts);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }).toUpperCase();
}

function StreamingContent({ message }: { message: Message }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const rawTextRef = useRef<string>(message.content);
  const renderTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleRender = useCallback(() => {
    if (renderTimerRef.current) return;
    renderTimerRef.current = setTimeout(() => {
      renderTimerRef.current = null;
      if (contentRef.current) {
        contentRef.current.innerHTML = renderMarkdown(rawTextRef.current);
      }
    }, 80);
  }, []);

  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;

    // Backend sends individual words as separate SSE "chunk" events,
    // so each __appendDelta call is already ~1 word. Just accumulate
    // and re-render markdown periodically.
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
        className="message-content text-[13px] leading-relaxed"
        style={{ color: 'var(--theme-text-muted)' }}
        data-streaming-content={message.id}
      />
      {message.streaming && (
        <span
          className="inline-block w-[3px] h-[14px] ml-0.5 align-middle"
          style={{ background: 'var(--chart-2)', animation: 'cursor-blink 0.6s steps(1) infinite' }}
        />
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

  const agentColors = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-5)', 'var(--chart-3)', 'var(--chart-4)'];

  return (
    <div className="mt-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-[11px] tracking-wider transition-colors group"
        style={{ color: 'var(--theme-text-dim)' }}
      >
        <Zap className="h-3 w-3" style={{ color: 'var(--chart-3)' }} />
        <span className="group-hover:text-[var(--chart-3)] transition-colors font-bold">
          {successCount}/{subagents.length} AGENTS &middot; {(totalDuration / 1000).toFixed(1)}S
        </span>
        <ChevronDown
          className={`h-3 w-3 transition-transform duration-100 ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      {expanded && (
        <div className="mt-2 animate-expand">
          {subagents.map((agent, i) => (
            <div
              key={`${agent.agent}-${i}`}
              className="flex items-center gap-2.5 text-[11px] py-1.5 animate-timeline-enter"
              style={{
                animationDelay: `${i * 40}ms`,
                borderLeft: `3px solid ${agent.success ? agentColors[i % agentColors.length] : 'var(--destructive)'}`,
                paddingLeft: '10px',
                marginLeft: '4px',
              }}
            >
              <span style={{ color: 'var(--theme-text-dim)' }}>
                {agent.agent.replace(/_/g, ' ').toUpperCase()}
              </span>
              <span className="font-bold ml-auto" style={{ color: agent.success ? agentColors[i % agentColors.length] : 'var(--destructive)' }}>
                {agent.duration != null ? `${(agent.duration / 1000).toFixed(1)}S` : ''}
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
    <div className="group relative inline-flex items-center gap-1 text-[10px] ml-2 cursor-default" style={{ color: 'var(--theme-border)' }}>
      <Coins className="h-2.5 w-2.5" />
      <span>{(tokens.total_tokens / 1000).toFixed(1)}K</span>
      <div className="absolute bottom-full left-0 mb-1 hidden group-hover:block z-10">
        <div
          className="px-3 py-2 text-[10px] whitespace-nowrap"
          style={{ background: 'var(--card)', border: '2px solid var(--theme-border)', color: 'var(--theme-text-muted)' }}
        >
          <div>INPUT: {tokens.total_input.toLocaleString()}</div>
          <div>OUTPUT: {tokens.total_output.toLocaleString()}</div>
          <div className="pt-0.5 font-bold" style={{ color: 'var(--theme-accent)' }}>COST: ${tokens.total_cost_usd.toFixed(4)}</div>
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
        <div className="flex justify-end">
          <div className="max-w-[80%]">
            <div
              className="px-4 py-3"
              style={{ background: 'var(--card)', border: '2px solid var(--chart-2)', borderLeft: '4px solid var(--chart-2)' }}
            >
              {/* Attachment previews (multi-file or legacy single) */}
              {(() => {
                const allAttachments = message.attachments && message.attachments.length > 0
                  ? message.attachments
                  : message.attachment
                    ? [message.attachment]
                    : [];
                if (allAttachments.length === 0) return null;
                return (
                  <div className={`mb-2 flex flex-col gap-1.5 ${allAttachments.length > 1 ? '' : ''}`}>
                    {allAttachments.map((att, idx) => (
                      <div key={`${att.filename}-${idx}`}>
                        {att.type === 'image' ? (
                          <img
                            src={att.url}
                            alt={att.originalName || att.filename}
                            className="max-w-full rounded"
                            style={{
                              maxHeight: '280px',
                              border: '2px solid var(--theme-border)',
                              borderRadius: '2px',
                            }}
                            loading="lazy"
                          />
                        ) : (
                          <div
                            className="flex items-center gap-2 px-3 py-2"
                            style={{
                              background: 'var(--theme-bg-tertiary)',
                              border: '2px solid var(--chart-1)',
                              borderRadius: '2px',
                            }}
                          >
                            <FileText className="h-4 w-4 shrink-0" style={{ color: 'var(--chart-1)' }} />
                            <span className="text-[11px] font-bold tracking-wider truncate" style={{ color: 'var(--chart-1)' }}>
                              {att.originalName || att.filename}
                            </span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                );
              })()}
              {message.content && (
                <p className="text-[13px] whitespace-pre-wrap leading-relaxed" style={{ color: 'var(--foreground)' }}>
                  {message.content}
                </p>
              )}
            </div>
            <div className="flex justify-end mt-1.5 pr-1">
              <span className="text-[10px] font-bold tracking-wider" style={{ color: 'var(--chart-2)', opacity: 0.4 }}>
                {formatTime(message.timestamp)}
              </span>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex gap-3.5">
          {/* Nimrod avatar */}
          <div
            className="flex h-7 w-7 shrink-0 items-center justify-center mt-0.5"
            style={{ background: 'var(--theme-bg-primary)', border: '2px solid var(--theme-accent)' }}
          >
            <span className="text-[10px] font-bold" style={{ color: 'var(--theme-accent)' }}>N</span>
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-[11px] font-bold tracking-widest" style={{ color: 'var(--theme-accent)' }}>
                NIMROD
              </span>
              <span className="text-[10px] font-bold tracking-wider" style={{ color: 'var(--theme-border)' }}>
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
