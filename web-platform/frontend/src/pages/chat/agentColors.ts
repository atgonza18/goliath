const STORAGE_KEY = 'goliath-agent-colors';

const DEFAULT_COLORS: Record<string, string> = {
  nimrod: '#a3e635',
  schedule_analyst: '#22d3ee',
  constraints_manager: '#f472b6',
  pod_analyst: '#fbbf24',
  report_writer: '#a78bfa',
  excel_expert: '#fb923c',
  construction_manager: '#2dd4bf',
  scheduling_expert: '#60a5fa',
  cost_analyst: '#e879f9',
  devops: '#34d399',
  researcher: '#f87171',
  folder_organizer: '#facc15',
  transcript_processor: '#38bdf8',
};

// Fallback cycle for unknown agent names
const FALLBACK_COLORS = [
  '#a3e635', '#22d3ee', '#f472b6', '#fbbf24', '#a78bfa',
  '#fb923c', '#2dd4bf', '#60a5fa', '#e879f9', '#34d399',
  '#f87171', '#facc15',
];

// Color picker palette — 16 high-contrast colors
export const AGENT_COLOR_PALETTE = [
  '#a3e635', '#22d3ee', '#f472b6', '#fbbf24',
  '#a78bfa', '#fb923c', '#2dd4bf', '#60a5fa',
  '#e879f9', '#34d399', '#f87171', '#facc15',
  '#38bdf8', '#f97316', '#818cf8', '#4ade80',
];

function getOverrides(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

let _overrideCache: Record<string, string> | null = null;

export function getAgentColor(name: string): string {
  if (!_overrideCache) _overrideCache = getOverrides();
  if (_overrideCache[name]) return _overrideCache[name];
  if (DEFAULT_COLORS[name]) return DEFAULT_COLORS[name];
  // Hash-based fallback for unknown agents
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = ((hash << 5) - hash + name.charCodeAt(i)) | 0;
  return FALLBACK_COLORS[Math.abs(hash) % FALLBACK_COLORS.length];
}

export function setAgentColor(name: string, color: string): void {
  const overrides = getOverrides();
  overrides[name] = color;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
  _overrideCache = overrides;
}
