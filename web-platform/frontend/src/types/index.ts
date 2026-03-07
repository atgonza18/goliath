// ---- Chat types ----
export interface MessageAttachment {
  type: 'image' | 'pdf';
  filename: string;
  originalName: string;
  url: string;
  mimeType: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  streaming?: boolean;
  metadata?: MessageMetadata | null;
  attachment?: MessageAttachment | null;
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
  type: 'agent_start' | 'agent_complete' | 'pass' | 'tool_start' | 'tool_done';
  agent?: string;
  task?: string;
  success?: boolean;
  duration?: number;
  pass?: number;
  status?: 'start' | 'complete';
  tool?: string;
  inputPreview?: string;
  input_preview?: string;  // snake_case from backend
}

export interface ToolActivity {
  tool: string;
  inputPreview?: string;
  startTime: number;
  completed: boolean;
}

export interface ActiveAgent {
  agent: string;
  task?: string;
  startTime: number;
  success?: boolean;
  duration?: number;
  completed: boolean;
  tools: ToolActivity[];
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
  slug?: string;
  role: string;
  description: string;
  status: 'active' | 'idle' | 'error';
  lastActive?: string;
  tasksCompleted?: number;
  category?: string;
  model?: 'sonnet' | 'opus';
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

// ---- Stream types (persistent multi-stream SSE) ----
export type StreamStatus = 'idle' | 'streaming' | 'complete' | 'error';

export interface StreamState {
  conversationId: string;
  status: StreamStatus;
  messages: Message[];
  isThinking: boolean;
  agentActivity: AgentActivity;
  error: string | null;
}

export interface StreamMutableData {
  streamingMsgId: string | null;
  streamingText: string;
  cleanupStream: (() => void) | null;
  scrollRafId: number;
}

// ---- Production / POD trends types ----
export interface DailyCount {
  date: string;
  count: number;
}

export interface ProjectTrend {
  key: string;
  name: string;
  number: number;
  today: number;
  yesterday: number;
  delta_units: number;
  delta_pct: number;
  seven_day_total: number;
  all_time_total: number;
  trend: 'up' | 'down' | 'flat' | 'none';
  latest_pod_date: string | null;
  days_since_last_pod: number | null;
  daily: DailyCount[];
}

export interface PortfolioSummary {
  today: number;
  yesterday: number;
  delta_units: number;
  seven_day_total: number;
  projects_reporting_today: number;
  total_projects: number;
  projects_with_data: number;
  daily: DailyCount[];
}

export interface ProductionTrends {
  generated_at: string;
  date_range: { start: string; end: string };
  portfolio: PortfolioSummary;
  projects: ProjectTrend[];
}

// ---- Production Dashboard types (extracted POD data) ----
export interface DailySeries {
  date: string;
  activities: Record<string, number>;
  total: number;
}

export interface PodActivity {
  activity_name: string;
  qty_to_date: number | null;
  qty_last_workday: number | null;
  qty_completed_yesterday: number;
  total_qty: number | null;
  unit: string | null;
  pct_complete: number | null;
  today_location: string | null;
  notes: string | null;
}

export interface PodCategory {
  category: string;
  activities: PodActivity[];
}

export interface CategorySummary {
  category: string;
  activity_count: number;
  avg_pct_complete: number | null;
}

export interface ProjectProductionSummary {
  key: string;
  name: string;
  number: number;
  latest_date: string | null;
  has_data: boolean;
  activity_count: number;
  category_count: number;
  categories_summary: CategorySummary[];
  overall_progress: number | null;
}

export interface ProjectProductionDetail {
  key: string;
  name: string;
  number: number;
  latest_date: string | null;
  categories: PodCategory[];
}

export interface PortfolioProductionSummary {
  active_sites: number;
  total_projects: number;
  projects_with_data: number;
}

export interface ProductionDashboardData {
  generated_at: string;
  portfolio: PortfolioProductionSummary;
  projects: ProjectProductionSummary[];
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
