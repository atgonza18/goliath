import { useState, useEffect } from 'react';
import { ArrowLeft, ChevronDown, ChevronRight, MapPin, Loader2 } from 'lucide-react';
import { api } from '../../api/client';
import type { ProjectProductionDetail as DetailData, PodCategory } from '../../types';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ErrorState } from '../../components/common/ErrorState';

interface Props {
  projectKey: string;
  onBack: () => void;
}

function pctColor(pct: number | null) {
  if (pct == null) return { bg: 'rgba(100,100,100,0.1)', fg: 'var(--theme-text-dim)' };
  if (pct >= 90) return { bg: 'rgba(34,197,94,0.15)', fg: '#22c55e' };
  if (pct >= 50) return { bg: 'rgba(245,158,11,0.15)', fg: '#f59e0b' };
  if (pct >= 25) return { bg: 'rgba(59,130,246,0.15)', fg: '#3b82f6' };
  return { bg: 'rgba(239,68,68,0.15)', fg: '#ef4444' };
}

function noteColor(note: string) {
  const lower = note.toLowerCase();
  if (lower.includes('completed') || lower.includes('complete')) return { bg: 'rgba(34,197,94,0.15)', fg: '#22c55e' };
  if (lower.includes('hold') || lower.includes('rained') || lower.includes('weather') || lower.includes('snow')) return { bg: 'rgba(245,158,11,0.15)', fg: '#f59e0b' };
  return { bg: 'rgba(100,100,100,0.1)', fg: 'var(--theme-text-muted)' };
}

function formatQty(n: number | null): string {
  if (n == null) return '—';
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function CategorySection({ category }: { category: PodCategory }) {
  const [expanded, setExpanded] = useState(true);

  const validPcts = category.activities
    .map(a => a.pct_complete)
    .filter((p): p is number => p != null);
  const avgPct = validPcts.length > 0
    ? validPcts.reduce((s, v) => s + v, 0) / validPcts.length
    : null;

  return (
    <Card className="py-0 gap-0 overflow-hidden">
      {/* Category header */}
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left"
        style={{ borderBottom: expanded ? '2px solid var(--theme-border)' : undefined }}
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          {expanded
            ? <ChevronDown className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--theme-text-dim)' }} />
            : <ChevronRight className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--theme-text-dim)' }} />
          }
          <span className="text-sm font-semibold" style={{ color: 'var(--theme-text-primary)' }}>
            {category.category}
          </span>
          <span className="text-[10px]" style={{ color: 'var(--theme-text-dim)' }}>
            {category.activities.length} {category.activities.length === 1 ? 'activity' : 'activities'}
          </span>
        </div>
        {avgPct != null && (
          <span
            className="text-[10px] font-bold tabular-nums px-1.5 py-0.5 shrink-0"
            style={{ background: pctColor(avgPct).bg, color: pctColor(avgPct).fg }}
          >
            {avgPct.toFixed(1)}%
          </span>
        )}
      </button>

      {expanded && (
        <CardContent className="p-0">
          {category.activities.map((act, idx) => {
            const progressPct = (act.total_qty && act.qty_to_date != null)
              ? Math.min(100, (act.qty_to_date / act.total_qty) * 100)
              : null;
            const colors = pctColor(act.pct_complete);

            return (
              <div
                key={`${act.activity_name}-${idx}`}
                className="px-4 py-3 space-y-2"
                style={idx < category.activities.length - 1 ? { borderBottom: '1px solid var(--theme-border-subtle)' } : {}}
              >
                {/* Name + pct badge */}
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs font-semibold" style={{ color: 'var(--theme-text-primary)' }}>
                    {act.activity_name}
                  </span>
                  {act.pct_complete != null && (
                    <span
                      className="text-[10px] font-bold tabular-nums px-1.5 py-0.5 shrink-0"
                      style={{ background: colors.bg, color: colors.fg }}
                    >
                      {act.pct_complete.toFixed(1)}%
                    </span>
                  )}
                </div>

                {/* Progress bar */}
                {progressPct != null && (
                  <div className="h-1.5 w-full rounded-full" style={{ background: 'var(--theme-bg-tertiary)' }}>
                    <div
                      className="h-1.5 rounded-full transition-all"
                      style={{ width: `${progressPct}%`, background: colors.fg }}
                    />
                  </div>
                )}

                {/* Quantities + Yesterday */}
                {(act.qty_to_date != null || act.total_qty != null) && (
                  <div className="flex items-baseline gap-1">
                    <span className="text-sm font-semibold tabular-nums" style={{ color: 'var(--theme-text-primary)' }}>
                      {formatQty(act.qty_to_date)}
                    </span>
                    {act.total_qty != null && (
                      <>
                        <span className="text-[10px]" style={{ color: 'var(--theme-text-dim)' }}>/</span>
                        <span className="text-[10px] tabular-nums" style={{ color: 'var(--theme-text-muted)' }}>
                          {formatQty(act.total_qty)}
                        </span>
                      </>
                    )}
                    {act.unit && (
                      <span className="text-[10px]" style={{ color: 'var(--theme-text-dim)' }}>
                        {act.unit}
                      </span>
                    )}
                    {/* Yesterday's production — direct field from POD */}
                    <span
                      className="text-[10px] font-medium tabular-nums px-1.5 py-0.5 ml-1.5"
                      style={{
                        background: act.qty_completed_yesterday > 0
                          ? 'rgba(34,197,94,0.12)'
                          : 'rgba(100,100,100,0.08)',
                        color: act.qty_completed_yesterday > 0
                          ? '#22c55e'
                          : 'var(--theme-text-dim)',
                      }}
                    >
                      Y: {act.qty_completed_yesterday > 0 ? formatQty(act.qty_completed_yesterday) : '0'}
                    </span>
                  </div>
                )}

                {/* Location + notes */}
                <div className="flex flex-wrap gap-1.5">
                  {act.today_location && (
                    <span
                      className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5"
                      style={{ background: 'rgba(59,130,246,0.1)', color: '#3b82f6' }}
                    >
                      <MapPin className="h-2.5 w-2.5" />
                      {act.today_location}
                    </span>
                  )}
                  {act.notes && (() => {
                    const nc = noteColor(act.notes);
                    return (
                      <span
                        className="text-[9px] px-1.5 py-0.5 font-medium"
                        style={{ background: nc.bg, color: nc.fg }}
                      >
                        {act.notes}
                      </span>
                    );
                  })()}
                </div>
              </div>
            );
          })}
        </CardContent>
      )}
    </Card>
  );
}

