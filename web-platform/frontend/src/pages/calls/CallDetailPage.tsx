import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Clock, Users, Phone, CheckCircle2, XCircle,
  CheckCheck, FileText, AlertTriangle, Loader2
} from 'lucide-react';
import { api } from '../../api/client';
import type { CallDetail, CallReviewConstraint } from '../../types';
import { PageHeader } from '../../components/common/PageHeader';
import { ListSkeleton } from '../../components/common/LoadingSpinner';
import { ErrorState } from '../../components/common/ErrorState';
import { Button } from '@/components/ui/button';

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr + 'Z');
    return d.toLocaleDateString('en-US', {
      weekday: 'short',
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
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

// ---------------------------------------------------------------------------
// Category badge component
// ---------------------------------------------------------------------------
function CategoryBadge({
  category,
  onChange,
}: {
  category: string;
  onChange?: (newCategory: string) => void;
}) {
  const configs: Record<string, { bg: string; text: string; label: string }> = {
    NEW: { bg: 'color-mix(in srgb, var(--chart-1) 15%, transparent)', text: 'var(--chart-1)', label: 'NEW' },
    UPDATE: { bg: 'color-mix(in srgb, var(--chart-4) 15%, transparent)', text: 'var(--chart-4)', label: 'UPDATE' },
    CLOSE: { bg: 'color-mix(in srgb, var(--chart-3) 15%, transparent)', text: 'var(--chart-3)', label: 'CLOSE' },
    SKIP: { bg: 'color-mix(in srgb, var(--muted-foreground) 15%, transparent)', text: 'var(--muted-foreground)', label: 'SKIP' },
  };
  const config = configs[category] || configs.NEW;

  if (!onChange) {
    return (
      <span
        className="inline-block px-2 py-0.5 rounded text-[10px] font-bold tracking-wider"
        style={{ background: config.bg, color: config.text, border: `1px solid ${config.text}30` }}
      >
        {config.label}
      </span>
    );
  }

  return (
    <select
      value={category}
      onChange={(e) => onChange(e.target.value)}
      className="px-2 py-0.5 rounded text-[11px] font-bold tracking-wider border cursor-pointer bg-transparent"
      style={{ color: config.text, borderColor: `${config.text}40` }}
    >
      <option value="NEW">NEW</option>
      <option value="UPDATE">UPDATE</option>
      <option value="CLOSE">CLOSE</option>
      <option value="SKIP">SKIP</option>
    </select>
  );
}

// ---------------------------------------------------------------------------
// Priority badge
// ---------------------------------------------------------------------------
function PriorityDot({ priority }: { priority: string }) {
  const colors: Record<string, string> = {
    high: 'hsl(0 84% 60%)',
    medium: 'hsl(45 93% 58%)',
    low: 'hsl(210 14% 53%)',
  };
  const color = colors[priority] || colors.medium;
  return (
    <span
      className="inline-block w-2 h-2 rounded-full shrink-0"
      style={{ background: color }}
      title={`${priority} priority`}
    />
  );
}

// ---------------------------------------------------------------------------
// Constraint row component
// ---------------------------------------------------------------------------
function ConstraintRow({
  constraint,
  onApprove,
  onReject,
  onCategoryChange,
  loading,
}: {
  constraint: CallReviewConstraint;
  onApprove: () => void;
  onReject: () => void;
  onCategoryChange: (category: string) => void;
  loading: boolean;
}) {
  const isPending = constraint.action_status === 'pending';
  const isApproved = constraint.action_status === 'approved';
  const isRejected = constraint.action_status === 'rejected';
  const isPushed = constraint.action_status === 'pushed';

  return (
    <div
      className="border border-border rounded-lg p-3 transition-colors"
      style={{
        opacity: isRejected ? 0.5 : 1,
        borderColor: isApproved
          ? 'color-mix(in srgb, var(--chart-3) 40%, transparent)'
          : isPushed
            ? 'color-mix(in srgb, var(--chart-1) 40%, transparent)'
            : undefined,
      }}
    >
      <div className="flex items-start gap-3">
        {/* Priority dot + description */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <PriorityDot priority={constraint.priority} />
            <span className="text-[11px] font-medium text-muted-foreground">
              {constraint.discipline}
            </span>
            <CategoryBadge
              category={constraint.category}
              onChange={isPending ? onCategoryChange : undefined}
            />
          </div>
          <p className="text-sm text-foreground leading-relaxed">{constraint.description}</p>
          {constraint.owner && (
            <p className="text-xs text-muted-foreground mt-1">
              Owner: {constraint.owner}
            </p>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1.5 shrink-0">
          {isPending && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={onApprove}
                disabled={loading}
                className="h-8 px-2 text-xs"
                style={{ color: 'var(--chart-3)' }}
              >
                <CheckCircle2 className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onReject}
                disabled={loading}
                className="h-8 px-2 text-xs"
                style={{ color: 'hsl(0 84% 60%)' }}
              >
                <XCircle className="size-4" />
              </Button>
            </>
          )}
          {isApproved && (
            <span
              className="flex items-center gap-1 text-[10px] font-bold tracking-wider"
              style={{ color: 'var(--chart-3)' }}
            >
              <CheckCircle2 className="size-3.5" /> APPROVED
            </span>
          )}
          {isRejected && (
            <span
              className="flex items-center gap-1 text-[10px] font-bold tracking-wider text-muted-foreground"
            >
              <XCircle className="size-3.5" /> SKIPPED
            </span>
          )}
          {isPushed && (
            <span
              className="flex items-center gap-1 text-[10px] font-bold tracking-wider"
              style={{ color: 'var(--chart-1)' }}
            >
              <CheckCheck className="size-3.5" /> PUSHED
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main CallDetailPage
// ---------------------------------------------------------------------------
export function CallDetailPage() {
  const { botId } = useParams<{ botId: string }>();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showTranscript, setShowTranscript] = useState(false);

  const loadDetail = useCallback(async () => {
    if (!botId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.getCallDetail(botId);
      setDetail(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load call');
    } finally {
      setLoading(false);
    }
  }, [botId]);

  useEffect(() => { loadDetail(); }, [loadDetail]);

  const handleApprove = async (constraintId: string) => {
    setActionLoading(constraintId);
    try {
      await api.approveConstraint(constraintId);
      setDetail(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          constraints: prev.constraints.map(c =>
            c.id === constraintId ? { ...c, action_status: 'approved' as const } : c
          ),
        };
      });
    } catch (err) {
      console.error('Failed to approve:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async (constraintId: string) => {
    setActionLoading(constraintId);
    try {
      await api.rejectConstraint(constraintId);
      setDetail(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          constraints: prev.constraints.map(c =>
            c.id === constraintId ? { ...c, action_status: 'rejected' as const } : c
          ),
        };
      });
    } catch (err) {
      console.error('Failed to reject:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleCategoryChange = async (constraintId: string, category: string) => {
    try {
      await api.updateConstraintCategory(constraintId, category);
      setDetail(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          constraints: prev.constraints.map(c =>
            c.id === constraintId ? { ...c, category: category as any } : c
          ),
        };
      });
    } catch (err) {
      console.error('Failed to update category:', err);
    }
  };

  const handleApproveAll = async () => {
    if (!botId) return;
    setActionLoading('bulk');
    try {
      const result = await api.approveAllConstraints(botId);
      if (result.success) {
        setDetail(prev => {
          if (!prev) return prev;
          return {
            ...prev,
            constraints: prev.constraints.map(c =>
              c.action_status === 'pending' && c.category !== 'SKIP'
                ? { ...c, action_status: 'approved' as const }
                : c
            ),
          };
        });
      }
    } catch (err) {
      console.error('Failed to bulk approve:', err);
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col h-full min-h-0">
        <PageHeader title="Call Detail" />
        <div className="flex-1 p-6"><ListSkeleton /></div>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="flex flex-col h-full min-h-0">
        <PageHeader title="Call Detail" />
        <div className="flex-1 p-6">
          <ErrorState
            message={error || 'Call not found'}
            onRetry={loadDetail}
          />
        </div>
      </div>
    );
  }

  const pendingCount = detail.constraints.filter(c => c.action_status === 'pending').length;
  const approvedCount = detail.constraints.filter(c => c.action_status === 'approved').length;

  return (
    <div className="flex flex-col h-full min-h-0">
      <PageHeader
        title={detail.review?.meeting_title || `Call ${detail.bot_id.slice(0, 8)}`}
        subtitle={formatDate(detail.created_at)}
        actions={
          <Button variant="ghost" size="sm" onClick={() => navigate('/calls')}>
            <ArrowLeft className="size-4 mr-1" />
            Back
          </Button>
        }
      />

      <div className="flex-1 overflow-y-auto min-h-0" data-scroll-container>
        <div className="max-w-4xl mx-auto p-6 space-y-6">

          {/* ---------- Metadata header ---------- */}
          <div
            className="rounded-lg p-4 border border-border"
            style={{ background: 'var(--card)' }}
          >
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <span className="flex items-center gap-2 text-muted-foreground">
                <Clock className="size-4" />
                {formatDuration(detail.duration_minutes)}
              </span>
              <span className="flex items-center gap-2 text-muted-foreground">
                <Users className="size-4" />
                {detail.participant_count} participant{detail.participant_count !== 1 ? 's' : ''}
              </span>
              <span className="flex items-center gap-2 text-muted-foreground">
                <Phone className="size-4" />
                {detail.bot_name}
              </span>
            </div>
            {detail.participants.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {detail.participants.map((name, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border border-border bg-background text-foreground"
                  >
                    {name}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* ---------- Summary ---------- */}
          {detail.review?.summary && (
            <section>
              <h2 className="text-xs font-bold tracking-widest text-muted-foreground mb-2 uppercase">
                Meeting Summary
              </h2>
              <div
                className="rounded-lg p-4 border border-border text-sm leading-relaxed text-foreground whitespace-pre-wrap"
                style={{ background: 'var(--card)' }}
              >
                {detail.review.summary}
              </div>
            </section>
          )}

          {/* ---------- Constraint Review Table ---------- */}
          <section>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-xs font-bold tracking-widest text-muted-foreground uppercase">
                Constraints ({detail.constraints.length})
              </h2>
              {pendingCount > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleApproveAll}
                  disabled={actionLoading === 'bulk'}
                  className="text-xs"
                >
                  {actionLoading === 'bulk' ? (
                    <Loader2 className="size-3.5 mr-1 animate-spin" />
                  ) : (
                    <CheckCheck className="size-3.5 mr-1" />
                  )}
                  Approve All ({pendingCount})
                </Button>
              )}
              {pendingCount === 0 && approvedCount > 0 && (
                <span
                  className="text-[11px] font-bold tracking-wider"
                  style={{ color: 'var(--chart-3)' }}
                >
                  {approvedCount} approved
                </span>
              )}
            </div>

            {detail.constraints.length === 0 ? (
              <div
                className="rounded-lg p-6 border border-border text-center text-sm text-muted-foreground"
                style={{ background: 'var(--card)' }}
              >
                <AlertTriangle className="size-5 mx-auto mb-2 opacity-60" />
                No constraints extracted from this call.
                <br />
                <span className="text-xs">
                  The transcript may not have discussed constraints, or processing is still in progress.
                </span>
              </div>
            ) : (
              <div className="space-y-2">
                {detail.constraints.map(constraint => (
                  <ConstraintRow
                    key={constraint.id}
                    constraint={constraint}
                    onApprove={() => handleApprove(constraint.id)}
                    onReject={() => handleReject(constraint.id)}
                    onCategoryChange={(cat) => handleCategoryChange(constraint.id, cat)}
                    loading={actionLoading === constraint.id}
                  />
                ))}
              </div>
            )}
          </section>

          {/* ---------- Action Items ---------- */}
          {detail.review?.action_items && (
            <section>
              <h2 className="text-xs font-bold tracking-widest text-muted-foreground mb-2 uppercase">
                Action Items
              </h2>
              <div
                className="rounded-lg p-4 border border-border text-sm leading-relaxed text-foreground whitespace-pre-wrap"
                style={{ background: 'var(--card)' }}
              >
                {detail.review.action_items}
              </div>
            </section>
          )}

          {/* ---------- Decisions ---------- */}
          {detail.review?.decisions && (
            <section>
              <h2 className="text-xs font-bold tracking-widest text-muted-foreground mb-2 uppercase">
                Key Decisions
              </h2>
              <div
                className="rounded-lg p-4 border border-border text-sm leading-relaxed text-foreground whitespace-pre-wrap"
                style={{ background: 'var(--card)' }}
              >
                {detail.review.decisions}
              </div>
            </section>
          )}

          {/* ---------- Transcript preview ---------- */}
          {detail.transcript_preview && (
            <section>
              <button
                onClick={() => setShowTranscript(!showTranscript)}
                className="flex items-center gap-2 text-xs font-bold tracking-widest text-muted-foreground mb-2 uppercase hover:text-foreground transition-colors"
              >
                <FileText className="size-3.5" />
                Transcript {showTranscript ? '(hide)' : '(show)'}
              </button>
              {showTranscript && (
                <div
                  className="rounded-lg p-4 border border-border text-xs leading-relaxed text-muted-foreground font-mono whitespace-pre-wrap max-h-[500px] overflow-y-auto"
                  style={{ background: 'var(--card)' }}
                >
                  {detail.transcript_preview}
                  {detail.transcript_preview.length >= 10000 && (
                    <p className="text-center mt-4 text-muted-foreground italic">
                      ... transcript truncated (showing first 10,000 characters) ...
                    </p>
                  )}
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
