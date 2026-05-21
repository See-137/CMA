import React, { useState, useCallback, useEffect, useRef } from 'react';
import { ChevronLeft, ChevronRight, Filter, Search, List } from 'lucide-react';
import { api } from '../api/client';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import LoadingSpinner from '../components/LoadingSpinner';
import { formatCurrency, formatNumber, timeAgo } from '../utils/format';
import type { PaginatedEvents, Event, SemanticSearchResponse, SemanticSearchResult } from '../types';

/** Stale-guard ref counter — increment before each fetch, ignore responses whose id != current. */
function useRequestId() {
  const ref = useRef(0);
  const next = () => { ref.current += 1; return ref.current; };
  const current = () => ref.current;
  return { next, current };
}

/** Debounce a value by `delay` ms. */
function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

const Events: React.FC = () => {
  const [searchMode, setSearchMode] = useState<'structured' | 'semantic'>('structured');
  const [data, setData] = useState<PaginatedEvents | null>(null);
  const [semanticData, setSemanticData] = useState<SemanticSearchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const perPage = 50;

  const [semanticQuery, setSemanticQuery] = useState('');

  // Raw filter state — text fields are debounced below before triggering fetches
  const [filters, setFilters] = useState({
    agent_name: '',
    provider: '',
    model: '',
    status: '',
    from_date: '',
    to_date: '',
  });

  // Debounce text inputs (~300 ms) so rapid keystrokes don't fire many requests
  const debouncedAgentName = useDebounce(filters.agent_name, 300);
  const debouncedProvider  = useDebounce(filters.provider,   300);
  const debouncedModel     = useDebounce(filters.model,      300);
  const debouncedSemQuery  = useDebounce(semanticQuery,      300);

  // Stale-guard counters — one per search mode to avoid cross-mode collisions
  const structuredReqId = useRequestId();
  const semanticReqId   = useRequestId();

  const fetchEvents = useCallback(async () => {
    const myId = structuredReqId.next();
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<PaginatedEvents>('/api/v1/events', {
        page,
        per_page: perPage,
        agent_name: debouncedAgentName || undefined,
        provider:   debouncedProvider  || undefined,
        model:      debouncedModel     || undefined,
        status:     filters.status     || undefined,
        from_date:  filters.from_date  || undefined,
        to_date:    filters.to_date    || undefined,
      });
      // Discard if a newer request has already been issued
      if (myId !== structuredReqId.current()) return;
      setData(result);
    } catch (err) {
      if (myId !== structuredReqId.current()) return;
      setError(err instanceof Error ? err.message : 'Failed to load events');
    } finally {
      if (myId === structuredReqId.current()) setLoading(false);
    }
  }, [page, debouncedAgentName, debouncedProvider, debouncedModel, filters.status, filters.from_date, filters.to_date]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchSemanticResults = useCallback(async () => {
    if (!debouncedSemQuery.trim()) return;
    const myId = semanticReqId.next();
    setLoading(true);
    setError(null);
    try {
      const result = await api.post<SemanticSearchResponse>('/api/v1/search/semantic', {
        query:      debouncedSemQuery,
        top_k:      20,
        agent_name: debouncedAgentName || undefined,
        status:     filters.status     || undefined,
        from_date:  filters.from_date  || undefined,
        to_date:    filters.to_date    || undefined,
      });
      if (myId !== semanticReqId.current()) return;
      setSemanticData(result);
    } catch (err) {
      if (myId !== semanticReqId.current()) return;
      setError(err instanceof Error ? err.message : 'Semantic search failed');
    } finally {
      if (myId === semanticReqId.current()) setLoading(false);
    }
  }, [debouncedSemQuery, debouncedAgentName, filters.status, filters.from_date, filters.to_date]); // eslint-disable-line react-hooks/exhaustive-deps

  // Structured mode: re-fetch whenever debounced deps change
  useEffect(() => {
    if (searchMode === 'structured') {
      fetchEvents();
    }
  }, [fetchEvents, searchMode]);

  // Semantic mode: auto-search when debounced query or filters change
  useEffect(() => {
    if (searchMode === 'semantic' && debouncedSemQuery.trim()) {
      fetchSemanticResults();
    }
  }, [fetchSemanticResults, searchMode, debouncedSemQuery]);

  const totalPages = data ? Math.ceil(data.total / data.per_page) : 0;

  const handleFilterChange = (key: string, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  };

  const columns: Column<Event>[] = [
    {
      header: 'Timestamp',
      accessor: 'timestamp',
      render: (row) => (
        <span className="numeral text-xs text-muted">{timeAgo(row.timestamp)}</span>
      ),
    },
    {
      header: 'Agent',
      accessor: 'agent_name',
      render: (row) => (
        <span className="font-medium text-primary font-display">{row.agent_name}</span>
      ),
    },
    {
      header: 'Model',
      accessor: 'model_name',
      render: (row) => <span className="text-secondary">{row.model_name}</span>,
    },
    {
      header: 'Provider',
      accessor: 'provider_name',
      render: (row) => <span className="text-secondary">{row.provider_name}</span>,
    },
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
        <span className="numeral font-semibold text-gold">{formatCurrency(row.cost)}</span>
      ),
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
        <span className="font-medium text-primary font-display">{row.event.agent_name}</span>
      ),
    },
    {
      header: 'Model',
      accessor: 'event' as keyof SemanticSearchResult,
      render: (row: SemanticSearchResult) => (
        <span className="text-secondary">{row.event.model_name}</span>
      ),
    },
    {
      header: 'Cost',
      accessor: 'event' as keyof SemanticSearchResult,
      render: (row: SemanticSearchResult) => (
        <span className="numeral font-semibold text-gold">{formatCurrency(row.event.cost)}</span>
      ),
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
        <span className="numeral text-xs text-muted">{timeAgo(row.event.timestamp)}</span>
      ),
    },
  ];

  const handleSemanticSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchSemanticResults();
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Dispatch Log</p>
          <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
            Events
          </h1>
          <p className="mt-1 text-sm text-muted">
            Every token dispatched by every agent, in the order Providence delivered them.
          </p>
        </div>
        {/* Mode toggle */}
        <div className="flex items-center gap-1 rounded-lg border border-token bg-surface p-1">
          <button
            onClick={() => setSearchMode('structured')}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              searchMode === 'structured'
                ? 'bg-brand-600 text-white shadow-subtle'
                : 'text-secondary hover:bg-surface-2 hover:text-primary'
            }`}
          >
            <List className="h-3.5 w-3.5" />
            Structured
          </button>
          <button
            onClick={() => setSearchMode('semantic')}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              searchMode === 'semantic'
                ? 'bg-brand-600 text-white shadow-subtle'
                : 'text-secondary hover:bg-surface-2 hover:text-primary'
            }`}
          >
            <Search className="h-3.5 w-3.5" />
            Semantic
          </button>
        </div>
      </header>

      {/* Filter Bar */}
      <div className="card p-4">
        {searchMode === 'semantic' ? (
          <>
            <div className="flex items-center gap-2 mb-3">
              <Search className="h-4 w-4 text-brand-500" />
              <span className="text-sm font-medium text-primary">Semantic Search</span>
              {semanticData && (
                <span className="numeral text-xs text-muted ml-auto">
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
                <label className="label">Agent</label>
                <input
                  type="text"
                  className="input text-xs"
                  placeholder="All agents"
                  value={filters.agent_name}
                  onChange={(e) => handleFilterChange('agent_name', e.target.value)}
                />
              </div>
              <div>
                <label className="label">Status</label>
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
                <label className="label">From</label>
                <input
                  type="date"
                  className="input text-xs"
                  value={filters.from_date}
                  onChange={(e) => handleFilterChange('from_date', e.target.value)}
                />
              </div>
              <div>
                <label className="label">To</label>
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
              <Filter className="h-4 w-4 text-muted" />
              <span className="text-sm font-medium text-primary">Filters</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              <div>
                <label className="label">Agent</label>
                <input
                  type="text"
                  className="input text-xs"
                  placeholder="All agents"
                  value={filters.agent_name}
                  onChange={(e) => handleFilterChange('agent_name', e.target.value)}
                />
              </div>
              <div>
                <label className="label">Provider</label>
                <input
                  type="text"
                  className="input text-xs"
                  placeholder="All providers"
                  value={filters.provider}
                  onChange={(e) => handleFilterChange('provider', e.target.value)}
                />
              </div>
              <div>
                <label className="label">Model</label>
                <input
                  type="text"
                  className="input text-xs"
                  placeholder="All models"
                  value={filters.model}
                  onChange={(e) => handleFilterChange('model', e.target.value)}
                />
              </div>
              <div>
                <label className="label">Status</label>
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
                <label className="label">From</label>
                <input
                  type="date"
                  className="input text-xs"
                  value={filters.from_date}
                  onChange={(e) => handleFilterChange('from_date', e.target.value)}
                />
              </div>
              <div>
                <label className="label">To</label>
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
        <LoadingSpinner
          text={searchMode === 'semantic' ? 'Consulting the index...' : 'Turning the pages...'}
        />
      ) : error ? (
        <div className="card-ledger mx-auto max-w-md p-8 text-center">
          <p className="font-display text-lg text-oxblood-600 dark:text-oxblood-300">{error}</p>
          <button
            onClick={searchMode === 'semantic' ? fetchSemanticResults : fetchEvents}
            className="btn-primary mt-4"
          >
            Try again
          </button>
        </div>
      ) : searchMode === 'semantic' ? (
        <>
          {semanticData ? (
            <DataTable<SemanticSearchResult>
              columns={semanticColumns}
              data={semanticData.results}
              getRowKey={(r) => r.event.id}
              emptyTitle="Nothing in the index"
              emptyDescription="Try a different query or adjust your filters."
            />
          ) : (
            <div className="card p-12 text-center">
              <Search className="h-8 w-8 text-muted mx-auto mb-3" />
              <p className="font-display text-base text-secondary">
                Pose a question in plain English.
              </p>
              <p className="mt-1 text-sm text-muted">
                Scrooge will search by meaning, not just matching text.
              </p>
            </div>
          )}
        </>
      ) : (
        <>
          <DataTable<Event>
            columns={columns}
            data={data?.items ?? []}
            getRowKey={(row) => row.id}
            emptyTitle="Not a single entry"
            emptyDescription="No events match your current filters. Adjust the criteria and try again."
          />

          {/* Pagination */}
          {data && data.total > 0 && (
            <div className="flex items-center justify-between">
              <p className="numeral text-sm text-muted">
                Showing{' '}
                <span className="text-secondary">{(page - 1) * perPage + 1}–{Math.min(page * perPage, data.total)}</span>
                {' '}of{' '}
                <span className="text-secondary">{formatNumber(data.total)}</span> entries
              </p>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="btn-secondary px-2 py-1.5"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <span className="numeral text-sm font-medium text-secondary px-2">
                  {page} / {totalPages}
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
