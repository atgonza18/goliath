import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface StatusBadgeProps {
  status: string;
  size?: 'sm' | 'md';
}

const statusConfig: Record<string, { className: string; label: string }> = {
  // Project statuses
  'on-track': { className: 'text-emerald-500 border-emerald-500/30', label: 'On Track' },
  'at-risk': { className: 'text-amber-500 border-amber-500/30', label: 'At Risk' },
  critical: { className: 'text-red-500 border-red-500/30', label: 'Critical' },
  unknown: { className: 'text-zinc-400 border-zinc-500/30', label: 'Unknown' },

  // Agent statuses
  active: { className: 'text-emerald-500 border-emerald-500/30', label: 'Active' },
  idle: { className: 'text-zinc-400 border-zinc-500/30', label: 'Idle' },
  error: { className: 'text-red-500 border-red-500/30', label: 'Error' },

  // Action item statuses
  open: { className: 'text-blue-400 border-blue-400/30', label: 'Open' },
  resolved: { className: 'text-emerald-500 border-emerald-500/30', label: 'Resolved' },
  'in-progress': { className: 'text-amber-500 border-amber-500/30', label: 'In Progress' },
  escalated: { className: 'text-red-500 border-red-500/30', label: 'Escalated' },

  // Priority levels
  high: { className: 'text-red-500 border-red-500/30', label: 'High' },
  medium: { className: 'text-amber-500 border-amber-500/30', label: 'Medium' },
  low: { className: 'text-zinc-400 border-zinc-500/30', label: 'Low' },
};

export function StatusBadge({ status, size = 'sm' }: StatusBadgeProps) {
  const config = statusConfig[status] || {
    className: 'text-zinc-400 border-zinc-500/30',
    label: status,
  };

  return (
    <Badge
      variant="outline"
      className={cn(
        'bg-transparent font-medium',
        size === 'sm' ? 'text-[11px] px-1.5 py-0' : 'text-xs px-2 py-0.5',
        config.className
      )}
    >
      {config.label}
    </Badge>
  );
}
