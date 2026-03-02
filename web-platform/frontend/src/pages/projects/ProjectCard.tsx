import { Card, CardContent } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import type { Project } from '../../types';
import { StatusBadge } from '../../components/common/StatusBadge';

interface ProjectCardProps {
  project: Project;
  onClick: () => void;
}

export function ProjectCard({ project, onClick }: ProjectCardProps) {
  return (
    <Card
      className="cursor-pointer py-0 gap-0 transition-colors hover:border-ring/30 group"
      onClick={onClick}
    >
      <CardContent className="p-5">
        <div className="flex items-start justify-between mb-1">
          <div>
            <h3 className="text-sm font-semibold text-foreground group-hover:text-foreground transition-colors">
              {project.name}
            </h3>
            <p className="text-[11px] text-muted-foreground font-mono mt-0.5">
              {project.key}
            </p>
          </div>
          <StatusBadge status={project.status} />
        </div>

        <Separator className="my-3" />

        <div className="flex items-center gap-6">
          <div>
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
              Constraints
            </p>
            <p className="text-lg font-semibold text-foreground tabular-nums mt-0.5">
              {project.constraintsCount}
            </p>
          </div>
          <Separator orientation="vertical" className="h-8" />
          <div>
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
              Open
            </p>
            <p className="text-lg font-semibold text-foreground tabular-nums mt-0.5">
              {project.openItems}
            </p>
          </div>
        </div>

        {project.recentActivity && (
          <p className="text-xs text-muted-foreground mt-3 truncate">
            {project.recentActivity}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
