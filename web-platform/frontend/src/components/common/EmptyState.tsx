import { cn } from '@/lib/utils';

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-4 py-20', className)}>
      {icon && (
        <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-card text-muted-foreground">
          {icon}
        </div>
      )}
      <div className="text-center">
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
        {description && (
          <p className="mt-1.5 text-sm text-muted-foreground max-w-sm leading-relaxed">
            {description}
          </p>
        )}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
