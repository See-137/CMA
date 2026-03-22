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
import { ArrowLeft } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import Badge from '../components/Badge';
import DataTable, { Column } from '../components/DataTable';
import LoadingSpinner from '../components/LoadingSpinner';
import type { AgentDetail as AgentDetailType } from '../types';

const formatCurrency = (v: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v);

const formatNumber = (v: number) => new Intl.NumberFormat('en-US').format(v);

const timeAgo = (dateStr: string) => {
  const now = Date.now();
  const date = new Date(dateStr).getTime();
  const diffMs = now - date;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return new Date(dateStr).toLocaleDateString();
};

const envVariant = (env: string) => {
  switch (env.toLowerCase()) {
    case 'dev':
    case 'development':
      return 'info' as const;
    case 'staging':
      return 'warning' as const;
    case 'prod':
    case 'production':
      return 'success' as const;
    default:
      return 'default' as const;
  }
};

const COLORS = ['#14B8A6', '#F59E0B', '#0D9488', '#D97706', '#0F766E', '#B45309', '#06b6d4'];

const AgentDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: agent, loading, error } = useApi<AgentDetailType>(`/api/v1/agents/${id}`);

  if (loading) return <LoadingSpinner text="Loading agent details..." />;
  if (error)
    return (
      <div className="text-center py-12">
        <p className="text-rose-600">{error}</p>
        <button onClick={() => navigate('/agents')} className="btn-secondary mt-4">
          Back to Agents
        </button>
      </div>
    );
  if (!agent) return null;

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
    const existing = eventsByDate.get(date) || { cost: 0, requests: 0 };
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
        <span className="text-slate-500">{timeAgo(row.timestamp)}</span>
      ),
    },
    {
      header: 'Model',
      accessor: 'model_name',
      render: (row) => <span className="font-medium">{row.model_name}</span>,
    },
    { header: 'Provider', accessor: 'provider_name' },
    {
      header: 'Tokens In',
      accessor: 'tokens_input',
      render: (row) => formatNumber(row.tokens_input),
    },
    {
      header: 'Tokens Out',
      accessor: 'tokens_output',
      render: (row) => formatNumber(row.tokens_output),
    },
    {
      header: 'Cost',
      accessor: 'cost',
      render: (row) => (
        <span className="font-medium">{formatCurrency(row.cost)}</span>
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
      {/* Back button */}
      <button
        onClick={() => navigate('/agents')}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-900 transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Agents
      </button>

      {/* Agent Info */}
      <div className="card p-6">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-slate-900">{agent.name}</h1>
              <Badge
                text={agent.is_active ? 'Active' : 'Inactive'}
                variant={agent.is_active ? 'success' : 'default'}
              />
              <Badge text={agent.environment} variant={envVariant(agent.environment)} />
            </div>
            {agent.description && (
              <p className="mt-2 text-sm text-slate-500">{agent.description}</p>
            )}
          </div>
          <div className="text-right">
            <p className="text-sm text-slate-500">Total Cost</p>
            <p className="text-2xl font-bold text-slate-900">{formatCurrency(agent.total_cost)}</p>
          </div>
        </div>
        <div className="mt-4 pt-4 border-t border-slate-200 flex flex-wrap gap-6 text-sm">
          <div>
            <span className="text-slate-500">Swarm:</span>{' '}
            <span className="font-medium text-slate-700">{agent.swarm || '-'}</span>
          </div>
          <div>
            <span className="text-slate-500">Workflow:</span>{' '}
            <span className="font-medium text-slate-700">{agent.workflow || '-'}</span>
          </div>
          <div>
            <span className="text-slate-500">Requests:</span>{' '}
            <span className="font-medium text-slate-700">{formatNumber(agent.request_count)}</span>
          </div>
          <div>
            <span className="text-slate-500">Created:</span>{' '}
            <span className="font-medium text-slate-700">
              {new Date(agent.created_at).toLocaleDateString()}
            </span>
          </div>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card p-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Cost by Model</h2>
          {modelChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={modelChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 11, fill: '#64748b' }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 12, fill: '#64748b' }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v}`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#fff',
                    border: '1px solid #e2e8f0',
                    borderRadius: '8px',
                    fontSize: '13px',
                  }}
                  formatter={(value: number) => formatCurrency(value)}
                />
                <Bar dataKey="cost" radius={[4, 4, 0, 0]} barSize={36}>
                  {modelChartData.map((_, idx) => (
                    <Cell key={idx} fill={COLORS[idx % COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-slate-500 text-center py-8">No model data</p>
          )}
        </div>

        <div className="card p-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Cost Over Time</h2>
          {costTimeseries.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={costTimeseries}>
                <defs>
                  <linearGradient id="colorCostAgent" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#14B8A6" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#14B8A6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 12, fill: '#64748b' }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 12, fill: '#64748b' }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v}`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#fff',
                    border: '1px solid #e2e8f0',
                    borderRadius: '8px',
                    fontSize: '13px',
                  }}
                  formatter={(value: number) => formatCurrency(value)}
                />
                <Area
                  type="monotone"
                  dataKey="cost"
                  stroke="#14B8A6"
                  strokeWidth={2}
                  fill="url(#colorCostAgent)"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-slate-500 text-center py-8">No cost data</p>
          )}
        </div>
      </div>

      {/* Recent Events */}
      <div>
        <h2 className="text-base font-semibold text-slate-900 mb-4">Recent Events</h2>
        <DataTable<RecentEvent>
          columns={eventColumns}
          data={agent.recent_events}
          emptyTitle="No events"
          emptyDescription="This agent hasn't logged any events yet."
        />
      </div>
    </div>
  );
};

export default AgentDetail;
