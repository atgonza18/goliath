import {
  useState,
  useEffect,
  useRef,
  useCallback,
  Component,
  type ReactNode,
  type ErrorInfo,
} from 'react';
import {
  ArrowLeft,
  Wrench,
  Rocket,
  ChevronRight,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  Loader,
  Terminal,
  Square,
  Send,
} from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { Separator } from '@/components/ui/separator';
import { renderMarkdown } from '@/utils/markdown';
import { cn } from '@/lib/utils';

// ─── Types ────────────────────────────────────────────────────────────────────

type Intent = 'goliath-feature' | 'new-app';
type BackendChoice = 'postgres' | 'convex-cloud' | 'convex-self-hosted';

interface ToolChip {
  id: string;
  name: string;
  inputRaw: string;
  inputSummary: string;
  status: 'running' | 'done';
}

interface MCQOption {
  label: string;
  description: string;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming: boolean;
  toolChips: ToolChip[];
  thinking: string;
  thinkingDone: boolean;
  question?: {
    question: string;
    header: string;
    options: MCQOption[];
    multiSelect: boolean;
    answered?: string;
  };
}

// ─── CSS Keyframes ───────────────────────────────────────────────────────────

function AppBuilderStyles() {
  return (
    <style>{`
      @keyframes cursor-blink {
        0%, 100% { opacity: 1; }
        50% { opacity: 0; }
      }
      @keyframes ab-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
      }
      @keyframes ab-dot-bounce {
        0%, 80%, 100% { opacity: 0.2; }
        40% { opacity: 1; }
      }
      .ab-scroll::-webkit-scrollbar {
        width: 6px;
      }
      .ab-scroll::-webkit-scrollbar-track {
        background: transparent;
      }
      .ab-scroll::-webkit-scrollbar-thumb {
        background: #21262d;
        border-radius: 3px;
      }
      .ab-scroll::-webkit-scrollbar-thumb:hover {
        background: #30363d;
      }
      .ab-thinking-dots .ab-dot {
        animation: ab-dot-bounce 1.4s ease-in-out infinite;
      }
      .ab-thinking-dots .ab-dot:nth-child(2) {
        animation-delay: 0.2s;
      }
      .ab-thinking-dots .ab-dot:nth-child(3) {
        animation-delay: 0.4s;
      }
      /* Override msg-markdown colors for dark CLI background */
      .ab-cli-chat .msg-markdown {
        color: #c9d1d9;
      }
      .ab-cli-chat .msg-markdown code {
        background: #161b22;
        border: 1px solid #21262d;
        color: #e6edf3;
      }
      .ab-cli-chat .msg-markdown pre {
        background: #161b22;
        border: 1px solid #21262d;
      }
      .ab-cli-chat .msg-markdown pre code {
        border: none;
        background: transparent;
      }
      .ab-cli-chat .msg-markdown a {
        color: #58a6ff;
      }
      .ab-cli-chat .msg-markdown blockquote {
        border-left-color: #30363d;
        color: #8b949e;
      }
      .ab-cli-chat .msg-markdown h1,
      .ab-cli-chat .msg-markdown h2,
      .ab-cli-chat .msg-markdown h3,
      .ab-cli-chat .msg-markdown h4 {
        color: #e6edf3;
        border-bottom-color: #21262d;
      }
      .ab-cli-chat .msg-markdown table th {
        background: #161b22;
        border-color: #21262d;
        color: #e6edf3;
      }
      .ab-cli-chat .msg-markdown table td {
        border-color: #21262d;
        color: #c9d1d9;
      }
      .ab-cli-chat .msg-markdown hr {
        border-color: #21262d;
      }
      .ab-cli-chat .msg-markdown strong {
        color: #e6edf3;
      }
      .ab-cli-chat .msg-markdown li::marker {
        color: #484f58;
      }
    `}</style>
  );
}

