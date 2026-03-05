import { useState } from 'react';
import { Check, ChevronDown, User, Calendar } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn, parseLocalDate } from '@/lib/utils';
import type { ActionItem } from '../../types';
import { StatusBadge } from '../../components/common/StatusBadge';

interface ActionItemRowProps {
  item: ActionItem;
  onResolve: (id: string) => Promise<void>;
}

export function ActionItemRow({ item, onResolve }: ActionItemRowProps) {
  const [resolving, setResolving] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const isResolved = item.status === 'resolved';

  const handleResolve = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (resolving || isResolved) return;
    setResolving(true);
    try {
      await onResolve(item.id);
    } finally {
      setResolving(false);
    }
  };

  return (
    <div className="border-b border-border last:border-b-0 transition-colors">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-accent/30 transition-colors"
      >
        {/* Checkbox */}
        <button
          onClick={handleResolve}
          disabled={resolving || isResolved}
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors',
            isResolved
              ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-500'
              : resolving
              ? 'border-muted-foreground/30'
              : 'border-border hover:border-muted-foreground'
          )}
        >
          {isResolved && <Check className="h-2.5 w-2.5" />}
        </button>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p
            className={cn(
              'text-sm font-medium leading-snug',
              isResolved ? 'text-muted-foreground line-through' : 'text-foreground'
            )}
          >
            {item.summary}
          </p>
          <div className="flex items-center gap-3 mt-1">
            <span className="text-xs text-muted-foreground">
              {parseLocalDate(item.date).toLocaleDateString()}
            </span>
            {item.project && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
                {item.project}
              </Badge>
            )}
          </div>
        </div>

        {/* Status and expand */}
        <div className="flex items-center gap-2 shrink-0">
          <StatusBadge status={item.status} />
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 text-muted-foreground transition-transform duration-150',
              expanded && 'rotate-180'
            )}
          />
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="animate-expand border-t border-border">
          <div className="px-4 pb-4 pt-3 pl-11">
            <p className="text-sm text-foreground/70 leading-relaxed">{item.detail}</p>
            <div className="flex items-center gap-4 mt-3">
              {item.assignee && (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <User className="h-3 w-3" />
                  {item.assignee}
                </div>
              )}
              {item.dueDate && (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Calendar className="h-3 w-3" />
                  Due: {parseLocalDate(item.dueDate).toLocaleDateString()}
                </div>
              )}
            </div>
            {!isResolved && (
              <Button
                variant="outline"
                size="xs"
                className="mt-3 text-emerald-500 border-emerald-500/30 hover:bg-emerald-500/10"
                onClick={handleResolve}
                disabled={resolving}
              >
                {resolving ? 'Resolving...' : 'Mark resolved'}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
