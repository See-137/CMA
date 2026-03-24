import React, { useState, useCallback, useEffect } from 'react';
import { ChevronLeft, ChevronRight, Filter, Search, List } from 'lucide-react';
import { api } from '../api/client';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import LoadingSpinner from '../components/LoadingSpinner';
import type { PaginatedEvents, Event, SemanticSearchResponse, SemanticSearchResult } from '../types';

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
  const [searchMode, setSearchMode] = useState<'structured' | 'semantic'>('structured');
  const [data, setData] = useState<PaginatedEvents | null>(null);
  const [semanticData, setSemanticData] = useState<SemanticSearchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const perPage = 50;
  const [semanticQuery, setSemanticQuery] = useState('');

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

  const fetchSemanticResults = useCallback(async () => {
    if (!semanticQuery.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.post<SemanticSearchResponse>('/api/v1/search/semantic', {
        query: semanticQuery,
        top_k: 20,
        agent_name: filters.agent_name || undefined,
        status: filters.status || undefined,
        from_date: filters.from_date || undefined,
        to_date: filters.to_date || undefined,
      });
      setSemanticData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Semantic search failed');
    } finally {
      setLoading(false);
    }
  }, [semanticQuery, filters]);

  useEffect(() => {
    if (searchMode === 'structured') {
      fetchEvents();
    }
  }, [fetchEvents, searchMode]);

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

  const semanticColumns: Column<SemanticSearchResult>[] = [
    {
      header: 'Match',
      accessor: 'similarity_score' as keyof SemanticSearchResult,
      render: (row: SemanticSearchResult) => (
        <Badge
          text={`${row.similarity_score.toFixed(0)}%`}
          variant={row.similarity_score >= 80 ? 'success' : row.similarity_score >= 50 ? 'default' : 'danger'}
        />
      ),
    },
    {
      header: 'Agent',
      accessor: 'event' as keyof SemanticSearchResult,
      render: (row: SemanticSearchResult) => (
        <span className="font-medium text-slate-900">{row.event.agent_name}</span>
      ),
    },
    {
      header: 'Model',
      accessor: 'event' as keyof SemanticSearchResult,
      render: (row: SemanticSearchResult) => <span className="text-slate-700">{row.event.model_name}</span>,
    },
    {
      header: 'Cost',
      accessor: 'event' as keyof SemanticSearchResult,
      render: (row: SemanticSearchResult) => <span className="font-medium">{formatCurrency(row.event.cost)}</span>,
    },
    {
      header: 'Status',
      accessor: 'event' as keyof SemanticSearchResult,
      render: (row: SemanticSearchResult) => (
        <Badge
          text={row.event.status}
          variant={row.event.status === 'success' ? 'success' : 'danger'}
        />
      ),
    },
    {
      header: 'Time',
      accessor: 'event' as keyof SemanticSearchResult,
      render: (row: SemanticSearchResult) => (
        <span className="text-slate-500 text-xs">{timeAgo(row.event.timestamp)}</span>
      ),
    },
  ];

  const handleSemanticSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchSemanticResults();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Events</h1>
          <p className="text-sm text-slate-500 mt-1">View all LLM request events</p>
        </div>
        {/* Mode toggle */}
        <div className="flex items-center gap-1 bg-white border border-slate-200 rounded-lg p-1">
          <button
            onClick={() => setSearchMode('structured')}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              searchMode === 'structured'
                ? 'bg-teal-600 text-white'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <List className="h-3.5 w-3.5" />
            Structured
          </button>
          <button
            onClick={() => setSearchMode('semantic')}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              searchMode === 'semantic'
                ? 'bg-teal-600 text-white'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <Search className="h-3.5 w-3.5" />
            Semantic
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="card p-4">
        {searchMode === 'semantic' ? (
          <>
            <div className="flex items-center gap-2 mb-3">
              <Search className="h-4 w-4 text-teal-500" />
              <span className="text-sm font-medium text-slate-700">Semantic Search</span>
              {semanticData && (
                <span className="text-xs text-slate-400 ml-auto">
                  {semanticData.results.length} results &middot;
                  embed {semanticData.query_embedding_time_ms.toFixed(0)}ms &middot;
                  search {semanticData.search_time_ms.toFixed(0)}ms
                </span>
              )}
            </div>
            <form onSubmit={handleSemanticSearch} className="flex gap-3">
              <input
                type="text"
                className="input text-sm flex-1"
                placeholder='Try "failed requests from the summarizer" or "expensive GPT-4 calls"...'
                value={semanticQuery}
                onChange={(e) => setSemanticQuery(e.target.value)}
              />
              <button
                type="submit"
                disabled={!semanticQuery.trim() || loading}
                className="btn-primary px-4"
              >
                Search
              </button>
            </form>
            {/* Optional secondary filters */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
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
                <label className="block text-xs text-slate-500 mb-1">Status</label>
                <select
                  className="input text-xs"
                  value={filters.status}
                  onChange={(e) => handleFilterChange('status', e.target.value)}
                >
                  <option value="">All</option>
                  <option value="success">Success</option>
                  <option value="error">Error</option>
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
          </>
        ) : (
          <>
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
          </>
        )}
      </div>

      {loading ? (
        <LoadingSpinner text={searchMode === 'semantic' ? 'Searching...' : 'Loading events...'} />
      ) : error ? (
        <div className="text-center py-12">
          <p className="text-rose-600">{error}</p>
          <button
            onClick={searchMode === 'semantic' ? fetchSemanticResults : fetchEvents}
            className="btn-primary mt-4"
          >
            Retry
          </button>
        </div>
      ) : searchMode === 'semantic' ? (
        <>
          {semanticData ? (
            <DataTable<SemanticSearchResult>
              columns={semanticColumns}
              data={semanticData.results}
              emptyTitle="No matching events"
              emptyDescription="Try a different search query or adjust your filters."
            />
          ) : (
            <div className="text-center py-12">
              <Search className="h-8 w-8 text-slate-300 mx-auto mb-3" />
              <p className="text-sm text-slate-500">
                Enter a natural language query to search events by meaning
              </p>
            </div>
          )}
        </>
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
