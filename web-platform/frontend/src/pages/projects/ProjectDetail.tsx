import { useEffect, useState } from 'react';
import { ArrowLeft, AlertTriangle, Users, Clock, Database } from 'lucide-react';
import { api } from '../../api/client';
import type { ProjectDetail as ProjectDetailType, ConvexConstraint } from '../../types';
import { StatusBadge } from '../../components/common/StatusBadge';
import { LoadingSpinner } from '../../components/common/LoadingSpinner';
import { ErrorState } from '../../components/common/ErrorState';
import { Button } from '@/components/ui/button';
import { parseLocalDate } from '@/lib/utils';
import { Card, CardContent } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';

interface ProjectDetailProps {
  projectKey: string;
  onBack: () => void;
}

const DISCIPLINE_COLORS: Record<string, string> = {
  Safety: 'text-red-400',
  Quality: 'text-purple-400',
  Civil: 'text-amber-400',
  Modules: 'text-blue-400',
  'AG Electrical': 'text-yellow-400',
  Piles: 'text-orange-400',
  Environmental: 'text-green-400',
  Commissioning: 'text-teal-400',
  Racking: 'text-indigo-400',
  Procurement: 'text-pink-400',
  Other: 'text-muted-foreground',
};

export function ProjectDetail({ projectKey, onBack }: ProjectDetailProps) {
  const [project, setProject] = useState<ProjectDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Live constraints from ConstraintsPro
  const [liveConstraints, setLiveConstraints] = useState<ConvexConstraint[]>([]);
  const [constraintsLoading, setConstraintsLoading] = useState(true);
  const [constraintsSource, setConstraintsSource] = useState<'convex' | 'memory'>('memory');

  useEffect(() => {
    loadProject();
    loadLiveConstraints();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey]);

  const loadProject = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getProject(projectKey);
      setProject(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load project');
    } finally {
      setLoading(false);
    }
  };

  const loadLiveConstraints = async () => {
    setConstraintsLoading(true);
    try {
      const data = await api.getConstraintsByProject(projectKey);
      if (data && data.length > 0) {
        setLiveConstraints(data);
        setConstraintsSource('convex');
      }
    } catch {
      // Fall back to memory DB constraints (already in project data)
      setConstraintsSource('memory');
    } finally {
      setConstraintsLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner message="Loading project..." />
      </div>
    );
  }

  if (error || !project) {
    return <ErrorState message={error || 'Project not found'} onRetry={loadProject} />;
  }

  // Use live constraints if available, otherwise fall back to memory DB
  const displayConstraints = constraintsSource === 'convex' ? liveConstraints : [];
  const memoryConstraints = project.constraints || [];
  const totalConstraintCount = constraintsSource === 'convex'
    ? liveConstraints.length
    : memoryConstraints.length;

  const openCount = constraintsSource === 'convex'
    ? liveConstraints.filter(c => c.status !== 'resolved').length
    : memoryConstraints.filter(c => c.status !== 'resolved').length;

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Button variant="ghost" size="icon-xs" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-foreground">{project.name}</h2>
            <StatusBadge status={project.status} size="md" />
          </div>
          <p className="text-xs text-muted-foreground font-mono mt-0.5">{project.key}</p>
        </div>
      </div>

      <Tabs defaultValue="constraints">
        <TabsList>
          <TabsTrigger value="constraints">
            <AlertTriangle className="h-3.5 w-3.5 mr-1.5" />
            Constraints ({totalConstraintCount})
          </TabsTrigger>
          <TabsTrigger value="contacts">
            <Users className="h-3.5 w-3.5 mr-1.5" />
            Contacts ({project.contacts.length})
          </TabsTrigger>
          <TabsTrigger value="activity">
            <Clock className="h-3.5 w-3.5 mr-1.5" />
            Activity
          </TabsTrigger>
        </TabsList>

        <TabsContent value="constraints" className="mt-4">
          {/* Data source indicator */}
          {constraintsSource === 'convex' && liveConstraints.length > 0 && (
            <div className="flex items-center gap-1.5 text-[10px] text-emerald-500 mb-3">
              <Database className="h-3 w-3" />
              <span>Live from ConstraintsPro ({openCount} open)</span>
            </div>
          )}

          {constraintsLoading ? (
            <div className="flex items-center justify-center h-32">
              <LoadingSpinner message="Loading constraints..." />
            </div>
          ) : constraintsSource === 'convex' && displayConstraints.length > 0 ? (
            <div className="space-y-2">
              {displayConstraints.map((constraint) => (
                <Card key={constraint.id} className="py-0 gap-0">
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-foreground/80 leading-relaxed">
                          {constraint.description}
                        </p>
                        {constraint.owner && (
                          <p className="text-xs text-muted-foreground mt-1">
                            Owner: {constraint.owner}
                          </p>
                        )}
                        {constraint.dscLead && (
                          <p className="text-xs text-muted-foreground">
                            DSC Lead: {constraint.dscLead}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className={`text-[10px] font-medium ${DISCIPLINE_COLORS[constraint.discipline] || 'text-muted-foreground'}`}>
                          {constraint.discipline}
                        </span>
                        <StatusBadge status={constraint.priority} />
                        <StatusBadge status={constraint.status === 'in_progress' ? 'at-risk' : constraint.status} />
                      </div>
                    </div>
                    <div className="flex items-center gap-4 mt-3 text-xs text-muted-foreground">
                      {constraint.createdAt && (
                        <span>Logged: {parseLocalDate(constraint.createdAt).toLocaleDateString()}</span>
                      )}
                      {constraint.dueDate && (
                        <span>Due: {parseLocalDate(constraint.dueDate).toLocaleDateString()}</span>
                      )}
                    </div>
                    {constraint.notes && (
                      <div className="mt-2 text-xs text-muted-foreground bg-accent/30 rounded px-2 py-1.5 max-h-20 overflow-y-auto whitespace-pre-wrap">
                        {constraint.notes.slice(0, 300)}
                        {constraint.notes.length > 300 ? '...' : ''}
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : memoryConstraints.length > 0 ? (
            <div className="space-y-2">
              {memoryConstraints.map((constraint) => (
                <Card key={constraint.id} className="py-0 gap-0">
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-sm text-foreground/80 leading-relaxed">
                        {constraint.description}
                      </p>
                      <div className="flex items-center gap-2 shrink-0">
                        <StatusBadge status={constraint.priority} />
                        <StatusBadge status={constraint.status} />
                      </div>
                    </div>
                    <div className="flex items-center gap-4 mt-3 text-xs text-muted-foreground">
                      <span>Logged: {parseLocalDate(constraint.dateLogged).toLocaleDateString()}</span>
                      {constraint.dueDate && (
                        <span>Due: {parseLocalDate(constraint.dueDate).toLocaleDateString()}</span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground py-8 text-center">No constraints logged</p>
          )}
        </TabsContent>

        <TabsContent value="contacts" className="mt-4">
          {project.contacts.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">No contacts listed</p>
          ) : (
            <div className="space-y-1">
              {project.contacts.map((contact, i) => (
                <div key={i} className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-accent/50 transition-colors">
                  <Avatar size="sm">
                    <AvatarFallback className="text-[10px] font-semibold">
                      {contact.name.split(' ').map(n => n[0]).join('').substring(0, 2)}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground truncate">{contact.name}</p>
                    <p className="text-xs text-muted-foreground truncate">{contact.role}</p>
                  </div>
                  {contact.email && (
                    <span className="text-xs text-muted-foreground truncate hidden sm:block">{contact.email}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="activity" className="mt-4">
          {project.recentActivities.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">No recent activity</p>
          ) : (
            <div className="space-y-0">
              {project.recentActivities.slice(0, 15).map((activity, i) => (
                <div key={activity.id} className="flex gap-3 py-3">
                  <div className="flex flex-col items-center">
                    <div className="h-1.5 w-1.5 rounded-full bg-muted-foreground mt-1.5 shrink-0" />
                    {i < project.recentActivities.length - 1 && (
                      <div className="w-px flex-1 bg-border mt-1.5" />
                    )}
                  </div>
                  <div className="min-w-0 pb-1">
                    <p className="text-sm text-foreground/80 leading-relaxed">{activity.summary}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {new Date(activity.timestamp).toLocaleString()}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