// ─── Error Boundary ───────────────────────────────────────────────────────────

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
        <div className="p-4 bg-destructive/10 border border-destructive/30 rounded m-2">
          <span className="block text-xs font-mono font-bold text-destructive mb-1 tracking-wide">
            RENDER ERROR
          </span>
          <span className="text-xs font-mono text-muted-foreground">
            {this.props.fallbackMessage || 'Stream display crashed. Content was received — refresh to view.'}
          </span>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="block mt-2 text-xs font-mono text-destructive border border-destructive/40 px-3 py-1 hover:bg-destructive/10 transition-colors"
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
          label="Goliath Feature"
          sublabel="PATCH MODE"
          description="Add a page, component, API route, or agent to Goliath itself. DevOps edits the existing codebase and may trigger a restart."
          tags={['Edits Goliath source', 'May require RESTART', 'Uses existing stack']}
          accentColor="var(--chart-4)"
          onClick={() => onSelect('goliath-feature')}
        />
        <IntentCard
          icon={<Rocket size={32} />}
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
  label: string;
  sublabel: string;
  description: string;
  pros: string[];
  accentColor: string;
}[] = [
  {
    id: 'postgres',
    label: 'Postgres',
    sublabel: 'SIDECAR CONTAINER',
    description:
      'A Postgres container runs alongside your app in docker-compose. Fully isolated on-server, standard SQL. Connection injected as DATABASE_URL.',
    pros: ['On-server', 'Standard SQL', 'Destroyed with app', 'Zero external deps'],
    accentColor: 'var(--chart-2)',
  },
  {
    id: 'convex-cloud',
    label: 'Convex (Cloud)',
    sublabel: 'CONVEX.DEV',
    description:
      'App connects to convex.dev cloud. DevOps generates schema + server functions. Best for real-time apps. Requires a Convex API key.',
    pros: ['Real-time sync', 'Fast prototyping', 'Live subscriptions', 'No local DB'],
    accentColor: 'var(--chart-3)',
  },
  {
    id: 'convex-self-hosted',
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

// ─── Tool summarizer ─────────────────────────────────────────────────────────

function summarizeTool(name: string, inputRaw: string): string {
  try {
    const input = JSON.parse(inputRaw);
    if (input.file_path) return input.file_path.split('/').slice(-2).join('/');
    if (input.command) return String(input.command).slice(0, 50);
    if (input.pattern) return String(input.pattern).slice(0, 40);
    if (input.path) return String(input.path).split('/').slice(-2).join('/');
    if (input.prompt) return String(input.prompt).slice(0, 40) + '...';
    return name;
  } catch {
    return name;
  }
}

// ─── MCQ detector ────────────────────────────────────────────────────────────

function detectMultipleChoice(text: string): { question: string; options: string[] } | null {
  const match = text.match(/([^\n]*(?:\?|:)[^\n]*)\n((?:[ \t]*\d+[.)]\s+[^\n]+\n?){2,})/s);
  if (!match) return null;
  const question = match[1].trim();
  const raw = match[2].match(/\d+[.)]\s+([^\n]+)/g) || [];
  if (raw.length < 2) return null;
  return { question, options: raw.map(l => l.replace(/^\d+[.)]\s+/, '').trim()) };
}

// ─── Message ID generator ────────────────────────────────────────────────────

let _msgCounter = 0;
function generateMsgId(): string {
  return `msg-${Date.now()}-${++_msgCounter}`;
}

