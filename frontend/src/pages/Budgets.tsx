import React, { useState } from 'react';
import { Plus, Pencil, Trash2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api, ApiError } from '../api/client';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import LoadingSpinner from '../components/LoadingSpinner';
import { formatCurrency } from '../utils/format';
import type { Budget } from '../types';

const Budgets: React.FC = () => {
  const { data: budgets, loading, error, refetch } = useApi<Budget[]>('/api/v1/budgets');
  const [modalOpen, setModalOpen] = useState(false);
  const [editBudget, setEditBudget] = useState<Budget | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    scope: 'global',
    scope_ref: '',
    period: 'daily',
    limit_amount: '',
    alert_thresholds: '50,80,95',
    control_action: 'alert',
  });

  const openCreate = () => {
    setEditBudget(null);
    setSaveError(null);
    setFormData({
      name: '',
      scope: 'global',
      scope_ref: '',
      period: 'daily',
      limit_amount: '',
      alert_thresholds: '50,80,95',
      control_action: 'alert',
    });
    setModalOpen(true);
  };

  const openEdit = (budget: Budget) => {
    setEditBudget(budget);
    setSaveError(null);
    setFormData({
      name: budget.name,
      scope: budget.scope,
      scope_ref: budget.scope_ref || '',
      period: budget.period,
      limit_amount: budget.limit_amount.toString(),
      alert_thresholds: budget.alert_thresholds.join(','),
      control_action: budget.control_action,
    });
    setModalOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveError(null);
    const payload = {
      name: formData.name,
      scope: formData.scope,
      scope_ref: formData.scope_ref || undefined,
      period: formData.period,
      limit_amount: parseFloat(formData.limit_amount),
      alert_thresholds: formData.alert_thresholds
        .split(',')
        .map((s) => parseInt(s.trim(), 10))
        .filter((n) => !isNaN(n)),
      control_action: formData.control_action,
    };
    try {
      if (editBudget) {
        await api.put(`/api/v1/budgets/${editBudget.id}`, payload);
      } else {
        await api.post('/api/v1/budgets', payload);
      }
      setModalOpen(false);
      refetch();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? (err.body as { detail?: string })?.detail ?? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to save budget';
      setSaveError(msg);
    }
  };

  const handleDelete = async () => {
    if (confirmId === null) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.delete(`/api/v1/budgets/${confirmId}`);
      refetch();
      setConfirmId(null);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? (err.body as { detail?: string })?.detail ?? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to delete budget';
      setDeleteError(msg);
    } finally {
      setDeleting(false);
    }
  };

  const columns: Column<Budget>[] = [
    {
      header: 'Name',
      accessor: 'name',
      render: (row) => (
        <span className="font-medium text-primary font-display">{row.name}</span>
      ),
    },
    {
      header: 'Scope',
      accessor: 'scope',
      render: (row) => <Badge text={row.scope} variant="info" />,
    },
    {
      header: 'Period',
      accessor: 'period',
      render: (row) => (
        <span className="capitalize text-secondary">{row.period}</span>
      ),
    },
    {
      header: 'Limit',
      accessor: 'limit_amount',
      render: (row) => (
        <span className="numeral text-primary">{formatCurrency(row.limit_amount)}</span>
      ),
    },
    {
      header: 'Current Spend',
      accessor: 'current_spend',
      render: (row) => (
        <span className="numeral text-secondary">{formatCurrency(row.current_spend)}</span>
      ),
    },
    {
      header: 'Used',
      accessor: 'percentage',
      render: (row) => {
        const pct = Math.min(row.percentage, 100);
        const tone = pct > 90 ? 'oxblood' : pct > 50 ? 'gold' : 'brand';
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
          <div className="flex items-center gap-3 min-w-[140px]">
            <div className="flex-1 h-2 bg-surface-2 rounded-full overflow-hidden">
              <div
                className={`h-full ${barColor} rounded-full transition-all`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className={`numeral text-xs font-semibold ${textColor} w-12 text-right`}>
              {pct.toFixed(1)}%
            </span>
          </div>
        );
      },
    },
    {
      header: 'Action',
      accessor: 'control_action',
      render: (row) => (
        <Badge
          text={row.control_action}
          variant={row.control_action === 'block' ? 'danger' : 'warning'}
        />
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
      accessor: '_actions',
      render: (row) => (
        <div className="flex items-center gap-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              openEdit(row);
            }}
            className="p-1.5 rounded text-muted hover:text-brand-600 hover:bg-brand-500/10 transition-colors"
            title="Edit budget"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setDeleteError(null);
              setConfirmId(row.id);
            }}
            className="p-1.5 rounded text-muted hover:text-oxblood-600 hover:bg-oxblood-500/10 transition-colors"
            title="Delete budget"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ),
    },
  ];

  if (loading) return <LoadingSpinner text="Counting the coffers..." />;
  if (error)
    return (
      <div className="card-ledger mx-auto max-w-md p-8 text-center">
        <p className="font-display text-lg text-oxblood-600 dark:text-oxblood-300">{error}</p>
        <button onClick={refetch} className="btn-primary mt-4">
          Try again
        </button>
      </div>
    );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Cost Controls</p>
          <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
            Budgets
          </h1>
          <p className="mt-1 text-sm text-muted">
            Every pound has its limit. Set them here before the agents spend you out of house and home.
          </p>
        </div>
        <button onClick={openCreate} className="btn-gold gap-2">
          <Plus className="h-4 w-4" />
          New Budget
        </button>
      </header>

      <DataTable<Budget>
        columns={columns}
        data={budgets ?? []}
        getRowKey={(row) => row.id}
        emptyTitle="No budgets yet"
        emptyDescription="A penny ungoverned is a penny lost. Create your first spending limit."
      />

      <Modal
        isOpen={modalOpen}
        onClose={() => { setModalOpen(false); setSaveError(null); }}
        title={editBudget ? 'Amend Budget' : 'Open New Budget'}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          {saveError && (
            <div className="rounded-lg border border-oxblood-600/30 bg-oxblood-500/10 px-4 py-3">
              <p className="text-sm text-oxblood-600 dark:text-oxblood-300">{saveError}</p>
            </div>
          )}
          <div>
            <label className="label">Budget Name</label>
            <input
              type="text"
              className="input"
              required
              placeholder="e.g., Daily Global Limit"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Scope</label>
              <select
                className="input"
                value={formData.scope}
                onChange={(e) => setFormData({ ...formData, scope: e.target.value })}
              >
                <option value="global">Global</option>
                <option value="agent">Agent</option>
                <option value="provider">Provider</option>
                <option value="model">Model</option>
              </select>
            </div>
            <div>
              <label className="label">Period</label>
              <select
                className="input"
                value={formData.period}
                onChange={(e) => setFormData({ ...formData, period: e.target.value })}
              >
                <option value="hourly">Hourly</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>
          </div>
          {formData.scope !== 'global' && (
            <div>
              <label className="label">Scope Reference</label>
              <input
                type="text"
                className="input"
                placeholder="e.g., agent name or model ID"
                value={formData.scope_ref}
                onChange={(e) => setFormData({ ...formData, scope_ref: e.target.value })}
              />
            </div>
          )}
          <div>
            <label className="label">Limit Amount ($)</label>
            <input
              type="number"
              className="input numeral"
              required
              min="0.01"
              step="0.01"
              placeholder="100.00"
              value={formData.limit_amount}
              onChange={(e) => setFormData({ ...formData, limit_amount: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Alert Thresholds (%)</label>
            <input
              type="text"
              className="input"
              placeholder="50,80,95"
              value={formData.alert_thresholds}
              onChange={(e) => setFormData({ ...formData, alert_thresholds: e.target.value })}
            />
            <p className="mt-1 text-xs text-muted">Comma-separated percentages — e.g. 50,80,95</p>
          </div>
          <div>
            <label className="label">Control Action</label>
            <select
              className="input"
              value={formData.control_action}
              onChange={(e) => setFormData({ ...formData, control_action: e.target.value })}
            >
              <option value="alert">Alert Only</option>
              <option value="block">Block Requests</option>
              <option value="throttle">Throttle</option>
            </select>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => { setModalOpen(false); setSaveError(null); }}
              className="btn-secondary"
            >
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              {editBudget ? 'Update Budget' : 'Create Budget'}
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        isOpen={confirmId !== null}
        onClose={() => { setConfirmId(null); setDeleteError(null); }}
        onConfirm={handleDelete}
        title="Strike this budget from the ledger?"
        description="This will permanently delete the budget. Alerts triggered by this budget will cease forthwith."
        confirmLabel="Delete budget"
        isLoading={deleting}
      />

      {/* Delete error surfaces outside the dialog since the dialog may close */}
      {deleteError && (
        <div className="rounded-lg border border-oxblood-600/30 bg-oxblood-500/10 px-4 py-3">
          <p className="text-sm text-oxblood-600 dark:text-oxblood-300">{deleteError}</p>
        </div>
      )}
    </div>
  );
};

export default Budgets;
