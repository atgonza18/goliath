import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-16">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-destructive/20 bg-destructive/5">
        <AlertTriangle className="h-5 w-5 text-destructive" />
      </div>
      <p className="text-sm text-muted-foreground max-w-md text-center">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
