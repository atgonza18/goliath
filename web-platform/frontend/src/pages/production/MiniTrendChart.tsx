import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import type { DailyCount, ProjectTrend } from '../../types';

interface MiniTrendChartProps {
  daily: DailyCount[];
  trend: ProjectTrend['trend'];
}

/** Bar color based on trend direction */
function barFill(trend: ProjectTrend['trend']): string {
  switch (trend) {
    case 'up':
      return '#22c55e';
    case 'down':
      return '#ef4444';
    case 'flat':
      return 'var(--chart-3)';
    case 'none':
    default:
      return 'var(--theme-text-dim)';
  }
}

/** Format date string "YYYY-MM-DD" to "Mon" or "3/5" */
function formatDateLabel(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00');
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return days[d.getDay()];
}

/** Custom tooltip matching the brutal aesthetic */
function ChartTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const data = payload[0].payload as DailyCount;
  return (
    <div
      style={{
        background: 'var(--theme-bg-secondary)',
        border: '1px solid var(--theme-border)',
        padding: '6px 10px',
        fontSize: '11px',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <p style={{ color: 'var(--theme-text-muted)', fontSize: '9px', marginBottom: '2px' }}>
        {data.date}
      </p>
      <p style={{ color: 'var(--theme-text-primary)', fontWeight: 600 }}>
        {data.count} POD{data.count !== 1 ? 's' : ''}
      </p>
    </div>
  );
}

export function MiniTrendChart({ daily, trend }: MiniTrendChartProps) {
  const fill = barFill(trend);
  const hasData = daily.some((d) => d.count > 0);

  if (!hasData) return null;

  const chartData = daily.map((d) => ({
    ...d,
    label: formatDateLabel(d.date),
  }));

  return (
    <div className="w-full" style={{ height: 80 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
          <XAxis
            dataKey="label"
            tick={{
              fontSize: 9,
              fill: 'var(--theme-text-dim)',
              fontFamily: 'var(--font-sans)',
            }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis hide />
          <Tooltip content={<ChartTooltip />} cursor={false} />
          <Bar dataKey="count" radius={0} maxBarSize={24}>
            {chartData.map((entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={entry.count > 0 ? fill : 'var(--theme-border-subtle)'}
                fillOpacity={entry.count > 0 ? 0.85 : 0.3}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
