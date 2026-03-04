import { Plus, Trash2 } from 'lucide-react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { Conversation } from '../../types';

interface ConversationListProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete?: (id: string) => void;
}

function formatTimestamp(ts: string): string {
  const date = new Date(ts);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'now';
  if (diffMins < 60) return `${diffMins}m`;
  if (diffHours < 24) return `${diffHours}h`;
  if (diffDays < 7) return `${diffDays}d`;
  return date.toLocaleDateString();
}

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onNewChat,
  onDelete,
}: ConversationListProps) {
  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-3">
        <button
          onClick={onNewChat}
          className="w-full flex items-center justify-center gap-1.5 text-[12px] font-medium text-zinc-400 hover:text-zinc-200 py-2 rounded-lg border border-zinc-800 hover:border-zinc-700 transition-colors"
        >
          <Plus className="h-3.5 w-3.5" />
          New chat
        </button>
      </div>

      <ScrollArea className="flex-1">
        <div className="px-2 pb-2">
          {conversations.length === 0 ? (
            <div className="px-3 py-10 text-center">
              <p className="text-[11px] text-zinc-600">No conversations yet</p>
            </div>
          ) : (
            <div className="space-y-px">
              {conversations.map((convo) => (
                <div
                  key={convo.id}
                  className={cn(
                    'group relative w-full text-left px-3 py-2 rounded-lg transition-colors cursor-pointer',
                    activeId === convo.id
                      ? 'bg-zinc-800/60 text-zinc-200'
                      : 'text-zinc-500 hover:bg-zinc-800/30 hover:text-zinc-300'
                  )}
                  onClick={() => onSelect(convo.id)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px] font-medium truncate flex-1">
                      {convo.title}
                    </span>
                    <div className="flex items-center gap-1 shrink-0">
                      <span className="text-[10px] text-zinc-600">
                        {formatTimestamp(convo.updated_at)}
                      </span>
                      {onDelete && (
                        <button
                          className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-red-500/10 hover:text-red-400"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDelete(convo.id);
                          }}
                          title="Delete"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
