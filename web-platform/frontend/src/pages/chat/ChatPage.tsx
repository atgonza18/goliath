import { useState, useEffect, useRef, useCallback } from 'react';
import { PanelLeftClose, PanelLeft, Bot } from 'lucide-react';
import { api } from '../../api/client';
import type { Message, ChatSession } from '../../types';
import { ConversationList } from './ConversationList';
import { MessageBubble } from './MessageBubble';
import { ThinkingIndicator } from './ThinkingIndicator';
import { ChatInput } from './ChatInput';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { Skeleton } from '@/components/ui/skeleton';

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).substring(2, 8);
}

/**
 * Get the streaming content DOM element for a message ID.
 * The MessageBubble component renders a div with data-streaming-content={id}
 * and attaches __appendDelta / __setContent methods to it.
 */
function getStreamingEl(messageId: string): HTMLDivElement | null {
  return document.querySelector(
    `[data-streaming-content="${messageId}"]`
  ) as HTMLDivElement | null;
}

export function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [isThinking, setIsThinking] = useState(false);
  const [showConversations, setShowConversations] = useState(true);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const cleanupStreamRef = useRef<(() => void) | null>(null);
  // Track whether the user has manually scrolled up (to avoid fighting with auto-scroll)
  const userScrolledUpRef = useRef(false);
  // Track the streaming assistant message ID so we can update it via DOM refs
  const streamingMsgIdRef = useRef<string | null>(null);
  // Accumulate raw text during streaming (for final React state sync)
  const streamingTextRef = useRef<string>('');

  // Auto-scroll helper: smoothly scroll to bottom during streaming
  const scrollToBottom = useCallback(() => {
    if (userScrolledUpRef.current) return;
    const container = scrollContainerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, []);

  // Detect when user scrolls up manually
  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    // If the user is within 100px of the bottom, consider them "at bottom"
    const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    userScrolledUpRef.current = !atBottom;
  }, []);

  // Auto-scroll to bottom when messages change, unless user scrolled up
  useEffect(() => {
    if (userScrolledUpRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  // Load sessions on mount
  useEffect(() => {
    loadSessions();
  }, []);

  // Hide sidebar on mobile
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)');
    if (mq.matches) {
      setShowConversations(false);
    }
  }, []);

  const loadSessions = async () => {
    setSessionsLoading(true);
    try {
      const data = await api.getChatSessions();
      setSessions(data);
    } catch {
      setSessions([]);
    } finally {
      setSessionsLoading(false);
    }
  };

  const loadSession = async (id: string) => {
    try {
      const data = await api.getChatSession(id);
      setMessages(data.messages || []);
      setActiveSessionId(id);
    } catch {
      // Silently fail
    }
  };

  const handleNewChat = useCallback(async () => {
    if (cleanupStreamRef.current) {
      cleanupStreamRef.current();
      cleanupStreamRef.current = null;
    }
    setMessages([]);
    setActiveSessionId(null);
    setIsThinking(false);
    streamingMsgIdRef.current = null;
    streamingTextRef.current = '';
  }, []);

  const handleSelectSession = (id: string) => {
    if (id === activeSessionId) return;
    if (cleanupStreamRef.current) {
      cleanupStreamRef.current();
      cleanupStreamRef.current = null;
    }
    setIsThinking(false);
    streamingMsgIdRef.current = null;
    streamingTextRef.current = '';
    loadSession(id);
  };

  const handleDeleteSession = async (id: string) => {
    try {
      await api.deleteChatSession(id);
      if (id === activeSessionId) {
        setMessages([]);
        setActiveSessionId(null);
      }
      loadSessions();
    } catch {
      // Silently fail
    }
  };

  const handleSendMessage = useCallback(
    async (content: string) => {
      // Reset scroll-lock so auto-scroll works for the new response
      userScrolledUpRef.current = false;

      const userMessage: Message = {
        id: generateId(),
        role: 'user',
        content,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMessage]);
      setIsThinking(true);

      try {
        // Use the session ID or let the backend create one
        const sessionId = activeSessionId || generateId();
        if (!activeSessionId) {
          setActiveSessionId(sessionId);
        }

        const assistantId = generateId();

        // Create the assistant message placeholder (React renders it once)
        const assistantMessage: Message = {
          id: assistantId,
          role: 'assistant',
          content: '',
          timestamp: new Date().toISOString(),
          streaming: true,
        };
        streamingMsgIdRef.current = assistantId;
        streamingTextRef.current = '';
        setMessages((prev) => [...prev, assistantMessage]);
        setIsThinking(false);

        // Set up a throttled scroll during streaming (every ~100ms via rAF)
        let scrollRafId = 0;
        const scheduleScroll = () => {
          if (scrollRafId) return;
          scrollRafId = requestAnimationFrame(() => {
            scrollRafId = 0;
            scrollToBottom();
          });
        };

        // Use SSE streaming via fetch+ReadableStream — works reliably through
        // Cloudflare tunnel (WebSocket connections were being dropped by the tunnel)
        const cleanup = api.createSSEStream(
          sessionId,
          content,
          // onDelta: append just the new text fragment via DOM ref (no React re-render)
          (delta: string) => {
            streamingTextRef.current += delta;
            const el = getStreamingEl(assistantId);
            if (el && (el as any).__appendDelta) {
              (el as any).__appendDelta(delta);
            }
            scheduleScroll();
          },
          // onDone: sync final text to React state (single re-render)
          (resolvedSessionId?: string) => {
            if (scrollRafId) cancelAnimationFrame(scrollRafId);
            // Update session ID if the backend assigned one
            if (resolvedSessionId) {
              setActiveSessionId(resolvedSessionId);
            }
            const finalText = streamingTextRef.current;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: finalText, streaming: false }
                  : m
              )
            );
            streamingMsgIdRef.current = null;
            cleanupStreamRef.current = null;
            loadSessions();
          },
          // onError
          (error: Error) => {
            if (scrollRafId) cancelAnimationFrame(scrollRafId);
            const errorText = streamingTextRef.current || `**Error:** ${error.message}`;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: errorText, streaming: false }
                  : m
              )
            );
            streamingMsgIdRef.current = null;
            cleanupStreamRef.current = null;
          },
        );
        cleanupStreamRef.current = cleanup;
      } catch (err) {
        setIsThinking(false);
        const errorMessage: Message = {
          id: generateId(),
          role: 'assistant',
          content: `**Error:** ${err instanceof Error ? err.message : 'Failed to send message. Please check your connection and try again.'}`,
          timestamp: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, errorMessage]);
      }
    },
    [activeSessionId, scrollToBottom]
  );

  const suggestedPrompts = [
    "What are today's top priorities?",
    'Show project constraints summary',
    'Any escalations this week?',
    'Summarize the morning report',
  ];

  // Convert sessions to the format ConversationList expects
  const conversationItems = sessions.map((s) => ({
    id: s.id,
    title: s.title || 'New Chat',
    lastMessage: s.lastMessage || '',
    timestamp: s.updatedAt || s.createdAt,
    messageCount: s.messageCount || 0,
  }));

  return (
    <div className="flex h-full min-h-0" style={{ height: '100%' }}>
      {/* Conversation sidebar */}
      <div
        className={`border-r border-border bg-card/50 transition-all duration-200 shrink-0 ${
          showConversations ? 'w-64 min-w-[16rem]' : 'w-0 overflow-hidden'
        }`}
      >
        {showConversations && (
          sessionsLoading ? (
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
              conversations={conversationItems}
              activeId={activeSessionId}
              onSelect={handleSelectSession}
              onNewChat={handleNewChat}
              onDelete={handleDeleteSession}
            />
          )
        )}
      </div>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Chat header */}
        <div className="flex items-center gap-3 px-4 h-12 border-b border-border shrink-0">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="h-4" />
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => setShowConversations(!showConversations)}
            title={showConversations ? 'Hide conversations' : 'Show conversations'}
          >
            {showConversations ? (
              <PanelLeftClose className="h-4 w-4" />
            ) : (
              <PanelLeft className="h-4 w-4" />
            )}
          </Button>
          <div className="flex items-center gap-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-card">
              <Bot className="h-3 w-3 text-muted-foreground" />
            </div>
            <span className="text-sm font-semibold text-foreground">Nimrod</span>
            <span className="text-[10px] text-muted-foreground font-mono">Claude CLI</span>
          </div>
        </div>

        {/* Messages area — scrollable container */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto min-h-0"
          data-scroll-container
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehaviorY: 'contain' }}
        >
          <div className="max-w-[720px] mx-auto px-4 py-6 space-y-6">
            {messages.length === 0 && !isThinking ? (
              <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-card">
                  <Bot className="h-7 w-7 text-muted-foreground" />
                </div>
                <div className="text-center max-w-md">
                  <h2 className="text-base font-semibold text-foreground mb-1.5">
                    How can I help?
                  </h2>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    Ask about project statuses, constraints, schedules, or any operational question.
                    Powered by Claude CLI with full access to Goliath project data.
                  </p>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-md">
                  {suggestedPrompts.map((suggestion) => (
                    <Button
                      key={suggestion}
                      variant="outline"
                      className="h-auto px-3 py-2.5 text-xs text-muted-foreground hover:text-foreground justify-start whitespace-normal text-left"
                      onClick={() => handleSendMessage(suggestion)}
                    >
                      {suggestion}
                    </Button>
                  ))}
                </div>
              </div>
            ) : (
              <>
                {messages.map((msg) => (
                  <MessageBubble key={msg.id} message={msg} />
                ))}
                {isThinking && <ThinkingIndicator />}
                <div ref={messagesEndRef} />
              </>
            )}
          </div>
        </div>

        {/* Input */}
        <ChatInput onSend={handleSendMessage} disabled={isThinking} />
      </div>
    </div>
  );
}
