import { useState, useEffect } from 'react';
import { ArrowLeft, Wrench, Rocket, ChevronRight, Server, CheckCircle, AlertCircle, ExternalLink, Loader } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';

// ─── Types ────────────────────────────────────────────────────────────────────

type Intent = 'goliath-feature' | 'new-app';
type BackendChoice = 'postgres' | 'convex-cloud' | 'convex-self-hosted';
type FeatureType = 'page' | 'component' | 'api' | 'agent' | 'data-pipeline' | 'other';

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
    // Try to read the phase1-status.json from the API
    // For now, we check if the file exists via a lightweight fetch
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
          // API not available — infrastructure not set up
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
      icon: <Loader size={12} style={{ animation: 'spin 1s linear infinite' }} />,
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
      {/* Top row — always visible */}
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
          <Server size={13} style={{ color: config.color, flexShrink: 0 }} />
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

      {/* Expanded detail */}
      {expanded && (
        <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {/* Checklist items */}
          <InfraCheckItem
            label="Docker Engine"
            status={infra.docker}
          />
          <InfraCheckItem
            label="Traefik Reverse Proxy"
            status={infra.traefik}
          />
          <InfraCheckItem
            label="Wildcard DNS"
            status={infra.domain ? true : false}
            detail={infra.domain ? `*.${infra.domain}` : undefined}
          />

          {/* Hello world link */}
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

          {/* Setup instructions for unconfigured state */}
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
              <span
                style={{
                  display: 'block',
                  fontSize: '8px',
                  color: 'var(--theme-text-dim)',
                  fontFamily: 'JetBrains Mono, monospace',
                  letterSpacing: '0.08em',
                  marginTop: '8px',
                }}
              >
                See /opt/goliath/docs/app-builder-phase1-setup.md for full instructions
              </span>
            </div>
          )}

          {/* Last checked timestamp */}
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
        <Loader size={10} style={{ color: 'var(--theme-text-dim)', animation: 'spin 1s linear infinite', flexShrink: 0 }} />
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
      {/* Header */}
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
            APP BUILDER — PHASE 1
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

      {/* Phase 1 Infrastructure Status */}
      <InfraStatusBanner />

      {/* Intent Cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: '20px',
          width: '100%',
          maxWidth: '640px',
        }}
      >
        {/* Goliath Feature Card */}
        <IntentCard
          icon={<Wrench size={32} />}
          emoji="🔧"
          label="Goliath Feature"
          sublabel="PATCH MODE"
          description="Add a page, component, API route, or agent to Goliath itself. DevOps edits the existing codebase and may trigger a restart."
          tags={['Edits Goliath source', 'May require RESTART', 'Uses existing stack']}
          accentColor="var(--chart-4)"
          onClick={() => onSelect('goliath-feature')}
        />

        {/* New App Card */}
        <IntentCard
          icon={<Rocket size={32} />}
          emoji="🚀"
          label="New App"
          sublabel="CONTAINER MODE"
          description="Spin up a standalone application in its own Docker container with its own database, URL, and isolated runtime."
          tags={['Isolated container', 'Auto-assigned URL', 'Never touches Goliath']}
          accentColor="var(--chart-1)"
          onClick={() => onSelect('new-app')}
        />
      </div>

      {/* Footer hint */}
      <p
        style={{
          marginTop: '32px',
          fontSize: '10px',
          color: 'var(--theme-text-dim)',
          letterSpacing: '0.08em',
          fontFamily: 'JetBrains Mono, monospace',
          textAlign: 'center',
        }}
      >
        Phase 1 — UI shell · Build pipeline wired in Phase 2
      </p>
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

