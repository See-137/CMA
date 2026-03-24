import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Trash2 } from 'lucide-react';
import { api } from '../api/client';
import { useApi } from '../hooks/useApi';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import LoadingSpinner from '../components/LoadingSpinner';
import type { Agent } from '../types';

const formatCurrency = (v: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v);

const envVariant = (env: string) => {
  switch (env.toLowerCase()) {
    case 'dev':
    case 'development':
      return 'info';
    case 'staging':
      return 'warning';
    case 'prod':
    case 'production':
      return 'success';
    default:
      return 'default';
  }
};

const Agents: React.FC = () => {
  const navigate = useNavigate();
  const { data: agents, loading, error, refetch } = useApi<Agent[]>('/api/v1/agents');
  const [search, setSearch] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
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

  const columns: Column<Agent>[] = [
    {
      header: 'Name',
      accessor: 'name',
      render: (row) => <span className="font-medium text-slate-900">{row.name}</span>,
    },
    {
      header: 'Environment',
      accessor: 'environment',
      render: (row) => <Badge text={row.environment} variant={envVariant(row.environment)} />,
    },
    {
      header: 'Swarm',
      accessor: 'swarm',
      render: (row) => <span className="text-slate-500">{row.swarm || '-'}</span>,
    },
    {
      header: 'Workflow',
      accessor: 'workflow',
      render: (row) => <span className="text-slate-500">{row.workflow || '-'}</span>,
    },
    {
      header: 'Total Cost',
      accessor: 'total_cost',
      render: (row) => <span className="font-medium">{formatCurrency(row.total_cost)}</span>,
    },
    {
      header: 'Requests',
      accessor: 'request_count',
      render: (row) => new Intl.NumberFormat('en-US').format(row.request_count),
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
          onClick={(e) => { e.stopPropagation(); setConfirmId(row.id); }}
          className="p-1.5 rounded text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors"
          title="Delete agent"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      ),
    },
  ];

  const handleDelete = async () => {
    if (confirmId === null) return;
    setDeleting(true);
    try {
      await api.delete(`/api/v1/agents/${confirmId}`);
      refetch();
    } catch (err) {
      console.error('Failed to delete agent:', err);
    } finally {
      setDeleting(false);
      setConfirmId(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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
      console.error('Failed to create agent:', err);
    }
  };

  if (loading) return <LoadingSpinner text="Loading agents..." />;
  if (error)
    return (
      <div className="text-center py-12">
        <p className="text-rose-600">{error}</p>
        <button onClick={refetch} className="btn-primary mt-4">
          Retry
        </button>
      </div>
    );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Agents</h1>
          <p className="text-sm text-slate-500 mt-1">Manage your LLM agents</p>
        </div>
        <button onClick={() => setModalOpen(true)} className="btn-primary gap-2">
          <Plus className="h-4 w-4" />
          Add Agent
        </button>
      </div>

      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
        <input
          type="text"
          className="input pl-9"
          placeholder="Search agents..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <DataTable<Agent>
        columns={columns}
        data={filtered}
        onRowClick={(row) => navigate(`/agents/${row.id}`)}
        emptyTitle="No agents found"
        emptyDescription="Create your first agent or adjust your search."
      />

      <Modal isOpen={modalOpen} onClose={() => setModalOpen(false)} title="Add Agent">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="label">Name</label>
            <input
              type="text"
              className="input"
              required
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Description</label>
            <textarea
              className="input"
              rows={2}
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
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={() => setModalOpen(false)} className="btn-secondary">
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              Create Agent
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        isOpen={confirmId !== null}
        onClose={() => setConfirmId(null)}
        onConfirm={handleDelete}
        title="Delete agent"
        description="This will deactivate the agent. Existing cost events will be preserved."
        confirmLabel="Delete agent"
        isLoading={deleting}
      />
    </div>
  );
};

export default Agents;
