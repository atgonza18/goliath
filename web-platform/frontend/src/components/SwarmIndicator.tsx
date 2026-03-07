import { useState, useEffect, useRef } from 'react';

interface AgentStatus {
  status: 'running' | 'completed' | 'failed';
  duration_ms?: number;
}

interface SwarmState {
  status: 'idle' | 'active' | 'completed';
  swarm_id?: string;
  agents?: string[];
  count?: number;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  succeeded?: number;
  failed?: number;
  agent_status?: Record<string, AgentStatus>;
}

/**
 * SwarmIndicator — shows when the Python orchestrator has dispatched a parallel
 * swarm of 2+ subagents. Polls /api/swarm/status every 1.5 seconds.
 *
 * States:
 *   idle      → hidden
 *   active    → animated amber badge with per-agent pills (spinner/checkmark)
 *   completed → brief green flash with results for 4 seconds, then fades
 */
export function SwarmIndicator() {
  const [state, setState] = useState<SwarmState>({ status: 'idle' });
  const [showComplete, setShowComplete] = useState(false);
  const [visible, setVisible] = useState(false);
  const prevStatusRef = useRef<string>('idle');
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Poll swarm status
  useEffect(() => {
    let mounted = true;

    const poll = async () => {
      try {
        const res = await fetch('/api/swarm/status');
        if (res.ok && mounted) {
          const data: SwarmState = await res.json();
          setState(data);
        }
      } catch {
        // Silent — API might be down
      }
    };

    poll(); // Initial fetch
    const interval = setInterval(poll, 1500);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  // Handle state transitions
  useEffect(() => {
    const prev = prevStatusRef.current;
    const curr = state.status;

    if (curr === 'active') {
      setVisible(true);
      setShowComplete(false);
      if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current);
    } else if (curr === 'completed' && prev === 'active') {
      // Transition from active → completed: show green flash
      setShowComplete(true);
      setVisible(true);
      if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current);
      fadeTimerRef.current = setTimeout(() => {
        setVisible(false);
        setShowComplete(false);
      }, 4000);
    } else if (curr === 'idle') {
      if (!showComplete) {
        setVisible(false);
      }
    }

    prevStatusRef.current = curr;
  }, [state.status, showComplete]);

  // Cleanup
  useEffect(() => {
    return () => {
      if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current);
    };
  }, []);

  if (!visible) return null;

  const agentStatus = state.agent_status || {};
  const runningCount = Object.values(agentStatus).filter(a => a.status === 'running').length;
  const completedCount = Object.values(agentStatus).filter(a => a.status === 'completed').length;
  const failedCount = Object.values(agentStatus).filter(a => a.status === 'failed').length;

  // Completed state — green flash with per-agent results
  if (showComplete) {
    return (
      <div
        className="px-3 py-2 animate-swarm-complete"
        style={{
          background: 'rgba(34, 197, 94, 0.1)',
          borderLeft: '3px solid rgb(34, 197, 94)',
        }}
      >
        <div className="flex items-center gap-2">
          <span style={{ color: 'rgb(34, 197, 94)', fontSize: '13px' }}>✓</span>
          <span
            className="text-[10px] font-bold tracking-widest"
            style={{ color: 'rgb(34, 197, 94)' }}
          >
            SWARM DONE
          </span>
          {state.duration_ms != null && (
            <span
              className="text-[9px] tracking-wider ml-auto"
              style={{ color: 'rgba(34, 197, 94, 0.7)' }}
            >
              {(state.duration_ms / 1000).toFixed(1)}s
            </span>
          )}
        </div>
        {/* Per-agent completion pills */}
        {state.agents && state.agents.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {state.agents.map((agent) => {
              const status = agentStatus[agent];
              const isFailed = status?.status === 'failed';
              return (
                <span
                  key={agent}
                  className="flex items-center gap-1 text-[8px] font-bold tracking-wider px-1.5 py-0.5"
                  style={{
                    background: isFailed ? 'rgba(239, 68, 68, 0.12)' : 'rgba(34, 197, 94, 0.12)',
                    color: isFailed ? 'rgba(239, 68, 68, 0.8)' : 'rgba(34, 197, 94, 0.8)',
                    border: `1px solid ${isFailed ? 'rgba(239, 68, 68, 0.2)' : 'rgba(34, 197, 94, 0.2)'}`,
                  }}
                >
                  {isFailed ? '✕' : '✓'} {agent.replace(/_/g, ' ').toUpperCase()}
                </span>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  // Active state — animated amber badge with per-agent pills
  return (
    <div
      className="px-3 py-2 animate-swarm-active"
      style={{
        background: 'rgba(245, 158, 11, 0.08)',
        borderLeft: '3px solid rgb(245, 158, 11)',
      }}
    >
      <div className="flex items-center gap-2">
        {/* Lightning bolt icon */}
        <span style={{ color: 'rgb(245, 158, 11)', fontSize: '12px' }}>⚡</span>

        <span
          className="text-[10px] font-bold tracking-widest"
          style={{ color: 'rgb(245, 158, 11)' }}
        >
          {runningCount > 0 ? `${runningCount} ACTIVE` : 'SWARM'}
        </span>

        {/* Counts badge */}
        <span
          className="text-[9px] font-bold tracking-wider ml-auto"
          style={{ color: 'rgba(245, 158, 11, 0.7)' }}
        >
          {completedCount > 0 && (
            <span style={{ color: 'rgb(34, 197, 94)' }}>
              {completedCount}✓{' '}
            </span>
          )}
          {failedCount > 0 && (
            <span style={{ color: 'rgb(239, 68, 68)' }}>
              {failedCount}✕{' '}
            </span>
          )}
          {state.count ?? 0} total
        </span>
      </div>

      {/* Per-agent pills with status indicators */}
      {state.agents && state.agents.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {state.agents.map((agent) => {
            const status = agentStatus[agent];
            const isRunning = !status || status.status === 'running';
            const isCompleted = status?.status === 'completed';
            const isFailed = status?.status === 'failed';

            return (
              <span
                key={agent}
                className="flex items-center gap-1 text-[8px] font-bold tracking-wider px-1.5 py-0.5"
                style={{
                  background: isFailed
                    ? 'rgba(239, 68, 68, 0.12)'
                    : isCompleted
                    ? 'rgba(34, 197, 94, 0.12)'
                    : 'rgba(245, 158, 11, 0.12)',
                  color: isFailed
                    ? 'rgba(239, 68, 68, 0.8)'
                    : isCompleted
                    ? 'rgba(34, 197, 94, 0.8)'
                    : 'rgba(245, 158, 11, 0.8)',
                  border: `1px solid ${
                    isFailed
                      ? 'rgba(239, 68, 68, 0.2)'
                      : isCompleted
                      ? 'rgba(34, 197, 94, 0.2)'
                      : 'rgba(245, 158, 11, 0.2)'
                  }`,
                  transition: 'all 0.3s ease',
                }}
              >
                {isRunning && (
                  <span
                    className="inline-block w-1.5 h-1.5 shrink-0"
                    style={{
                      background: 'rgb(245, 158, 11)',
                      animation: 'pulse 1s ease-in-out infinite',
                    }}
                  />
                )}
                {isCompleted && <span>✓</span>}
                {isFailed && <span>✕</span>}
                {agent.replace(/_/g, ' ').toUpperCase()}
                {isCompleted && status.duration_ms != null && (
                  <span style={{ opacity: 0.6 }}>
                    {(status.duration_ms / 1000).toFixed(1)}s
                  </span>
                )}
              </span>
            );
          })}
        </div>
      )}

      {/* Live timer */}
      {state.started_at && <SwarmTimer startedAt={state.started_at} />}
    </div>
  );
}

function SwarmTimer({ startedAt }: { startedAt: string }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = new Date(startedAt).getTime();
    const interval = setInterval(() => {
      setElapsed((Date.now() - start) / 1000);
    }, 100);
    return () => clearInterval(interval);
  }, [startedAt]);

  return (
    <span
      className="text-[9px] tracking-wider tabular-nums block mt-0.5"
      style={{ color: 'rgba(245, 158, 11, 0.5)' }}
    >
      {elapsed.toFixed(1)}s
    </span>
  );
}

/**
 * SwarmBadge — compact floating badge for top-right placement.
 * Shows "⚡ N agents active" when swarm is running.
 */
export function SwarmBadge() {
  const [state, setState] = useState<SwarmState>({ status: 'idle' });
  const [visible, setVisible] = useState(false);
  const [showComplete, setShowComplete] = useState(false);
  const prevStatusRef = useRef<string>('idle');
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const res = await fetch('/api/swarm/status');
        if (res.ok && mounted) setState(await res.json());
      } catch { /* silent */ }
    };
    poll();
    const interval = setInterval(poll, 1500);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  useEffect(() => {
    const prev = prevStatusRef.current;
    const curr = state.status;
    if (curr === 'active') {
      setVisible(true);
      setShowComplete(false);
      if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current);
    } else if (curr === 'completed' && prev === 'active') {
      setShowComplete(true);
      setVisible(true);
      if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current);
      fadeTimerRef.current = setTimeout(() => { setVisible(false); setShowComplete(false); }, 3000);
    } else if (curr === 'idle' && !showComplete) {
      setVisible(false);
    }
    prevStatusRef.current = curr;
  }, [state.status, showComplete]);

  useEffect(() => () => { if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current); }, []);

  if (!visible) return null;

  const agentStatus = state.agent_status || {};
  const runningCount = Object.values(agentStatus).filter(a => a.status === 'running').length;

  if (showComplete) {
    return (
      <div
        className="flex items-center gap-1.5 px-2.5 py-1.5"
        style={{
          background: 'rgba(34, 197, 94, 0.15)',
          border: '1px solid rgba(34, 197, 94, 0.3)',
          animation: 'fadeIn 0.2s ease',
        }}
      >
        <span style={{ color: 'rgb(34, 197, 94)', fontSize: '11px' }}>✓</span>
        <span className="text-[10px] font-bold tracking-wider" style={{ color: 'rgb(34, 197, 94)' }}>
          SWARM DONE
        </span>
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-1.5 px-2.5 py-1.5"
      style={{
        background: 'rgba(245, 158, 11, 0.12)',
        border: '1px solid rgba(245, 158, 11, 0.3)',
        animation: 'fadeIn 0.2s ease',
      }}
    >
      <span style={{ fontSize: '11px' }}>⚡</span>
      <span className="text-[10px] font-bold tracking-wider" style={{ color: 'rgb(245, 158, 11)' }}>
        {runningCount > 0 ? runningCount : state.count ?? 0} AGENTS ACTIVE
      </span>
    </div>
  );
}
