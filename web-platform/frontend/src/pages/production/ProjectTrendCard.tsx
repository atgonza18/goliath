import { TrendingUp, TrendingDown, Minus, FileText } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import type { ProjectTrend } from '../../types';
import { MiniTrendChart } from './MiniTrendChart';

interface ProjectTrendCardProps {
  project: ProjectTrend;
}

/** Accent color based on trend direction */
function trendColor(trend: ProjectTrend['trend']): string {
  switch (trend) {
    case 'up':
      return '#22c55e';
    case 'down':
      return '#ef4444';
    case 'flat':
      return 'var(--chart-3)';
    case 'none':
    default:
      return 'var(--theme-text-dim)';
  }
}

/** Icon for trend direction */
function TrendIcon({ trend }: { trend: ProjectTrend['trend'] }) {
  const color = trendColor(trend);
  const cls = 'h-3.5 w-3.5';
  switch (trend) {
    case 'up':
      return <TrendingUp className={cls} style={{ color }} />;
    case 'down':
      return <TrendingDown className={cls} style={{ color }} />;
    case 'flat':
      return <Minus className={cls} style={{ color }} />;
    case 'none':
    default:
      return <FileText className={cls} style={{ color }} />;
  }
}

/** Format delta display string */
function formatDelta(units: number, pct: number): string {
  const sign = units >= 0 ? '+' : '';
  const pctStr = pct !== 0 ? ` (${pct > 0 ? '+' : ''}${pct}%)` : '';
  return `${sign}${units}${pctStr}`;
}

export function ProjectTrendCard({ project }: ProjectTrendCardProps) {
  const isNoData = project.trend === 'none';
  const isDown = project.trend === 'down' || project.delta_units < 0;
  const isUp = project.trend === 'up' || project.delta_units > 0;

  // Card border color based on trend: red for down, green for up, default for flat/none
  const borderStyle: React.CSSProperties = isDown
    ? { borderColor: 'rgba(239, 68, 68, 0.4)' }
    : isUp
      ? { borderColor: 'rgba(34, 197, 94, 0.25)' }
      : {};

  return (
    <Card
      className="py-0 gap-0 transition-colors"
      style={borderStyle}
    >
      <CardContent className="p-5">
        {/* ─── Header ───────────────────────────────────────── */}
        <div className="flex items-start justify-between mb-1">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground truncate">
              {project.name}
            </h3>
            <p className="text-[11px] text-muted-foreground font-mono mt-0.5">
              #{project.number} · {project.key}
            </p>
          </div>
          <div
            className="flex items-center gap-1 shrink-0 px-2 py-0.5"
            style={{
              background: `${trendColor(project.trend)}15`,
              border: `1px solid ${trendColor(project.trend)}30`,
            }}
          >
            <TrendIcon trend={project.trend} />
            <span
              className="text-[10px] font-bold uppercase tracking-wider"
              style={{ color: trendColor(project.trend) }}
            >
              {project.trend === 'none' ? 'NO DATA' : project.trend}
            </span>
          </div>
        </div>

        {/* ─── No Data State ────────────────────────────────── */}
        {isNoData ? (
          <div className="flex flex-col items-center justify-center py-6">
            <FileText className="h-8 w-8 mb-2" style={{ color: 'var(--theme-text-dim)' }} />
            <p className="text-xs text-muted-foreground">No POD data yet</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              Files will appear once received via email
            </p>
          </div>
        ) : (
          <>
            <Separator className="my-3" />

            {/* ─── KPI Row ────────────────────────────────────── */}
            <div className="flex items-center gap-0">
              {/* Today */}
              <div className="flex-1 min-w-0">
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                  Today
                </p>
                <p className="text-lg font-semibold tabular-nums mt-0.5" style={{ color: 'var(--theme-text-primary)' }}>
                  {project.today}
                </p>
              </div>

              <Separator orientation="vertical" className="h-8 mx-3" />

              {/* Yesterday */}
              <div className="flex-1 min-w-0">
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                  Yesterday
                </p>
                <p className="text-lg font-semibold text-foreground tabular-nums mt-0.5">
                  {project.yesterday}
                </p>
              </div>

              <Separator orientation="vertical" className="h-8 mx-3" />

              {/* Delta */}
              <div className="flex-1 min-w-0">
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                  Delta
                </p>
                <p
                  className="text-lg font-semibold tabular-nums mt-0.5"
                  style={{
                    color: isDown ? '#ef4444' : isUp ? '#22c55e' : 'var(--theme-text-primary)',
                  }}
                >
                  {formatDelta(project.delta_units, project.delta_pct)}
                </p>
              </div>
            </div>

            <Separator className="my-3" />

            {/* ─── Weekly Stats ────────────────────────────────── */}
            <div className="flex items-center gap-0 mb-3">
              <div className="flex-1">
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                  7-Day Total
                </p>
                <p className="text-base font-semibold text-foreground tabular-nums mt-0.5">
                  {project.seven_day_total}
                </p>
              </div>
              <Separator orientation="vertical" className="h-8 mx-3" />
              <div className="flex-1">
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                  All Time
                </p>
                <p className="text-base font-semibold text-foreground tabular-nums mt-0.5">
                  {project.all_time_total}
                </p>
              </div>
              <Separator orientation="vertical" className="h-8 mx-3" />
              <div className="flex-1">
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                  Last POD
                </p>
                <p className="text-[11px] font-medium text-foreground mt-1">
                  {project.days_since_last_pod === 0
                    ? 'Today'
                    : project.days_since_last_pod === 1
                      ? 'Yesterday'
                      : project.latest_pod_date
                        ? `${project.days_since_last_pod}d ago`
                        : '—'}
                </p>
              </div>
            </div>

            {/* ─── Mini Chart ─────────────────────────────────── */}
            <MiniTrendChart daily={project.daily} trend={project.trend} />
          </>
        )}
      </CardContent>
    </Card>
  );
}
