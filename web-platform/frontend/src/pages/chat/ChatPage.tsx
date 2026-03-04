import { useState, useEffect, useRef, useCallback } from 'react';
import { PanelLeftClose, PanelLeft, Square } from 'lucide-react';
import { api } from '../../api/client';
import type { Message, Conversation, AgentActivity, SubagentEvent } from '../../types';
import { ConversationList } from './ConversationList';
import { MessageBubble } from './MessageBubble';
import { ThinkingIndicator } from './ThinkingIndicator';
import { AgentActivityPanel } from './AgentActivityPanel';
import { ChatInput } from './ChatInput';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { Skeleton } from '@/components/ui/skeleton';

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).substring(2, 8);
}

function getStreamingEl(messageId: string): HTMLDivElement | null {
  return document.querySelector(
    `[data-streaming-content="${messageId}"]`
  ) as HTMLDivElement | null;
}

const initialAgentActivity: AgentActivity = {
  agents: new Map(),
  isProcessing: false,
  thinkingMessage: null,
  currentPass: null,
  passStatus: null,
};

export function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isThinking, setIsThinking] = useState(false);
  const [agentActivity, setAgentActivity] = useState<AgentActivity>(initialAgentActivity);
  const [showConversations, setShowConversations] = useState(true);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const cleanupStreamRef = useRef<(() => void) | null>(null);
  const userScrolledUpRef = useRef(false);
  const streamingMsgIdRef = useRef<string | null>(null);
  const streamingTextRef = useRef<string>('');

  const scrollToBottom = useCallback(() => {
    if (userScrolledUpRef.current) return;
    const container = scrollContainerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, []);

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

  useEffect(() => {
    loadConversations();
  }, []);

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)');
    if (mq.matches) {
      setShowConversations(false);
    }
  }, []);

  const loadConversations = async () => {
    setConversationsLoading(true);
    try {
      const data = await api.getConversations();
      setConversations(data);
    } catch {
      setConversations([]);
    } finally {
      setConversationsLoading(false);
    }
  };

  const loadConversation = async (id: string) => {
    try {
      const data = await api.getConversation(id);
      const mapped: Message[] = data.map((m: any, i: number) => ({
        id: `${id}-${i}`,
        role: m.role,
        content: m.content,
        timestamp: m.timestamp || new Date().toISOString(),
        metadata: m.metadata || null,
      }));
      setMessages(mapped);
      setActiveConversationId(id);
    } catch {
      // Silently fail
    }
  };

  const handleNewChat = useCallback(() => {
    if (cleanupStreamRef.current) {
      cleanupStreamRef.current();
      cleanupStreamRef.current = null;
    }
    setMessages([]);
    setActiveConversationId(null);
    setIsThinking(false);
    setAgentActivity(initialAgentActivity);
    streamingMsgIdRef.current = null;
    streamingTextRef.current = '';
  }, []);

  const handleSelectConversation = (id: string) => {
    if (id === activeConversationId) return;
    if (cleanupStreamRef.current) {
      cleanupStreamRef.current();
      cleanupStreamRef.current = null;
    }
    setIsThinking(false);
    setAgentActivity(initialAgentActivity);
    streamingMsgIdRef.current = null;
    streamingTextRef.current = '';
    loadConversation(id);
  };

  const handleStopGenerating = useCallback(() => {
    if (cleanupStreamRef.current) {
      cleanupStreamRef.current();
      cleanupStreamRef.current = null;
    }
    setIsThinking(false);
    setAgentActivity(initialAgentActivity);
    // Finalize streaming message
    if (streamingMsgIdRef.current) {
      const finalText = streamingTextRef.current || '*Generation stopped.*';
      setMessages(prev =>
        prev.map(m =>
          m.id === streamingMsgIdRef.current
            ? { ...m, content: finalText, streaming: false }
            : m
        )
      );
      streamingMsgIdRef.current = null;
    }
  }, []);

  const handleSendMessage = useCallback(
    async (content: string) => {
      userScrolledUpRef.current = false;

      const userMessage: Message = {
        id: generateId(),
        role: 'user',
        content,
        timestamp: new Date().toISOString(),
      };
      setMessages(prev => [...prev, userMessage]);
      setIsThinking(true);
      setAgentActivity({
        agents: new Map(),
        isProcessing: true,
        thinkingMessage: null,
        currentPass: null,
        passStatus: null,
      });

      try {
        const assistantId = generateId();
        streamingTextRef.current = '';

        let scrollRafId = 0;
        const scheduleScroll = () => {
          if (scrollRafId) return;
          scrollRafId = requestAnimationFrame(() => {
            scrollRafId = 0;
            scrollToBottom();
          });
        };

        const cleanup = api.createSSEStream(
          activeConversationId,
          content,
          {
            onThinking: (message: string) => {
              setAgentActivity(prev => ({
                ...prev,
                thinkingMessage: message,
              }));
            },

            onSubagent: (event: SubagentEvent) => {
              if (event.type === 'pass') {
                setAgentActivity(prev => ({
                  ...prev,
                  currentPass: event.pass || null,
                  passStatus: event.status || null,
                }));
                return;
              }

              if (event.type === 'agent_start') {
                setAgentActivity(prev => {
                  const agents = new Map(prev.agents);
                  agents.set(event.agent!, {
                    agent: event.agent!,
                    task: event.task,
                    startTime: Date.now(),
                    completed: false,
                  });
                  return { ...prev, agents };
                });
              } else if (event.type === 'agent_complete') {
                setAgentActivity(prev => {
                  const agents = new Map(prev.agents);
                  const existing = agents.get(event.agent!);
                  if (existing) {
                    agents.set(event.agent!, {
                      ...existing,
                      success: event.success,
                      duration: event.duration,
                      completed: true,
                    });
                  } else {
                    agents.set(event.agent!, {
                      agent: event.agent!,
                      startTime: Date.now(),
                      success: event.success,
                      duration: event.duration,
                      completed: true,
                    });
                  }
                  return { ...prev, agents };
                });
              }
            },

            onChunk: (chunk: string) => {
              // Create assistant message on first chunk if not yet created
              if (!streamingMsgIdRef.current) {
                streamingMsgIdRef.current = assistantId;
                const assistantMessage: Message = {
                  id: assistantId,
                  role: 'assistant',
                  content: '',
                  timestamp: new Date().toISOString(),
                  streaming: true,
                };
                setMessages(prev => [...prev, assistantMessage]);
                setIsThinking(false);
              }

              streamingTextRef.current += chunk;
              const el = getStreamingEl(assistantId);
              if (el && (el as any).__appendDelta) {
                (el as any).__appendDelta(chunk);
              }
              scheduleScroll();
            },

            onComplete: (data) => {
              if (scrollRafId) cancelAnimationFrame(scrollRafId);

              // If we never got chunks, create the message with full text
              if (!streamingMsgIdRef.current && data.text) {
                const assistantMessage: Message = {
                  id: assistantId,
                  role: 'assistant',
                  content: data.text,
                  timestamp: new Date().toISOString(),
                  metadata: {
                    subagents: data.subagents,
                    file_paths: data.file_paths,
                  },
                };
                setMessages(prev => [...prev, assistantMessage]);
              } else {
                const finalText = streamingTextRef.current || data.text;
                setMessages(prev =>
                  prev.map(m =>
                    m.id === assistantId
                      ? {
                          ...m,
                          content: finalText,
                          streaming: false,
                          metadata: {
                            subagents: data.subagents,
                            file_paths: data.file_paths,
                          },
                        }
                      : m
                  )
                );
              }

              // Update conversation ID
              const resolvedId = (callbacks as any)._resolvedConversationId;
              if (resolvedId) {
                setActiveConversationId(resolvedId);
              }

              setIsThinking(false);
              setAgentActivity(prev => ({ ...prev, isProcessing: false }));
              streamingMsgIdRef.current = null;
              cleanupStreamRef.current = null;
              loadConversations();
            },

            onError: (error: string) => {
              if (scrollRafId) cancelAnimationFrame(scrollRafId);
              const errorText = streamingTextRef.current || `**Error:** ${error}`;

              if (streamingMsgIdRef.current) {
                setMessages(prev =>
                  prev.map(m =>
                    m.id === assistantId
                      ? { ...m, content: errorText, streaming: false }
                      : m
                  )
                );
              } else {
                setMessages(prev => [
                  ...prev,
                  {
                    id: assistantId,
                    role: 'assistant' as const,
                    content: errorText,
                    timestamp: new Date().toISOString(),
                  },
                ]);
              }

              setIsThinking(false);
              setAgentActivity(prev => ({ ...prev, isProcessing: false }));
              streamingMsgIdRef.current = null;
              cleanupStreamRef.current = null;
            },
          },
        );

        // Capture callbacks ref for resolvedConversationId
        const callbacks = (cleanup as any);
        cleanupStreamRef.current = cleanup;
      } catch (err) {
        setIsThinking(false);
        setAgentActivity(initialAgentActivity);
        const errorMessage: Message = {
          id: generateId(),
          role: 'assistant',
          content: `**Error:** ${err instanceof Error ? err.message : 'Failed to send message.'}`,
          timestamp: new Date().toISOString(),
        };
        setMessages(prev => [...prev, errorMessage]);
      }
    },
    [activeConversationId, scrollToBottom]
  );

  const suggestedPrompts = [
    "What are today's top priorities?",
    'Show project constraints summary',
    'Any escalations this week?',
    'Summarize the morning report',
  ];

  const isStreaming = !!streamingMsgIdRef.current || isThinking;

  return (
    <div className="flex h-full min-h-0" style={{ height: '100%' }}>
      {/* Conversation sidebar */}
      <div
        className={`border-r border-zinc-800/60 transition-all duration-200 shrink-0 ${
          showConversations ? 'w-60 min-w-[15rem]' : 'w-0 overflow-hidden'
        }`}
      >
        {showConversations && (
          conversationsLoading ? (
            <div className="p-4 space-y-3">
              <Skeleton className="h-8 w-full rounded-md" />
              <div className="space-y-2 pt-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="space-y-1.5 px-1">
                    <Skeleton className="h-3.5 w-3/4" />
                    <Skeleton className="h-3 w-1/2" />
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <ConversationList
              conversations={conversations}
              activeId={activeConversationId}
              onSelect={handleSelectConversation}
              onNewChat={handleNewChat}
            />
          )
        )}
      </div>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Chat header — minimal */}
        <div className="flex items-center gap-3 px-4 h-11 border-b border-zinc-800/60 shrink-0">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="h-3.5" />
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => setShowConversations(!showConversations)}
            title={showConversations ? 'Hide conversations' : 'Show conversations'}
          >
            {showConversations ? (
              <PanelLeftClose className="h-3.5 w-3.5" />
            ) : (
              <PanelLeft className="h-3.5 w-3.5" />
            )}
          </Button>
          <div className="flex items-center gap-1.5">
            <span className="text-[13px] font-medium text-zinc-300">Nimrod</span>
            <span className="text-[10px] text-zinc-600 font-mono">orchestrator</span>
          </div>
        </div>

        {/* Messages area */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto min-h-0"
          data-scroll-container
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehaviorY: 'contain' }}
        >
          <div className="max-w-[700px] mx-auto px-4 py-6 space-y-5">
            {messages.length === 0 && !isThinking ? (
              <div className="flex flex-col items-center justify-center min-h-[60vh] gap-5">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-zinc-800">
                  <span className="text-sm font-semibold text-zinc-400">N</span>
                </div>
                <div className="text-center max-w-sm">
                  <h2 className="text-[15px] font-medium text-zinc-200 mb-1">
                    How can I help?
                  </h2>
                  <p className="text-[13px] text-zinc-600 leading-relaxed">
                    Ask about projects, constraints, schedules, or anything operational.
                  </p>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-md mt-2">
                  {suggestedPrompts.map((suggestion) => (
                    <button
                      key={suggestion}
                      className="px-3 py-2.5 text-[12px] text-zinc-500 hover:text-zinc-300 border border-zinc-800 hover:border-zinc-700 rounded-lg text-left transition-colors"
                      onClick={() => handleSendMessage(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
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

                {isThinking && !agentActivity.thinkingMessage && (
                  <ThinkingIndicator />
                )}

                <div ref={messagesEndRef} />
              </>
            )}
          </div>
        </div>

        {/* Input area */}
        <div className="shrink-0">
          {isStreaming && (
            <div className="flex justify-center pb-2">
              <button
                className="flex items-center gap-1.5 h-7 px-3 text-[11px] text-zinc-500 border border-zinc-800 rounded-lg hover:border-zinc-700 hover:text-zinc-400 transition-colors"
                onClick={handleStopGenerating}
              >
                <Square className="h-2.5 w-2.5 fill-current" />
                Stop
              </button>
            </div>
          )}
          <ChatInput onSend={handleSendMessage} disabled={isThinking} />
        </div>
      </div>
    </div>
  );
}
