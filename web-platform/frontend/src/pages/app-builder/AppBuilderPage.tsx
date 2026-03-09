import { useState, useEffect, useRef, useCallback, Component, type ReactNode, type ErrorInfo } from 'react';
import {
  ArrowLeft,
  ArrowUp,
  Wrench,
  Rocket,
  ChevronRight,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  Loader,
  Square,
  Terminal,
} from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { renderMarkdown } from '@/utils/markdown';

// ─── Types ────────────────────────────────────────────────────────────────────

type Intent = 'goliath-feature' | 'new-app';
type BackendChoice = 'postgres' | 'convex-cloud' | 'convex-self-hosted';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  streaming?: boolean;
}

// ─── CSS Keyframes ───────────────────────────────────────────────────────────

function AppBuilderStyles() {
  return (
    <style>{`
      @keyframes cursor-blink {
        0%, 100% { opacity: 1; }
        50% { opacity: 0; }
      }
      @keyframes dot-pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.4; transform: scale(0.75); }
      }
      @keyframes terminal-scanline {
        0% { opacity: 0; }
        50% { opacity: 0.03; }
        100% { opacity: 0; }
      }
      @keyframes glow-line {
        0% { background-position: -200% 0; }
        100% { background-position: 200% 0; }
      }
    `}</style>
  );
}

// ─── Error Boundary (prevents streaming crashes from killing the entire app) ─

interface StreamErrorBoundaryProps {
  children: ReactNode;
  fallbackMessage?: string;
}

interface StreamErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class StreamErrorBoundary extends Component<StreamErrorBoundaryProps, StreamErrorBoundaryState> {
  constructor(props: StreamErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): StreamErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('[StreamErrorBoundary] Caught render error:', error.message, errorInfo.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            padding: '16px 20px',
            background: 'rgba(239, 68, 68, 0.06)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderLeft: '3px solid #ef4444',
            margin: '8px 0',
          }}
        >
          <span
            style={{
              display: 'block',
              fontSize: '10px',
              fontWeight: 700,
              letterSpacing: '0.12em',
              color: '#ef4444',
              fontFamily: 'JetBrains Mono, monospace',
              marginBottom: '4px',
            }}
          >
            RENDER ERROR
          </span>
          <span
            style={{
              fontSize: '11px',
              color: 'var(--theme-text-muted)',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.03em',
              lineHeight: 1.5,
            }}
          >
            {this.props.fallbackMessage || 'Stream display crashed. Content was received — refresh to view.'}
          </span>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              display: 'block',
              marginTop: '8px',
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.1em',
              color: '#ef4444',
              background: 'transparent',
              border: '1px solid rgba(239, 68, 68, 0.4)',
              padding: '4px 10px',
              cursor: 'pointer',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            RETRY RENDER
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ─── Phase 1 Infrastructure Status Banner ────────────────────────────────────

type InfraStatus = 'checking' | 'not-configured' | 'partial' | 'ready';

interface InfraCheckResult {
  status: InfraStatus;
  docker: boolean | null;
  traefik: boolean | null;
  domain: string | null;
  helloWorldUrl: string | null;
  lastChecked: string | null;
}

function useInfraStatus(): InfraCheckResult {
  const [result, setResult] = useState<InfraCheckResult>({
    status: 'checking',
    docker: null,
    traefik: null,
    domain: null,
    helloWorldUrl: null,
    lastChecked: null,
  });

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const res = await fetch('/api/infra/phase1-status');
        if (res.ok) {
          const data = await res.json();
          setResult({
            status: data.traefik_running && data.hello_world_running ? 'ready' :
                    data.docker_installed ? 'partial' : 'not-configured',
            docker: data.docker_installed ?? false,
            traefik: data.traefik_running ?? false,
            domain: data.domain ?? null,
            helloWorldUrl: data.hello_world_url ?? null,
            lastChecked: data.setup_completed_at ?? null,
          });
        } else {
          setResult({
            status: 'not-configured',
            docker: false,
            traefik: false,
            domain: null,
            helloWorldUrl: null,
            lastChecked: null,
          });
        }
      } catch {
        setResult({
          status: 'not-configured',
          docker: false,
          traefik: false,
          domain: null,
          helloWorldUrl: null,
          lastChecked: null,
        });
      }
    };

    checkStatus();
  }, []);

  return result;
}

