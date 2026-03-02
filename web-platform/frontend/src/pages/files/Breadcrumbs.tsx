import { ChevronRight, Home } from 'lucide-react';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';

interface BreadcrumbsProps {
  currentPath: string;
  onNavigate: (path: string) => void;
}

export function Breadcrumbs({ currentPath, onNavigate }: BreadcrumbsProps) {
  const segments = currentPath ? currentPath.split('/').filter(Boolean) : [];

  return (
    <ScrollArea className="w-full whitespace-nowrap">
      <nav className="flex items-center gap-1 text-sm">
        <button
          onClick={() => onNavigate('')}
          className="flex items-center gap-1.5 px-2 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors shrink-0"
        >
          <Home className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Projects</span>
        </button>

        {segments.map((segment, i) => {
          const pathUpTo = segments.slice(0, i + 1).join('/');
          const isLast = i === segments.length - 1;

          return (
            <div key={pathUpTo} className="flex items-center gap-1 shrink-0">
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />
              <button
                onClick={() => onNavigate(pathUpTo)}
                className={
                  isLast
                    ? 'px-2 py-1 rounded-md text-foreground font-medium'
                    : 'px-2 py-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors'
                }
              >
                {segment}
              </button>
            </div>
          );
        })}
      </nav>
      <ScrollBar orientation="horizontal" />
    </ScrollArea>
  );
}