// ─── Builder Chat Interface ───────────────────────────────────────────────────

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
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef(true);

  // Track the ID of the current streaming assistant message
  const streamingMsgIdRef = useRef<string | null>(null);

  // Direct DOM refs for streaming: msgId → contentDiv
  const streamingDomRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  // Accumulate text for each streaming message (for final commit to state)
  const streamingTextBuffers = useRef<Map<string, string>>(new Map());

  // Render timer per message
  const renderTimers = useRef<Map<string, number>>(new Map());

  const accentColor = INTENT_COLORS[intent];

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      // Cancel any pending render timers
      renderTimers.current.forEach(t => window.clearTimeout(t));
      renderTimers.current.clear();
      if (abortRef.current) {
        abortRef.current.abort();
      }
    };
  }, []);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length]);

  // Debounced DOM update for streaming text
  const scheduleRender = useCallback((msgId: string) => {
    if (renderTimers.current.has(msgId)) return;
    const t = window.setTimeout(() => {
      renderTimers.current.delete(msgId);
      const el = streamingDomRefs.current.get(msgId);
      const text = streamingTextBuffers.current.get(msgId) || '';
      if (el) {
        el.innerHTML = renderMarkdown(text);
      }
    }, 80);
    renderTimers.current.set(msgId, t);
  }, []);

  // Update a streaming message's state fields (non-content fields)
  const updateMessage = useCallback((msgId: string, updater: (m: ChatMessage) => ChatMessage) => {
    if (!isMountedRef.current) return;
    setMessages(prev => prev.map(m => m.id === msgId ? updater(m) : m));
  }, []);

  const handleStop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    const msgId = streamingMsgIdRef.current;
    if (msgId) {
      const finalText = streamingTextBuffers.current.get(msgId) || '';
      setMessages(prev =>
        prev.map(m =>
          m.id === msgId ? { ...m, content: finalText, isStreaming: false, thinkingDone: true } : m
        )
      );
      streamingMsgIdRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  const handleSend = useCallback(async (overrideText?: string) => {
    const trimmed = (overrideText ?? input).trim();
    if (!trimmed || isStreaming) return;

    if (!overrideText) {
      setInput('');
      if (inputRef.current) {
        inputRef.current.style.height = 'auto';
      }
    }

    // Add user message
    const userMsg: ChatMessage = {
      id: generateMsgId(),
      role: 'user',
      content: trimmed,
      isStreaming: false,
      toolChips: [],
      thinking: '',
      thinkingDone: true,
    };

    // Add assistant placeholder
    const assistantMsgId = generateMsgId();
    const assistantMsg: ChatMessage = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      isStreaming: true,
      toolChips: [],
      thinking: '',
      thinkingDone: false,
    };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);
    streamingMsgIdRef.current = assistantMsgId;
    streamingTextBuffers.current.set(assistantMsgId, '');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // POST to create the stream
      const postRes = await fetch('/api/app-builder/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionId,
          backend,
          intent,
          message: trimmed,
        }),
        signal: controller.signal,
      });

      if (!postRes.ok) {
        throw new Error(`Failed to send message: ${postRes.status}`);
      }

      const postData = await postRes.json();
      if (!sessionId && postData.sessionId) {
        setSessionId(postData.sessionId);
      }

      const streamUrl: string = postData.streamUrl;

      // Connect to SSE stream
      const sseRes = await fetch(streamUrl, {
        signal: controller.signal,
        headers: {
          'Accept': 'text/event-stream',
          'Cache-Control': 'no-cache',
        },
      });

      if (!sseRes.ok || !sseRes.body) {
        throw new Error(`SSE connection failed: ${sseRes.status}`);
      }

      const reader = sseRes.body.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = '';

      // SSE line parser state
      let currentEventName = 'message';
      let currentData = '';

      const dispatchSseEvent = (eventName: string, dataStr: string) => {
        if (!isMountedRef.current) return;

        let data: any = {};
        try { data = JSON.parse(dataStr); } catch {}

        const msgId = streamingMsgIdRef.current;
        if (!msgId) return;

        switch (eventName) {
          case 'text_start':
            // new text block started — nothing to do
            break;

          case 'delta': {
            const text: string = data.text || '';
            const prev = streamingTextBuffers.current.get(msgId) || '';
            streamingTextBuffers.current.set(msgId, prev + text);
            scheduleRender(msgId);
            // Auto-scroll
            requestAnimationFrame(() => {
              bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
            });
            break;
          }

          case 'text_stop':
            updateMessage(msgId, m => ({ ...m, thinkingDone: true }));
            break;

          case 'tool_use': {
            const chip: ToolChip = {
              id: data.id || '',
              name: data.name || '',
              inputRaw: '',
              inputSummary: '',
              status: 'running',
            };
            updateMessage(msgId, m => ({ ...m, toolChips: [...m.toolChips, chip] }));
            break;
          }

          case 'tool_delta': {
            const chipId: string = data.id || '';
            const partial: string = data.partial_json || '';
            updateMessage(msgId, m => ({
              ...m,
              toolChips: m.toolChips.map(c =>
                c.id === chipId ? { ...c, inputRaw: c.inputRaw + partial } : c
              ),
            }));
            break;
          }

          case 'tool_done': {
            const chipId: string = data.id || '';
            updateMessage(msgId, m => ({
              ...m,
              toolChips: m.toolChips.map(c => {
                if (c.id !== chipId) return c;
                return {
                  ...c,
                  status: 'done' as const,
                  inputSummary: summarizeTool(c.name, c.inputRaw),
                };
              }),
            }));
            break;
          }

          case 'thinking_start':
            // nothing
            break;

          case 'thinking': {
            const text: string = data.text || '';
            updateMessage(msgId, m => ({ ...m, thinking: m.thinking + text }));
            break;
          }

          case 'thinking_stop':
            updateMessage(msgId, m => ({ ...m, thinkingDone: true }));
            break;

          case 'question': {
            // Finalize content
            const finalText = streamingTextBuffers.current.get(msgId) || '';
            setMessages(prev =>
              prev.map(m =>
                m.id === msgId
                  ? {
                      ...m,
                      content: finalText,
                      isStreaming: false,
                      thinkingDone: true,
                      question: {
                        question: data.question || '',
                        header: data.header || '',
                        options: data.options || [],
                        multiSelect: data.multiSelect || false,
                        answered: undefined,
                      },
                    }
                  : m
              )
            );
            setIsStreaming(false);
            streamingMsgIdRef.current = null;
            break;
          }

          case 'done': {
            // Finalize content
            const finalText = streamingTextBuffers.current.get(msgId) || '';
            // Flush any pending render
            const t = renderTimers.current.get(msgId);
            if (t) {
              window.clearTimeout(t);
              renderTimers.current.delete(msgId);
            }
            setMessages(prev =>
              prev.map(m =>
                m.id === msgId
                  ? { ...m, content: finalText, isStreaming: false, thinkingDone: true }
                  : m
              )
            );
            setIsStreaming(false);
            streamingMsgIdRef.current = null;
            abortRef.current = null;
            break;
          }

          case 'error': {
            const errMsg = data.message || 'Unknown error';
            setMessages(prev =>
              prev.map(m =>
                m.id === msgId
                  ? { ...m, content: `**Error:** ${errMsg}`, isStreaming: false, thinkingDone: true }
                  : m
              )
            );
            setIsStreaming(false);
            streamingMsgIdRef.current = null;
            break;
          }

          default:
            break;
        }
      };

      // SSE read loop
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        sseBuffer += decoder.decode(value, { stream: true });
        const lines = sseBuffer.split('\n');
        sseBuffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith(':')) continue; // comment/heartbeat

          if (line === '') {
            // Dispatch accumulated event
            if (currentData) {
              dispatchSseEvent(currentEventName, currentData.trim());
            }
            currentEventName = 'message';
            currentData = '';
            continue;
          }

          if (line.startsWith('event:')) {
            currentEventName = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            currentData += line.slice(5).trim();
          }
        }
      }

      // EOF — finalize if still streaming
      if (isMountedRef.current) {
        const msgId = streamingMsgIdRef.current;
        if (msgId) {
          const finalText = streamingTextBuffers.current.get(msgId) || '';
          setMessages(prev =>
            prev.map(m =>
              m.id === msgId
                ? { ...m, content: finalText, isStreaming: false, thinkingDone: true }
                : m
            )
          );
          streamingMsgIdRef.current = null;
        }
        setIsStreaming(false);
        abortRef.current = null;
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      if (!isMountedRef.current) return;

      const msgId = streamingMsgIdRef.current;
      const errorText = err instanceof Error ? err.message : String(err);
      if (msgId) {
        setMessages(prev =>
          prev.map(m =>
            m.id === msgId
              ? { ...m, content: `**Error:** ${errorText}`, isStreaming: false, thinkingDone: true }
              : m
          )
        );
        streamingMsgIdRef.current = null;
      }
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [input, isStreaming, sessionId, backend, intent, scheduleRender, updateMessage]);

  // When a MessageBlock mounts with isStreaming=true, register its DOM ref
  const registerStreamingRef = useCallback((msgId: string, el: HTMLDivElement | null) => {
    if (el) {
      streamingDomRefs.current.set(msgId, el);
      // Render any already-accumulated text
      const text = streamingTextBuffers.current.get(msgId) || '';
      if (text) el.innerHTML = renderMarkdown(text);
    } else {
      streamingDomRefs.current.delete(msgId);
    }
  }, []);

  const handleOptionClick = useCallback(async (msgId: string, index: number, opt: string) => {
    setMessages(prev =>
      prev.map(m =>
        m.id === msgId && m.question
          ? { ...m, question: { ...m.question, answered: opt } }
          : m
      )
    );
    const answerText = `${index + 1}. ${opt}`;
    await handleSend(answerText);
  }, [handleSend]);

  const handleEndSession = useCallback(async () => {
    if (sessionId) {
      try {
        await fetch(`/api/app-builder/sessions/${sessionId}`, { method: 'DELETE' });
      } catch {}
    }
    onBack();
  }, [sessionId, onBack]);

  const canSend = input.trim().length > 0 && !isStreaming;

  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: '#0d1117' }}>
      {/* Minimal CLI header */}
      <div
        className="shrink-0 flex items-center gap-2 px-4 py-2"
        style={{ background: '#161b22', borderBottom: '1px solid #21262d' }}
      >
        <SidebarTrigger className="-ml-1" style={{ color: '#8b949e' }} />
        <Separator orientation="vertical" className="h-3.5" style={{ background: '#21262d' }} />

        <button
          onClick={onBack}
          className="p-1 rounded transition-colors"
          title="Back"
          style={{ color: '#8b949e' }}
          onMouseEnter={e => { e.currentTarget.style.background = '#21262d'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
        >
          <ArrowLeft size={13} />
        </button>

        <Terminal size={13} style={{ color: accentColor, flexShrink: 0 }} />

        <span
          className="font-mono font-bold"
          style={{ fontSize: '10px', letterSpacing: '0.15em', color: '#8b949e' }}
        >
          CLAUDE CODE
        </span>

        {/* Intent chip */}
        <span
          className="font-mono font-bold"
          style={{
            fontSize: '9px',
            letterSpacing: '0.12em',
            padding: '2px 8px',
            color: accentColor,
            background: `color-mix(in srgb, ${accentColor} 12%, transparent)`,
            border: `1px solid color-mix(in srgb, ${accentColor} 30%, transparent)`,
          }}
        >
          {INTENT_LABELS[intent].toUpperCase()}
        </span>

        {backend && (
          <span
            className="font-mono font-bold"
            style={{
              fontSize: '9px',
              letterSpacing: '0.12em',
              padding: '2px 8px',
              color: 'var(--chart-2)',
              background: 'rgba(34,211,238,0.08)',
              border: '1px solid rgba(34,211,238,0.25)',
            }}
          >
            {BACKEND_LABELS[backend].toUpperCase()}
          </span>
        )}

        <div className="flex items-center gap-2 ml-auto">
          {isStreaming && (
            <>
              <div
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: '#3fb950', animation: 'ab-pulse 1.5s ease-in-out infinite' }}
              />
              <span
                className="font-mono font-bold"
                style={{ fontSize: '10px', letterSpacing: '0.1em', color: '#3fb950' }}
              >
                STREAMING
              </span>
            </>
          )}
          <button
            onClick={handleEndSession}
            className="font-mono font-bold transition-colors"
            style={{
              fontSize: '10px',
              letterSpacing: '0.08em',
              padding: '4px 10px',
              color: '#8b949e',
              border: '1px solid #21262d',
              background: 'transparent',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = '#21262d'; e.currentTarget.style.color = '#c9d1d9'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#8b949e'; }}
          >
            END SESSION
          </button>
        </div>
      </div>

      {/* Messages — full-height dark CLI pane */}
      <StreamErrorBoundary fallbackMessage="Stream display crashed. Content was received — refresh to view.">
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto min-h-0 ab-scroll ab-cli-chat"
          style={{ background: '#0d1117' }}
        >
          <div className="max-w-3xl mx-auto px-6 py-8">
            {/* Empty state */}
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center gap-6 py-20">
                <div
                  className="flex items-center justify-center w-16 h-16"
                  style={{ border: '2px solid #21262d' }}
                >
                  <Terminal size={28} style={{ color: '#30363d' }} />
                </div>
                <div className="text-center max-w-md">
                  <h3
                    className="font-mono font-bold text-sm mb-2"
                    style={{ color: '#c9d1d9', letterSpacing: '0.06em' }}
                  >
                    {intent === 'new-app' ? 'Describe your app' : 'Describe the feature'}
                  </h3>
                  <p
                    className="font-mono text-xs leading-relaxed mb-8"
                    style={{ color: '#484f58', letterSpacing: '0.04em' }}
                  >
                    {intent === 'new-app'
                      ? 'Tell Claude Code what to build. Be as detailed or brief as you like.'
                      : 'Tell Claude Code what feature to add. Describe the change and it will implement it.'}
                  </p>
                  <div className="flex flex-col gap-2 text-left">
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
                          inputRef.current?.focus();
                        }}
                        className="flex items-center gap-3 px-4 py-2.5 font-mono text-xs transition-all text-left"
                        style={{ color: '#8b949e', border: '1px solid #21262d', background: 'transparent' }}
                        onMouseEnter={e => {
                          e.currentTarget.style.borderColor = `color-mix(in srgb, ${accentColor} 40%, transparent)`;
                          e.currentTarget.style.background = 'rgba(255,255,255,0.02)';
                        }}
                        onMouseLeave={e => {
                          e.currentTarget.style.borderColor = '#21262d';
                          e.currentTarget.style.background = 'transparent';
                        }}
                      >
                        <span style={{ color: accentColor, flexShrink: 0, opacity: 0.6 }}>❯</span>
                        {hint}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Chat messages */}
            <div className="space-y-0">
              {messages.map(msg => (
                <StreamingMessageBlock
                  key={msg.id}
                  msg={msg}
                  accentColor={accentColor}
                  onOptionClick={handleOptionClick}
                  onRegisterRef={registerStreamingRef}
                />
              ))}
            </div>
            <div ref={bottomRef} />
          </div>
        </div>
      </StreamErrorBoundary>

      {/* Input bar — dark, CLI-style */}
      <div
        className="shrink-0 px-4 py-3"
        style={{ background: '#161b22', borderTop: '1px solid #21262d' }}
      >
        <div className="flex items-end gap-3 max-w-3xl mx-auto">
          <span
            className="font-mono text-sm font-bold pb-1.5 select-none"
            style={{ color: accentColor, opacity: 0.7 }}
          >
            ❯
          </span>
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => {
              setInput(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = Math.min(e.target.scrollHeight, 128) + 'px';
            }}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey && !isStreaming) {
                e.preventDefault();
                handleSend();
              }
            }}
            disabled={isStreaming}
            placeholder={isStreaming ? 'Claude is working...' : 'Ask Claude Code anything...'}
            rows={1}
            className="flex-1 bg-transparent resize-none outline-none text-sm font-mono overflow-hidden"
            style={{
              color: '#c9d1d9',
              maxHeight: '128px',
              caretColor: accentColor,
            }}
          />
          <div className="flex gap-2 pb-0.5">
            {isStreaming ? (
              <button
                onClick={handleStop}
                className="flex items-center gap-1.5 font-mono font-bold transition-colors"
                style={{
                  fontSize: '10px',
                  letterSpacing: '0.08em',
                  padding: '5px 12px',
                  color: '#f85149',
                  border: '1px solid rgba(248,81,73,0.3)',
                  background: 'rgba(248,81,73,0.08)',
                }}
              >
                <Square size={9} fill="currentColor" />
                STOP
              </button>
            ) : (
              <button
                onClick={() => handleSend()}
                disabled={!canSend}
                className="flex items-center gap-1.5 font-mono font-bold transition-colors"
                style={{
                  fontSize: '10px',
                  letterSpacing: '0.08em',
                  padding: '5px 12px',
                  color: canSend ? '#0d1117' : '#484f58',
                  background: canSend ? accentColor : 'transparent',
                  border: canSend ? 'none' : '1px solid #21262d',
                  opacity: canSend ? 1 : 0.4,
                }}
              >
                <Send size={10} />
                SEND
              </button>
            )}
          </div>
        </div>
        <p
          className="text-center mt-2 font-mono"
          style={{ fontSize: '8px', letterSpacing: '0.12em', color: '#30363d' }}
        >
          ENTER TO SEND · SHIFT+ENTER FOR NEW LINE
        </p>
      </div>
    </div>
  );
}