function InfraStatusBanner() {
  const infra = useInfraStatus();
  const [expanded, setExpanded] = useState(false);

  const statusConfig = {
    checking: {
      color: 'var(--theme-text-dim)',
      bgColor: 'var(--theme-bg-tertiary)',
      borderColor: 'var(--theme-border)',
      label: 'CHECKING INFRASTRUCTURE...',
      icon: <Loader size={12} className="animate-spin" />,
    },
    'not-configured': {
      color: 'var(--chart-4)',
      bgColor: 'rgba(251, 146, 60, 0.06)',
      borderColor: 'rgba(251, 146, 60, 0.3)',
      label: 'INFRASTRUCTURE NOT CONFIGURED',
      icon: <AlertCircle size={12} />,
    },
    partial: {
      color: 'var(--chart-3)',
      bgColor: 'rgba(250, 204, 21, 0.06)',
      borderColor: 'rgba(250, 204, 21, 0.3)',
      label: 'INFRASTRUCTURE PARTIALLY CONFIGURED',
      icon: <AlertCircle size={12} />,
    },
    ready: {
      color: '#22c55e',
      bgColor: 'rgba(34, 197, 94, 0.06)',
      borderColor: 'rgba(34, 197, 94, 0.3)',
      label: 'INFRASTRUCTURE READY',
      icon: <CheckCircle size={12} />,
    },
  };

  const config = statusConfig[infra.status];

  return (
    <div
      style={{
        background: config.bgColor,
        border: `1px solid ${config.borderColor}`,
        padding: '12px 16px',
        marginBottom: '20px',
        width: '100%',
        maxWidth: '640px',
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          width: '100%',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          padding: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ color: config.color, flexShrink: 0, display: 'flex', alignItems: 'center' }}>{config.icon}</span>
          <span
            style={{
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.14em',
              color: config.color,
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            PHASE 1: {config.label}
          </span>
        </div>
        <ChevronRight
          size={11}
          style={{
            color: 'var(--theme-text-dim)',
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform 0.15s',
            flexShrink: 0,
          }}
        />
      </button>

      {expanded && (
        <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <InfraCheckItem label="Docker Engine" status={infra.docker} />
          <InfraCheckItem label="Traefik Reverse Proxy" status={infra.traefik} />
          <InfraCheckItem
            label="Wildcard DNS"
            status={infra.domain ? true : false}
            detail={infra.domain ? `*.${infra.domain}` : undefined}
          />

          {infra.helloWorldUrl && infra.status === 'ready' && (
            <a
              href={infra.helloWorldUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                fontSize: '9px',
                fontWeight: 700,
                letterSpacing: '0.1em',
                color: '#22c55e',
                fontFamily: 'JetBrains Mono, monospace',
                textDecoration: 'none',
                marginTop: '4px',
              }}
            >
              <ExternalLink size={10} />
              OPEN TEST APP: {infra.helloWorldUrl}
            </a>
          )}

          {infra.status === 'not-configured' && (
            <div
              style={{
                marginTop: '6px',
                padding: '10px 12px',
                background: 'var(--theme-bg-secondary)',
                border: '1px solid var(--theme-border)',
                borderLeft: '3px solid var(--chart-4)',
              }}
            >
              <span
                style={{
                  display: 'block',
                  fontSize: '9px',
                  fontWeight: 700,
                  letterSpacing: '0.12em',
                  color: 'var(--chart-4)',
                  fontFamily: 'JetBrains Mono, monospace',
                  marginBottom: '6px',
                }}
              >
                SETUP REQUIRED (SSH)
              </span>
              <pre
                style={{
                  fontSize: '9px',
                  color: 'var(--theme-text-muted)',
                  fontFamily: 'JetBrains Mono, monospace',
                  letterSpacing: '0.04em',
                  lineHeight: 1.8,
                  whiteSpace: 'pre-wrap',
                  margin: 0,
                }}
              >
{`cd /opt/goliath/infrastructure
cp .env.example .env
nano .env           # Set GOLIATH_DOMAIN + ACME_EMAIL
sudo bash setup-phase1.sh`}
              </pre>
            </div>
          )}

          {infra.lastChecked && (
            <span
              style={{
                fontSize: '8px',
                color: 'var(--theme-text-dim)',
                fontFamily: 'JetBrains Mono, monospace',
                letterSpacing: '0.08em',
                marginTop: '2px',
              }}
            >
              LAST CHECKED: {new Date(infra.lastChecked).toLocaleString()}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function InfraCheckItem({ label, status, detail }: { label: string; status: boolean | null; detail?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      {status === null ? (
        <Loader size={10} className="animate-spin" style={{ color: 'var(--theme-text-dim)', flexShrink: 0 }} />
      ) : status ? (
        <CheckCircle size={10} style={{ color: '#22c55e', flexShrink: 0 }} />
      ) : (
        <AlertCircle size={10} style={{ color: 'var(--chart-4)', flexShrink: 0 }} />
      )}
      <span
        style={{
          fontSize: '10px',
          fontWeight: 600,
          color: status ? 'var(--theme-text-secondary)' : 'var(--theme-text-dim)',
          fontFamily: 'JetBrains Mono, monospace',
          letterSpacing: '0.06em',
        }}
      >
        {label}
      </span>
      {detail && (
        <span
          style={{
            fontSize: '9px',
            color: 'var(--theme-text-dim)',
            fontFamily: 'JetBrains Mono, monospace',
            letterSpacing: '0.04em',
          }}
        >
          ({detail})
        </span>
      )}
    </div>
  );
}

// ─── Intent Selector ─────────────────────────────────────────────────────────

interface IntentSelectorProps {
  onSelect: (intent: Intent) => void;
}

function IntentSelector({ onSelect }: IntentSelectorProps) {
  return (
    <div className="flex flex-col items-center justify-center flex-1 p-8 min-h-0">
      <div className="text-center mb-12 max-w-lg">
        <div
          style={{
            display: 'inline-block',
            background: 'var(--theme-accent-dim)',
            border: '1px solid var(--theme-accent)',
            padding: '3px 10px',
            marginBottom: '16px',
          }}
        >
          <span
            style={{
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.18em',
              color: 'var(--theme-accent)',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            APP BUILDER
          </span>
        </div>
        <h1
          style={{
            fontSize: '22px',
            fontWeight: 700,
            color: 'var(--theme-text-primary)',
            letterSpacing: '0.04em',
            fontFamily: 'JetBrains Mono, monospace',
            marginBottom: '10px',
          }}
        >
          What are you building?
        </h1>
        <p
          style={{
            fontSize: '12px',
            color: 'var(--theme-text-muted)',
            letterSpacing: '0.06em',
            fontFamily: 'JetBrains Mono, monospace',
            lineHeight: 1.6,
          }}
        >
          Select your build intent before DevOps starts work.
          This eliminates ambiguity and determines how code gets generated.
        </p>
      </div>

      <InfraStatusBanner />

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: '20px',
          width: '100%',
          maxWidth: '640px',
        }}
      >
        <IntentCard
          icon={<Wrench size={32} />}
          emoji=""
          label="Goliath Feature"
          sublabel="PATCH MODE"
          description="Add a page, component, API route, or agent to Goliath itself. DevOps edits the existing codebase and may trigger a restart."
          tags={['Edits Goliath source', 'May require RESTART', 'Uses existing stack']}
          accentColor="var(--chart-4)"
          onClick={() => onSelect('goliath-feature')}
        />
        <IntentCard
          icon={<Rocket size={32} />}
          emoji=""
          label="New App"
          sublabel="CONTAINER MODE"
          description="Spin up a standalone application in its own Docker container with its own database, URL, and isolated runtime."
          tags={['Isolated container', 'Auto-assigned URL', 'Never touches Goliath']}
          accentColor="var(--chart-1)"
          onClick={() => onSelect('new-app')}
        />
      </div>
    </div>
  );
}

interface IntentCardProps {
  icon: React.ReactNode;
  emoji: string;
  label: string;
  sublabel: string;
  description: string;
  tags: string[];
  accentColor: string;
  onClick: () => void;
}

function IntentCard({ icon, label, sublabel, description, tags, accentColor, onClick }: IntentCardProps) {
  const [hovered, setHovered] = useState(false);

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered ? 'var(--theme-bg-tertiary)' : 'var(--theme-bg-secondary)',
        border: hovered ? `2px solid ${accentColor}` : '2px solid var(--theme-border)',
        padding: '28px 24px',
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'border-color 0.15s, background 0.15s, transform 0.1s',
        transform: hovered ? 'translateY(-2px)' : 'translateY(0)',
        display: 'flex',
        flexDirection: 'column',
        gap: '14px',
        boxShadow: hovered ? `0 0 0 1px ${accentColor}22, 0 4px 20px ${accentColor}18` : 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ color: accentColor, flexShrink: 0, display: 'flex', alignItems: 'center' }}>{icon}</span>
        <span
          style={{
            fontSize: '8px',
            fontWeight: 700,
            letterSpacing: '0.14em',
            color: accentColor,
            fontFamily: 'JetBrains Mono, monospace',
            background: `${accentColor}18`,
            border: `1px solid ${accentColor}44`,
            padding: '2px 8px',
          }}
        >
          {sublabel}
        </span>
      </div>

      <div>
        <span
          style={{
            display: 'block',
            fontSize: '15px',
            fontWeight: 700,
            color: hovered ? accentColor : 'var(--theme-text-primary)',
            letterSpacing: '0.06em',
            fontFamily: 'JetBrains Mono, monospace',
            transition: 'color 0.15s',
            marginBottom: '6px',
          }}
        >
          {label}
        </span>
        <span
          style={{
            display: 'block',
            fontSize: '11px',
            color: 'var(--theme-text-muted)',
            letterSpacing: '0.04em',
            fontFamily: 'JetBrains Mono, monospace',
            lineHeight: 1.55,
          }}
        >
          {description}
        </span>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px' }}>
        {tags.map((tag) => (
          <span
            key={tag}
            style={{
              fontSize: '9px',
              fontWeight: 600,
              letterSpacing: '0.1em',
              color: 'var(--theme-text-dim)',
              border: '1px solid var(--theme-border)',
              padding: '2px 7px',
              fontFamily: 'JetBrains Mono, monospace',
              background: 'var(--theme-bg-tertiary)',
            }}
          >
            {tag}
          </span>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginTop: '4px' }}>
        <span
          style={{
            fontSize: '10px',
            fontWeight: 700,
            color: hovered ? accentColor : 'var(--theme-text-dim)',
            letterSpacing: '0.1em',
            fontFamily: 'JetBrains Mono, monospace',
            transition: 'color 0.15s',
          }}
        >
          SELECT
        </span>
        <ChevronRight
          size={12}
          style={{ color: hovered ? accentColor : 'var(--theme-text-dim)', transition: 'color 0.15s' }}
        />
      </div>
    </button>
  );
}

// ─── Backend Selector (New App flow) ─────────────────────────────────────────

interface BackendSelectorProps {
  onSelect: (backend: BackendChoice) => void;
  onBack: () => void;
}

const BACKEND_OPTIONS: {
  id: BackendChoice;
  emoji: string;
  label: string;
  sublabel: string;
  description: string;
  pros: string[];
  accentColor: string;
}[] = [
  {
    id: 'postgres',
    emoji: '',
    label: 'Postgres',
    sublabel: 'SIDECAR CONTAINER',
    description:
      'A Postgres container runs alongside your app in docker-compose. Fully isolated on-server, standard SQL. Connection injected as DATABASE_URL.',
    pros: ['On-server', 'Standard SQL', 'Destroyed with app', 'Zero external deps'],
    accentColor: 'var(--chart-2)',
  },
  {
    id: 'convex-cloud',
    emoji: '',
    label: 'Convex (Cloud)',
    sublabel: 'CONVEX.DEV',
    description:
      'App connects to convex.dev cloud. DevOps generates schema + server functions. Best for real-time apps. Requires a Convex API key.',
    pros: ['Real-time sync', 'Fast prototyping', 'Live subscriptions', 'No local DB'],
    accentColor: 'var(--chart-3)',
  },
  {
    id: 'convex-self-hosted',
    emoji: '',
    label: 'Convex (Self-Hosted)',
    sublabel: 'ON-SERVER',
    description:
      'Open-source Convex backend runs as an additional Docker container. All data on-server, no external dependency, real-time sync.',
    pros: ['Data sovereignty', 'Real-time sync', 'No vendor lock-in', 'More complex'],
    accentColor: 'var(--chart-5)',
  },
];

function BackendSelector({ onSelect, onBack }: BackendSelectorProps) {
  return (
    <div className="flex flex-col flex-1 p-8 min-h-0 overflow-y-auto" data-scroll-container>
      <BackNavBar onBack={onBack} label="Back to intent selector" />

      <div className="mb-10 mt-2">
        <Breadcrumb items={['App Builder', 'New App', 'Backend']} />
        <h2
          style={{
            fontSize: '18px',
            fontWeight: 700,
            color: 'var(--theme-text-primary)',
            letterSpacing: '0.04em',
            fontFamily: 'JetBrains Mono, monospace',
            marginBottom: '6px',
            marginTop: '14px',
          }}
        >
          Choose a backend
        </h2>
        <p style={{ fontSize: '11px', color: 'var(--theme-text-muted)', fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.04em', lineHeight: 1.6 }}>
          This is locked at build time. Changing it after deploy requires a full redeploy.
        </p>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: '16px',
          maxWidth: '820px',
        }}
      >
        {BACKEND_OPTIONS.map((opt) => (
          <BackendCard
            key={opt.id}
            {...opt}
            onClick={() => onSelect(opt.id)}
          />
        ))}
      </div>

      <p
        style={{
          marginTop: '28px',
          fontSize: '9px',
          color: 'var(--theme-text-dim)',
          fontFamily: 'JetBrains Mono, monospace',
          letterSpacing: '0.08em',
          borderLeft: '2px solid var(--theme-border)',
          paddingLeft: '10px',
          maxWidth: '560px',
        }}
      >
        The Goliath Feature path skips this selector — it uses the existing Goliath DB and stack.
      </p>
    </div>
  );
}

interface BackendCardProps {
  emoji: string;
  label: string;
  sublabel: string;
  description: string;
  pros: string[];
  accentColor: string;
  onClick: () => void;
}

function BackendCard({ label, sublabel, description, pros, accentColor, onClick }: BackendCardProps) {
  const [hovered, setHovered] = useState(false);

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered ? 'var(--theme-bg-tertiary)' : 'var(--theme-bg-secondary)',
        border: hovered ? `2px solid ${accentColor}` : '2px solid var(--theme-border)',
        padding: '22px 20px',
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'border-color 0.15s, background 0.15s, transform 0.1s',
        transform: hovered ? 'translateY(-2px)' : 'translateY(0)',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        boxShadow: hovered ? `0 0 0 1px ${accentColor}22, 0 4px 16px ${accentColor}14` : 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span
          style={{
            fontSize: '13px',
            fontWeight: 700,
            color: hovered ? accentColor : 'var(--theme-text-primary)',
            letterSpacing: '0.06em',
            fontFamily: 'JetBrains Mono, monospace',
            transition: 'color 0.15s',
          }}
        >
          {label}
        </span>
        <span
          style={{
            fontSize: '8px',
            fontWeight: 700,
            letterSpacing: '0.12em',
            color: accentColor,
            fontFamily: 'JetBrains Mono, monospace',
            background: `${accentColor}18`,
            border: `1px solid ${accentColor}44`,
            padding: '2px 7px',
          }}
        >
          {sublabel}
        </span>
      </div>

      <span
        style={{
          display: 'block',
          fontSize: '10px',
          color: 'var(--theme-text-muted)',
          fontFamily: 'JetBrains Mono, monospace',
          lineHeight: 1.55,
          letterSpacing: '0.03em',
        }}
      >
        {description}
      </span>

      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {pros.map((pro) => (
          <li
            key={pro}
            style={{
              fontSize: '9px',
              fontWeight: 600,
              color: 'var(--theme-text-dim)',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.08em',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <span style={{ color: accentColor, flexShrink: 0 }}>+</span>
            {pro}
          </li>
        ))}
      </ul>

      <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginTop: '2px' }}>
        <span
          style={{
            fontSize: '10px',
            fontWeight: 700,
            color: hovered ? accentColor : 'var(--theme-text-dim)',
            letterSpacing: '0.1em',
            fontFamily: 'JetBrains Mono, monospace',
            transition: 'color 0.15s',
          }}
        >
          USE THIS BACKEND
        </span>
        <ChevronRight size={11} style={{ color: hovered ? accentColor : 'var(--theme-text-dim)', transition: 'color 0.15s' }} />
      </div>
    </button>
  );
}

// ─── Builder Chat Interface ──────────────────────────────────────────────────

const BACKEND_LABELS: Record<BackendChoice, string> = {
  postgres: 'Postgres',
  'convex-cloud': 'Convex Cloud',
  'convex-self-hosted': 'Convex Self-Hosted',
};

const INTENT_LABELS: Record<Intent, string> = {
  'goliath-feature': 'Goliath Feature',
  'new-app': 'New App',
};

const INTENT_COLORS: Record<Intent, string> = {
  'goliath-feature': 'var(--chart-4)',
  'new-app': 'var(--chart-1)',
};

interface BuilderChatProps {
  intent: Intent;
  backend: BackendChoice | null;
  onBack: () => void;
}

function BuilderChat({ intent, backend, onBack }: BuilderChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const streamingTextRef = useRef('');
  const streamingMsgIdRef = useRef<string | null>(null);
  const streamingElRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const renderTimerRef = useRef<number | null>(null);
  const isMountedRef = useRef(true);

  // ─── DevOps Activity Panel state ───
  const terminalContentRef = useRef<HTMLPreElement | null>(null);
  const terminalScrollRef = useRef<HTMLDivElement | null>(null);
  const activityAutoScrollRef = useRef(true);
  const buildStartTimeRef = useRef<number | null>(null);
  const [isBuildComplete, setIsBuildComplete] = useState(false);
  const [showTerminal, setShowTerminal] = useState(false);
  const [terminalCollapsed, setTerminalCollapsed] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  const accentColor = INTENT_COLORS[intent];

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus textarea on mount
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  // Cleanup on unmount — prevent state updates after unmount, cancel timers/streams
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      if (renderTimerRef.current) {
        window.clearTimeout(renderTimerRef.current);
        renderTimerRef.current = null;
      }
      if (abortRef.current) {
        abortRef.current();
        abortRef.current = null;
      }
    };
  }, []);

  // Elapsed time ticker for DevOps Activity panel
  useEffect(() => {
    if (!isStreaming) return;
    buildStartTimeRef.current = Date.now();
    setElapsedSeconds(0);
    const interval = setInterval(() => {
      if (buildStartTimeRef.current && isMountedRef.current) {
        setElapsedSeconds(Math.floor((Date.now() - buildStartTimeRef.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [isStreaming]);

  // Append text to terminal panel (direct DOM manipulation, no React re-renders)
  const appendToTerminal = useCallback((text: string) => {
    const pre = terminalContentRef.current;
    if (pre) {
      pre.textContent = (pre.textContent || '') + text;
    }
    if (activityAutoScrollRef.current && terminalScrollRef.current) {
      requestAnimationFrame(() => {
        if (terminalScrollRef.current) {
          terminalScrollRef.current.scrollTop = terminalScrollRef.current.scrollHeight;
        }
      });
    }
  }, []);

  // Schedule a debounced markdown re-render for the streaming element
  const scheduleRender = useCallback(() => {
    if (renderTimerRef.current) return;
    renderTimerRef.current = window.setTimeout(() => {
      renderTimerRef.current = null;
      const el = streamingElRef.current;
      if (el && streamingTextRef.current) {
        el.innerHTML = renderMarkdown(streamingTextRef.current);
      }
    }, 80);
  }, []);

  const handleStop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current();
      abortRef.current = null;
    }
    setIsStreaming(false);
    // Finalize the streaming message
    if (streamingMsgIdRef.current) {
      const finalText = streamingTextRef.current;
      const finalId = streamingMsgIdRef.current;
      setMessages(prev =>
        prev.map(m =>
          m.id === finalId ? { ...m, content: finalText, streaming: false } : m
        )
      );
      streamingMsgIdRef.current = null;
      streamingTextRef.current = '';
      streamingElRef.current = null;
    }
  }, []);

  const handleSend = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;

    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    // Add user message
    const userMsgId = `user-${Date.now()}`;
    const userMsg: ChatMessage = {
      id: userMsgId,
      role: 'user',
      content: trimmed,
      timestamp: new Date().toISOString(),
    };

    // Add assistant placeholder
    const assistantMsgId = `assistant-${Date.now()}`;
    const assistantMsg: ChatMessage = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      streaming: true,
    };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);
    streamingTextRef.current = '';
    streamingMsgIdRef.current = assistantMsgId;

    const abortController = new AbortController();
    abortRef.current = () => abortController.abort();

    try {
      // POST to app-builder chat
      const postResponse = await fetch('/api/app-builder/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionId,
          backend,
          intent,
          message: trimmed,
        }),
        signal: abortController.signal,
      });

      if (!postResponse.ok) {
        const errText = await postResponse.text().catch(() => 'Unknown error');
        throw new Error(`Failed to send message: ${errText}`);
      }

      const postData = await postResponse.json();
      const newSessionId = postData.sessionId;
      const streamUrl = postData.streamUrl;

      if (!sessionId && newSessionId) {
        setSessionId(newSessionId);
      }

      // Initialize DevOps Activity Panel
      setShowTerminal(true);
      setIsBuildComplete(false);
      setTerminalCollapsed(false);
      if (terminalContentRef.current) {
        terminalContentRef.current.textContent = '';
      }

      // Connect to SSE stream
      const sseResponse = await fetch(streamUrl, {
        signal: abortController.signal,
        headers: {
          'Accept': 'text/event-stream',
          'Cache-Control': 'no-cache',
        },
      });

      if (!sseResponse.ok || !sseResponse.body) {
        throw new Error(`SSE connection failed: ${sseResponse.status}`);
      }

      const reader = sseResponse.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n');
        buffer = parts.pop() || '';

        for (const line of parts) {
          if (line.startsWith(':') || !line.trim()) continue;
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();

          if (data === '[DONE]') {
            // Stream complete
            if (!isMountedRef.current) return;
            const finalText = streamingTextRef.current;
            setMessages(prev =>
              prev.map(m =>
                m.id === assistantMsgId ? { ...m, content: finalText, streaming: false } : m
              )
            );
            setIsStreaming(false);
            setIsBuildComplete(true);
            streamingMsgIdRef.current = null;
            streamingTextRef.current = '';
            streamingElRef.current = null;
            abortRef.current = null;
            return;
          }

          try {
            const event = JSON.parse(data);

            if (event.type === 'delta') {
              streamingTextRef.current += event.text;
              // Direct DOM update for performance
              scheduleRender();
              // Feed to terminal panel
              appendToTerminal(event.text);
            } else if (event.type === 'snapshot') {
              streamingTextRef.current = event.text;
              scheduleRender();
              // Reset terminal to snapshot
              if (terminalContentRef.current) {
                terminalContentRef.current.textContent = event.text;
              }
            } else if (event.type === 'error') {
              throw new Error(event.text || 'Build agent error');
            }
          } catch (e) {
            if ((e as Error).message?.includes('Build agent error') || (e as Error).message?.includes('Failed')) {
              throw e;
            }
            // Skip non-JSON SSE data
          }
        }
      }

      // Stream ended without [DONE]
      if (!isMountedRef.current) return;
      const finalText = streamingTextRef.current;
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantMsgId ? { ...m, content: finalText, streaming: false } : m
        )
      );
      setIsStreaming(false);
      setIsBuildComplete(true);
      streamingMsgIdRef.current = null;
      streamingTextRef.current = '';
      streamingElRef.current = null;
      abortRef.current = null;
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        // User cancelled
        setIsBuildComplete(true);
        return;
      }
      if (!isMountedRef.current) return;
      const errorText = err instanceof Error ? err.message : String(err);
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, content: `**Error:** ${errorText}`, streaming: false }
            : m
        )
      );
      setIsStreaming(false);
      setIsBuildComplete(true);
      streamingMsgIdRef.current = null;
      streamingTextRef.current = '';
      streamingElRef.current = null;
      abortRef.current = null;
    }
  }, [input, isStreaming, sessionId, backend, intent, scheduleRender, appendToTerminal]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const target = e.target;
    target.style.height = 'auto';
    target.style.height = Math.min(Math.max(target.scrollHeight, 44), 160) + 'px';
  };

  const canSend = input.trim().length > 0 && !isStreaming;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Chat header bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '10px 20px',
          borderBottom: '2px solid var(--theme-border)',
          background: 'var(--theme-bg-secondary)',
          flexShrink: 0,
        }}
      >
        <button
          onClick={onBack}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: '4px',
            color: 'var(--theme-text-dim)',
          }}
          title="Back to selection"
        >
          <ArrowLeft size={14} />
        </button>

        <Terminal size={14} style={{ color: accentColor, flexShrink: 0 }} />
        <span
          style={{
            fontSize: '11px',
            fontWeight: 700,
            letterSpacing: '0.1em',
            color: 'var(--theme-text-primary)',
            fontFamily: 'JetBrains Mono, monospace',
          }}
        >
          DEVOPS BUILD SESSION
        </span>

        {/* Intent chip */}
        <span
          style={{
            fontSize: '8px',
            fontWeight: 700,
            letterSpacing: '0.12em',
            color: accentColor,
            fontFamily: 'JetBrains Mono, monospace',
            background: `color-mix(in srgb, ${accentColor} 15%, transparent)`,
            border: `1px solid color-mix(in srgb, ${accentColor} 40%, transparent)`,
            padding: '2px 8px',
          }}
        >
          {INTENT_LABELS[intent].toUpperCase()}
        </span>

        {/* Backend chip (only for new-app) */}
        {backend && (
          <span
            style={{
              fontSize: '8px',
              fontWeight: 700,
              letterSpacing: '0.12em',
              color: 'var(--chart-2)',
              fontFamily: 'JetBrains Mono, monospace',
              background: 'color-mix(in srgb, var(--chart-2) 15%, transparent)',
              border: '1px solid color-mix(in srgb, var(--chart-2) 40%, transparent)',
              padding: '2px 8px',
            }}
          >
            {BACKEND_LABELS[backend].toUpperCase()}
          </span>
        )}

        {/* Streaming indicator */}
        {isStreaming && (
          <span
            style={{
              marginLeft: 'auto',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: '#22c55e',
                animation: 'pulse 1.5s ease-in-out infinite',
              }}
            />
            <span
              style={{
                fontSize: '9px',
                fontWeight: 700,
                letterSpacing: '0.1em',
                color: '#22c55e',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              BUILDING
            </span>
          </span>
        )}
      </div>

      {/* Messages area */}
      <StreamErrorBoundary fallbackMessage="The streaming display encountered an error. Your build content was received — refresh to view it.">
      <div
        className="flex-1 overflow-y-auto min-h-0"
        style={{
          padding: '20px',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px',
        }}
        data-scroll-container
      >
        {/* Empty state */}
        {messages.length === 0 && (
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '16px',
              padding: '40px 20px',
            }}
          >
            <Terminal size={40} style={{ color: 'var(--theme-border)' }} />
            <div style={{ textAlign: 'center', maxWidth: '440px' }}>
              <h3
                style={{
                  fontSize: '14px',
                  fontWeight: 700,
                  color: 'var(--theme-text-primary)',
                  fontFamily: 'JetBrains Mono, monospace',
                  letterSpacing: '0.06em',
                  marginBottom: '8px',
                }}
              >
                {intent === 'new-app' ? 'Describe your app' : 'Describe the feature'}
              </h3>
              <p
                style={{
                  fontSize: '11px',
                  color: 'var(--theme-text-muted)',
                  fontFamily: 'JetBrains Mono, monospace',
                  letterSpacing: '0.04em',
                  lineHeight: 1.6,
                  marginBottom: '20px',
                }}
              >
                {intent === 'new-app'
                  ? 'Tell DevOps what you want to build. Be as detailed or as brief as you like — you can refine as you go.'
                  : 'Tell DevOps what feature to add to Goliath. Describe the change and DevOps will implement it.'}
              </p>
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                  textAlign: 'left',
                }}
              >
                {(intent === 'new-app'
                  ? [
                      'Build me a note-taking app with markdown support and dark mode',
                      'Create a dashboard for tracking solar panel installations',
                      'Spin up a simple API with user auth and a React frontend',
                    ]
                  : [
                      'Add a weather widget to the production dashboard',
                      'Create a new API endpoint for schedule data',
                      'Add a dark/light mode toggle to the sidebar',
                    ]
                ).map((hint, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      setInput(hint);
                      textareaRef.current?.focus();
                    }}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '8px 12px',
                      background: 'var(--theme-bg-secondary)',
                      border: '1px solid var(--theme-border)',
                      cursor: 'pointer',
                      textAlign: 'left',
                      transition: 'border-color 0.12s',
                      fontFamily: 'JetBrains Mono, monospace',
                    }}
                    onMouseEnter={(e) => {
                      (e.currentTarget.style.borderColor as string) = accentColor;
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = 'var(--theme-border)';
                    }}
                  >
                    <ChevronRight size={10} style={{ color: accentColor, flexShrink: 0 }} />
                    <span
                      style={{
                        fontSize: '10px',
                        color: 'var(--theme-text-muted)',
                        letterSpacing: '0.03em',
                      }}
                    >
                      {hint}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Chat messages */}
        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            accentColor={accentColor}
            onStreamingRef={
              msg.streaming
                ? (el) => {
                    streamingElRef.current = el;
                    // Initial render if we already have text
                    if (el && streamingTextRef.current) {
                      el.innerHTML = renderMarkdown(streamingTextRef.current);
                    }
                  }
                : undefined
            }
          />
        ))}
        <div ref={messagesEndRef} />
      </div>
      </StreamErrorBoundary>

      {/* DevOps Activity Panel — terminal-style live feed */}
      {showTerminal && (
        <DevOpsActivityPanel
          isActive={isStreaming}
          isComplete={isBuildComplete}
          terminalRef={terminalContentRef}
          scrollContainerRef={terminalScrollRef}
          autoScrollRef={activityAutoScrollRef}
          onToggleCollapse={() => setTerminalCollapsed(c => !c)}
          isCollapsed={terminalCollapsed}
          elapsedSeconds={elapsedSeconds}
        />
      )}

      {/* Input area */}
      <div
        style={{
          padding: '12px 20px 20px',
          borderTop: '2px solid var(--theme-border)',
          background: 'var(--theme-bg-primary)',
          flexShrink: 0,
        }}
      >
        <div style={{ maxWidth: '720px', margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'end', gap: '10px' }}>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder={
                intent === 'new-app'
                  ? 'Describe what to build...'
                  : 'Describe the Goliath feature...'
              }
              disabled={isStreaming}
              rows={1}
              style={{
                flex: 1,
                minHeight: '44px',
                maxHeight: '160px',
                resize: 'none',
                background: 'var(--card)',
                border: '2px solid var(--theme-border)',
                borderRadius: '3px',
                color: 'var(--foreground)',
                fontSize: '13px',
                fontFamily: 'JetBrains Mono, monospace',
                letterSpacing: '0.03em',
                padding: '10px 14px',
                outline: 'none',
                transition: 'border-color 0.12s',
                opacity: isStreaming ? 0.5 : 1,
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = accentColor;
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = 'var(--theme-border)';
              }}
            />

            {isStreaming ? (
              <button
                onClick={handleStop}
                style={{
                  width: '44px',
                  height: '44px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: 'var(--destructive)',
                  border: '2px solid var(--destructive)',
                  borderRadius: '3px',
                  color: 'white',
                  cursor: 'pointer',
                  flexShrink: 0,
                }}
                title="Stop generation"
              >
                <Square size={16} fill="white" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!canSend}
                style={{
                  width: '44px',
                  height: '44px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: canSend ? accentColor : 'var(--theme-bg-tertiary)',
                  border: canSend ? `2px solid ${accentColor}` : '2px solid var(--theme-border)',
                  borderRadius: '3px',
                  color: canSend ? 'var(--primary-foreground)' : 'var(--theme-border)',
                  cursor: canSend ? 'pointer' : 'not-allowed',
                  flexShrink: 0,
                  transition: 'background 0.12s, border-color 0.12s',
                }}
                title="Send message"
              >
                <ArrowUp size={18} strokeWidth={3} />
              </button>
            )}
          </div>
          <p
            style={{
              textAlign: 'center',
              marginTop: '8px',
              fontSize: '10px',
              fontWeight: 700,
              letterSpacing: '0.12em',
              color: 'var(--theme-border)',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            ENTER TO SEND &middot; SHIFT+ENTER FOR NEW LINE
          </p>
        </div>
      </div>
    </div>
  );
}

