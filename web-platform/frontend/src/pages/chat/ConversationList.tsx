import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';

interface ConversationItem {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  messageCount: number;
}

interface ConversationListProps {
  conversations: ConversationItem[];
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
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-center gap-1.5 text-xs"
          onClick={onNewChat}
        >
          <Plus className="h-3.5 w-3.5" />
          New chat
        </Button>
      </div>

      <Separator />

      <ScrollArea className="flex-1">
        <div className="p-2">
          {conversations.length === 0 ? (
            <div className="px-3 py-10 text-center">
              <p className="text-xs text-muted-foreground">No conversations yet</p>
            </div>
          ) : (
            <div className="space-y-0.5">
              {conversations.map((convo) => (
                <div
                  key={convo.id}
                  className={cn(
                    'group relative w-full text-left px-3 py-2.5 rounded-lg transition-colors cursor-pointer',
                    activeId === convo.id
                      ? 'bg-accent text-accent-foreground'
                      : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
                  )}
                  onClick={() => onSelect(convo.id)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium truncate flex-1">
                      {convo.title}
                    </span>
                    <div className="flex items-center gap-1 shrink-0">
                      <span className="text-[10px] text-muted-foreground">
                        {formatTimestamp(convo.timestamp)}
                      </span>
                      {onDelete && (
                        <button
                          className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-destructive/20 hover:text-destructive"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDelete(convo.id);
                          }}
                          title="Delete conversation"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      )}
                    </div>
                  </div>
                  {convo.lastMessage && (
                    <p className="text-[11px] text-muted-foreground truncate mt-0.5">
                      {convo.lastMessage}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
