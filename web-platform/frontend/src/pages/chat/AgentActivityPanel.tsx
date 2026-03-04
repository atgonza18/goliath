import { useState, useEffect, useRef, useCallback } from 'react';
import { Check, X, ChevronDown, ChevronRight } from 'lucide-react';
import type { ActiveAgent, ToolActivity } from '../../types';
import { getAgentColor, setAgentColor, AGENT_COLOR_PALETTE } from './agentColors';

interface AgentActivityPanelProps {
  agents: Map<string, ActiveAgent>;
  isProcessing: boolean;
  currentPass: number | null;
  passStatus: string | null;
  thinkingMessage: string | null;
}

function formatAgentName(name: string): string {
  return name.replace(/_/g, ' ').toUpperCase();
}

function formatToolName(name: string): string {
  return name.replace(/([A-Z])/g, ' $1').trim();
}

function LiveDuration({ startTime }: { startTime: number }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed((Date.now() - startTime) / 1000);
    }, 100);
    return () => clearInterval(interval);
  }, [startTime]);

  return <span className="tabular-nums">{elapsed.toFixed(1)}S</span>;
}

function ToolActivityLine({ tool, agentColor }: { tool: ToolActivity; agentColor: string }) {
  return (
    <div
      className="flex items-center gap-2 py-1 px-3 animate-tool-enter"
      style={{ borderLeft: `2px solid ${tool.completed ? '#1a1a25' : agentColor}40` }}
    >
      {tool.completed ? (
        <div className="w-1.5 h-1.5 shrink-0" style={{ background: '#333' }} />
      ) : (
        <div className="relative flex items-center justify-center shrink-0">
          <div className="w-1.5 h-1.5" style={{ background: agentColor }} />
          <div className="absolute w-3 h-3 animate-pulse-ring" style={{ border: `1px solid ${agentColor}40` }} />
        </div>
      )}
      <span
        className="text-[10px] tracking-wide truncate"
        style={{ color: tool.completed ? '#333' : '#666' }}
      >
        {formatToolName(tool.tool)}
      </span>
      {tool.inputPreview && !tool.completed && (
        <span className="text-[9px] truncate ml-auto max-w-[200px]" style={{ color: '#2a2a35' }}>
          {tool.inputPreview}
        </span>
      )}
    </div>
  );
}

function ColorPicker({ agentName, currentColor, onClose }: {
  agentName: string;
  currentColor: string;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="absolute left-6 top-full mt-1 z-20 p-2 flex flex-wrap gap-1.5"
      style={{ background: '#0c0c12', border: '2px solid #2a2a35', width: '164px' }}
    >
      {AGENT_COLOR_PALETTE.map((color) => (
        <button
          key={color}
          className="w-5 h-5 transition-transform hover:scale-125"
          style={{
            background: color,
            border: color === currentColor ? '2px solid #fff' : '2px solid transparent',
          }}
          onClick={(e) => {
            e.stopPropagation();
            setAgentColor(agentName, color);
            onClose();
          }}
        />
      ))}
    </div>
  );
}

