import React, { useState } from 'react';
import { Plus, Pencil, Trash2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import LoadingSpinner from '../components/LoadingSpinner';
import type { Budget } from '../types';

const formatCurrency = (v: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v);

const Budgets: React.FC = () => {
  const { data: budgets, loading, error, refetch } = useApi<Budget[]>('/api/v1/budgets');
  const [modalOpen, setModalOpen] = useState(false);
  const [editBudget, setEditBudget] = useState<Budget | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
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
      console.error('Failed to save budget:', err);
    }
  };

  const handleDelete = async () => {
    if (confirmId === null) return;
    setDeleting(true);
    try {
      await api.delete(`/api/v1/budgets/${confirmId}`);
      refetch();
    } catch (err) {
      console.error('Failed to delete budget:', err);
    } finally {
      setDeleting(false);
      setConfirmId(null);
    }
  };

  const columns: Column<Budget>[] = [
    {
      header: 'Name',
      accessor: 'name',
      render: (row) => <span className="font-medium text-slate-900">{row.name}</span>,
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
        <span className="capitalize text-slate-600">{row.period}</span>
      ),
    },
    {
      header: 'Limit',
      accessor: 'limit_amount',
      render: (row) => formatCurrency(row.limit_amount),
    },
    {
      header: 'Current Spend',
      accessor: 'current_spend',
      render: (row) => formatCurrency(row.current_spend),
    },
    {
      header: 'Used',
      accessor: 'percentage',
      render: (row) => {
        const pct = Math.min(row.percentage, 100);
        const barColor =
          pct > 90 ? 'bg-rose-500' : pct > 50 ? 'bg-amber-500' : 'bg-emerald-500';
        const textColor =
          pct > 90 ? 'text-rose-600' : pct > 50 ? 'text-amber-600' : 'text-emerald-600';
        return (
          <div className="flex items-center gap-3 min-w-[140px]">
            <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
              <div
                className={`h-full ${barColor} rounded-full transition-all`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className={`text-xs font-medium ${textColor} w-12 text-right`}>
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
            className="p-1.5 rounded text-slate-400 hover:text-teal-600 hover:bg-teal-50 transition-colors"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setConfirmId(row.id);
            }}
            className="p-1.5 rounded text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ),
    },
  ];

  if (loading) return <LoadingSpinner text="Loading budgets..." />;
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
          <h1 className="text-2xl font-bold text-slate-900">Budgets</h1>
          <p className="text-sm text-slate-500 mt-1">Set spending limits and cost controls</p>
        </div>
        <button onClick={openCreate} className="btn-primary gap-2">
          <Plus className="h-4 w-4" />
          Create Budget
        </button>
      </div>

      <DataTable<Budget>
        columns={columns}
        data={budgets ?? []}
        emptyTitle="No budgets"
        emptyDescription="Create your first budget to start tracking spending limits."
      />

      <Modal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editBudget ? 'Edit Budget' : 'Create Budget'}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
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
              className="input"
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
            <p className="mt-1 text-xs text-slate-500">Comma-separated percentages</p>
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
            <button type="button" onClick={() => setModalOpen(false)} className="btn-secondary">
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
        onClose={() => setConfirmId(null)}
        onConfirm={handleDelete}
        title="Delete budget"
        description="This will permanently delete the budget. Alerts triggered by this budget will also stop."
        confirmLabel="Delete budget"
        isLoading={deleting}
      />
    </div>
  );
};

export default Budgets;
