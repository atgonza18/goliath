// ---- Chat types ----
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  streaming?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  messageCount: number;
}

export interface ChatResponse {
  id: string;
  message: string;
  conversationId: string;
  streamUrl?: string;
}

// ---- Chat Session types (Claude CLI-backed) ----
export interface ChatSession {
  id: string;
  title: string;
  createdAt: string;
  updatedAt?: string;
  messageCount?: number;
  lastMessage?: string;
}

export interface ChatSessionDetail {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: Message[];
}

export interface ChatMessageResponse {
  id: string;
  sessionId: string;
  streamUrl: string;
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