function AgentCard({ agent, defaultExpanded }: { agent: ActiveAgent; defaultExpanded: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [showColorPicker, setShowColorPicker] = useState(false);
  const [color, setColor] = useState(() => getAgentColor(agent.agent));
  const [justCompleted, setJustCompleted] = useState(false);
  const wasRunningRef = useRef(!agent.completed);

  const isRunning = !agent.completed;
  const isSuccess = agent.completed && agent.success !== false;
  const isFailed = agent.completed && agent.success === false;

  // Detect completion transition
  useEffect(() => {
    if (agent.completed && wasRunningRef.current) {
      wasRunningRef.current = false;
      setJustCompleted(true);
      const timer = setTimeout(() => setJustCompleted(false), 800);
      return () => clearTimeout(timer);
    }
  }, [agent.completed]);

  // Auto-expand when running
  useEffect(() => {
    if (isRunning) setExpanded(true);
  }, [isRunning]);

  const handleColorChange = useCallback(() => {
    setColor(getAgentColor(agent.agent));
    setShowColorPicker(false);
  }, [agent.agent]);

  const activeColor = isFailed ? '#ef4444' : color;

  return (
    <div
      className={`relative ${justCompleted ? 'animate-agent-complete' : ''}`}
      style={{
        borderLeft: `4px solid ${isRunning ? activeColor : justCompleted ? activeColor : isFailed ? '#ef4444' : '#1a1a25'}`,
        borderBottom: '1px solid #1a1a25',
        '--agent-flash-color': activeColor,
      } as React.CSSProperties}
    >
      {/* Card header */}
      <button
        className="flex items-center gap-3 w-full text-left px-4 py-3 group"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Status indicator */}
        <div className="flex items-center justify-center w-5 h-5 shrink-0">
          {isRunning ? (
            <div className="relative flex items-center justify-center">
              <div className="w-2.5 h-2.5" style={{ background: activeColor }} />
              <div className="absolute w-5 h-5 animate-pulse-ring" style={{ border: `1px solid ${activeColor}50` }} />
            </div>
          ) : isSuccess ? (
            <div
              className={`flex items-center justify-center w-5 h-5 ${justCompleted ? 'animate-success-stamp' : ''}`}
              style={{ border: `2px solid ${activeColor}` }}
            >
              <Check className="h-3 w-3" style={{ color: activeColor }} strokeWidth={3} />
            </div>
          ) : isFailed ? (
            <div className="flex items-center justify-center w-5 h-5" style={{ border: '2px solid #ef4444' }}>
              <X className="h-3 w-3" style={{ color: '#ef4444' }} strokeWidth={3} />
            </div>
          ) : (
            <div className="w-2 h-2" style={{ background: '#2a2a35' }} />
          )}
        </div>

        {/* Agent name + task */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={`text-[12px] truncate tracking-wider ${isRunning ? 'font-bold' : 'font-medium'}`}
              style={{ color: isRunning ? activeColor : isFailed ? '#ef4444' : '#777' }}
            >
              {formatAgentName(agent.agent)}
            </span>
            {/* Color dot — click to open picker */}
            <div className="relative">
              <button
                className="w-2.5 h-2.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
                style={{ background: color }}
                onClick={(e) => {
                  e.stopPropagation();
                  setShowColorPicker(!showColorPicker);
                }}
                title="Change color"
              />
              {showColorPicker && (
                <ColorPicker
                  agentName={agent.agent}
                  currentColor={color}
                  onClose={handleColorChange}
                />
              )}
            </div>
          </div>
          {agent.task && (
            <span
              className="text-[10px] block mt-0.5 leading-relaxed"
              style={{
                color: isRunning ? '#888' : '#444',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {agent.task}
            </span>
          )}
        </div>

        {/* Duration + expand */}
        <div className="flex items-center gap-2 shrink-0">
          <span
            className="text-[11px] font-bold tracking-wider"
            style={{ color: isRunning ? activeColor : '#444' }}
          >
            {agent.completed && agent.duration != null
              ? `${(agent.duration / 1000).toFixed(1)}S`
              : isRunning
              ? <LiveDuration startTime={agent.startTime} />
              : null}
          </span>
          <ChevronRight
            className={`h-3 w-3 transition-transform duration-100 ${expanded ? 'rotate-90' : ''}`}
            style={{ color: '#333' }}
          />
        </div>
      </button>

      {/* Tool activity log / expanded details */}
      {expanded && (
        <div className="pb-2 ml-8" style={{ borderTop: '1px solid #111118' }}>
          {agent.tools.length > 0 ? (
            agent.tools.map((tool, i) => (
              <ToolActivityLine key={`${tool.tool}-${i}`} tool={tool} agentColor={activeColor} />
            ))
          ) : isRunning ? (
            <div className="flex items-center gap-2 py-1.5 px-3 animate-tool-enter">
              <div className="relative flex items-center justify-center shrink-0">
                <div className="w-1.5 h-1.5" style={{ background: activeColor }} />
                <div className="absolute w-3 h-3 animate-pulse-ring" style={{ border: `1px solid ${activeColor}40` }} />
              </div>
              <span className="text-[10px] tracking-wide animate-status-pulse" style={{ color: '#444' }}>
                WORKING...
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 py-1.5 px-3">
              <div className="w-1.5 h-1.5 shrink-0" style={{ background: '#333' }} />
              <span className="text-[10px] tracking-wide" style={{ color: '#333' }}>
                {isSuccess ? 'COMPLETED' : 'FAILED'}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function AgentActivityPanel({
  agents,
  isProcessing,
  currentPass,
  passStatus,
  thinkingMessage,
}: AgentActivityPanelProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  const prevCountRef = useRef(0);
  const autoCollapseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const agentList = Array.from(agents.values());
  const running = agentList.filter((a) => !a.completed);
  const completed = agentList.filter((a) => a.completed);
  const succeeded = completed.filter((a) => a.success !== false);
  const failed = completed.filter((a) => a.success === false);

  // Auto-expand when new agents arrive
  useEffect(() => {
    if (agentList.length > prevCountRef.current) setIsExpanded(true);
    prevCountRef.current = agentList.length;
  }, [agentList.length]);

  // Auto-collapse 1.5s after all agents complete
  useEffect(() => {
    const allDone = agentList.length > 0 && running.length === 0 && !isProcessing;
    if (allDone) {
      autoCollapseTimerRef.current = setTimeout(() => setIsExpanded(false), 1500);
    }
    return () => {
      if (autoCollapseTimerRef.current) clearTimeout(autoCollapseTimerRef.current);
    };
  }, [agentList.length, running.length, isProcessing]);

  if (!isProcessing && agentList.length === 0) return null;

  const passLabel = currentPass
    ? currentPass === 1
      ? passStatus === 'start' ? 'ROUTING' : 'ROUTED'
      : passStatus === 'start' ? 'SYNTHESIZING' : 'SYNTHESIZED'
    : agentList.length > 0 && running.length > 0 ? 'DISPATCHING AGENTS' : null;

  // Thinking only — no agents yet
  if (agentList.length === 0 && thinkingMessage) {
    return (
      <div className="animate-fade-in ml-[42px]">
        <div
          className="flex items-center gap-3 px-4 py-3"
          style={{ background: '#0a0a10', borderLeft: '4px solid #fbbf24' }}
        >
          <div className="relative flex items-center justify-center">
            <div className="w-2.5 h-2.5" style={{ background: '#fbbf24' }} />
            <div className="absolute w-5 h-5 animate-pulse-ring" style={{ border: '1px solid rgba(251,191,36,0.4)' }} />
          </div>
          <span className="text-[12px] font-bold tracking-wider" style={{ color: '#fbbf24' }}>
            {thinkingMessage.toUpperCase()}
          </span>
        </div>
      </div>
    );
  }

  const allDone = agentList.length > 0 && running.length === 0 && !isProcessing;
  const totalDuration = completed
    .filter((a) => a.duration != null)
    .reduce((sum, a) => sum + (a.duration || 0), 0);

  return (
    <div className="animate-fade-in ml-[42px]">
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2.5 w-full text-left py-1.5 group"
      >
        <div className="flex items-center gap-2">
          {running.length > 0 ? (
            <div className="relative flex items-center justify-center">
              <div className="w-2 h-2" style={{ background: '#fbbf24' }} />
              <div className="absolute w-5 h-5 animate-pulse-ring" style={{ border: '1px solid rgba(251,191,36,0.4)' }} />
            </div>
          ) : allDone ? (
            <div className="w-2 h-2" style={{ background: '#a3e635' }} />
          ) : (
            <div className="w-2 h-2" style={{ background: '#2a2a35' }} />
          )}

          <span className="text-[11px] font-bold tracking-wider" style={{ color: running.length > 0 ? '#fbbf24' : '#555' }}>
            {allDone
              ? `${succeeded.length} AGENT${succeeded.length !== 1 ? 'S' : ''} COMPLETED · ${(totalDuration / 1000).toFixed(1)}S${failed.length > 0 ? ` · ${failed.length} FAILED` : ''}`
              : `${agentList.length} AGENT${agentList.length !== 1 ? 'S' : ''}${running.length > 0 ? ` · ${running.length} RUNNING` : ''}`}
          </span>
        </div>

        {passLabel && (
          <span className="text-[10px] font-bold tracking-widest ml-auto mr-1" style={{ color: '#a78bfa' }}>
            {currentPass ? `PASS ${currentPass} · ` : ''}{passLabel}
          </span>
        )}

        <ChevronDown
          className={`h-3 w-3 transition-transform duration-100 ${isExpanded ? '' : '-rotate-90'}`}
          style={{ color: '#444' }}
        />
      </button>

      {/* Agent cards */}
      {isExpanded && agentList.length > 0 && (
        <div className="mt-1.5 animate-expand">
          <div
            className="relative overflow-hidden"
            style={{ background: '#0a0a10', border: '2px solid #2a2a35' }}
          >
            {agentList.map((agent) => (
              <AgentCard
                key={agent.agent}
                agent={agent}
                defaultExpanded={!agent.completed}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
