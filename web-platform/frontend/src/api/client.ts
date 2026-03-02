import type {
  ActionItem,
  Agent,
  ChatResponse,
  ChatSession,
  ChatSessionDetail,
  ChatMessageResponse,
  ConstraintStats,
  Conversation,
  ConvexConstraint,
  ConvexConstraintDetail,
  FileItem,
  HealthResponse,
  Message,
  Project,
  ProjectDetail,
  UploadResult,
} from '../types';

class ApiClient {
  private baseUrl = '/api';

  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const response = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    });

    if (!response.ok) {
      const errorBody = await response.text().catch(() => 'Unknown error');
      throw new Error(`API Error ${response.status}: ${errorBody}`);
    }

    return response.json() as Promise<T>;
  }

  // Health
  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health');
  }

  // ---- Chat Sessions (Claude CLI-backed) ----

  async createChatSession(): Promise<ChatSession> {
    return this.request<ChatSession>('/chat/sessions', {
      method: 'POST',
    });
  }

  async getChatSessions(): Promise<ChatSession[]> {
    return this.request<ChatSession[]>('/chat/sessions');
  }

  async getChatSession(id: string): Promise<ChatSessionDetail> {
    return this.request<ChatSessionDetail>(`/chat/sessions/${id}`);
  }

  async deleteChatSession(id: string): Promise<{ success: boolean }> {
    return this.request<{ success: boolean }>(`/chat/sessions/${id}`, {
      method: 'DELETE',
    });
  }

  async sendChatMessage(
    sessionId: string,
    message: string
  ): Promise<ChatMessageResponse> {
    return this.request<ChatMessageResponse>('/chat/message', {
      method: 'POST',
      body: JSON.stringify({ sessionId, message }),
    });
  }

  /**
   * SSE-based streaming using fetch() + ReadableStream.
   *
   * Flow:
   *   1. POST /api/chat/message → get streamUrl
   *   2. GET streamUrl with fetch() → ReadableStream of SSE events
   *   3. Parse SSE events and call onDelta for each text delta
   *
   * Uses fetch+ReadableStream instead of EventSource because:
   *   - EventSource has browser-level buffering that delays SSE events
   *   - ReadableStream gives byte-level control — no browser buffering
   *   - Works reliably through Cloudflare tunnel (WebSocket drops connections)
   *
   * Returns a cleanup function to abort the stream.
   */
  createSSEStream(
    sessionId: string,
    message: string,
    onDelta: (delta: string) => void,
    onDone: (resolvedSessionId?: string) => void,
    onError: (error: Error) => void,
  ): () => void {
    const abortController = new AbortController();
    let done = false;

    const run = async () => {
      try {
        // Step 1: POST the message to get the stream URL
        const postResponse = await fetch(`${this.baseUrl}/chat/message`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId, message }),
          signal: abortController.signal,
        });

        if (!postResponse.ok) {
          const errText = await postResponse.text().catch(() => 'Unknown error');
          throw new Error(`Failed to send message: ${errText}`);
        }

        const { sessionId: resolvedSessionId, streamUrl } = await postResponse.json();

        // Step 2: Connect to the SSE stream using fetch + ReadableStream
        const sseResponse = await fetch(streamUrl, {
          signal: abortController.signal,
          headers: {
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
          },
        });

        if (!sseResponse.ok || !sseResponse.body) {
          throw new Error(`SSE connection failed: ${sseResponse.status}`);
        }

        // Step 3: Read the stream byte-by-byte and parse SSE events
        const reader = sseResponse.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done: streamDone, value } = await reader.read();
          if (streamDone) break;

          buffer += decoder.decode(value, { stream: true });

          // Process complete SSE events (double newline separated)
          const parts = buffer.split('\n');
          buffer = parts.pop() || '';

          for (const line of parts) {
            // Skip SSE comments (heartbeats, padding)
            if (line.startsWith(':') || !line.trim()) continue;

            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6).trim();

            // Check for stream end marker
            if (data === '[DONE]') {
              done = true;
              onDone(resolvedSessionId);
              return;
            }

            try {
              const event = JSON.parse(data);
              if (event.type === 'delta' && event.text) {
                onDelta(event.text);
              } else if (event.type === 'snapshot' && event.text) {
                // Full snapshot (for late-joining clients) — treat as one big delta
                onDelta(event.text);
              } else if (event.type === 'error' && event.text) {
                done = true;
                onError(new Error(event.text));
                return;
              }
            } catch {
              // Non-JSON SSE data — skip
            }
          }
        }

        // Stream ended without explicit [DONE]
        if (!done) {
          done = true;
          onDone(resolvedSessionId);
        }
      } catch (err) {
        if (done) return;
        if ((err as Error).name === 'AbortError') return;
        done = true;
        onError(err instanceof Error ? err : new Error(String(err)));
      }
    };

    run();

    // Return cleanup function
    return () => {
      if (!done) {
        done = true;
        abortController.abort();
      }
    };
  }

  // ---- Legacy chat (backward compat) ----

  async sendMessage(
    message: string,
    conversationId?: string
  ): Promise<ChatResponse> {
    return this.request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({ message, conversationId }),
    });
  }

  // Conversations
  async getConversations(): Promise<Conversation[]> {
    return this.request<Conversation[]>('/conversations');
  }

  async getConversation(id: string): Promise<Message[]> {
    return this.request<Message[]>(`/conversations/${id}`);
  }

  // Projects
  async getProjects(): Promise<Project[]> {
    return this.request<Project[]>('/projects');
  }

  async getProject(key: string): Promise<ProjectDetail> {
    return this.request<ProjectDetail>(`/projects/${key}`);
  }

  // Action Items
  async getActionItems(project?: string): Promise<ActionItem[]> {
    const query = project ? `?project=${encodeURIComponent(project)}` : '';
    return this.request<ActionItem[]>(`/action-items${query}`);
  }

  async resolveActionItem(id: string): Promise<{ success: boolean }> {
    return this.request<{ success: boolean }>(
      `/action-items/${id}/resolve`,
      { method: 'POST' }
    );
  }

  // Agents
  async getAgents(): Promise<Agent[]> {
    return this.request<Agent[]>('/agents');
  }

  // Files
  async getFiles(path?: string): Promise<FileItem[]> {
    const query = path ? `?path=${encodeURIComponent(path)}` : '';
    return this.request<FileItem[]>(`/files${query}`);
  }

  async downloadFile(filePath: string): Promise<void> {
    const url = `${this.baseUrl}/files/download?path=${encodeURIComponent(filePath)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async uploadFiles(targetPath: string, files: File[]): Promise<UploadResult> {
    const formData = new FormData();
    formData.append('path', targetPath);
    for (const file of files) {
      formData.append('files', file);
    }
    const url = `${this.baseUrl}/files/upload`;
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const errorBody = await response.text().catch(() => 'Unknown error');
      throw new Error(`Upload failed: ${errorBody}`);
    }
    return response.json() as Promise<UploadResult>;
  }

  async createDirectory(dirPath: string): Promise<{ success: boolean; path: string }> {
    return this.request<{ success: boolean; path: string }>('/files/mkdir', {
      method: 'POST',
      body: JSON.stringify({ path: dirPath }),
    });
  }

  // Constraints (from ConstraintsPro/Convex)
  async getConstraints(filters?: {
    project?: string;
    status?: string;
    priority?: string;
  }): Promise<ConvexConstraint[]> {
    const params = new URLSearchParams();
    if (filters?.project) params.set('project', filters.project);
    if (filters?.status) params.set('status', filters.status);
    if (filters?.priority) params.set('priority', filters.priority);
    const query = params.toString() ? `?${params.toString()}` : '';
    return this.request<ConvexConstraint[]>(`/constraints${query}`);
  }

  async getConstraint(id: string): Promise<ConvexConstraintDetail> {
    return this.request<ConvexConstraintDetail>(`/constraints/${id}`);
  }

  async getConstraintStats(): Promise<ConstraintStats> {
    return this.request<ConstraintStats>('/constraints/stats');
  }

  async getConstraintsByProject(projectKey: string): Promise<ConvexConstraint[]> {
    return this.request<ConvexConstraint[]>(`/constraints/by-project/${projectKey}`);
  }

  // Memory search
  async searchMemories(
    query: string
  ): Promise<{ results: { content: string; score: number }[] }> {
    return this.request<{ results: { content: string; score: number }[] }>(
      `/memories/search?q=${encodeURIComponent(query)}`
    );
  }
}

export const api = new ApiClient();
