// ---- Chat types ----
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  streaming?: boolean;
  metadata?: MessageMetadata | null;
}

export interface MessageMetadata {
  subagents?: SubagentLogEntry[];
  file_paths?: string[];
  token_summary?: TokenSummary | null;
}

export interface SubagentLogEntry {
  agent: string;
  success: boolean;
  duration: number;
  error?: string | null;
}

export interface TokenSummary {
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_cost_usd: number;
  agents: Array<{
    name: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    duration_ms: number;
  }>;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatResponse {
  conversation_id: string;
  stream_url: string;
}

// ---- Subagent Event types (real-time SSE) ----
export interface SubagentEvent {
  type: 'agent_start' | 'agent_complete' | 'pass';
  agent?: string;
  task?: string;
  success?: boolean;
  duration?: number;
  pass?: number;
  status?: 'start' | 'complete';
}

export interface ActiveAgent {
  agent: string;
  task?: string;
  startTime: number;
  success?: boolean;
  duration?: number;
  completed: boolean;
}

export interface AgentActivity {
  agents: Map<string, ActiveAgent>;
  isProcessing: boolean;
  thinkingMessage: string | null;
  currentPass: number | null;
  passStatus: string | null;
}

// ---- Project types ----
export interface Project {
  key: string;
  name: string;
  status: 'on-track' | 'at-risk' | 'critical' | 'unknown';
  constraintsCount: number;
  openItems: number;
  recentActivity: string;
}

export interface ProjectDetail extends Project {
  contacts: Contact[];
  constraints: Constraint[];
  recentActivities: ActivityItem[];
}

export interface Contact {
  name: string;
  role: string;
  email?: string;
  phone?: string;
}

export interface Constraint {
  id: string;
  description: string;
  status: 'open' | 'resolved' | 'escalated';
  priority: 'high' | 'medium' | 'low';
  dateLogged: string;
  dueDate?: string;
}

export interface ActivityItem {
  id: string;
  type: string;
  summary: string;
  timestamp: string;
}

// ---- Action Item types ----
export interface ActionItem {
  id: string;
  date: string;
  summary: string;
  detail: string;
  project: string;
  status: 'open' | 'resolved' | 'in-progress';
  assignee?: string;
  dueDate?: string;
}

// ---- Agent types ----
export interface Agent {
  name: string;
  role: string;
  description: string;
  status: 'active' | 'idle' | 'error';
  lastActive?: string;
  tasksCompleted?: number;
}

// ---- File Explorer types ----
export interface FileItem {
  name: string;
  type: 'file' | 'directory';
  size: number;
  sizeFormatted: string;
  modified: string;
  extension: string;
  path: string;
}

export interface UploadResult {
  uploaded: Array<{
    name: string;
    size: number;
    sizeFormatted: string;
    path: string;
  }>;
}

// ---- Convex Constraint types (from ConstraintsPro) ----
export interface ConvexConstraint {
  id: string;
  description: string;
  discipline: string;
  status: 'open' | 'in_progress' | 'resolved';
  priority: 'high' | 'medium' | 'low';
  owner: string | null;
  projectId: string | null;
  projectName: string | null;
  dscLead: string | null;
  dueDate: string | null;
  createdAt: string | null;
  notes: string;
}

export interface ConvexConstraintDetail extends ConvexConstraint {
  activity: Array<{
    id: string;
    type: string;
    detail: string;
    timestamp: string;
    user: string;
  }>;
}

export interface ConstraintStats {
  total: number;
  byStatus: Record<string, number>;
  byPriority: Record<string, number>;
  byProject: Record<string, number>;
  overdue: number;
  aging: {
    over7d: number;
    over14d: number;
    over30d: number;
  };
}

// ---- API types ----
export interface ApiError {
  message: string;
  status: number;
}

export interface HealthResponse {
  status: string;
  uptime: number;
  version: string;
}
