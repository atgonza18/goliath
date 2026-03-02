import { Bot } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';

export function ThinkingIndicator() {
  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-card mt-0.5">
        <Bot className="h-3.5 w-3.5 text-muted-foreground" />
      </div>
      <div className="flex-1 pt-1 space-y-2 max-w-md">
        <Skeleton className="h-3.5 w-48" />
        <Skeleton className="h-3.5 w-32" />
      </div>
    </div>
  );
}