// ─── StreamingMessageBlock ────────────────────────────────────────────────────
// Separate component so we can use a callback ref for the streaming content div

interface StreamingMessageBlockProps {
  msg: ChatMessage;
  accentColor: string;
  onOptionClick: (msgId: string, index: number, opt: string) => void;
  onRegisterRef: (msgId: string, el: HTMLDivElement | null) => void;
}

function StreamingMessageBlock({ msg, accentColor, onOptionClick, onRegisterRef }: StreamingMessageBlockProps) {
  if (msg.role === 'user') {
    return (
      <div className="py-3" style={{ borderBottom: '1px solid rgba(33,38,45,0.5)' }}>
        <div className="flex items-start gap-3">
          <span
            className="font-mono text-sm font-bold mt-0.5 select-none shrink-0"
            style={{ color: accentColor }}
          >
            ❯
          </span>
          <div
            className="font-mono text-sm whitespace-pre-wrap break-words"
            style={{ color: '#c9d1d9' }}
          >
            {msg.content}
          </div>
        </div>
      </div>
    );
  }

  // Assistant message
  const detected = !msg.isStreaming ? detectMultipleChoice(msg.content) : null;
  const mcqOptions = msg.question?.options?.length
    ? msg.question.options.map(o => o.label)
    : detected?.options || null;
  const mcqQuestion = msg.question?.question || detected?.question || '';

  return (
    <div className="py-4" style={{ borderBottom: '1px solid rgba(33,38,45,0.5)' }}>
      {/* Tool chips — inline in the flow */}
      {msg.toolChips.length > 0 && (
        <div className="flex flex-col gap-0.5 mb-3">
          {msg.toolChips.map(chip => (
            <div
              key={chip.id}
              className="flex items-center gap-1.5 font-mono py-0.5"
              style={{ fontSize: '12px' }}
            >
              <span style={{ color: 'var(--chart-2)', fontSize: '11px' }}>⚡</span>
              <span style={{ color: '#c9d1d9', fontWeight: 600 }}>{chip.name}</span>
              {chip.inputSummary && (
                <>
                  <span style={{ color: '#484f58' }}>→</span>
                  <span style={{ color: '#6e7681' }}>{chip.inputSummary}</span>
                </>
              )}
              {chip.status === 'running' ? (
                <span
                  className="ml-1"
                  style={{
                    fontSize: '10px',
                    color: 'var(--chart-3)',
                    animation: 'ab-pulse 1s ease-in-out infinite',
                    display: 'inline-block',
                  }}
                >
                  ⟳
                </span>
              ) : (
                <span className="ml-1" style={{ fontSize: '11px', color: '#3fb950' }}>✓</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Thinking indicator — animated inline */}
      {msg.thinking && (
        <div className="mb-3">
          {!msg.thinkingDone ? (
            <div className="flex items-center gap-2">
              <span style={{ fontSize: '13px' }}>💭</span>
              <span
                className="font-mono"
                style={{
                  fontSize: '12px',
                  color: '#8b949e',
                  animation: 'ab-pulse 1.5s ease-in-out infinite',
                }}
              >
                thinking
              </span>
              <span className="ab-thinking-dots font-mono" style={{ fontSize: '12px', color: '#8b949e' }}>
                <span className="ab-dot">.</span>
                <span className="ab-dot">.</span>
                <span className="ab-dot">.</span>
              </span>
            </div>
          ) : (
            <details className="group">
              <summary
                className="cursor-pointer select-none flex items-center gap-1.5 font-mono list-none"
                style={{ fontSize: '11px', color: '#484f58' }}
              >
                <span>💭</span>
                <span>thought for a moment</span>
                <ChevronRight
                  size={10}
                  className="transition-transform group-open:rotate-90"
                  style={{ color: '#484f58' }}
                />
              </summary>
              <div
                className="mt-2 pl-4 font-mono whitespace-pre-wrap max-h-40 overflow-y-auto ab-scroll"
                style={{
                  fontSize: '11px',
                  color: '#484f58',
                  borderLeft: '2px solid #21262d',
                }}
              >
                {msg.thinking}
              </div>
            </details>
          )}
        </div>
      )}

      {/* Text content */}
      {msg.isStreaming ? (
        <div className="font-mono text-sm leading-relaxed" style={{ color: '#c9d1d9' }}>
          {/* IMPORTANT: streaming div must not have React children — innerHTML is set directly */}
          <div
            ref={el => onRegisterRef(msg.id, el)}
            className="msg-markdown inline"
          />
          <span
            className="inline-block w-2 h-4 align-text-bottom ml-0.5"
            style={{
              background: accentColor,
              animation: 'cursor-blink 1s step-end infinite',
            }}
          />
        </div>
      ) : msg.content ? (
        <div
          className="msg-markdown font-mono text-sm leading-relaxed"
          style={{ color: '#c9d1d9' }}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
        />
      ) : null}

      {/* MCQ choice cards */}
      {mcqOptions && mcqOptions.length > 0 && !msg.isStreaming && (
        <div className="mt-5">
          {mcqQuestion && (
            <p
              className="font-mono font-bold mb-3"
              style={{ fontSize: '12px', color: '#c9d1d9', letterSpacing: '0.04em' }}
            >
              {mcqQuestion}
            </p>
          )}
          <div
            className="grid gap-2"
            style={{
              gridTemplateColumns: mcqOptions.length <= 3
                ? `repeat(${mcqOptions.length}, 1fr)`
                : 'repeat(auto-fit, minmax(200px, 1fr))',
            }}
          >
            {mcqOptions.map((opt, i) => {
              const isSelected = msg.question?.answered === opt;
              const isAnswered = !!msg.question?.answered;
              const dimmed = isAnswered && !isSelected;

              return (
                <button
                  key={i}
                  disabled={isAnswered}
                  onClick={() => onOptionClick(msg.id, i, opt)}
                  className="text-left font-mono transition-all"
                  style={{
                    fontSize: '12px',
                    padding: '14px 16px',
                    background: isSelected
                      ? `color-mix(in srgb, ${accentColor} 10%, #0d1117)`
                      : '#161b22',
                    border: isSelected
                      ? `2px solid ${accentColor}`
                      : '2px solid #21262d',
                    opacity: dimmed ? 0.3 : 1,
                    cursor: isAnswered ? 'default' : 'pointer',
                  }}
                  onMouseEnter={e => {
                    if (!isAnswered) {
                      e.currentTarget.style.borderColor = `color-mix(in srgb, ${accentColor} 60%, transparent)`;
                      e.currentTarget.style.background = `color-mix(in srgb, ${accentColor} 5%, #161b22)`;
                    }
                  }}
                  onMouseLeave={e => {
                    if (!isAnswered && !isSelected) {
                      e.currentTarget.style.borderColor = '#21262d';
                      e.currentTarget.style.background = '#161b22';
                    }
                  }}
                >
                  <div className="flex items-start gap-3">
                    <span
                      className="font-bold text-sm mt-px shrink-0"
                      style={{ color: isSelected ? accentColor : '#484f58', minWidth: '16px' }}
                    >
                      {i + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <span
                        className="font-semibold block"
                        style={{
                          fontSize: '12px',
                          letterSpacing: '0.03em',
                          color: isSelected ? accentColor : '#c9d1d9',
                        }}
                      >
                        {opt}
                      </span>
                      {msg.question?.options?.[i]?.description && (
                        <span
                          className="block mt-1 leading-relaxed"
                          style={{ color: '#6e7681', fontSize: '10px' }}
                        >
                          {msg.question.options[i].description}
                        </span>
                      )}
                    </div>
                    {isSelected && (
                      <CheckCircle
                        size={14}
                        className="shrink-0 mt-0.5"
                        style={{ color: accentColor }}
                      />
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
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
      setState({ screen: 'chat', intent: 'goliath-feature', backend: null });
    }
  }

  function handleBackendSelect(backend: BackendChoice) {
    setState({ screen: 'chat', intent: 'new-app', backend });
  }

  const subtitleMap: Record<string, string> = {
    'intent': 'Select your build intent to begin',
    'new-app-backend': 'New App — choose a backend',
    'chat': 'DevOps build session',
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <AppBuilderStyles />

      {/* Hide PageHeader in chat mode — BuilderChat has its own minimal header */}
      {state.screen !== 'chat' && (
        <PageHeader
          title="App Builder"
          subtitle={subtitleMap[state.screen]}
        />
      )}

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
