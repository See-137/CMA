import { useState, useEffect, useCallback, useRef } from 'react';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { Coins, Zap, Bot, Hash, MessageSquare } from 'lucide-react';
import ChatDrawer from '../components/ChatDrawer';
import { api } from '../api/client';
import StatsCard from '../components/StatsCard';
import LoadingSpinner from '../components/LoadingSpinner';
import { useTheme } from '../contexts/ThemeContext';
import { getChartTheme, tooltipStyle } from '../utils/chartTheme';
import { formatCurrency, formatNumber } from '../utils/format';
import type {
  DashboardOverview,
  TimeseriesResponse,
  TopAgentsResponse,
  BudgetStatusResponse,
} from '../types';

type Period = 'today' | 'week' | 'month';

const periodLabels: Record<Period, string> = {
  today: 'Today',
  week: 'This Week',
  month: 'This Month',
};

const granularityMap: Record<Period, string> = {
  today: 'hour',
  week: 'day',
  month: 'day',
};

const POLL_MS = 30_000;

export default function Dashboard() {
  const { theme } = useTheme();
  const chart = getChartTheme(theme === 'dark');

  const [chatOpen, setChatOpen] = useState(false);
  const [period, setPeriod] = useState<Period>('week');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesResponse | null>(null);
  const [topAgents, setTopAgents] = useState<TopAgentsResponse | null>(null);
  const [budgetStatus, setBudgetStatus] = useState<BudgetStatusResponse | null>(null);
  const firstLoad = useRef(true);

  const fetchData = useCallback(async () => {
    if (firstLoad.current) setLoading(true);
    setError(null);
    try {
      const [ov, ts, ta, bs] = await Promise.all([
        api.get<DashboardOverview>('/api/v1/dashboard/overview', { period }),
        api.get<TimeseriesResponse>('/api/v1/dashboard/timeseries', {
          period,
          granularity: granularityMap[period],
        }),
        api.get<TopAgentsResponse>('/api/v1/dashboard/top-agents', { period, limit: 10 }),
        api.get<BudgetStatusResponse>('/api/v1/dashboard/budget-status'),
      ]);
      setOverview(ov);
      setTimeseries(ts);
      setTopAgents(ta);
      setBudgetStatus(bs);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the ledger');
    } finally {
      setLoading(false);
      firstLoad.current = false;
    }
  }, [period]);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, POLL_MS);
    return () => clearInterval(id);
  }, [fetchData]);

  if (loading) return <LoadingSpinner text="Tallying the books..." size="lg" />;
  if (error)
    return (
      <div className="card-ledger mx-auto max-w-md p-8 text-center">
        <p className="font-display text-lg text-oxblood-600 dark:text-oxblood-300">{error}</p>
        <button onClick={fetchData} className="btn-primary mt-4">
          Try the count again
        </button>
      </div>
    );

  const tsData = (timeseries?.data ?? []).map((d) => ({
    ...d,
    label:
      period === 'today'
        ? new Date(d.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : new Date(d.timestamp).toLocaleDateString([], { month: 'short', day: 'numeric' }),
  }));

  const agentData = (topAgents?.data ?? []).slice(0, 8);
  const budgets = budgetStatus?.data ?? [];

  return (
    <div className="relative space-y-6">
      <button
        onClick={() => setChatOpen(true)}
        className="fixed bottom-6 right-6 z-30 flex h-14 w-14 items-center justify-center rounded-full bg-gold-leaf text-emerald-950 shadow-glow-gold transition-all hover:scale-105"
        title="Ask Scrooge"
        aria-label="Ask Scrooge"
      >
        <MessageSquare className="h-6 w-6" />
      </button>
      <ChatDrawer open={chatOpen} onClose={() => setChatOpen(false)} />

      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Counting House · {periodLabels[period]}</p>
          <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
            The Ledger
          </h1>
          <p className="mt-1 text-sm text-muted">
            Every farthing your agents spend, accounted for.
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-token bg-surface p-1">
          {(Object.keys(periodLabels) as Period[]).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                period === p
                  ? 'bg-brand-600 text-white shadow-subtle'
                  : 'text-secondary hover:bg-surface-2 hover:text-primary'
              }`}
            >
              {periodLabels[p]}
            </button>
          ))}
        </div>
      </header>

      {/* Stats */}
      {overview && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatsCard
            title="Total Cost"
            value={formatCurrency(overview.total_cost)}
            change={overview.cost_change_pct}
            icon={Coins}
            delay={0}
            invertChange
          />
          <StatsCard
            title="Total Requests"
            value={formatNumber(overview.total_requests)}
            change={overview.request_change_pct}
            icon={Zap}
            delay={60}
          />
          <StatsCard
            title="Active Agents"
            value={formatNumber(overview.active_agents)}
            icon={Bot}
            delay={120}
          />
          <StatsCard
            title="Total Tokens"
            value={formatNumber(overview.total_tokens)}
            icon={Hash}
            delay={180}
          />
        </div>
      )}

      {/* Timeseries */}
      <section className="card-ledger p-6">
        <h2 className="mb-4 font-display text-lg font-semibold text-primary">
          Expenditure &amp; Activity
        </h2>
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart data={tsData}>
            <defs>
              <linearGradient id="colorCost" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={chart.gold} stopOpacity={0.22} />
                <stop offset="95%" stopColor={chart.gold} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="colorRequests" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={chart.forest} stopOpacity={0.18} />
                <stop offset="95%" stopColor={chart.forest} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 12, fill: chart.axis }}
              axisLine={{ stroke: chart.grid }}
              tickLine={false}
            />
            <YAxis
              yAxisId="cost"
              tick={{ fontSize: 12, fill: chart.axis }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${v}`}
            />
            <YAxis
              yAxisId="requests"
              orientation="right"
              tick={{ fontSize: 12, fill: chart.axis }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={tooltipStyle(chart)}
              formatter={(value: number, name: string) =>
                name === 'cost' ? formatCurrency(value) : formatNumber(value)
              }
            />
            <Legend
              wrapperStyle={{ fontSize: '13px', color: chart.axis }}
              formatter={(value) => (value === 'cost' ? 'Cost' : 'Requests')}
            />
            <Area
              yAxisId="cost"
              type="monotone"
              dataKey="cost"
              stroke={chart.gold}
              strokeWidth={2.5}
              fill="url(#colorCost)"
            />
            <Area
              yAxisId="requests"
              type="monotone"
              dataKey="requests"
              stroke={chart.forest}
              strokeWidth={2}
              fill="url(#colorRequests)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </section>

      {/* Bottom row */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="card p-6">
          <h2 className="mb-4 font-display text-lg font-semibold text-primary">
            Biggest Spenders
          </h2>
          {agentData.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={agentData} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fontSize: 12, fill: chart.axis }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v}`}
                />
                <YAxis
                  type="category"
                  dataKey="agent_name"
                  tick={{ fontSize: 12, fill: chart.axis }}
                  axisLine={false}
                  tickLine={false}
                  width={100}
                />
                <Tooltip
                  contentStyle={tooltipStyle(chart)}
                  cursor={{ fill: chart.grid, opacity: 0.25 }}
                  formatter={(value: number) => formatCurrency(value)}
                />
                <Bar dataKey="total_cost" fill={chart.gold} radius={[0, 4, 4, 0]} barSize={20} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-8 text-center text-sm text-muted">No agent has spent a penny yet.</p>
          )}
        </section>

        <section className="card p-6">
          <h2 className="mb-4 font-display text-lg font-semibold text-primary">Coffers</h2>
          {budgets.length > 0 ? (
            <div className="space-y-4">
              {budgets.map((b) => {
                const pct = Math.min(b.percentage, 100);
                const tone =
                  pct > 90 ? 'oxblood' : pct > 50 ? 'gold' : 'brand';
                const barColor =
                  tone === 'oxblood'
                    ? 'bg-oxblood-500'
                    : tone === 'gold'
                      ? 'bg-gold-400'
                      : 'bg-brand-500';
                const textColor =
                  tone === 'oxblood'
                    ? 'text-oxblood-600 dark:text-oxblood-300'
                    : tone === 'gold'
                      ? 'text-gold-700 dark:text-gold-300'
                      : 'text-brand-600 dark:text-brand-300';
                return (
                  <div key={b.budget_id}>
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="text-sm font-medium text-primary">{b.name}</span>
                      <span className="numeral text-xs text-muted">
                        {formatCurrency(b.spent)} / {formatCurrency(b.limit_amount)}
                      </span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-surface-2">
                      <div
                        className={`h-full rounded-full transition-all ${barColor}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="text-xs capitalize text-muted">{b.scope}</span>
                      <span className={`numeral text-xs font-semibold ${textColor}`}>
                        {pct.toFixed(1)}%
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-muted">No coffers under watch.</p>
          )}
        </section>
      </div>
    </div>
  );
}
