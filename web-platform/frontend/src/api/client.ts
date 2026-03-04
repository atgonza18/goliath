import type {
  ActionItem,
  Agent,
  ChatResponse,
  SubagentEvent,
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

export interface SSECallbacks {
  onThinking?: (message: string) => void;
  onSubagent?: (event: SubagentEvent) => void;
  onChunk?: (chunk: string) => void;
  onComplete?: (data: { text: string; file_paths?: string[]; subagents?: any[] }) => void;
  onError?: (error: string) => void;
}

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

  // ---- Chat (Nimrod Orchestration) ----

  async sendMessage(
    message: string,
    conversationId?: string
  ): Promise<ChatResponse> {
    return this.request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({ message, conversation_id: conversationId }),
    });
  }

  /**
   * SSE-based streaming using fetch() + ReadableStream.
   *
   * Flow:
   *   1. POST /api/chat → get conversation_id + stream_url
   *   2. GET stream_url → ReadableStream of SSE events
   *   3. Parse SSE events with typed callbacks
   *
   * Bot emits: data: {"type": "thinking|subagent|chunk|complete|error", "data": "..."}\n\n
   *
   * Returns a cleanup function to abort the stream.
   */
  createSSEStream(
    conversationId: string | null,
    message: string,
    callbacks: SSECallbacks,
  ): () => void {
    const abortController = new AbortController();
    let done = false;

    const run = async () => {
      try {
        // Step 1: POST the message
        const postResponse = await fetch(`${this.baseUrl}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message,
            conversation_id: conversationId,
          }),
          signal: abortController.signal,
        });

        if (!postResponse.ok) {
          const errText = await postResponse.text().catch(() => 'Unknown error');
          throw new Error(`Failed to send message: ${errText}`);
        }

        const { conversation_id, stream_url } = await postResponse.json();

        // Step 2: Connect to the SSE stream
        const sseResponse = await fetch(stream_url, {
          signal: abortController.signal,
          headers: {
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
          },
        });

        if (!sseResponse.ok || !sseResponse.body) {
          throw new Error(`SSE connection failed: ${sseResponse.status}`);
        }

        // Step 3: Parse SSE events
        const reader = sseResponse.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done: streamDone, value } = await reader.read();
          if (streamDone) break;

          buffer += decoder.decode(value, { stream: true });

          const parts = buffer.split('\n');
          buffer = parts.pop() || '';

          for (const line of parts) {
            if (line.startsWith(':') || !line.trim()) continue;
            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6).trim();

            try {
              const event = JSON.parse(data);
              const eventType = event.type as string;
              const eventData = event.data;

              switch (eventType) {
                case 'thinking':
                  callbacks.onThinking?.(eventData);
                  break;
                case 'subagent': {
                  try {
                    const parsed = typeof eventData === 'string' ? JSON.parse(eventData) : eventData;
                    callbacks.onSubagent?.(parsed as SubagentEvent);
                  } catch {
                    // Skip malformed subagent events
                  }
                  break;
                }
                case 'chunk':
                  callbacks.onChunk?.(eventData);
                  break;
                case 'complete': {
                  done = true;
                  try {
                    const parsed = typeof eventData === 'string' ? JSON.parse(eventData) : eventData;
                    callbacks.onComplete?.({
                      text: parsed.text || '',
                      file_paths: parsed.file_paths,
                      subagents: parsed.subagents,
                    });
                  } catch {
                    callbacks.onComplete?.({ text: eventData || '' });
                  }
                  // Return conversation_id for the caller
                  (callbacks as any)._resolvedConversationId = conversation_id;
                  return;
                }
                case 'error':
                  done = true;
                  callbacks.onError?.(eventData);
                  return;
              }
            } catch {
              // Non-JSON SSE data — skip
            }
          }
        }

        // Stream ended without complete event
        if (!done) {
          done = true;
          callbacks.onComplete?.({ text: '' });
        }
      } catch (err) {
        if (done) return;
        if ((err as Error).name === 'AbortError') return;
        done = true;
        callbacks.onError?.(err instanceof Error ? err.message : String(err));
      }
    };

    run();

    return () => {
      if (!done) {
        done = true;
        abortController.abort();
      }
    };
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

  async uploadFolder(targetPath: string, files: File[]): Promise<UploadResult> {
    const formData = new FormData();
    formData.append('path', targetPath);
    for (const file of files) {
      // Use webkitRelativePath for folder structure preservation
      const relativePath = (file as any).webkitRelativePath || file.name;
      formData.append('files', file, relativePath);
    }
    const url = `${this.baseUrl}/files/upload-folder`;
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const errorBody = await response.text().catch(() => 'Unknown error');
      throw new Error(`Folder upload failed: ${errorBody}`);
    }
    return response.json() as Promise<UploadResult>;
  }

  async createDirectory(dirPath: string): Promise<{ success: boolean; path: string }> {
    return this.request<{ success: boolean; path: string }>('/files/mkdir', {
      method: 'POST',
      body: JSON.stringify({ path: dirPath }),
    });
  }

  async previewFile(filePath: string): Promise<{
    content: string;
    size: number;
    truncated: boolean;
    name: string;
    path: string;
  }> {
    return this.request(`/files/preview?path=${encodeURIComponent(filePath)}`);
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
