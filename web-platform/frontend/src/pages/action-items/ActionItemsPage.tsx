import { useState, useEffect, useCallback } from 'react';
import { RefreshCw, ClipboardList } from 'lucide-react';
import { api } from '../../api/client';
import type { ActionItem } from '../../types';
import { PageHeader } from '../../components/common/PageHeader';
import { ListSkeleton } from '../../components/common/LoadingSpinner';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { ActionItemRow } from './ActionItemRow';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';

const PROJECT_OPTIONS = [
  'All Projects',
  'Union Ridge',
  'Duff',
  'Salt Branch',
  'Blackford',
  'Delta Bobcat',
  'Tehuacana',
  'Three Rivers',
  'Scioto Ridge',
  'Mayes',
  'Graceland',
  'Pecan Prairie',
  'Duffy-Bess',
];

export function ActionItemsPage() {
  const [items, setItems] = useState<ActionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [projectFilter, setProjectFilter] = useState('All Projects');
  const [statusFilter, setStatusFilter] = useState<'all' | 'open' | 'resolved'>('all');

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const project = projectFilter === 'All Projects' ? undefined : projectFilter;
      const data = await api.getActionItems(project);
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load action items');
    } finally {
      setLoading(false);
    }
  }, [projectFilter]);

  useEffect(() => {
    loadItems();
  }, [loadItems]);

  const handleResolve = async (id: string) => {
    try {
      await api.resolveActionItem(id);
      setItems((prev) =>
        prev.map((item) =>
          item.id === id ? { ...item, status: 'resolved' as const } : item
        )
      );
    } catch {
      setItems((prev) =>
        prev.map((item) =>
          item.id === id ? { ...item, status: 'resolved' as const } : item
        )
      );
    }
  };

  const filteredItems = items
    .filter((item) => {
      if (statusFilter === 'open') return item.status !== 'resolved';
      if (statusFilter === 'resolved') return item.status === 'resolved';
      return true;
    })
    .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());

  const openCount = items.filter((i) => i.status !== 'resolved').length;
  const resolvedCount = items.filter((i) => i.status === 'resolved').length;

  return (
    <div className="flex flex-col h-full min-h-0">
      <PageHeader
        title="Action Items"
        subtitle={`${openCount} open, ${resolvedCount} resolved`}
      />

      {/* Filters */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-border">
        <select
          value={projectFilter}
          onChange={(e) => setProjectFilter(e.target.value)}
          className="h-8 px-2.5 text-xs rounded-md border border-input bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-ring/50 focus:border-ring transition-[border-color,box-shadow] appearance-none pr-7"
          style={{
            backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%2371717a' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
            backgroundPosition: 'right 4px center',
            backgroundRepeat: 'no-repeat',
            backgroundSize: '16px',
          }}
        >
          {PROJECT_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>

        <Tabs value={statusFilter} onValueChange={(v) => setStatusFilter(v as 'all' | 'open' | 'resolved')}>
          <TabsList className="h-8">
            <TabsTrigger value="all" className="text-xs px-3 h-7">
              All
            </TabsTrigger>
            <TabsTrigger value="open" className="text-xs px-3 h-7">
              Open ({openCount})
            </TabsTrigger>
            <TabsTrigger value="resolved" className="text-xs px-3 h-7">
              Resolved ({resolvedCount})
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto"
          onClick={loadItems}
          title="Refresh"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto min-h-0 p-6" data-scroll-container>
        {loading ? (
          <div className="max-w-4xl">
            <ListSkeleton count={6} />
          </div>
        ) : error ? (
          <ErrorState message={error} onRetry={loadItems} />
        ) : filteredItems.length === 0 ? (
          <EmptyState
            icon={<ClipboardList className="h-5 w-5" />}
            title="No action items found"
            description={
              statusFilter !== 'all'
                ? `No ${statusFilter} action items for the selected project.`
                : 'Action items from conversations will appear here.'
            }
          />
        ) : (
          <div className="max-w-4xl rounded-lg border border-border bg-card overflow-hidden">
            {filteredItems.map((item) => (
              <ActionItemRow key={item.id} item={item} onResolve={handleResolve} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