function IntentCard({ emoji, label, sublabel, description, tags, accentColor, onClick }: IntentCardProps) {
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
      {/* Emoji + sublabel row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '36px', lineHeight: 1 }}>{emoji}</span>
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

      {/* Label */}
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

      {/* Tags */}
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

      {/* CTA arrow */}
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

// ─── Backend Selector (New App flow — step 1) ─────────────────────────────────

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
    emoji: '🐘',
    label: 'Postgres',
    sublabel: 'SIDECAR CONTAINER',
    description:
      'A Postgres container runs alongside your app in docker-compose. Fully isolated on-server, standard SQL. Connection injected as DATABASE_URL.',
    pros: ['On-server', 'Standard SQL', 'Destroyed with app', 'Zero external deps'],
    accentColor: 'var(--chart-2)',
  },
  {
    id: 'convex-cloud',
    emoji: '⚡',
    label: 'Convex (Cloud)',
    sublabel: 'CONVEX.DEV',
    description:
      'App connects to convex.dev cloud. DevOps generates schema + server functions. Best for real-time apps. Requires a Convex API key.',
    pros: ['Real-time sync', 'Fast prototyping', 'Live subscriptions', 'No local DB'],
    accentColor: 'var(--chart-3)',
  },
  {
    id: 'convex-self-hosted',
    emoji: '🏠',
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

      {/* Header */}
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

      {/* Backend cards */}
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

      {/* Note */}
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
        The 🔧 Goliath Feature path skips this selector — it uses the existing Goliath DB and stack.
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

function BackendCard({ emoji, label, sublabel, description, pros, accentColor, onClick }: BackendCardProps) {
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
      {/* Emoji + sublabel */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '28px', lineHeight: 1 }}>{emoji}</span>
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

      {/* Label + desc */}
      <div>
        <span
          style={{
            display: 'block',
            fontSize: '13px',
            fontWeight: 700,
            color: hovered ? accentColor : 'var(--theme-text-primary)',
            letterSpacing: '0.06em',
            fontFamily: 'JetBrains Mono, monospace',
            marginBottom: '5px',
            transition: 'color 0.15s',
          }}
        >
          {label}
        </span>
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
      </div>

      {/* Pros list */}
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

      {/* CTA */}
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

// ─── New App — Describe step ──────────────────────────────────────────────────

interface NewAppDescribeProps {
  backend: BackendChoice;
  onBack: () => void;
}

const BACKEND_LABELS: Record<BackendChoice, string> = {
  postgres: 'Postgres',
  'convex-cloud': 'Convex Cloud',
  'convex-self-hosted': 'Convex Self-Hosted',
};

const BACKEND_EMOJIS: Record<BackendChoice, string> = {
  postgres: '🐘',
  'convex-cloud': '⚡',
  'convex-self-hosted': '🏠',
};

function NewAppDescribe({ backend, onBack }: NewAppDescribeProps) {
  const [appName, setAppName] = useState('');
  const [description, setDescription] = useState('');
  const [submitted, setSubmitted] = useState(false);

  const sanitizedName = appName.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/--+/g, '-').replace(/^-|-$/g, '');
  const previewUrl = sanitizedName ? `${sanitizedName}.yourdomain.com` : 'appname.yourdomain.com';

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!appName.trim() || !description.trim()) return;
    setSubmitted(true);
  }

  if (submitted) {
    return <StubPlaceholder intent="new-app" backend={backend} appName={appName} description={description} onReset={() => setSubmitted(false)} />;
  }

  return (
    <div className="flex flex-col flex-1 p-8 min-h-0 overflow-y-auto" data-scroll-container>
      <BackNavBar onBack={onBack} label="Back to backend selector" />

      <div className="mb-8 mt-2">
        <Breadcrumb items={['App Builder', 'New App', BACKEND_LABELS[backend], 'Describe']} />
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
          Describe your app
        </h2>
        <p style={{ fontSize: '11px', color: 'var(--theme-text-muted)', fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.04em', lineHeight: 1.6 }}>
          Tell DevOps what you want to build. The more detail, the better the initial output.
        </p>
      </div>

      {/* Selected backend badge */}
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '8px',
          background: 'var(--theme-bg-tertiary)',
          border: '1px solid var(--theme-border)',
          padding: '6px 12px',
          marginBottom: '24px',
          alignSelf: 'flex-start',
        }}
      >
        <span style={{ fontSize: '14px' }}>{BACKEND_EMOJIS[backend]}</span>
        <span style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--theme-text-secondary)', fontFamily: 'JetBrains Mono, monospace' }}>
          BACKEND: {BACKEND_LABELS[backend].toUpperCase()}
        </span>
        <span
          style={{ fontSize: '9px', color: 'var(--theme-accent)', cursor: 'pointer', letterSpacing: '0.08em', fontFamily: 'JetBrains Mono, monospace' }}
          onClick={onBack}
        >
          CHANGE
        </span>
      </div>

      <form onSubmit={handleSubmit} style={{ maxWidth: '600px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {/* App name */}
        <FormField label="App name" hint={`Will be deployed at: ${previewUrl}`}>
          <input
            type="text"
            value={appName}
            onChange={(e) => setAppName(e.target.value)}
            placeholder="my-solar-dashboard"
            style={{
              width: '100%',
              background: 'var(--theme-bg-secondary)',
              border: '2px solid var(--theme-border)',
              color: 'var(--theme-text-primary)',
              fontSize: '13px',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.04em',
              padding: '10px 12px',
              outline: 'none',
            }}
            onFocus={(e) => (e.target.style.borderColor = 'var(--theme-accent)')}
            onBlur={(e) => (e.target.style.borderColor = 'var(--theme-border)')}
          />
        </FormField>

        {/* Description */}
        <FormField label="Describe what you want to build" hint="Plain English. DevOps figures out the tech.">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Build me a landing page for my solar consulting business with a contact form and email integration."
            rows={5}
            style={{
              width: '100%',
              background: 'var(--theme-bg-secondary)',
              border: '2px solid var(--theme-border)',
              color: 'var(--theme-text-primary)',
              fontSize: '12px',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.04em',
              padding: '10px 12px',
              outline: 'none',
              resize: 'vertical',
              lineHeight: 1.6,
            }}
            onFocus={(e) => (e.target.style.borderColor = 'var(--theme-accent)')}
            onBlur={(e) => (e.target.style.borderColor = 'var(--theme-border)')}
          />
        </FormField>

        {/* Convex API key notice */}
        {backend === 'convex-cloud' && (
          <div
            style={{
              background: 'var(--theme-bg-tertiary)',
              border: '1px solid var(--chart-3)',
              borderLeft: '3px solid var(--chart-3)',
              padding: '10px 14px',
              fontSize: '10px',
              color: 'var(--theme-text-muted)',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.06em',
              lineHeight: 1.6,
            }}
          >
            <span style={{ color: 'var(--chart-3)', fontWeight: 700 }}>⚡ Convex Cloud requires an API key.</span>{' '}
            DevOps will prompt for it before generating code. The key is stored in a per-app .env file and never committed to git.
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={!appName.trim() || !description.trim()}
          style={{
            background: appName.trim() && description.trim() ? 'var(--theme-accent)' : 'var(--theme-bg-tertiary)',
            color: appName.trim() && description.trim() ? 'var(--theme-bg-primary)' : 'var(--theme-text-dim)',
            border: 'none',
            padding: '12px 24px',
            fontSize: '11px',
            fontWeight: 700,
            letterSpacing: '0.14em',
            fontFamily: 'JetBrains Mono, monospace',
            cursor: appName.trim() && description.trim() ? 'pointer' : 'not-allowed',
            alignSelf: 'flex-start',
            transition: 'background 0.15s, color 0.15s',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <Rocket size={13} />
          QUEUE BUILD
        </button>
      </form>
    </div>
  );
}

// ─── Goliath Feature — Describe step ─────────────────────────────────────────

const FEATURE_TYPES: { id: FeatureType; label: string; description: string }[] = [
  { id: 'page',          label: 'New Page',       description: 'A full page in the Goliath GUI with its own route' },
  { id: 'component',    label: 'UI Component',   description: 'A reusable widget, card, or panel component' },
  { id: 'api',          label: 'API Endpoint',   description: 'A new route in the backend API layer' },
  { id: 'agent',        label: 'New Agent',      description: 'A specialized AI agent with a defined role' },
  { id: 'data-pipeline', label: 'Data Pipeline', description: 'A cron job, poller, or automated data flow' },
  { id: 'other',        label: 'Other / Mixed',  description: 'Multi-part change or something that doesn\'t fit the above' },
];

interface GoliathFeatureDescribeProps {
  onBack: () => void;
}

function GoliathFeatureDescribe({ onBack }: GoliathFeatureDescribeProps) {
  const [featureType, setFeatureType] = useState<FeatureType | null>(null);
  const [description, setDescription] = useState('');
  const [submitted, setSubmitted] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!featureType || !description.trim()) return;
    setSubmitted(true);
  }

  if (submitted && featureType) {
    return <StubPlaceholder intent="goliath-feature" featureType={featureType} description={description} onReset={() => setSubmitted(false)} />;
  }

  return (
    <div className="flex flex-col flex-1 p-8 min-h-0 overflow-y-auto" data-scroll-container>
      <BackNavBar onBack={onBack} label="Back to intent selector" />

      <div className="mb-8 mt-2">
        <Breadcrumb items={['App Builder', 'Goliath Feature', 'Describe']} />
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
          Add a Goliath feature
        </h2>
        <p style={{ fontSize: '11px', color: 'var(--theme-text-muted)', fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.04em', lineHeight: 1.6 }}>
          DevOps will edit the Goliath source code directly. A restart may be required to apply changes.
        </p>
      </div>

      <form onSubmit={handleSubmit} style={{ maxWidth: '620px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
        {/* Feature type selector */}
        <div>
          <label
            style={{
              display: 'block',
              fontSize: '10px',
              fontWeight: 700,
              letterSpacing: '0.14em',
              color: 'var(--theme-text-secondary)',
              fontFamily: 'JetBrains Mono, monospace',
              marginBottom: '10px',
            }}
          >
            FEATURE TYPE
          </label>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))',
              gap: '8px',
            }}
          >
            {FEATURE_TYPES.map((ft) => {
              const isSelected = featureType === ft.id;
              return (
                <button
                  key={ft.id}
                  type="button"
                  onClick={() => setFeatureType(ft.id)}
                  style={{
                    background: isSelected ? 'var(--theme-accent-dim)' : 'var(--theme-bg-secondary)',
                    border: isSelected ? '2px solid var(--theme-accent)' : '2px solid var(--theme-border)',
                    padding: '10px 12px',
                    textAlign: 'left',
                    cursor: 'pointer',
                    transition: 'border-color 0.12s, background 0.12s',
                  }}
                >
                  <span
                    style={{
                      display: 'block',
                      fontSize: '11px',
                      fontWeight: 700,
                      color: isSelected ? 'var(--theme-accent)' : 'var(--theme-text-primary)',
                      fontFamily: 'JetBrains Mono, monospace',
                      letterSpacing: '0.06em',
                      marginBottom: '3px',
                    }}
                  >
                    {ft.label}
                  </span>
                  <span
                    style={{
                      display: 'block',
                      fontSize: '9px',
                      color: 'var(--theme-text-dim)',
                      fontFamily: 'JetBrains Mono, monospace',
                      letterSpacing: '0.04em',
                      lineHeight: 1.45,
                    }}
                  >
                    {ft.description}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Description */}
        <FormField label="Describe the feature" hint="Be specific. DevOps will locate the right files and implement the change.">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Add a Production Dashboard page that shows daily energy output per project over the past 30 days using the existing production data."
            rows={5}
            style={{
              width: '100%',
              background: 'var(--theme-bg-secondary)',
              border: '2px solid var(--theme-border)',
              color: 'var(--theme-text-primary)',
              fontSize: '12px',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.04em',
              padding: '10px 12px',
              outline: 'none',
              resize: 'vertical',
              lineHeight: 1.6,
            }}
            onFocus={(e) => (e.target.style.borderColor = 'var(--theme-accent)')}
            onBlur={(e) => (e.target.style.borderColor = 'var(--theme-border)')}
          />
        </FormField>

        {/* Restart warning */}
        <div
          style={{
            background: 'var(--theme-bg-tertiary)',
            border: '1px solid var(--chart-4)',
            borderLeft: '3px solid var(--chart-4)',
            padding: '10px 14px',
            fontSize: '10px',
            color: 'var(--theme-text-muted)',
            fontFamily: 'JetBrains Mono, monospace',
            letterSpacing: '0.06em',
            lineHeight: 1.6,
          }}
        >
          <span style={{ color: 'var(--chart-4)', fontWeight: 700 }}>🔧 Patch mode.</span>{' '}
          DevOps will edit Goliath source files directly. Changes to the frontend require a Vite rebuild.
          Changes to the backend may emit RESTART_REQUIRED.
        </div>

        {/* Submit */}
        <button
          type="submit"
          disabled={!featureType || !description.trim()}
          style={{
            background: featureType && description.trim() ? 'var(--theme-accent)' : 'var(--theme-bg-tertiary)',
            color: featureType && description.trim() ? 'var(--theme-bg-primary)' : 'var(--theme-text-dim)',
            border: 'none',
            padding: '12px 24px',
            fontSize: '11px',
            fontWeight: 700,
            letterSpacing: '0.14em',
            fontFamily: 'JetBrains Mono, monospace',
            cursor: featureType && description.trim() ? 'pointer' : 'not-allowed',
            alignSelf: 'flex-start',
            transition: 'background 0.15s, color 0.15s',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <Wrench size={13} />
          QUEUE BUILD
        </button>
      </form>
    </div>
  );
}

// ─── Stub Placeholder (Phase 1 — pipeline not wired) ─────────────────────────

interface StubPlaceholderProps {
  intent: Intent;
  backend?: BackendChoice;
  appName?: string;
  featureType?: FeatureType;
  description: string;
  onReset: () => void;
}

function StubPlaceholder({ intent, backend, appName, featureType, description, onReset }: StubPlaceholderProps) {
  return (
    <div className="flex flex-col flex-1 p-8 min-h-0 overflow-y-auto" data-scroll-container>
      <div
        style={{
          maxWidth: '560px',
          background: 'var(--theme-bg-secondary)',
          border: '2px solid var(--theme-border)',
          padding: '32px',
          display: 'flex',
          flexDirection: 'column',
          gap: '20px',
        }}
      >
        {/* Status badge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: 'var(--chart-3)',
              display: 'inline-block',
              animation: 'pulse 1.5s ease-in-out infinite',
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.16em',
              color: 'var(--chart-3)',
              fontFamily: 'JetBrains Mono, monospace',
            }}
          >
            BUILD QUEUED — PHASE 1 STUB
          </span>
        </div>

        {/* Summary */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <SummaryRow label="Intent" value={intent === 'new-app' ? '🚀 New App' : '🔧 Goliath Feature'} />
          {intent === 'new-app' && backend && (
            <SummaryRow label="Backend" value={`${BACKEND_EMOJIS[backend]} ${BACKEND_LABELS[backend]}`} />
          )}
          {intent === 'new-app' && appName && (
            <SummaryRow label="App name" value={appName} />
          )}
          {intent === 'goliath-feature' && featureType && (
            <SummaryRow label="Feature type" value={FEATURE_TYPES.find((f) => f.id === featureType)?.label ?? featureType} />
          )}
          <SummaryRow label="Description" value={description} multiline />
        </div>

        {/* Phase 1 note */}
        <div
          style={{
            background: 'var(--theme-bg-tertiary)',
            border: '1px solid var(--theme-border)',
            borderLeft: '3px solid var(--chart-2)',
            padding: '12px 14px',
            fontSize: '10px',
            color: 'var(--theme-text-muted)',
            fontFamily: 'JetBrains Mono, monospace',
            letterSpacing: '0.06em',
            lineHeight: 1.7,
          }}
        >
          <span style={{ color: 'var(--chart-2)', fontWeight: 700 }}>Phase 1 — UI shell only.</span>
          <br />
          The build pipeline is not wired yet. In Phase 2, submitting this form will dispatch DevOps,
          open a live preview panel with an iframe, and stream container logs to the debug console below.
          <br /><br />
          For now, copy this configuration and paste it into the Chat tab to start a DevOps build session manually.
        </div>

        {/* Copyable config block */}
        <div>
          <span
            style={{
              display: 'block',
              fontSize: '9px',
              fontWeight: 700,
              letterSpacing: '0.14em',
              color: 'var(--theme-text-dim)',
              fontFamily: 'JetBrains Mono, monospace',
              marginBottom: '6px',
            }}
          >
            COPY TO CHAT
          </span>
          <pre
            style={{
              background: 'var(--theme-bg-primary)',
              border: '1px solid var(--theme-border)',
              padding: '12px',
              fontSize: '10px',
              color: 'var(--theme-text-secondary)',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: '0.04em',
              lineHeight: 1.7,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              margin: 0,
            }}
          >
            {intent === 'new-app'
              ? `@DevOps Build a new app (container mode)\nBackend: ${backend ? BACKEND_LABELS[backend] : ''}\nApp name: ${appName || ''}\n\n${description}`
              : `@DevOps Patch Goliath (feature mode)\nFeature type: ${featureType ? FEATURE_TYPES.find((f) => f.id === featureType)?.label : ''}\n\n${description}`}
          </pre>
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          <button
            onClick={onReset}
            style={{
              background: 'transparent',
              border: '2px solid var(--theme-border)',
              color: 'var(--theme-text-muted)',
              padding: '8px 16px',
              fontSize: '10px',
              fontWeight: 700,
              letterSpacing: '0.12em',
              fontFamily: 'JetBrains Mono, monospace',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <ArrowLeft size={11} />
            EDIT
          </button>
        </div>
      </div>
    </div>
  );
}

function SummaryRow({ label, value, multiline }: { label: string; value: string; multiline?: boolean }) {
  return (
    <div style={{ display: 'flex', gap: '12px', alignItems: multiline ? 'flex-start' : 'center' }}>
      <span
        style={{
          fontSize: '9px',
          fontWeight: 700,
          letterSpacing: '0.12em',
          color: 'var(--theme-text-dim)',
          fontFamily: 'JetBrains Mono, monospace',
          minWidth: '90px',
          flexShrink: 0,
          paddingTop: multiline ? '1px' : 0,
        }}
      >
        {label.toUpperCase()}
      </span>
      <span
        style={{
          fontSize: '11px',
          color: 'var(--theme-text-secondary)',
          fontFamily: 'JetBrains Mono, monospace',
          letterSpacing: '0.03em',
          lineHeight: 1.5,
        }}
      >
        {value}
      </span>
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

interface FormFieldProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
}

function FormField({ label, hint, children }: FormFieldProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <label
        style={{
          fontSize: '10px',
          fontWeight: 700,
          letterSpacing: '0.14em',
          color: 'var(--theme-text-secondary)',
          fontFamily: 'JetBrains Mono, monospace',
        }}
      >
        {label.toUpperCase()}
      </label>
      {children}
      {hint && (
        <span
          style={{
            fontSize: '9px',
            color: 'var(--theme-text-dim)',
            fontFamily: 'JetBrains Mono, monospace',
            letterSpacing: '0.06em',
          }}
        >
          {hint}
        </span>
      )}
    </div>
  );
}

// ─── Root Page ────────────────────────────────────────────────────────────────

type AppBuilderState =
  | { screen: 'intent' }
  | { screen: 'new-app-backend' }
  | { screen: 'new-app-describe'; backend: BackendChoice }
  | { screen: 'goliath-feature-describe' };

export function AppBuilderPage() {
  const [state, setState] = useState<AppBuilderState>({ screen: 'intent' });

  function handleIntentSelect(intent: Intent) {
    if (intent === 'new-app') {
      setState({ screen: 'new-app-backend' });
    } else {
      setState({ screen: 'goliath-feature-describe' });
    }
  }

  function handleBackendSelect(backend: BackendChoice) {
    setState({ screen: 'new-app-describe', backend });
  }

  // Build subtitle based on current screen
  const subtitleMap: Record<AppBuilderState['screen'], string> = {
    'intent': 'Select your build intent to begin',
    'new-app-backend': 'New App — choose a backend',
    'new-app-describe': 'New App — describe your app',
    'goliath-feature-describe': 'Goliath Feature — describe the change',
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <PageHeader
        title="App Builder"
        subtitle={subtitleMap[state.screen]}
      />

      {/* Render the correct screen */}
      {state.screen === 'intent' && (
        <IntentSelector onSelect={handleIntentSelect} />
      )}

      {state.screen === 'new-app-backend' && (
        <BackendSelector
          onSelect={handleBackendSelect}
          onBack={() => setState({ screen: 'intent' })}
        />
      )}

      {state.screen === 'new-app-describe' && (
        <NewAppDescribe
          backend={state.backend}
          onBack={() => setState({ screen: 'new-app-backend' })}
        />
      )}

      {state.screen === 'goliath-feature-describe' && (
        <GoliathFeatureDescribe
          onBack={() => setState({ screen: 'intent' })}
        />
      )}
    </div>
  );
}
