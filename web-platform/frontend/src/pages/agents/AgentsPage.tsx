import { useState, useEffect } from 'react';
import { RefreshCw } from 'lucide-react';
import { api } from '../../api/client';
import type { Agent } from '../../types';
import { PageHeader } from '../../components/common/PageHeader';
import { CardGridSkeleton } from '../../components/common/LoadingSpinner';
import { ErrorState } from '../../components/common/ErrorState';
import { AgentCard } from './AgentCard';
import { Button } from '@/components/ui/button';

const fallbackAgents: Agent[] = [
  {
    name: 'Nimrod',
    role: 'Chief Operating Officer',
    description: 'Lead orchestrator agent. Routes incoming messages, coordinates multi-agent workflows, and serves as the primary interface for all operational queries and decisions.',
    status: 'active',
    lastActive: new Date().toISOString(),
    tasksCompleted: 0,
  },
  {
    name: 'Constraints Manager',
    role: 'Constraint Tracking & Escalation',
    description: 'Monitors and manages project constraints across all solar sites. Tracks open issues, escalates blockers, and ensures resolution timelines are met.',
    status: 'active',
    lastActive: new Date().toISOString(),
    tasksCompleted: 0,
  },
  {
    name: 'Schedule Analyst',
    role: 'Schedule Analysis & Tracking',
    description: 'Analyzes project schedules, identifies critical path items, tracks milestone progress, and flags schedule risks across the portfolio.',
    status: 'idle',
    tasksCompleted: 0,
  },
  {
    name: 'POD Analyst',
    role: 'Plan of the Day Analysis',
    description: 'Processes daily Plan of the Day reports from each project site. Extracts key metrics, weather impacts, crew counts, and daily progress updates.',
    status: 'active',
    lastActive: new Date().toISOString(),
    tasksCompleted: 0,
  },
  {
    name: 'Report Writer',
    role: 'Report Generation',
    description: 'Generates formatted reports including morning briefings, weekly summaries, constraint reports, and custom project analyses on demand.',
    status: 'idle',
    tasksCompleted: 0,
  },
  {
    name: 'Excel Expert',
    role: 'Spreadsheet Analysis',
    description: 'Specializes in reading, analyzing, and extracting data from Excel spreadsheets. Handles pile installation trackers, material logs, and schedule exports.',
    status: 'idle',
    tasksCompleted: 0,
  },
  {
    name: 'Construction Manager',
    role: 'Construction Operations',
    description: 'Provides expertise on construction operations including pile driving, tracker installation, electrical work, and commissioning activities.',
    status: 'idle',
    tasksCompleted: 0,
  },
  {
    name: 'Scheduling Expert',
    role: 'P6/Schedule Expertise',
    description: 'Expert in Primavera P6 scheduling, critical path method analysis, resource leveling, and schedule optimization for construction projects.',
    status: 'idle',
    tasksCompleted: 0,
  },
  {
    name: 'Cost Analyst',
    role: 'Cost & Budget Analysis',
    description: 'Tracks project budgets, analyzes cost variances, monitors change orders, and provides financial insights across the project portfolio.',
    status: 'idle',
    tasksCompleted: 0,
  },
  {
    name: 'DevOps',
    role: 'System Operations',
    description: 'Manages the Goliath platform infrastructure, monitors system health, handles deployments, and maintains operational reliability.',
    status: 'active',
    lastActive: new Date().toISOString(),
    tasksCompleted: 0,
  },
  {
    name: 'Researcher',
    role: 'Information Research',
    description: 'Conducts web research, gathers industry data, and provides contextual information to support decision-making across all project operations.',
    status: 'idle',
    tasksCompleted: 0,
  },
  {
    name: 'Folder Organizer',
    role: 'File & Folder Management',
    description: 'Manages the project file structure, organizes documents into appropriate project folders, and maintains the document management system.',
    status: 'idle',
    tasksCompleted: 0,
  },
  {
    name: 'Transcript Processor',
    role: 'Meeting Transcript Analysis',
    description: 'Processes meeting transcripts, extracts action items, key decisions, and important discussions. Links findings to relevant projects and constraints.',
    status: 'idle',
    tasksCompleted: 0,
  },
];

export function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadAgents();
  }, []);

  const loadAgents = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getAgents();
      setAgents(data);
    } catch {
      setAgents(fallbackAgents);
      setError(null);
    } finally {
      setLoading(false);
    }
  };

  const activeCount = agents.filter((a) => a.status === 'active').length;

  return (
    <div className="flex flex-col h-full min-h-0">
      <PageHeader
        title="Agents"
        subtitle={`${activeCount} active of ${agents.length} deployed`}
        actions={
          <Button variant="ghost" size="icon-xs" onClick={loadAgents} title="Refresh">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        }
      />

      <div className="flex-1 overflow-y-auto min-h-0 p-6" data-scroll-container>
        {loading ? (
          <CardGridSkeleton count={6} />
        ) : error ? (
          <ErrorState message={error} onRetry={loadAgents} />
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {agents.map((agent) => (
              <AgentCard key={agent.name} agent={agent} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
