import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Phone, RefreshCw, Clock, Users, FileText, AlertCircle, CheckCircle2 } from 'lucide-react';
import { api } from '../../api/client';
import type { CallSummary } from '../../types';
import { PageHeader } from '../../components/common/PageHeader';
import { ListSkeleton } from '../../components/common/LoadingSpinner';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { Button } from '@/components/ui/button';

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr + 'Z');
    return d.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return dateStr;
  }
}

function formatDuration(minutes: number): string {
  if (!minutes) return '--';
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function getStatusInfo(status: string): { color: string; label: string; icon: typeof Phone } {
  switch (status) {
    case 'done':
    case 'call_ended':
      return { color: 'var(--chart-3)', label: 'Completed', icon: CheckCircle2 };
    case 'recording':
    case 'in_call':
      return { color: 'var(--chart-1)', label: 'In Call', icon: Phone };
    case 'joining':
    case 'waiting_room':
      return { color: 'var(--chart-4)', label: 'Joining', icon: Clock };
    case 'fatal':
      return { color: 'hsl(0 84% 60%)', label: 'Failed', icon: AlertCircle };
    default:
      return { color: 'var(--chart-5)', label: status, icon: Phone };
  }
}

function CallCard({ call, onClick }: { call: CallSummary; onClick: () => void }) {
  const statusInfo = getStatusInfo(call.status);
  const StatusIcon = statusInfo.icon;

  return (
    <button
      onClick={onClick}
      className="w-full text-left border border-border rounded-lg p-4 hover:border-[var(--theme-accent)] transition-colors bg-card"
      style={{ cursor: 'pointer' }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {/* Title row */}
          <div className="flex items-center gap-2">
            <StatusIcon className="size-4 shrink-0" style={{ color: statusInfo.color }} />
            <h3 className="text-sm font-semibold text-foreground truncate">
              {call.meeting_title || `Call ${call.bot_id.slice(0, 8)}`}
            </h3>
          </div>

          {/* Date */}
          <p className="text-xs text-muted-foreground mt-1">
            {formatDate(call.created_at)}
          </p>

          {/* Metadata row */}
          <div className="flex items-center gap-4 mt-2.5 flex-wrap">
            {call.participant_count > 0 && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Users className="size-3.5" />
                {call.participant_count} participants
              </span>
            )}
            {call.duration_minutes > 0 && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Clock className="size-3.5" />
                {formatDuration(call.duration_minutes)}
              </span>
            )}
            {call.has_transcript && (
              <span className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--chart-3)' }}>
                <FileText className="size-3.5" />
                Transcript ready
              </span>
            )}
          </div>

          {/* Participants preview */}
          {call.participants.length > 0 && (
            <p className="text-xs text-muted-foreground mt-1.5 truncate">
              {call.participants.slice(0, 4).join(', ')}
              {call.participants.length > 4 && ` +${call.participants.length - 4} more`}
            </p>
          )}
        </div>

        {/* Review status badge */}
        <div className="shrink-0">
          {call.review_status === 'reviewed' ? (
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider"
              style={{
                background: 'color-mix(in srgb, var(--chart-3) 15%, transparent)',
                color: 'var(--chart-3)',
                border: '1px solid color-mix(in srgb, var(--chart-3) 30%, transparent)',
              }}
            >
              REVIEWED
            </span>
          ) : call.has_transcript ? (
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider"
              style={{
                background: 'color-mix(in srgb, var(--chart-4) 15%, transparent)',
                color: 'var(--chart-4)',
                border: '1px solid color-mix(in srgb, var(--chart-4) 30%, transparent)',
              }}
            >
              NEEDS REVIEW
            </span>
          ) : null}
        </div>
      </div>

      {/* Error display */}
      {call.error && (
        <div className="mt-2 px-2 py-1 rounded text-xs" style={{
          background: 'color-mix(in srgb, hsl(0 84% 60%) 10%, transparent)',
          color: 'hsl(0 84% 60%)',
        }}>
          {call.error}
        </div>
      )}
    </button>
  );
}

export function CallsPage() {
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const loadCalls = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getCalls();
      setCalls(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load calls');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadCalls(); }, [loadCalls]);

  const completedCalls = calls.filter(c =>
    ['done', 'call_ended'].includes(c.status) && c.has_transcript
  );
  const activeCalls = calls.filter(c =>
    !['done', 'call_ended', 'fatal'].includes(c.status)
  );
  const failedCalls = calls.filter(c => c.status === 'fatal' || c.error);

  return (
    <div className="flex flex-col h-full min-h-0">
      <PageHeader
        title="Call Debrief"
        subtitle={`${completedCalls.length} calls with transcripts`}
        actions={
          <Button variant="ghost" size="sm" onClick={loadCalls} disabled={loading}>
            <RefreshCw className={`size-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        }
      />

      <div className="flex-1 overflow-y-auto min-h-0 p-6" data-scroll-container>
        {loading ? (
          <ListSkeleton />
        ) : error ? (
          <ErrorState
            message={error}
            onRetry={loadCalls}
          />
        ) : calls.length === 0 ? (
          <EmptyState
            icon={<Phone className="size-6" />}
            title="No calls recorded yet"
            description="Send a meeting bot to a Teams, Zoom, or Google Meet call to get started."
          />
        ) : (
          <div className="space-y-6 max-w-3xl mx-auto">
            {/* Active calls */}
            {activeCalls.length > 0 && (
              <section>
                <h2 className="text-xs font-bold tracking-widest text-muted-foreground mb-3 uppercase">
                  Active Calls
                </h2>
                <div className="space-y-2">
                  {activeCalls.map(call => (
                    <CallCard
                      key={call.bot_id}
                      call={call}
                      onClick={() => navigate(`/calls/${call.bot_id}`)}
                    />
                  ))}
                </div>
              </section>
            )}

            {/* Completed with transcripts */}
            {completedCalls.length > 0 && (
              <section>
                <h2 className="text-xs font-bold tracking-widest text-muted-foreground mb-3 uppercase">
                  Completed Calls
                </h2>
                <div className="space-y-2">
                  {completedCalls.map(call => (
                    <CallCard
                      key={call.bot_id}
                      call={call}
                      onClick={() => navigate(`/calls/${call.bot_id}`)}
                    />
                  ))}
                </div>
              </section>
            )}

            {/* Failed calls */}
            {failedCalls.length > 0 && (
              <section>
                <h2 className="text-xs font-bold tracking-widest text-muted-foreground mb-3 uppercase">
                  Failed
                </h2>
                <div className="space-y-2">
                  {failedCalls.map(call => (
                    <CallCard
                      key={call.bot_id}
                      call={call}
                      onClick={() => navigate(`/calls/${call.bot_id}`)}
                    />
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