// ─── Message Bubble ──────────────────────────────────────────────────────────

interface MessageBubbleProps {
  message: ChatMessage;
  accentColor: string;
  onStreamingRef?: (el: HTMLDivElement | null) => void;
}

function MessageBubble({ message, accentColor, onStreamingRef }: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';

  if (isSystem) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '8px 14px',
          background: 'var(--theme-bg-tertiary)',
          borderLeft: `3px solid ${accentColor}`,
        }}
      >
        <Loader size={10} className="animate-spin" style={{ color: accentColor, flexShrink: 0 }} />
        <span
          style={{
            fontSize: '10px',
            fontWeight: 700,
            letterSpacing: '0.08em',
            color: accentColor,
            fontFamily: 'JetBrains Mono, monospace',
          }}
        >
          {message.content}
        </span>
      </div>
    );
  }

  if (isUser) {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <div
          style={{
            maxWidth: '80%',
            padding: '12px 16px',
            background: 'var(--theme-bg-secondary)',
            border: `2px solid color-mix(in srgb, ${accentColor} 40%, var(--theme-border))`,
            borderRadius: '3px',
          }}
        >
          <span
            style={{
              fontSize: '12px',
              color: 'var(--theme-text-primary)',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.03em',
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {message.content}
          </span>
        </div>
      </div>
    );
  }

  // Assistant message
  return (
    <div style={{ display: 'flex', gap: '10px', maxWidth: '100%' }}>
      {/* Avatar */}
      <div
        style={{
          width: '28px',
          height: '28px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: `color-mix(in srgb, ${accentColor} 15%, transparent)`,
          border: `2px solid color-mix(in srgb, ${accentColor} 40%, transparent)`,
          borderRadius: '3px',
          flexShrink: 0,
        }}
      >
        <Terminal size={14} style={{ color: accentColor }} />
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: 'block',
            fontSize: '9px',
            fontWeight: 700,
            letterSpacing: '0.12em',
            color: accentColor,
            fontFamily: 'JetBrains Mono, monospace',
            marginBottom: '6px',
          }}
        >
          DEVOPS
        </span>
        {message.streaming ? (
          /* IMPORTANT: The streaming content div (ref={onStreamingRef}) must NEVER
             have React-managed children, because scheduleRender() sets innerHTML
             directly. If React children exist inside, React's vDOM will desync from
             the real DOM, causing "removeChild" crashes during reconciliation.
             The cursor is rendered as a SIBLING, not a child. */
          <div style={{ position: 'relative' }}>
            <div
              ref={onStreamingRef}
              className="msg-markdown"
              style={{
                fontSize: '12px',
                color: 'var(--theme-text-primary)',
                fontFamily: 'JetBrains Mono, monospace',
                letterSpacing: '0.02em',
                lineHeight: 1.65,
                wordBreak: 'break-word',
                minHeight: '20px',
              }}
            />
            {/* Cursor rendered OUTSIDE the innerHTML container to avoid DOM desync */}
            {!streamingHasContent(message) && (
              <span
                style={{
                  display: 'inline-block',
                  width: '8px',
                  height: '16px',
                  background: accentColor,
                  animation: 'cursor-blink 1s step-end infinite',
                }}
              />
            )}
          </div>
        ) : (
          <div
            className="msg-markdown"
            style={{
              fontSize: '12px',
              color: 'var(--theme-text-primary)',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.02em',
              lineHeight: 1.65,
              wordBreak: 'break-word',
            }}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
          />
        )}
      </div>
    </div>
  );
}

