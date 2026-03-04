import { Plus } from 'lucide-react';
import { useChatContext } from './ChatContext';
import type { StreamStatus } from '../../types';

const statusColors: Record<StreamStatus, string> = {
  idle: '#555',
  streaming: '#fbbf24',
  complete: '#a3e635',
  error: '#ef4444',
};

function truncate(text: string, max: number): string {
  return text.length > max ? text.slice(0, max) + '...' : text;
}

export function ChatTabBar() {
  const ctx = useChatContext();
  if (!ctx) return null;

  const { streams, activeConversationId, setActiveConversationId, onNewChat, conversations } = ctx;

  // Collect tab candidates: current active + all streaming conversations
  const tabIds = new Set<string>();
  if (activeConversationId && streams[activeConversationId]) {
    tabIds.add(activeConversationId);
  }
  for (const id of Object.keys(streams)) {
    if (streams[id].status === 'streaming') {
      tabIds.add(id);
    }
  }

  // Don't render tab bar if there's only 0-1 tabs
  if (tabIds.size < 2) return null;

  return (
    <div
      className="flex items-center gap-0 shrink-0 overflow-x-auto"
      style={{ background: '#0a0a10', borderBottom: '2px solid #2a2a35' }}
    >
      {[...tabIds].map(id => {
        const stream = streams[id];
        if (!stream) return null;
        const isActive = id === activeConversationId;
        const status = stream.status;
        const color = statusColors[status];

        // Try to get a title from the first user message or conversation list
        const conv = conversations.find(c => c.id === id);
        const firstUserMsg = stream.messages.find(m => m.role === 'user');
        const title = conv?.title || (firstUserMsg ? truncate(firstUserMsg.content, 30) : 'New Chat');

        return (
          <button
            key={id}
            onClick={() => setActiveConversationId(id)}
            className="flex items-center gap-2 px-4 py-2 text-[11px] font-bold tracking-wider transition-all duration-100 shrink-0"
            style={{
              color: isActive ? '#e4e4e7' : '#555',
              background: isActive ? '#0c0c12' : 'transparent',
              borderLeft: isActive ? `3px solid ${color}` : '3px solid transparent',
              borderRight: '1px solid #2a2a35',
            }}
          >
            <div
              className="w-2 h-2 shrink-0"
              style={{
                background: color,
                borderRadius: '50%',
                animation: status === 'streaming' ? 'pulse 1.5s ease-in-out infinite' : 'none',
              }}
            />
            <span>{truncate(title, 24)}</span>
          </button>
        );
      })}

      <button
        onClick={onNewChat}
        className="flex items-center gap-1 px-3 py-2 text-[11px] font-bold tracking-wider transition-colors shrink-0"
        style={{ color: '#555' }}
        onMouseEnter={(e) => { e.currentTarget.style.color = '#a3e635'; }}
        onMouseLeave={(e) => { e.currentTarget.style.color = '#555'; }}
      >
        <Plus className="h-3 w-3" />
      </button>
    </div>
  );
}
