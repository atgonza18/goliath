import { useState, useEffect, useRef, useCallback } from 'react';
import { Square } from 'lucide-react';
import { api } from '../../api/client';
import type { Message, AgentActivity } from '../../types';
import { MessageBubble } from './MessageBubble';
import { ThinkingIndicator } from './ThinkingIndicator';
import { AgentActivityPanel } from './AgentActivityPanel';
import { ChatInput } from './ChatInput';
import { ChatTabBar } from './ChatTabBar';
import { useChatContext } from './ChatContext';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { SwarmBadge } from '@/components/SwarmIndicator';

const initialAgentActivity: AgentActivity = {
  agents: new Map(),
  isProcessing: false,
  thinkingMessage: null,
  currentPass: null,
  passStatus: null,
};

// Colors for the prompt cards — use chart CSS vars so they follow the theme
const cardColors = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-5)',
  'var(--chart-3)',
];

export function ChatPage() {
  const ctx = useChatContext();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);
  const prevConversationIdRef = useRef<string | null>(null);
  const [loadedMessages, setLoadedMessages] = useState<Message[]>([]);

  const activeConversationId = ctx?.activeConversationId ?? null;

  // Derive display state from context streams
  const streamState = activeConversationId ? ctx?.getStreamState(activeConversationId) ?? null : null;
  const messages = streamState?.messages ?? loadedMessages;
  const isThinking = streamState?.isThinking ?? false;
  const agentActivity = streamState?.agentActivity ?? initialAgentActivity;
  const isStreaming = streamState?.status === 'streaming';

  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    userScrolledUpRef.current = !atBottom;
  }, []);

  useEffect(() => {
    if (userScrolledUpRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  // Load conversation messages when switching to a conversation that has no stream state
  useEffect(() => {
    if (activeConversationId === prevConversationIdRef.current) return;
    prevConversationIdRef.current = activeConversationId;

    if (!activeConversationId) {
      setLoadedMessages([]);
      return;
    }

    // If there's already a stream state (active or completed stream), use that
    const existing = ctx?.getStreamState(activeConversationId);
    if (existing) {
      setLoadedMessages([]);
      return;
    }

    // Load conversation from server
    const load = async () => {
      try {
        const data = await api.getConversation(activeConversationId);
        const mapped: Message[] = data.map((m: any, i: number) => ({
          id: `${activeConversationId}-${i}`,
          role: m.role,
          content: m.content,
          timestamp: m.timestamp || new Date().toISOString(),
          metadata: m.metadata || null,
        }));
        setLoadedMessages(mapped);
      } catch { /* Silently fail */ }
    };
    load();
  }, [activeConversationId, ctx]);

  // Resume streaming display when navigating back to an active stream
  useEffect(() => {
    if (!activeConversationId || !ctx) return;
    const state = ctx.getStreamState(activeConversationId);
    if (state?.status !== 'streaming') return;

    const mutData = ctx.getStreamData(activeConversationId);
    if (!mutData?.streamingMsgId || !mutData.streamingText) return;

    // Give StreamingContent a tick to mount, then flush accumulated text
    requestAnimationFrame(() => {
      const el = document.querySelector(`[data-streaming-content="${mutData.streamingMsgId}"]`) as any;
      if (el?.__setContent) el.__setContent(mutData.streamingText);
    });
  }, [activeConversationId, ctx]);

  const handleSendMessage = useCallback((content: string, file?: File) => {
    if (!ctx) return;
    userScrolledUpRef.current = false;
    ctx.sendMessage(activeConversationId, content, file);
  }, [ctx, activeConversationId]);

  const handleStopGenerating = useCallback(() => {
    if (!ctx || !activeConversationId) return;
    ctx.stopGenerating(activeConversationId);
  }, [ctx, activeConversationId]);

  return (
    <div className="flex h-full min-h-0" style={{ height: '100%', background: 'var(--theme-bg-primary)' }}>
      <div className="flex-1 flex flex-col min-w-0 min-h-0" style={{ background: 'var(--theme-bg-primary)' }}>
        {/* Top bar */}
        <div className="flex items-center justify-between px-4 h-12 shrink-0">
          <div className="flex items-center gap-2">
            <SidebarTrigger className="-ml-1" style={{ color: 'var(--theme-text-dim)' }} />
          </div>
          <div className="flex items-center gap-3">
            <SwarmBadge />
            <span
              className="text-[11px] font-bold select-none"
              style={{ color: 'var(--theme-border)', letterSpacing: '0.15em' }}
            >
              NIMROD
            </span>
            <div className="w-1.5 h-1.5" style={{ background: 'var(--theme-accent)' }} />
          </div>
        </div>

        {/* Tab bar for concurrent chats */}
        <ChatTabBar />

        {/* Messages */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto min-h-0"
          data-scroll-container
        >
          <div className="max-w-[720px] mx-auto px-5">
            {messages.length === 0 && !isThinking ? (
              <div className="flex flex-col items-center justify-center min-h-[70vh] animate-fade-in">
                <div className="mb-8">
                  <div
                    className="h-20 w-20 flex items-center justify-center mx-auto"
                    style={{ border: '3px solid var(--theme-accent)', background: 'var(--theme-bg-primary)' }}
                  >
                    <span className="text-3xl font-bold" style={{ color: 'var(--theme-accent)' }}>G</span>
                  </div>
                </div>
                <h1
                  className="text-lg font-bold mb-2"
                  style={{ color: 'var(--theme-text-primary)', letterSpacing: '0.12em' }}
                >
                  WHAT CAN I HELP WITH?
                </h1>
                <p className="text-[11px] mb-12 max-w-sm text-center" style={{ color: 'var(--theme-text-dim)', lineHeight: '1.6', letterSpacing: '0.05em' }}>
                  12 SPECIALIZED AGENTS &middot; YOUR SOLAR PORTFOLIO
                </p>
                <div className="grid grid-cols-2 gap-3 w-full max-w-lg">
                  {[
                    { text: "What are today's top priorities?", num: '01' },
                    { text: 'Show project constraints summary', num: '02' },
                    { text: 'Any escalations this week?', num: '03' },
                    { text: 'Summarize the morning report', num: '04' },
                  ].map((item, i) => (
                    <button
                      key={item.text}
                      className="group relative text-left px-4 py-4 transition-all duration-100"
                      style={{
                        border: `2px solid var(--theme-border-subtle)`,
                        background: 'var(--theme-bg-primary)',
                      }}
                      onClick={() => handleSendMessage(item.text)}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.borderColor = cardColors[i];
                        e.currentTarget.style.borderLeftWidth = '4px';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.borderColor = 'var(--theme-border-subtle)';
                        e.currentTarget.style.borderLeftWidth = '2px';
                      }}
                    >
                      <span
                        className="text-[10px] font-bold block mb-1.5"
                        style={{ color: cardColors[i], letterSpacing: '0.15em' }}
                      >
                        {item.num}
                      </span>
                      <span className="text-[12px] leading-snug block" style={{ color: 'var(--theme-text-muted)' }}>
                        {item.text}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="py-8 space-y-6">
                {messages.map((msg) => (
                  <MessageBubble key={msg.id} message={msg} />
                ))}

                {(agentActivity.isProcessing || agentActivity.agents.size > 0) && (
                  <AgentActivityPanel
                    agents={agentActivity.agents}
                    isProcessing={agentActivity.isProcessing}
                    currentPass={agentActivity.currentPass}
                    passStatus={agentActivity.passStatus}
                    thinkingMessage={agentActivity.thinkingMessage}
                  />
                )}

                {isThinking && !agentActivity.thinkingMessage && <ThinkingIndicator />}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>
        </div>

        {/* Bottom input area */}
        <div className="shrink-0 relative">
          {isStreaming && (
            <div className="absolute -top-10 left-1/2 -translate-x-1/2 z-10">
              <button
                className="flex items-center gap-2 h-8 px-4 text-[11px] font-bold tracking-wider transition-all duration-100"
                style={{
                  color: '#fff',
                  background: 'var(--destructive)',
                  border: '2px solid var(--destructive)',
                }}
                onClick={handleStopGenerating}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'var(--theme-bg-primary)';
                  e.currentTarget.style.color = 'var(--destructive)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'var(--destructive)';
                  e.currentTarget.style.color = '#fff';
                }}
              >
                <Square className="h-2.5 w-2.5 fill-current" />
                STOP
              </button>
            </div>
          )}
          <ChatInput onSend={handleSendMessage} disabled={isThinking} />
        </div>
      </div>
    </div>
  );
}
