import { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { api } from '../../api/client';
import type { Conversation, StreamState, StreamMutableData, Message, MessageAttachment, SubagentEvent } from '../../types';

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).substring(2, 8);
}

interface ChatContextValue {
  // Conversation list
  conversations: Conversation[];
  activeConversationId: string | null;
  conversationsLoading: boolean;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  setActiveConversationId: (id: string | null) => void;
  refreshConversations: () => void;

  // Stream management
  streams: Record<string, StreamState>;
  sendMessage: (convId: string | null, content: string, files?: File[]) => string;
  stopGenerating: (convId: string) => void;
  getStreamState: (convId: string) => StreamState | null;
  getStreamData: (convId: string) => StreamMutableData | null;
  hasActiveStreams: boolean;
  activeStreamIds: string[];
}

const ChatContext = createContext<ChatContextValue | null>(null);

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [streams, setStreams] = useState<Record<string, StreamState>>({});

  // Mutable per-stream data — high-frequency writes that must NOT trigger re-renders
  const streamDataRef = useRef<Record<string, StreamMutableData>>({});

  // Maps temp conversation IDs to real IDs returned by backend
  const idRemapRef = useRef<Record<string, string>>({});

  // Resolve a convId through the remap table
  const findConvId = useCallback((tempId: string): string => {
    return idRemapRef.current[tempId] || tempId;
  }, []);

  // ---- Conversation list management ----

  const loadConversations = useCallback(async () => {
    setConversationsLoading(true);
    try {
      const data = await api.getConversations();
      setConversations(data);
    } catch {
      setConversations([]);
    } finally {
      setConversationsLoading(false);
    }
  }, []);

  useEffect(() => { loadConversations(); }, [loadConversations]);

  const handleNewChat = useCallback(() => {
    setActiveConversationId(null);
  }, []);

  const handleSelectConversation = useCallback((id: string) => {
    setActiveConversationId(id);
  }, []);

  // ---- Stream state helpers ----

  // ---- sendMessage ----

  const sendMessage = useCallback((convId: string | null, content: string, files?: File[]): string => {
    // Generate a temp ID for new conversations
    const tempId = convId || `temp-${generateId()}`;
    const assistantId = generateId();

    // Build attachment metadata for the user message (for display)
    let attachments: MessageAttachment[] | undefined;
    if (files && files.length > 0) {
      attachments = files.map((file) => {
        const isImage = file.type.startsWith('image/');
        return {
          type: isImage ? 'image' : 'pdf' as 'image' | 'pdf',
          filename: file.name,
          originalName: file.name,
          url: URL.createObjectURL(file), // local preview URL
          mimeType: file.type,
        };
      });
    }

    // Initialize stream state
    const userMessage: Message = {
      id: generateId(),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
      attachment: attachments?.[0] || null,       // backward compat
      attachments: attachments || null,            // multi-file
    };

    const newStreamState: StreamState = {
      conversationId: tempId,
      status: 'streaming',
      messages: [userMessage],
      isThinking: true,
      agentActivity: { agents: new Map(), isProcessing: true, thinkingMessage: null, currentPass: null, passStatus: null },
      error: null,
    };

    // If we're adding to an existing conversation, load its messages first
    if (convId) {
      setStreams(prev => {
        const existing = prev[convId];
        if (existing) {
          return {
            ...prev,
            [convId]: {
              ...existing,
              status: 'streaming',
              messages: [...existing.messages, userMessage],
              isThinking: true,
              agentActivity: { agents: new Map(), isProcessing: true, thinkingMessage: null, currentPass: null, passStatus: null },
              error: null,
            },
          };
        }
        return { ...prev, [convId]: newStreamState };
      });
    } else {
      setStreams(prev => ({ ...prev, [tempId]: newStreamState }));
    }

    // Initialize mutable data
    streamDataRef.current[tempId] = {
      streamingMsgId: null,
      streamingText: '',
      cleanupStream: null,
      scrollRafId: 0,
    };

    // Create the SSE stream
    const cleanup = api.createSSEStream(convId, content, {
      onThinking: (message: string) => {
        const resolvedId = findConvId(tempId);
        setStreams(prev => {
          const s = prev[resolvedId];
          if (!s) return prev;
          return { ...prev, [resolvedId]: { ...s, agentActivity: { ...s.agentActivity, thinkingMessage: message } } };
        });
      },

      onAttachmentUrl: (att) => {
        // Replace blob: URLs in the user message with permanent server URLs
        // att can be a single attachment or an array of attachments
        const attArray = Array.isArray(att) ? att : [att];
        const resolvedId = findConvId(tempId);
        setStreams(prev => {
          const s = prev[resolvedId];
          if (!s) return prev;
          const updatedMessages = s.messages.map(m => {
            if (m.role === 'user' && (m.attachments || m.attachment)) {
              // Update attachments array
              if (m.attachments && m.attachments.length > 0) {
                const updatedAttachments = m.attachments.map((existing, idx) => {
                  const serverAtt = attArray[idx];
                  if (serverAtt && existing.url.startsWith('blob:')) {
                    URL.revokeObjectURL(existing.url);
                    return { ...existing, url: serverAtt.url, filename: serverAtt.filename };
                  }
                  return existing;
                });
                return {
                  ...m,
                  attachments: updatedAttachments,
                  attachment: updatedAttachments[0] || null, // keep backward compat
                };
              }
              // Legacy single attachment fallback
              if (m.attachment && attArray[0]) {
                if (m.attachment.url.startsWith('blob:')) {
                  URL.revokeObjectURL(m.attachment.url);
                }
                return {
                  ...m,
                  attachment: { ...m.attachment, url: attArray[0].url, filename: attArray[0].filename },
                };
              }
            }
            return m;
          });
          return { ...prev, [resolvedId]: { ...s, messages: updatedMessages } };
        });
      },

      onSubagent: (event: SubagentEvent) => {
        const resolvedId = findConvId(tempId);
        if (event.type === 'pass') {
          setStreams(prev => {
            const s = prev[resolvedId];
            if (!s) return prev;
            return { ...prev, [resolvedId]: { ...s, agentActivity: { ...s.agentActivity, currentPass: event.pass || null, passStatus: event.status || null } } };
          });
          return;
        }
        if (event.type === 'agent_start') {
          setStreams(prev => {
            const s = prev[resolvedId];
            if (!s) return prev;
            const agents = new Map(s.agentActivity.agents);
            agents.set(event.agent!, { agent: event.agent!, task: event.task, startTime: Date.now(), completed: false, tools: [] });
            return { ...prev, [resolvedId]: { ...s, agentActivity: { ...s.agentActivity, agents } } };
          });
        } else if (event.type === 'agent_complete') {
          setStreams(prev => {
            const s = prev[resolvedId];
            if (!s) return prev;
            const agents = new Map(s.agentActivity.agents);
            const existing = agents.get(event.agent!);
            agents.set(event.agent!, { ...(existing || { agent: event.agent!, startTime: Date.now(), tools: [] }), success: event.success, duration: event.duration, completed: true });
            return { ...prev, [resolvedId]: { ...s, agentActivity: { ...s.agentActivity, agents } } };
          });
        } else if (event.type === 'tool_start' && event.agent) {
          setStreams(prev => {
            const s = prev[resolvedId];
            if (!s) return prev;
            const agents = new Map(s.agentActivity.agents);
            const existing = agents.get(event.agent!);
            if (existing) {
              const tools = [...existing.tools, { tool: event.tool || 'unknown', inputPreview: event.inputPreview || event.input_preview, startTime: Date.now(), completed: false }];
              agents.set(event.agent!, { ...existing, tools });
            }
            return { ...prev, [resolvedId]: { ...s, agentActivity: { ...s.agentActivity, agents } } };
          });
        } else if (event.type === 'tool_done' && event.agent) {
          setStreams(prev => {
            const s = prev[resolvedId];
            if (!s) return prev;
            const agents = new Map(s.agentActivity.agents);
            const existing = agents.get(event.agent!);
            if (existing) {
              const tools = [...existing.tools];
              for (let i = tools.length - 1; i >= 0; i--) {
                if (tools[i].tool === (event.tool || 'unknown') && !tools[i].completed) {
                  tools[i] = { ...tools[i], completed: true };
                  break;
                }
              }
              agents.set(event.agent!, { ...existing, tools });
            }
            return { ...prev, [resolvedId]: { ...s, agentActivity: { ...s.agentActivity, agents } } };
          });
        }
      },

      onChunk: (chunk: string) => {
        const resolvedId = findConvId(tempId);
        const data = streamDataRef.current[resolvedId] || streamDataRef.current[tempId];
        if (!data) return;

        if (!data.streamingMsgId) {
          data.streamingMsgId = assistantId;
          // Add assistant message placeholder — this is the one React state update from chunks
          setStreams(prev => {
            const s = prev[resolvedId];
            if (!s) return prev;
            return {
              ...prev,
              [resolvedId]: {
                ...s,
                isThinking: false,
                messages: [...s.messages, { id: assistantId, role: 'assistant', content: '', timestamp: new Date().toISOString(), streaming: true }],
              },
            };
          });
        }

        data.streamingText += chunk;

        // Direct DOM update — no React re-render
        const el = document.querySelector(`[data-streaming-content="${assistantId}"]`) as any;
        if (el?.__appendDelta) el.__appendDelta(chunk);
      },

      onComplete: (data) => {
        const resolvedId = findConvId(tempId);
        const mutData = streamDataRef.current[resolvedId] || streamDataRef.current[tempId];

        if (mutData?.scrollRafId) cancelAnimationFrame(mutData.scrollRafId);

        // Remap if backend returned a real conversation_id
        if (data.conversation_id && data.conversation_id !== tempId) {
          idRemapRef.current[tempId] = data.conversation_id;
          // Move mutable data to new key
          if (streamDataRef.current[tempId]) {
            streamDataRef.current[data.conversation_id] = streamDataRef.current[tempId];
            delete streamDataRef.current[tempId];
          }
        }

        const realConvId = data.conversation_id || resolvedId;

        setStreams(prev => {
          const s = prev[resolvedId] || prev[tempId];
          if (!s) return prev;

          let finalMessages: Message[];
          if (!mutData?.streamingMsgId && data.text) {
            // No chunks were received — add complete message directly
            finalMessages = [...s.messages, { id: assistantId, role: 'assistant', content: data.text, timestamp: new Date().toISOString(), metadata: { subagents: data.subagents, file_paths: data.file_paths } }];
          } else {
            const finalText = mutData?.streamingText || data.text;
            finalMessages = s.messages.map(m =>
              m.id === assistantId ? { ...m, content: finalText, streaming: false, metadata: { subagents: data.subagents, file_paths: data.file_paths } } : m
            );
          }

          // Remove old temp key, set under real convId
          const { [tempId]: _removed, [resolvedId]: _also, ...rest } = prev;
          return {
            ...rest,
            [realConvId]: {
              ...s,
              conversationId: realConvId,
              status: 'complete',
              messages: finalMessages,
              isThinking: false,
              agentActivity: { ...s.agentActivity, isProcessing: false },
              error: null,
            },
          };
        });

        // Update active conversation ID if this was the active one
        if (data.conversation_id) {
          setActiveConversationId(prev => {
            if (prev === tempId || prev === null) return data.conversation_id!;
            return prev;
          });
        }

        // Clean up mutable data
        if (mutData) {
          mutData.streamingMsgId = null;
          mutData.cleanupStream = null;
        }

        loadConversations();
      },

      onError: (error: string) => {
        const resolvedId = findConvId(tempId);
        const mutData = streamDataRef.current[resolvedId] || streamDataRef.current[tempId];

        if (mutData?.scrollRafId) cancelAnimationFrame(mutData.scrollRafId);

        // Detect connection loss (network error, not a server error)
        const isConnectionLoss = /fetch|network|abort|failed to send/i.test(error);

        if (isConnectionLoss && resolvedId && !resolvedId.startsWith('temp-')) {
          // Connection lost — the agent likely continued on the server.
          // Try to reload the conversation from the server after a short delay.
          const recoveryMsg = '**Connection lost.** The agent may still be working on the server. Reloading...';

          setStreams(prev => {
            const s = prev[resolvedId];
            if (!s) return prev;
            const partialText = mutData?.streamingText || '';
            let finalMessages: Message[];
            if (mutData?.streamingMsgId) {
              finalMessages = s.messages.map(m =>
                m.id === assistantId ? { ...m, content: partialText || recoveryMsg, streaming: false } : m
              );
            } else {
              finalMessages = [...s.messages, { id: assistantId, role: 'assistant', content: recoveryMsg, timestamp: new Date().toISOString() }];
            }
            return {
              ...prev,
              [resolvedId]: { ...s, status: 'error', messages: finalMessages, isThinking: false, agentActivity: { ...s.agentActivity, isProcessing: false }, error: 'Connection lost — reloading' },
            };
          });

          // Auto-reload conversation from server after 3 seconds
          setTimeout(async () => {
            try {
              const data = await api.getConversation(resolvedId);
              const mapped: Message[] = data.map((m: any, i: number) => ({
                id: `${resolvedId}-${i}`,
                role: m.role,
                content: m.content,
                timestamp: m.timestamp || new Date().toISOString(),
                metadata: m.metadata || null,
              }));
              setStreams(prev => {
                const s = prev[resolvedId];
                if (!s) return prev;
                return { ...prev, [resolvedId]: { ...s, status: 'complete', messages: mapped, error: null } };
              });
            } catch {
              // Server not reachable yet — user can manually reload
              setStreams(prev => {
                const s = prev[resolvedId];
                if (!s) return prev;
                return { ...prev, [resolvedId]: { ...s, error: 'Connection lost. Refresh the page to see if the agent completed.' } };
              });
            }
          }, 3000);
        } else {
          // Regular server error — show as before
          setStreams(prev => {
            const s = prev[resolvedId] || prev[tempId];
            if (!s) return prev;

            const errorText = mutData?.streamingText || `**Error:** ${error}`;
            let finalMessages: Message[];
            if (mutData?.streamingMsgId) {
              finalMessages = s.messages.map(m =>
                m.id === assistantId ? { ...m, content: errorText, streaming: false } : m
              );
            } else {
              finalMessages = [...s.messages, { id: assistantId, role: 'assistant', content: errorText, timestamp: new Date().toISOString() }];
            }

            return {
              ...prev,
              [resolvedId]: {
                ...s,
                status: 'error',
                messages: finalMessages,
                isThinking: false,
                agentActivity: { ...s.agentActivity, isProcessing: false },
                error,
              },
            };
          });
        }

        if (mutData) {
          mutData.streamingMsgId = null;
          mutData.cleanupStream = null;
        }
      },
    }, files);

    streamDataRef.current[tempId].cleanupStream = cleanup;

    // Set active conversation to this temp ID if it's a new chat
    if (!convId) {
      setActiveConversationId(tempId);
    }

    return tempId;
  }, [findConvId, loadConversations]);

  // ---- stopGenerating ----

  const stopGenerating = useCallback((convId: string) => {
    const resolvedId = findConvId(convId);
    const data = streamDataRef.current[resolvedId] || streamDataRef.current[convId];
    if (data?.cleanupStream) {
      data.cleanupStream();
      data.cleanupStream = null;
    }

    setStreams(prev => {
      const s = prev[resolvedId];
      if (!s) return prev;

      let finalMessages = s.messages;
      if (data?.streamingMsgId) {
        const finalText = data.streamingText || '*Generation stopped.*';
        finalMessages = s.messages.map(m =>
          m.id === data.streamingMsgId ? { ...m, content: finalText, streaming: false } : m
        );
      }

      return {
        ...prev,
        [resolvedId]: {
          ...s,
          status: 'complete',
          messages: finalMessages,
          isThinking: false,
          agentActivity: { ...s.agentActivity, isProcessing: false },
        },
      };
    });

    if (data) {
      data.streamingMsgId = null;
    }
  }, [findConvId]);

  // ---- getStreamState ----

  const getStreamState = useCallback((convId: string): StreamState | null => {
    const resolvedId = findConvId(convId);
    return streams[resolvedId] || streams[convId] || null;
  }, [streams, findConvId]);

  const getStreamData = useCallback((convId: string): StreamMutableData | null => {
    const resolvedId = findConvId(convId);
    return streamDataRef.current[resolvedId] || streamDataRef.current[convId] || null;
  }, [findConvId]);

  // ---- Derived state ----

  const activeStreamIds = useMemo(() => {
    return Object.keys(streams).filter(id => streams[id].status === 'streaming');
  }, [streams]);

  const hasActiveStreams = activeStreamIds.length > 0;

  // ---- Cleanup on unmount ----

  useEffect(() => {
    return () => {
      Object.values(streamDataRef.current).forEach(data => {
        if (data.cleanupStream) data.cleanupStream();
      });
    };
  }, []);

  return (
    <ChatContext.Provider value={{
      conversations,
      activeConversationId,
      conversationsLoading,
      onSelectConversation: handleSelectConversation,
      onNewChat: handleNewChat,
      setActiveConversationId,
      refreshConversations: loadConversations,
      streams,
      sendMessage,
      stopGenerating,
      getStreamState,
      getStreamData,
      hasActiveStreams,
      activeStreamIds,
    }}>
      {children}
    </ChatContext.Provider>
  );
}

export function useChatContext() {
  return useContext(ChatContext);
}
