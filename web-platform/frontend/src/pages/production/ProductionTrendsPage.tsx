import { useState, useEffect, useRef, useCallback } from 'react';
import { RefreshCw, TrendingUp, TrendingDown, Minus, BarChart3, Activity } from 'lucide-react';
import { api } from '../../api/client';
import type { ProductionTrends } from '../../types';
import { PageHeader } from '../../components/common/PageHeader';
import { CardGridSkeleton } from '../../components/common/LoadingSpinner';
import { ErrorState } from '../../components/common/ErrorState';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';
import { ProjectTrendCard } from './ProjectTrendCard';
import { PortfolioChart } from './PortfolioChart';

const POLL_INTERVAL_MS = 60_000; // 60 seconds

export function ProductionTrendsPage() {
  const [trends, setTrends] = useState<ProductionTrends | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  const loadTrends = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const data = await api.getProductionTrends();
      if (mountedRef.current) {
        setTrends(data);
        setLastRefresh(new Date());
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to load production trends');
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  // Initial load + polling
  useEffect(() => {
    mountedRef.current = true;
    loadTrends(true);

    pollRef.current = setInterval(() => {
      loadTrends(false);
    }, POLL_INTERVAL_MS);

    return () => {
      mountedRef.current = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadTrends]);

  const portfolio = trends?.portfolio;
  const projects = trends?.projects ?? [];

  // Derived stats
  const portfolioDeltaPct =
    portfolio && portfolio.yesterday > 0
      ? Math.round(((portfolio.delta_units / portfolio.yesterday) * 100) * 10) / 10
      : portfolio && portfolio.today > 0
        ? 100
        : 0;

  const summaryCards = portfolio
    ? [
        {
          label: 'Today',
          value: portfolio.today,
          accent: portfolio.today > 0 ? 'var(--chart-1)' : undefined,
        },
        {
          label: 'Yesterday',
          value: portfolio.yesterday,
        },
        {
          label: 'Delta',
          value: portfolio.delta_units >= 0 ? `+${portfolio.delta_units}` : `${portfolio.delta_units}`,
          accent:
            portfolio.delta_units > 0
              ? '#22c55e'
              : portfolio.delta_units < 0
                ? '#ef4444'
                : undefined,
          sub: portfolioDeltaPct !== 0 ? `${portfolioDeltaPct > 0 ? '+' : ''}${portfolioDeltaPct}%` : undefined,
        },
        {
          label: '7-Day Total',
          value: portfolio.seven_day_total,
        },
        {
          label: 'Reporting Today',
          value: `${portfolio.projects_reporting_today}/${portfolio.total_projects}`,
          accent:
            portfolio.projects_reporting_today >= portfolio.total_projects * 0.75
              ? '#22c55e'
              : portfolio.projects_reporting_today >= portfolio.total_projects * 0.5
                ? 'var(--chart-3)'
                : '#ef4444',
        },
        {
          label: 'With Data',
          value: `${portfolio.projects_with_data}/${portfolio.total_projects}`,
        },
      ]
    : [];

  // Separate projects by trend status for ordering
  const projectsUp = projects.filter((p) => p.trend === 'up');
  const projectsDown = projects.filter((p) => p.trend === 'down');
  const projectsFlat = projects.filter((p) => p.trend === 'flat');
  const projectsNone = projects.filter((p) => p.trend === 'none');

  return (
    <div className="flex flex-col h-full min-h-0">
      <PageHeader
        title="Production Trends"
        subtitle={
          lastRefresh
            ? `${projects.length} projects · updated ${lastRefresh.toLocaleTimeString()}`
            : `${projects.length} projects`
        }
        actions={
          <div className="flex items-center gap-2">
            {/* Live indicator */}
            <span className="flex items-center gap-1.5">
              <span
                className="w-1.5 h-1.5"
                style={{
                  background: 'var(--chart-1)',
                  animation: 'pulse 2s ease-in-out infinite',
                }}
              />
              <span
                className="text-[9px] font-bold tracking-widest"
                style={{ color: 'var(--theme-text-muted)' }}
              >
                LIVE
              </span>
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => loadTrends(true)}
            >
              <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
            </Button>
          </div>
        }
      />

      <div className="flex-1 overflow-y-auto min-h-0 p-6" data-scroll-container>
        {loading && !trends ? (
          <div className="space-y-6">
            {/* Skeleton for summary cards */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Card key={i} className="py-0 gap-0">
                  <CardContent className="p-4 space-y-2">
                    <div className="h-3 w-16 bg-accent animate-pulse" />
                    <div className="h-7 w-10 bg-accent animate-pulse" />
                  </CardContent>
                </Card>
              ))}
            </div>
            <CardGridSkeleton count={12} />
          </div>
        ) : error && !trends ? (
          <ErrorState message={error} onRetry={() => loadTrends(true)} />
        ) : (
          <>
            {/* ─── Portfolio Summary Row ─────────────────────────── */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
              {summaryCards.map((card) => (
                <Card key={card.label} className="py-0 gap-0">
                  <CardContent className="p-4">
                    <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
                      {card.label}
                    </p>
                    <div className="flex items-baseline gap-1.5 mt-1">
                      <p
                        className="text-2xl font-semibold tabular-nums"
                        style={card.accent ? { color: card.accent } : {}}
                      >
                        {card.value}
                      </p>
                      {card.sub && (
                        <span
                          className="text-[11px] font-medium tabular-nums"
                          style={{ color: card.accent || 'var(--theme-text-muted)' }}
                        >
                          {card.sub}
                        </span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* ─── Portfolio 7-Day Chart ─────────────────────────── */}
            {portfolio && portfolio.seven_day_total > 0 && (
              <Card className="py-0 gap-0 mb-6">
                <CardContent className="p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <BarChart3 className="h-4 w-4" style={{ color: 'var(--chart-1)' }} />
                    <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--theme-text-secondary)' }}>
                      Portfolio — 7-Day POD Activity
                    </p>
                  </div>
                  <PortfolioChart daily={portfolio.daily} />
                </CardContent>
              </Card>
            )}

            {/* ─── Trend Legend ───────────────────────────────────── */}
            <div className="flex items-center gap-4 mb-4 flex-wrap">
              <div className="flex items-center gap-1.5">
                <TrendingUp className="h-3.5 w-3.5" style={{ color: '#22c55e' }} />
                <span className="text-[10px] font-medium tracking-wider" style={{ color: 'var(--theme-text-muted)' }}>
                  UP ({projectsUp.length})
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <TrendingDown className="h-3.5 w-3.5" style={{ color: '#ef4444' }} />
                <span className="text-[10px] font-medium tracking-wider" style={{ color: 'var(--theme-text-muted)' }}>
                  DOWN ({projectsDown.length})
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <Minus className="h-3.5 w-3.5" style={{ color: 'var(--chart-3)' }} />
                <span className="text-[10px] font-medium tracking-wider" style={{ color: 'var(--theme-text-muted)' }}>
                  FLAT ({projectsFlat.length})
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <Activity className="h-3.5 w-3.5" style={{ color: 'var(--theme-text-dim)' }} />
                <span className="text-[10px] font-medium tracking-wider" style={{ color: 'var(--theme-text-muted)' }}>
                  NO DATA ({projectsNone.length})
                </span>
              </div>
            </div>

            <Separator className="mb-6" />

            {/* ─── Project Cards Grid ────────────────────────────── */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {projects.map((project) => (
                <ProjectTrendCard key={project.key} project={project} />
              ))}
            </div>

            {projects.length === 0 && (
              <div className="text-center py-16">
                <p className="text-sm text-muted-foreground">
                  No project data available yet. POD files will appear once received.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