export function ProjectProductionDetail({ projectKey, onBack }: Props) {
  const [data, setData] = useState<DetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.getProductionProjectDetail(projectKey)
      .then(result => {
        if (!cancelled) { setData(result); setLoading(false); }
      })
      .catch(err => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load project detail');
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [projectKey]);

  const totalActivities = data?.categories.reduce((s, c) => s + c.activities.length, 0) ?? 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        {data && (
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="text-[10px] font-bold tabular-nums px-1.5 py-0.5"
              style={{ background: 'var(--theme-accent-dim)', color: 'var(--theme-text-secondary)' }}
            >
              #{data.number}
            </span>
            <span className="text-lg font-semibold" style={{ color: 'var(--theme-text-primary)' }}>
              {data.name}
            </span>
            {data.latest_date && (
              <span className="text-[10px] tabular-nums" style={{ color: 'var(--theme-text-muted)' }}>
                {data.latest_date}
              </span>
            )}
            <span className="text-[10px]" style={{ color: 'var(--theme-text-dim)' }}>
              {totalActivities} activities / {data.categories.length} categories
            </span>
          </div>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16 gap-2">
          <Loader2 className="h-5 w-5 animate-spin" style={{ color: 'var(--theme-text-muted)' }} />
          <span className="text-sm" style={{ color: 'var(--theme-text-muted)' }}>Loading detail...</span>
        </div>
      ) : error ? (
        <ErrorState message={error} onRetry={() => window.location.reload()} />
      ) : data && data.categories.length === 0 ? (
        <div className="text-center py-16">
          <p className="text-sm text-muted-foreground">No production data available for this project.</p>
        </div>
      ) : data ? (
        <div className="space-y-3">
          {data.categories.map((cat) => (
            <CategorySection key={cat.category} category={cat} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
