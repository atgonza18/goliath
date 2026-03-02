import { useState, useEffect } from 'react';
import { Search } from 'lucide-react';
import { api } from '../../api/client';
import type { Project, ConstraintStats } from '../../types';
import { PageHeader } from '../../components/common/PageHeader';
import { CardGridSkeleton } from '../../components/common/LoadingSpinner';
import { ErrorState } from '../../components/common/ErrorState';
import { ProjectCard } from './ProjectCard';
import { ProjectDetail } from './ProjectDetail';
import { Input } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

// Fallback project data when API is unavailable
const fallbackProjects: Project[] = [
  { key: 'union-ridge', name: 'Union Ridge', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'duff', name: 'Duff', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'salt-branch', name: 'Salt Branch', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'blackford', name: 'Blackford', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'delta-bobcat', name: 'Delta Bobcat', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'tehuacana', name: 'Tehuacana', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'three-rivers', name: 'Three Rivers', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'scioto-ridge', name: 'Scioto Ridge', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'mayes', name: 'Mayes', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'graceland', name: 'Graceland', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'pecan-prairie', name: 'Pecan Prairie', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
  { key: 'duffy-bess', name: 'Duffy-Bess', status: 'on-track', constraintsCount: 0, openItems: 0, recentActivity: '' },
];

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProjectKey, setSelectedProjectKey] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [, setConstraintStats] = useState<ConstraintStats | null>(null);

  useEffect(() => {
    loadProjects();
    loadConstraintStats();
  }, []);

  const loadConstraintStats = async () => {
    try {
      const stats = await api.getConstraintStats();
      setConstraintStats(stats);

      // Enrich project data with live constraint counts
      if (stats.byProject) {
        setProjects((prev) =>
          prev.map((p) => {
            const projName = p.name;
            const count = stats.byProject[projName] || 0;
            if (count > 0) {
              return {
                ...p,
                constraintsCount: count,
                status: count > 5 ? 'at-risk' as const : 'on-track' as const,
              };
            }
            return p;
          })
        );
      }
    } catch {
      // Live stats unavailable — projects keep memory DB data
    }
  };

  const loadProjects = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getProjects();
      setProjects(data);
    } catch {
      setProjects(fallbackProjects);
      setError(null);
    } finally {
      setLoading(false);
    }
  };

  const filteredProjects = projects.filter((p) =>
    p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    p.key.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const statusCounts = {
    total: projects.length,
    onTrack: projects.filter((p) => p.status === 'on-track').length,
    atRisk: projects.filter((p) => p.status === 'at-risk').length,
    critical: projects.filter((p) => p.status === 'critical').length,
  };

  if (selectedProjectKey) {
    return (
      <div className="flex flex-col h-full min-h-0">
        <PageHeader title="Projects" subtitle="Project detail" />
        <div className="flex-1 overflow-y-auto min-h-0 p-6" data-scroll-container>
          <ProjectDetail
            projectKey={selectedProjectKey}
            onBack={() => setSelectedProjectKey(null)}
          />
        </div>
      </div>
    );
  }

  const stats = [
    { label: 'Total', value: statusCounts.total, className: 'text-foreground' },
    { label: 'On Track', value: statusCounts.onTrack, className: 'text-emerald-500' },
    { label: 'At Risk', value: statusCounts.atRisk, className: 'text-amber-500' },
    { label: 'Critical', value: statusCounts.critical, className: 'text-red-500' },
  ];

  return (
    <div className="flex flex-col h-full min-h-0">
      <PageHeader
        title="Projects"
        subtitle={`${statusCounts.total} solar projects`}
        actions={
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Search..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-8 h-8 w-48 text-xs"
            />
          </div>
        }
      />

      <div className="flex-1 overflow-y-auto min-h-0 p-6" data-scroll-container>
        {loading ? (
          <div className="space-y-6">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Card key={i} className="py-0 gap-0">
                  <CardContent className="p-4 space-y-2">
                    <div className="h-3 w-12 rounded bg-accent animate-pulse" />
                    <div className="h-6 w-8 rounded bg-accent animate-pulse" />
                  </CardContent>
                </Card>
              ))}
            </div>
            <CardGridSkeleton count={6} />
          </div>
        ) : error ? (
          <ErrorState message={error} onRetry={loadProjects} />
        ) : (
          <>
            {/* Status summary */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
              {stats.map((stat) => (
                <Card key={stat.label} className="py-0 gap-0">
                  <CardContent className="p-4">
                    <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                      {stat.label}
                    </p>
                    <p className={cn('text-2xl font-semibold tabular-nums mt-1', stat.className)}>
                      {stat.value}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* Projects grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {filteredProjects.map((project) => (
                <ProjectCard
                  key={project.key}
                  project={project}
                  onClick={() => setSelectedProjectKey(project.key)}
                />
              ))}
            </div>

            {filteredProjects.length === 0 && searchQuery && (
              <div className="text-center py-16">
                <p className="text-sm text-muted-foreground">
                  No projects match &quot;{searchQuery}&quot;
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
