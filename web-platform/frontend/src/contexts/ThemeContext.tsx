import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';

// ─── Theme Definitions ────────────────────────────────────────────────────────

export type ThemeName = 'dark-brutal' | 'sand-brutal' | 'ice-brutal' | 'toxic-brutal' | 'blood-brutal';

export interface ThemeDefinition {
  name: ThemeName;
  label: string;
  description: string;
  /** Primary accent color — shown as color swatch */
  accent: string;
  /** Background color — shown as swatch backing */
  bg: string;
  /** Text color for contrast check in the picker */
  text: string;
}

export const THEMES: ThemeDefinition[] = [
  {
    name: 'dark-brutal',
    label: 'DARK BRUTAL',
    description: 'HACKER TERMINAL',
    accent: '#a3e635',
    bg: '#08080c',
    text: '#e4e4e7',
  },
  {
    name: 'sand-brutal',
    label: 'SAND BRUTAL',
    description: 'ANALOG BRUTALISM',
    accent: '#c2410c',
    bg: '#f5f0e8',
    text: '#1a1a1a',
  },
  {
    name: 'ice-brutal',
    label: 'ICE BRUTAL',
    description: 'OPS CENTER',
    accent: '#22d3ee',
    bg: '#060c10',
    text: '#e2f0f7',
  },
  {
    name: 'toxic-brutal',
    label: 'TOXIC BRUTAL',
    description: 'ACID HACKER',
    accent: '#39ff14',
    bg: '#050d05',
    text: '#e8f5e8',
  },
  {
    name: 'blood-brutal',
    label: 'BLOOD BRUTAL',
    description: 'CRIMSON OPS',
    accent: '#ef4444',
    bg: '#0d0808',
    text: '#f5e8e8',
  },
];

const STORAGE_KEY = 'goliath-theme';
const DEFAULT_THEME: ThemeName = 'dark-brutal';

// ─── Context ──────────────────────────────────────────────────────────────────

interface ThemeContextValue {
  theme: ThemeName;
  setTheme: (name: ThemeName) => void;
  themeDefinition: ThemeDefinition;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

// ─── Provider ─────────────────────────────────────────────────────────────────

function getInitialTheme(): ThemeName {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && THEMES.some((t) => t.name === stored)) {
      return stored as ThemeName;
    }
  } catch {
    // localStorage may be unavailable
  }
  return DEFAULT_THEME;
}

function applyTheme(name: ThemeName) {
  // Set data-theme on <html>
  document.documentElement.setAttribute('data-theme', name);

  // Mirror the legacy .dark class so shadcn components that read it still work
  // (all dark themes should keep .dark; sand-brutal removes it)
  if (name === 'sand-brutal') {
    document.documentElement.classList.remove('dark');
  } else {
    document.documentElement.classList.add('dark');
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>(getInitialTheme);

  // Apply on mount and whenever theme changes
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = (name: ThemeName) => {
    setThemeState(name);
    try {
      localStorage.setItem(STORAGE_KEY, name);
    } catch {
      // ignore
    }
  };

  const themeDefinition = THEMES.find((t) => t.name === theme) ?? THEMES[0];

  return (
    <ThemeContext.Provider value={{ theme, setTheme, themeDefinition }}>
      {children}
    </ThemeContext.Provider>
  );
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
