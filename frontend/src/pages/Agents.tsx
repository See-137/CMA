import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Trash2, Bot } from 'lucide-react';
import { api } from '../api/client';
import { useApi } from '../hooks/useApi';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import LoadingSpinner from '../components/LoadingSpinner';
import { formatCurrency, formatNumber, envVariant } from '../utils/format';
import type { Agent } from '../types';

const Agents: React.FC = () => {
  const navigate = useNavigate();
  const { data: agents, loading, error, refetch } = useApi<Agent[]>('/api/v1/agents');
  const [search, setSearch] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    environment: 'dev',
    swarm: '',
    workflow: '',
  });

  const filtered = useMemo(() => {
    if (!agents) return [];
    const q = search.toLowerCase();
    return agents.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        (a.swarm && a.swarm.toLowerCase().includes(q)) ||
        (a.workflow && a.workflow.toLowerCase().includes(q))
    );
  }, [agents, search]);

  const envBadgeVariant = (env: string): 'info' | 'warning' | 'success' | 'default' => {
    const v = envVariant(env);
    if (v === 'dev') return 'info';
    if (v === 'staging') return 'warning';
    if (v === 'prod') return 'success';
    return 'default';
  };

  const columns: Column<Agent>[] = [
    {
      header: 'Name',
      accessor: 'name',
      render: (row) => (
        <span className="font-medium text-primary">{row.name}</span>
      ),
    },
    {
      header: 'Environment',
      accessor: 'environment',
      render: (row) => (
        <Badge text={row.environment} variant={envBadgeVariant(row.environment)} />
      ),
    },
    {
      header: 'Swarm',
      accessor: 'swarm',
      render: (row) => <span className="text-muted">{row.swarm || '—'}</span>,
    },
    {
      header: 'Workflow',
      accessor: 'workflow',
      render: (row) => <span className="text-muted">{row.workflow || '—'}</span>,
    },
    {
      header: 'Total Cost',
      accessor: 'total_cost',
      render: (row) => (
        <span className="numeral font-medium text-gold">{formatCurrency(row.total_cost)}</span>
      ),
    },
    {
      header: 'Requests',
      accessor: 'request_count',
      render: (row) => (
        <span className="numeral text-secondary">{formatNumber(row.request_count)}</span>
      ),
    },
    {
      header: 'Status',
      accessor: 'is_active',
      render: (row) => (
        <Badge
          text={row.is_active ? 'Active' : 'Inactive'}
          variant={row.is_active ? 'success' : 'default'}
        />
      ),
    },
    {
      header: '',
      accessor: 'id',
      render: (row) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            setDeleteError(null);
            setConfirmId(row.id);
          }}
          className="rounded p-1.5 text-muted transition-colors hover:bg-oxblood-500/10 hover:text-oxblood-600 dark:hover:text-oxblood-300"
          title="Dismiss agent"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      ),
    },
  ];

  const handleDelete = async () => {
    if (confirmId === null) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.delete(`/api/v1/agents/${confirmId}`);
      refetch();
      setConfirmId(null);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Failed to dismiss agent. Try again.');
    } finally {
      setDeleting(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    try {
      await api.post('/api/v1/agents', {
        name: formData.name,
        description: formData.description || undefined,
        environment: formData.environment,
        swarm: formData.swarm || undefined,
        workflow: formData.workflow || undefined,
      });
      setModalOpen(false);
      setFormData({ name: '', description: '', environment: 'dev', swarm: '', workflow: '' });
      refetch();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to enlist agent. Try again.');
    }
  };

  if (loading) return <LoadingSpinner text="Reviewing the roster..." size="lg" />;
  if (error)
    return (
      <div className="card-ledger mx-auto max-w-md p-8 text-center">
        <p className="font-display text-lg text-oxblood-600 dark:text-oxblood-300">{error}</p>
        <button onClick={refetch} className="btn-primary mt-4">
          Try Again
        </button>
      </div>
    );

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Counting House · Workforce</p>
          <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
            The Agents
          </h1>
          <p className="mt-1 text-sm text-muted">
            Every clerk on the payroll — costs, requests, and status at a glance.
          </p>
        </div>
        <button onClick={() => { setCreateError(null); setModalOpen(true); }} className="btn-primary gap-2">
          <Plus className="h-4 w-4" />
          Enlist Agent
        </button>
      </header>

      {/* Search */}
      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
        <input
          type="text"
          className="input pl-9"
          placeholder="Search by name, swarm, or workflow…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Delete error notice */}
      {deleteError && (
        <div className="rounded-lg border border-[color:var(--color-danger)] bg-oxblood-500/8 px-4 py-3 text-sm text-oxblood-600 dark:text-oxblood-300">
          {deleteError}
        </div>
      )}

      <DataTable<Agent>
        columns={columns}
        data={filtered}
        onRowClick={(row) => navigate(`/agents/${row.id}`)}
        emptyTitle="The ledger is bare"
        emptyDescription="No agents match your search, or none have been enlisted yet."
        getRowKey={(row) => row.id}
      />

      {/* Add Agent Modal */}
      <Modal isOpen={modalOpen} onClose={() => setModalOpen(false)} title="Enlist a New Agent">
        <form onSubmit={handleSubmit} className="space-y-4">
          {createError && (
            <div className="rounded-lg border border-[color:var(--color-danger)] bg-oxblood-500/8 px-4 py-3 text-sm text-oxblood-600 dark:text-oxblood-300">
              {createError}
            </div>
          )}
          <div>
            <label className="label">Name</label>
            <input
              type="text"
              className="input"
              required
              placeholder="e.g., billing-agent"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Description</label>
            <textarea
              className="input"
              rows={2}
              placeholder="What does this agent do?"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Environment</label>
            <select
              className="input"
              value={formData.environment}
              onChange={(e) => setFormData({ ...formData, environment: e.target.value })}
            >
              <option value="dev">Development</option>
              <option value="staging">Staging</option>
              <option value="prod">Production</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Swarm</label>
              <input
                type="text"
                className="input"
                placeholder="Optional"
                value={formData.swarm}
                onChange={(e) => setFormData({ ...formData, swarm: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Workflow</label>
              <input
                type="text"
                className="input"
                placeholder="Optional"
                value={formData.workflow}
                onChange={(e) => setFormData({ ...formData, workflow: e.target.value })}
              />
            </div>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={() => setModalOpen(false)} className="btn-secondary">
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              <Bot className="h-4 w-4" />
              Enlist Agent
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        isOpen={confirmId !== null}
        onClose={() => { setConfirmId(null); setDeleteError(null); }}
        onConfirm={handleDelete}
        title="Dismiss agent"
        description="This will deactivate the agent. Every farthing they've spent stays in the record."
        confirmLabel="Dismiss"
        isLoading={deleting}
      />
    </div>
  );
};

export default Agents;
