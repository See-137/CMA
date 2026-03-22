import React, { useState, useCallback, useEffect } from 'react';
import { ChevronLeft, ChevronRight, Filter } from 'lucide-react';
import { api } from '../api/client';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import LoadingSpinner from '../components/LoadingSpinner';
import type { PaginatedEvents, Event } from '../types';

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

const Events: React.FC = () => {
  const [data, setData] = useState<PaginatedEvents | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const perPage = 50;

  const [filters, setFilters] = useState({
    agent_name: '',
    provider: '',
    model: '',
    status: '',
    from_date: '',
    to_date: '',
  });

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<PaginatedEvents>('/api/v1/events', {
        page,
        per_page: perPage,
        agent_name: filters.agent_name || undefined,
        provider: filters.provider || undefined,
        model: filters.model || undefined,
        status: filters.status || undefined,
        from_date: filters.from_date || undefined,
        to_date: filters.to_date || undefined,
      });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load events');
    } finally {
      setLoading(false);
    }
  }, [page, filters]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  const totalPages = data ? Math.ceil(data.total / data.per_page) : 0;

  const handleFilterChange = (key: string, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  };

  const columns: Column<Event>[] = [
    {
      header: 'Timestamp',
      accessor: 'timestamp',
      render: (row) => <span className="text-slate-500 text-xs">{timeAgo(row.timestamp)}</span>,
    },
    {
      header: 'Agent',
      accessor: 'agent_name',
      render: (row) => <span className="font-medium text-slate-900">{row.agent_name}</span>,
    },
    {
      header: 'Model',
      accessor: 'model_name',
      render: (row) => <span className="text-slate-700">{row.model_name}</span>,
    },
    {
      header: 'Provider',
      accessor: 'provider_name',
    },
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
      render: (row) => <span className="font-medium">{formatCurrency(row.cost)}</span>,
    },
    {
      header: 'Status',
      accessor: 'status',
      render: (row) => (
        <Badge
          text={row.status}
          variant={row.status === 'success' ? 'success' : row.status === 'error' ? 'danger' : 'default'}
        />
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Events</h1>
        <p className="text-sm text-slate-500 mt-1">View all LLM request events</p>
      </div>

      {/* Filter Bar */}
      <div className="card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Filter className="h-4 w-4 text-slate-400" />
          <span className="text-sm font-medium text-slate-700">Filters</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Agent</label>
            <input
              type="text"
              className="input text-xs"
              placeholder="All agents"
              value={filters.agent_name}
              onChange={(e) => handleFilterChange('agent_name', e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Provider</label>
            <input
              type="text"
              className="input text-xs"
              placeholder="All providers"
              value={filters.provider}
              onChange={(e) => handleFilterChange('provider', e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Model</label>
            <input
              type="text"
              className="input text-xs"
              placeholder="All models"
              value={filters.model}
              onChange={(e) => handleFilterChange('model', e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Status</label>
            <select
              className="input text-xs"
              value={filters.status}
              onChange={(e) => handleFilterChange('status', e.target.value)}
            >
              <option value="">All</option>
              <option value="success">Success</option>
              <option value="error">Error</option>
              <option value="timeout">Timeout</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">From</label>
            <input
              type="date"
              className="input text-xs"
              value={filters.from_date}
              onChange={(e) => handleFilterChange('from_date', e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">To</label>
            <input
              type="date"
              className="input text-xs"
              value={filters.to_date}
              onChange={(e) => handleFilterChange('to_date', e.target.value)}
            />
          </div>
        </div>
      </div>

      {loading ? (
        <LoadingSpinner text="Loading events..." />
      ) : error ? (
        <div className="text-center py-12">
          <p className="text-rose-600">{error}</p>
          <button onClick={fetchEvents} className="btn-primary mt-4">
            Retry
          </button>
        </div>
      ) : (
        <>
          <DataTable<Event>
            columns={columns}
            data={data?.items ?? []}
            emptyTitle="No events found"
            emptyDescription="No events match your current filters. Try adjusting the filter criteria."
          />

          {/* Pagination */}
          {data && data.total > 0 && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-slate-500">
                Showing {(page - 1) * perPage + 1}-
                {Math.min(page * perPage, data.total)} of{' '}
                {formatNumber(data.total)} events
              </p>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="btn-secondary px-2 py-1.5"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <span className="text-sm font-medium text-slate-700 px-2">
                  Page {page} of {totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  className="btn-secondary px-2 py-1.5"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default Events;
