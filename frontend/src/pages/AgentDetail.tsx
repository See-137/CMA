import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { ArrowLeft, Coins, Zap, Hash } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import Badge from '../components/Badge';
import DataTable, { Column } from '../components/DataTable';
import LoadingSpinner from '../components/LoadingSpinner';
import StatsCard from '../components/StatsCard';
import { useTheme } from '../contexts/ThemeContext';
import { getChartTheme, tooltipStyle } from '../utils/chartTheme';
import { formatCurrency, formatNumber, timeAgo, envVariant } from '../utils/format';
import type { AgentDetail as AgentDetailType } from '../types';

const AgentDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { theme } = useTheme();
  const chart = getChartTheme(theme === 'dark');

  const { data: agent, loading, error } = useApi<AgentDetailType>(`/api/v1/agents/${id}`);

  if (loading) return <LoadingSpinner text="Pulling the dossier…" size="lg" />;
  if (error)
    return (
      <div className="card-ledger mx-auto max-w-md p-8 text-center">
        <p className="font-display text-lg text-oxblood-600 dark:text-oxblood-300">{error}</p>
        <button onClick={() => navigate('/agents')} className="btn-secondary mt-4">
          Back to Agents
        </button>
      </div>
    );
  if (!agent) return null;

  const envBadgeVariant = (env: string): 'info' | 'warning' | 'success' | 'default' => {
    const v = envVariant(env);
    if (v === 'dev') return 'info';
    if (v === 'staging') return 'warning';
    if (v === 'prod') return 'success';
    return 'default';
  };

  // Bar chart colours drawn from the chart theme — rotate through gold / forest / oxblood
  const BAR_COLORS = [chart.gold, chart.forest, chart.oxblood, chart.gold, chart.forest, chart.oxblood, chart.gold];

  const modelChartData = agent.models_used.map((m) => ({
    name: m.model_name,
    cost: m.total_cost,
    requests: m.request_count,
  }));

  // Build a simple timeseries from recent events (group by date)
  const eventsByDate = new Map<string, { cost: number; requests: number }>();
  agent.recent_events.forEach((ev) => {
    const date = new Date(ev.timestamp).toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
    });
    const existing = eventsByDate.get(date) ?? { cost: 0, requests: 0 };
    existing.cost += ev.cost;
    existing.requests += 1;
    eventsByDate.set(date, existing);
  });
  const costTimeseries = Array.from(eventsByDate.entries()).map(([date, data]) => ({
    date,
    ...data,
  }));

  type RecentEvent = AgentDetailType['recent_events'][number];

  const eventColumns: Column<RecentEvent>[] = [
    {
      header: 'Time',
      accessor: 'timestamp',
      render: (row) => (
        <span className="numeral text-muted">{timeAgo(row.timestamp)}</span>
      ),
    },
    {
      header: 'Model',
      accessor: 'model_name',
      render: (row) => <span className="font-medium text-primary">{row.model_name}</span>,
    },
    { header: 'Provider', accessor: 'provider_name' },
    {
      header: 'Tokens In',
      accessor: 'tokens_input',
      render: (row) => (
        <span className="numeral text-secondary">{formatNumber(row.tokens_input)}</span>
      ),
    },
    {
      header: 'Tokens Out',
      accessor: 'tokens_output',
      render: (row) => (
        <span className="numeral text-secondary">{formatNumber(row.tokens_output)}</span>
      ),
    },
    {
      header: 'Cost',
      accessor: 'cost',
      render: (row) => (
        <span className="numeral font-medium text-gold">{formatCurrency(row.cost)}</span>
      ),
    },
    {
      header: 'Status',
      accessor: 'status',
      render: (row) => (
        <Badge
          text={row.status}
          variant={row.status === 'success' ? 'success' : 'danger'}
        />
      ),
    },
  ];

  return (
    <div className="space-y-6">
      {/* Back */}
      <button
        onClick={() => navigate('/agents')}
        className="inline-flex items-center gap-1.5 text-sm text-muted transition-colors hover:text-primary"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Agents
      </button>

      {/* Agent Identity Card */}
      <div className="card-ledger p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="eyebrow">Agent Dossier</p>
            <div className="mt-1 flex flex-wrap items-center gap-3">
              <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
                {agent.name}
              </h1>
              <Badge
                text={agent.is_active ? 'Active' : 'Inactive'}
                variant={agent.is_active ? 'success' : 'default'}
              />
              <Badge text={agent.environment} variant={envBadgeVariant(agent.environment)} />
            </div>
            {agent.description && (
              <p className="mt-2 text-sm text-muted">{agent.description}</p>
            )}
          </div>
          <div className="text-right">
            <p className="eyebrow">Total Expenditure</p>
            <p className="numeral mt-1 text-3xl font-semibold text-gold">
              {formatCurrency(agent.total_cost)}
            </p>
          </div>
        </div>

        <div className="hairline my-4" />

        <div className="flex flex-wrap gap-6 text-sm">
          <div>
            <span className="text-muted">Swarm:</span>{' '}
            <span className="font-medium text-secondary">{agent.swarm || '—'}</span>
          </div>
          <div>
            <span className="text-muted">Workflow:</span>{' '}
            <span className="font-medium text-secondary">{agent.workflow || '—'}</span>
          </div>
          <div>
            <span className="text-muted">Requests:</span>{' '}
            <span className="numeral font-medium text-secondary">
              {formatNumber(agent.request_count)}
            </span>
          </div>
          <div>
            <span className="text-muted">Enlisted:</span>{' '}
            <span className="numeral font-medium text-secondary">
              {new Date(agent.created_at).toLocaleDateString()}
            </span>
          </div>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatsCard
          title="Total Cost"
          value={formatCurrency(agent.total_cost)}
          icon={Coins}
          delay={0}
          invertChange
        />
        <StatsCard
          title="Total Requests"
          value={formatNumber(agent.request_count)}
          icon={Zap}
          delay={60}
        />
        <StatsCard
          title="Total Tokens"
          value={formatNumber(
            agent.recent_events.reduce(
              (acc, ev) => acc + (ev.tokens_input ?? 0) + (ev.tokens_output ?? 0),
              0
            )
          )}
          icon={Hash}
          delay={120}
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="card p-6">
          <h2 className="mb-4 font-display text-lg font-semibold text-primary">Cost by Model</h2>
          {modelChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={modelChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} />
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 11, fill: chart.axis }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 12, fill: chart.axis }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v}`}
                />
                <Tooltip
                  contentStyle={tooltipStyle(chart)}
                  formatter={(value: number) => formatCurrency(value)}
                />
                <Bar dataKey="cost" radius={[4, 4, 0, 0]} barSize={36}>
                  {modelChartData.map((_, idx) => (
                    <Cell key={idx} fill={BAR_COLORS[idx % BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-8 text-center text-sm text-muted">
              No model has billed this agent yet.
            </p>
          )}
        </section>

        <section className="card p-6">
          <h2 className="mb-4 font-display text-lg font-semibold text-primary">
            Cost Over Time
          </h2>
          {costTimeseries.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={costTimeseries}>
                <defs>
                  <linearGradient id="colorCostAgent" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={chart.gold} stopOpacity={0.22} />
                    <stop offset="95%" stopColor={chart.gold} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 12, fill: chart.axis }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 12, fill: chart.axis }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v}`}
                />
                <Tooltip
                  contentStyle={tooltipStyle(chart)}
                  formatter={(value: number) => formatCurrency(value)}
                />
                <Area
                  type="monotone"
                  dataKey="cost"
                  stroke={chart.gold}
                  strokeWidth={2.5}
                  fill="url(#colorCostAgent)"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-8 text-center text-sm text-muted">
              No expenditure on record yet.
            </p>
          )}
        </section>
      </div>

      {/* Recent Events */}
      <section>
        <h2 className="mb-4 font-display text-lg font-semibold text-primary">Recent Events</h2>
        <DataTable<RecentEvent>
          columns={eventColumns}
          data={agent.recent_events}
          emptyTitle="The ledger is empty"
          emptyDescription="This agent hasn't logged a single transaction yet."
          getRowKey={(row) => row.id ?? row.timestamp}
        />
      </section>
    </div>
  );
};

export default AgentDetail;
