import React, { useState, useEffect, useCallback } from 'react';
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
import { DollarSign, Zap, Bot, Hash } from 'lucide-react';
import { api } from '../api/client';
import StatsCard from '../components/StatsCard';
import LoadingSpinner from '../components/LoadingSpinner';
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

const formatCurrency = (value: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);

const formatNumber = (value: number) => new Intl.NumberFormat('en-US').format(value);

const Dashboard: React.FC = () => {
  const [period, setPeriod] = useState<Period>('week');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesResponse | null>(null);
  const [topAgents, setTopAgents] = useState<TopAgentsResponse | null>(null);
  const [budgetStatus, setBudgetStatus] = useState<BudgetStatusResponse | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
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
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) return <LoadingSpinner text="Loading dashboard..." />;
  if (error)
    return (
      <div className="text-center py-12">
        <p className="text-rose-600">{error}</p>
        <button onClick={fetchData} className="btn-primary mt-4">
          Retry
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
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
          <p className="text-sm text-slate-500 mt-1">Monitor your LLM costs and usage</p>
        </div>
        <div className="flex items-center gap-1 bg-white border border-slate-200 rounded-lg p-1">
          {(Object.keys(periodLabels) as Period[]).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                period === p
                  ? 'bg-teal-600 text-white'
                  : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
              }`}
            >
              {periodLabels[p]}
            </button>
          ))}
        </div>
      </div>

      {/* Stats Cards */}
      {overview && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <StatsCard
            title="Total Cost"
            value={formatCurrency(overview.total_cost)}
            change={overview.cost_change_pct}
            icon={DollarSign}
            iconColor="text-teal-600"
            iconBg="bg-teal-50"
          />
          <StatsCard
            title="Total Requests"
            value={formatNumber(overview.total_requests)}
            change={overview.request_change_pct}
            icon={Zap}
            iconColor="text-amber-600"
            iconBg="bg-amber-50"
          />
          <StatsCard
            title="Active Agents"
            value={formatNumber(overview.active_agents)}
            icon={Bot}
            iconColor="text-emerald-600"
            iconBg="bg-emerald-50"
          />
          <StatsCard
            title="Total Tokens"
            value={formatNumber(overview.total_tokens)}
            icon={Hash}
            iconColor="text-rose-600"
            iconBg="bg-rose-50"
          />
        </div>
      )}

      {/* Cost & Requests Timeseries */}
      <div className="card p-6">
        <h2 className="text-base font-semibold text-slate-900 mb-4">Cost & Requests Over Time</h2>
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart data={tsData}>
            <defs>
              <linearGradient id="colorCost" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#14B8A6" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#14B8A6" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="colorRequests" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10b981" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 12, fill: '#64748b' }}
              axisLine={{ stroke: '#e2e8f0' }}
              tickLine={false}
            />
            <YAxis
              yAxisId="cost"
              tick={{ fontSize: 12, fill: '#64748b' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${v}`}
            />
            <YAxis
              yAxisId="requests"
              orientation="right"
              tick={{ fontSize: 12, fill: '#64748b' }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#fff',
                border: '1px solid #e2e8f0',
                borderRadius: '8px',
                fontSize: '13px',
              }}
              formatter={(value: number, name: string) =>
                name === 'cost' ? formatCurrency(value) : formatNumber(value)
              }
            />
            <Legend
              wrapperStyle={{ fontSize: '13px' }}
              formatter={(value) => (value === 'cost' ? 'Cost' : 'Requests')}
            />
            <Area
              yAxisId="cost"
              type="monotone"
              dataKey="cost"
              stroke="#14B8A6"
              strokeWidth={2}
              fill="url(#colorCost)"
            />
            <Area
              yAxisId="requests"
              type="monotone"
              dataKey="requests"
              stroke="#10b981"
              strokeWidth={2}
              fill="url(#colorRequests)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Bottom Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Top Agents */}
        <div className="card p-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Top Agents by Cost</h2>
          {agentData.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={agentData} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fontSize: 12, fill: '#64748b' }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v}`}
                />
                <YAxis
                  type="category"
                  dataKey="agent_name"
                  tick={{ fontSize: 12, fill: '#64748b' }}
                  axisLine={false}
                  tickLine={false}
                  width={100}
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
                <Bar dataKey="total_cost" fill="#14B8A6" radius={[0, 4, 4, 0]} barSize={20} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-slate-500 text-center py-8">No agent data available</p>
          )}
        </div>

        {/* Budget Status */}
        <div className="card p-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Budget Status</h2>
          {budgets.length > 0 ? (
            <div className="space-y-4">
              {budgets.map((b) => {
                const pct = Math.min(b.percentage, 100);
                const barColor =
                  pct > 90 ? 'bg-rose-500' : pct > 50 ? 'bg-amber-500' : 'bg-emerald-500';
                return (
                  <div key={b.budget_id}>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-sm font-medium text-slate-700">{b.name}</span>
                      <span className="text-xs text-slate-500">
                        {formatCurrency(b.spent)} / {formatCurrency(b.limit_amount)}
                      </span>
                    </div>
                    <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full ${barColor} rounded-full transition-all`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="flex items-center justify-between mt-1">
                      <span className="text-xs text-slate-400">{b.scope}</span>
                      <span
                        className={`text-xs font-medium ${
                          pct > 90
                            ? 'text-rose-600'
                            : pct > 50
                              ? 'text-amber-600'
                              : 'text-emerald-600'
                        }`}
                      >
                        {pct.toFixed(1)}%
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-slate-500 text-center py-8">No budgets configured</p>
          )}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
