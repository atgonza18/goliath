import {
  Bot,
  AlertTriangle,
  Calendar,
  FileText,
  PenLine,
  Table2,
  Settings,
  Search,
  HardHat,
  Clock,
  Coins,
  FolderOpen,
  Mic,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';
import type { Agent } from '../../types';

interface AgentCardProps {
  agent: Agent;
}

const agentIcons: Record<string, React.ElementType> = {
  'Nimrod': Bot,
  'Constraints Manager': AlertTriangle,
  'Schedule Analyst': Calendar,
  'POD Analyst': FileText,
  'Report Writer': PenLine,
  'Excel Expert': Table2,
  'DevOps': Settings,
  'Researcher': Search,
  'Construction Manager': HardHat,
  'Scheduling Expert': Clock,
  'Cost Analyst': Coins,
  'Folder Organizer': FolderOpen,
  'Transcript Processor': Mic,
};

function getAgentIcon(name: string): React.ElementType {
  return agentIcons[name] || Bot;
}

function timeAgo(ts?: string): string {
  if (!ts) return 'Never';
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const statusDotColor: Record<string, string> = {
  active: 'bg-emerald-500',
  idle: 'bg-zinc-500',
  error: 'bg-red-500',
};

export function AgentCard({ agent }: AgentCardProps) {
  const isNimrod = agent.name === 'Nimrod';
  const Icon = getAgentIcon(agent.name);

  return (
    <Card
      className={cn(
        'py-0 gap-0 transition-colors',
        isNimrod && 'border-foreground/10'
      )}
    >
      <CardContent className="p-5">
        <div className="flex items-start gap-3">
          <div
            className={cn(
              'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
              agent.status === 'active'
                ? 'bg-foreground/5 text-foreground'
                : agent.status === 'error'
                ? 'bg-destructive/10 text-destructive'
                : 'bg-accent text-muted-foreground'
            )}
          >
            <Icon className="h-4 w-4" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-foreground">{agent.name}</h3>
              <div
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  statusDotColor[agent.status] || 'bg-zinc-500'
                )}
              />
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">{agent.role}</p>
            <p className="text-xs text-foreground/60 leading-relaxed mt-2">
              {agent.description}
            </p>
          </div>
        </div>

        <Separator className="my-3" />

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Clock className="h-3 w-3" />
            {timeAgo(agent.lastActive)}
          </div>
          {agent.tasksCompleted !== undefined && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <FileText className="h-3 w-3" />
              {agent.tasksCompleted} tasks
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