function streamingHasContent(msg: ChatMessage): boolean {
  return msg.content.length > 0;
}

// ─── DevOps Activity Panel (terminal-style live feed) ─────────────────────────

interface DevOpsActivityPanelProps {
  isActive: boolean;
  isComplete: boolean;
  terminalRef: React.RefObject<HTMLPreElement | null>;
  scrollContainerRef: React.RefObject<HTMLDivElement | null>;
  autoScrollRef: React.MutableRefObject<boolean>;
  onToggleCollapse: () => void;
  isCollapsed: boolean;
  elapsedSeconds: number;
}

function DevOpsActivityPanel({
  isActive,
  isComplete,
  terminalRef,
  scrollContainerRef,
  autoScrollRef,
  onToggleCollapse,
  isCollapsed,
  elapsedSeconds,
}: DevOpsActivityPanelProps) {
  // Detect manual scroll-up to pause auto-scroll
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    autoScrollRef.current = isAtBottom;
  }, [scrollContainerRef, autoScrollRef]);

  const statusColor = isComplete ? '#39d353' : isActive ? '#39d353' : '#484f58';
  const statusLabel = isComplete ? 'COMPLETE' : isActive ? 'ACTIVE' : 'IDLE';

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, '0')}`;
  };

  return (
    <div
      style={{
        flexShrink: 0,
        borderTop: '2px solid #1b2332',
        background: '#0d1117',
        display: 'flex',
        flexDirection: 'column',
        maxHeight: isCollapsed ? '38px' : '280px',
        minHeight: '38px',
        transition: 'max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {/* Subtle scanline overlay for CRT effect */}
      {isActive && (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            pointerEvents: 'none',
            zIndex: 1,
            background: 'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(255,255,255,0.008) 2px, rgba(255,255,255,0.008) 4px)',
          }}
        />
      )}

      {/* Header */}
      <button
        onClick={onToggleCollapse}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '9px 16px',
          background: '#161b22',
          border: 'none',
          borderBottom: isCollapsed ? 'none' : '1px solid #21262d',
          cursor: 'pointer',
          flexShrink: 0,
          width: '100%',
          textAlign: 'left',
          zIndex: 2,
          position: 'relative',
        }}
      >
        {/* Status dot with glow */}
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: statusColor,
            flexShrink: 0,
            animation: isActive ? 'dot-pulse 1.5s ease-in-out infinite' : 'none',
            boxShadow: isActive
              ? `0 0 6px ${statusColor}, 0 0 12px ${statusColor}66`
              : isComplete
              ? `0 0 4px ${statusColor}88`
              : 'none',
          }}
        />

        {/* Title */}
        <span
          style={{
            fontSize: '10px',
            fontWeight: 700,
            letterSpacing: '0.14em',
            color: '#58a6ff',
            fontFamily: 'JetBrains Mono, monospace',
          }}
        >
          ⚡ DEVOPS ENGINE
        </span>

        {/* Status label */}
        <span
          style={{
            fontSize: '8px',
            fontWeight: 700,
            letterSpacing: '0.12em',
            color: statusColor,
            fontFamily: 'JetBrains Mono, monospace',
            background: isActive ? 'rgba(57, 211, 83, 0.08)' : 'transparent',
            border: isActive ? '1px solid rgba(57, 211, 83, 0.25)' : '1px solid transparent',
            padding: '1px 6px',
            transition: 'all 0.2s',
          }}
        >
          {statusLabel}
        </span>

        {/* Elapsed time */}
        {(isActive || isComplete) && (
          <span
            style={{
              fontSize: '9px',
              fontWeight: 600,
              color: '#484f58',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.06em',
              marginLeft: 'auto',
            }}
          >
            {formatTime(elapsedSeconds)}
          </span>
        )}

        {/* Collapse chevron */}
        <ChevronRight
          size={11}
          style={{
            color: '#484f58',
            transform: isCollapsed ? 'rotate(0deg)' : 'rotate(90deg)',
            transition: 'transform 0.2s',
            marginLeft: isActive || isComplete ? '4px' : 'auto',
            flexShrink: 0,
          }}
        />
      </button>

      {/* Terminal body */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflowY: 'auto',
          overflowX: 'hidden',
          padding: '12px 16px',
          minHeight: 0,
          position: 'relative',
          zIndex: 2,
          /* Custom scrollbar for the terminal */
          scrollbarWidth: 'thin',
          scrollbarColor: '#21262d #0d1117',
        }}
      >
        {/* System start message */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            marginBottom: '8px',
            paddingBottom: '8px',
            borderBottom: '1px solid #21262d',
          }}
        >
          <Terminal size={10} style={{ color: '#484f58', flexShrink: 0 }} />
          <span
            style={{
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.1em',
              color: '#484f58',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            {isActive ? 'STREAMING OUTPUT' : isComplete ? 'BUILD OUTPUT' : 'WAITING'}
          </span>
        </div>

        {/* Raw streaming text — updated via direct DOM manipulation */}
        <pre
          ref={terminalRef}
          style={{
            margin: 0,
            padding: 0,
            fontSize: '11px',
            lineHeight: 1.7,
            color: '#c9d1d9',
            fontFamily: 'JetBrains Mono, monospace',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            letterSpacing: '0.02em',
          }}
        />

        {/* Blinking block cursor while streaming */}
        {isActive && (
          <span
            style={{
              display: 'inline-block',
              width: '7px',
              height: '14px',
              background: '#39d353',
              animation: 'cursor-blink 1s step-end infinite',
              verticalAlign: 'text-bottom',
              marginLeft: '2px',
              boxShadow: '0 0 4px rgba(57, 211, 83, 0.4)',
            }}
          />
        )}

        {/* Completion banner */}
        {isComplete && (
          <div
            style={{
              marginTop: '12px',
              paddingTop: '10px',
              borderTop: '1px solid #21262d',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <CheckCircle size={13} style={{ color: '#39d353', flexShrink: 0 }} />
            <span
              style={{
                fontSize: '10px',
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#39d353',
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              ✓ BUILD COMPLETE
            </span>
            <span
              style={{
                fontSize: '9px',
                color: '#484f58',
                fontFamily: 'JetBrains Mono, monospace',
                marginLeft: 'auto',
              }}
            >
              {formatTime(elapsedSeconds)}
            </span>
          </div>
        )}
      </div>

      {/* Bottom glow line when active */}
      {isActive && (
        <div
          style={{
            height: '2px',
            flexShrink: 0,
            background: 'linear-gradient(90deg, transparent, #39d353, #58a6ff, #39d353, transparent)',
            backgroundSize: '200% 100%',
            animation: 'glow-line 2s linear infinite',
            opacity: 0.7,
          }}
        />
      )}
    </div>
  );
}

// ─── Shared Utilities ─────────────────────────────────────────────────────────

function BackNavBar({ onBack, label }: { onBack: () => void; label: string }) {
  return (
    <button
      onClick={onBack}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        background: 'transparent',
        border: 'none',
        cursor: 'pointer',
        padding: '0 0 16px 0',
        color: 'var(--theme-text-dim)',
      }}
    >
      <ArrowLeft size={12} style={{ color: 'var(--theme-text-dim)' }} />
      <span
        style={{
          fontSize: '10px',
          fontWeight: 700,
          letterSpacing: '0.1em',
          fontFamily: 'JetBrains Mono, monospace',
          color: 'var(--theme-text-dim)',
        }}
      >
        {label.toUpperCase()}
      </span>
    </button>
  );
}

function Breadcrumb({ items }: { items: string[] }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
      {items.map((item, i) => (
        <span key={i} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
          <span
            style={{
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.1em',
              color: i === items.length - 1 ? 'var(--theme-accent)' : 'var(--theme-text-dim)',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            {item.toUpperCase()}
          </span>
          {i < items.length - 1 && (
            <ChevronRight size={9} style={{ color: 'var(--theme-border)', flexShrink: 0 }} />
          )}
        </span>
      ))}
    </div>
  );
}

// ─── Root Page ────────────────────────────────────────────────────────────────

type AppBuilderState =
  | { screen: 'intent' }
  | { screen: 'new-app-backend' }
  | { screen: 'chat'; intent: Intent; backend: BackendChoice | null };

export function AppBuilderPage() {
  const [state, setState] = useState<AppBuilderState>({ screen: 'intent' });

  function handleIntentSelect(intent: Intent) {
    if (intent === 'new-app') {
      setState({ screen: 'new-app-backend' });
    } else {
      // Goliath feature goes straight to chat
      setState({ screen: 'chat', intent: 'goliath-feature', backend: null });
    }
  }

  function handleBackendSelect(backend: BackendChoice) {
    setState({ screen: 'chat', intent: 'new-app', backend });
  }

  // Build subtitle based on current screen
  const subtitleMap: Record<string, string> = {
    'intent': 'Select your build intent to begin',
    'new-app-backend': 'New App — choose a backend',
    'chat': 'DevOps build session',
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <AppBuilderStyles />
      <PageHeader
        title="App Builder"
        subtitle={subtitleMap[state.screen]}
      />

      {state.screen === 'intent' && (
        <IntentSelector onSelect={handleIntentSelect} />
      )}

      {state.screen === 'new-app-backend' && (
        <BackendSelector
          onSelect={handleBackendSelect}
          onBack={() => setState({ screen: 'intent' })}
        />
      )}

      {state.screen === 'chat' && (
        <BuilderChat
          intent={state.intent}
          backend={state.backend}
          onBack={() => setState({ screen: 'intent' })}
        />
      )}
    </div>
  );
}
