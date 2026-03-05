import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Cell,
} from 'recharts';
import type { DailyCount } from '../../types';

interface PortfolioChartProps {
  daily: DailyCount[];
}

/** Format date string "YYYY-MM-DD" to "Mon 3/5" */
function formatDateLabel(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00');
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return `${days[d.getDay()]} ${d.getMonth() + 1}/${d.getDate()}`;
}

/** Custom tooltip matching the brutal aesthetic */
function ChartTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const data = payload[0].payload;
  return (
    <div
      style={{
        background: 'var(--theme-bg-secondary)',
        border: '2px solid var(--theme-border)',
        padding: '8px 12px',
        fontSize: '12px',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <p style={{ color: 'var(--theme-text-muted)', fontSize: '10px', marginBottom: '4px' }}>
        {data.fullDate}
      </p>
      <p style={{ color: 'var(--theme-text-primary)', fontWeight: 700, fontSize: '16px' }}>
        {data.count}
      </p>
      <p style={{ color: 'var(--theme-text-muted)', fontSize: '10px' }}>
        POD reports
      </p>
    </div>
  );
}

export function PortfolioChart({ daily }: PortfolioChartProps) {
  const hasData = daily.some((d) => d.count > 0);
  if (!hasData) return null;

  const chartData = daily.map((d) => ({
    ...d,
    label: formatDateLabel(d.date),
    fullDate: d.date,
  }));

  const maxCount = Math.max(...daily.map((d) => d.count), 1);

  return (
    <div className="w-full" style={{ height: 180 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="var(--theme-border-subtle)"
            vertical={false}
          />
          <XAxis
            dataKey="label"
            tick={{
              fontSize: 10,
              fill: 'var(--theme-text-muted)',
              fontFamily: 'var(--font-sans)',
            }}
            axisLine={{ stroke: 'var(--theme-border)' }}
            tickLine={false}
          />
          <YAxis
            tick={{
              fontSize: 10,
              fill: 'var(--theme-text-dim)',
              fontFamily: 'var(--font-sans)',
            }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
            domain={[0, Math.ceil(maxCount * 1.2)]}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: 'var(--theme-accent-dim)' }} />
          <Bar dataKey="count" radius={0} maxBarSize={48}>
            {chartData.map((entry, index) => {
              // Today (last bar) gets the accent color, others get chart-2
              const isToday = index === chartData.length - 1;
              return (
                <Cell
                  key={`cell-${index}`}
                  fill={
                    entry.count > 0
                      ? isToday
                        ? 'var(--chart-1)'
                        : 'var(--chart-2)'
                      : 'var(--theme-border-subtle)'
                  }
                  fillOpacity={entry.count > 0 ? 0.85 : 0.2}
                />
              );
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
