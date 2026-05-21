import React, { useState } from 'react';
import { Plus, ChevronDown, ChevronUp, Pencil, Check, X, Trash2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import Badge from '../components/Badge';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import type { Provider, Model } from '../types';

/** Format a per-million price to 2 dp */
const formatPrice = (v: number) => `$${v.toFixed(2)}`;

const providerTypeVariant = (type: string): 'info' | 'warning' | 'success' | 'default' => {
  switch (type.toLowerCase()) {
    case 'openai':
      return 'info';
    case 'anthropic':
      return 'warning';
    case 'google':
      return 'success';
    default:
      return 'default';
  }
};

const Providers: React.FC = () => {
  const { data: providers, loading, error, refetch } = useApi<Provider[]>('/api/v1/providers');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmProviderId, setConfirmProviderId] = useState<number | null>(null);
  const [deletingProvider, setDeletingProvider] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [pricingError, setPricingError] = useState<string | null>(null);
  const [editingModel, setEditingModel] = useState<{
    id: number;
    inputPrice: string;
    outputPrice: string;
  } | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    provider_type: 'openai',
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    try {
      await api.post('/api/v1/providers', {
        name: formData.name,
        provider_type: formData.provider_type,
        models: [],
      });
      setModalOpen(false);
      setFormData({ name: '', provider_type: 'openai' });
      refetch();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to add provider. Try again.');
    }
  };

  const startEditing = (model: Model) => {
    setPricingError(null);
    setEditingModel({
      id: model.id,
      inputPrice: model.input_price_per_million.toString(),
      outputPrice: model.output_price_per_million.toString(),
    });
  };

  const cancelEditing = () => {
    setEditingModel(null);
    setPricingError(null);
  };

  const handleDeleteProvider = async () => {
    if (confirmProviderId === null) return;
    setDeletingProvider(true);
    setDeleteError(null);
    try {
      await api.delete(`/api/v1/providers/${confirmProviderId}`);
      refetch();
      setConfirmProviderId(null);
    } catch (err) {
      setDeleteError(
        err instanceof Error ? err.message : 'Failed to remove provider. Try again.'
      );
    } finally {
      setDeletingProvider(false);
    }
  };

  const saveEditing = async () => {
    if (!editingModel) return;
    setPricingError(null);
    try {
      await api.put(`/api/v1/models/${editingModel.id}`, {
        input_price_per_million: parseFloat(editingModel.inputPrice),
        output_price_per_million: parseFloat(editingModel.outputPrice),
      });
      setEditingModel(null);
      refetch();
    } catch (err) {
      // Keep editing form open so the clerk can retry
      setPricingError(
        err instanceof Error ? err.message : 'Failed to save pricing. Try again.'
      );
    }
  };

  if (loading) return <LoadingSpinner text="Consulting the price book…" size="lg" />;
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
          <p className="eyebrow">Counting House · Suppliers</p>
          <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
            Providers
          </h1>
          <p className="mt-1 text-sm text-muted">
            The houses that supply the intelligence — and their tariff sheets.
          </p>
        </div>
        <button
          onClick={() => {
            setCreateError(null);
            setModalOpen(true);
          }}
          className="btn-primary gap-2"
        >
          <Plus className="h-4 w-4" />
          Add Provider
        </button>
      </header>

      {/* Delete error notice */}
      {deleteError && (
        <div className="rounded-lg border border-[color:var(--color-danger)] bg-oxblood-500/8 px-4 py-3 text-sm text-oxblood-600 dark:text-oxblood-300">
          {deleteError}
        </div>
      )}

      {providers && providers.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {providers.map((provider) => {
            const isExpanded = expandedId === provider.id;
            return (
              <div key={provider.id} className="card-ledger overflow-hidden">
                {/* Card header — click to expand */}
                <div
                  className="cursor-pointer p-5"
                  onClick={() => setExpandedId(isExpanded ? null : provider.id)}
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="font-display text-base font-semibold text-primary">
                        {provider.name}
                      </h3>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <Badge
                          text={provider.provider_type}
                          variant={providerTypeVariant(provider.provider_type)}
                        />
                        <Badge
                          text={provider.is_active ? 'Active' : 'Inactive'}
                          variant={provider.is_active ? 'success' : 'default'}
                        />
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted">
                        {provider.models.length} model{provider.models.length !== 1 ? 's' : ''}
                      </span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteError(null);
                          setConfirmProviderId(provider.id);
                        }}
                        className="rounded p-1 text-muted transition-colors hover:bg-oxblood-500/10 hover:text-oxblood-600 dark:hover:text-oxblood-300"
                        title="Remove provider"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                      {isExpanded ? (
                        <ChevronUp className="h-4 w-4 text-muted" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-muted" />
                      )}
                    </div>
                  </div>
                </div>

                {/* Pricing table — shown when expanded */}
                {isExpanded && (
                  <div className="border-t border-[color:var(--color-border)]">
                    {/* Per-provider pricing error */}
                    {pricingError && editingModel !== null &&
                      provider.models.some((m) => m.id === editingModel.id) && (
                        <div className="mx-4 mt-3 rounded-lg border border-[color:var(--color-danger)] bg-oxblood-500/8 px-3 py-2 text-xs text-oxblood-600 dark:text-oxblood-300">
                          {pricingError}
                        </div>
                      )}
                    {provider.models.length > 0 ? (
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="bg-surface-2">
                            <th className="px-4 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-muted">
                              Model
                            </th>
                            <th className="px-4 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-muted">
                              Input $/M
                            </th>
                            <th className="px-4 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-muted">
                              Output $/M
                            </th>
                            <th className="px-4 py-2 text-right text-[11px] font-semibold uppercase tracking-wider text-muted">
                              Actions
                            </th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-[color:var(--color-border)]">
                          {provider.models.map((model) => (
                            <tr
                              key={model.id}
                              className="transition-colors hover:bg-surface-2"
                            >
                              <td className="px-4 py-2.5">
                                <div className="flex items-center gap-2">
                                  <span className="font-medium text-primary">
                                    {model.model_name}
                                  </span>
                                  {!model.is_active && (
                                    <Badge text="Inactive" variant="default" />
                                  )}
                                </div>
                              </td>
                              <td className="px-4 py-2.5">
                                {editingModel?.id === model.id ? (
                                  <input
                                    type="number"
                                    className="input w-24 py-1 text-xs"
                                    step="0.01"
                                    value={editingModel.inputPrice}
                                    onChange={(e) =>
                                      setEditingModel({
                                        ...editingModel,
                                        inputPrice: e.target.value,
                                      })
                                    }
                                    onClick={(e) => e.stopPropagation()}
                                  />
                                ) : (
                                  <span className="numeral text-secondary">
                                    {formatPrice(model.input_price_per_million)}
                                  </span>
                                )}
                              </td>
                              <td className="px-4 py-2.5">
                                {editingModel?.id === model.id ? (
                                  <input
                                    type="number"
                                    className="input w-24 py-1 text-xs"
                                    step="0.01"
                                    value={editingModel.outputPrice}
                                    onChange={(e) =>
                                      setEditingModel({
                                        ...editingModel,
                                        outputPrice: e.target.value,
                                      })
                                    }
                                    onClick={(e) => e.stopPropagation()}
                                  />
                                ) : (
                                  <span className="numeral text-secondary">
                                    {formatPrice(model.output_price_per_million)}
                                  </span>
                                )}
                              </td>
                              <td className="px-4 py-2.5 text-right">
                                {editingModel?.id === model.id ? (
                                  <div className="flex items-center justify-end gap-1">
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        saveEditing();
                                      }}
                                      className="rounded p-1 text-brand-600 transition-colors hover:bg-brand-500/10 dark:text-brand-300"
                                      title="Save pricing"
                                    >
                                      <Check className="h-3.5 w-3.5" />
                                    </button>
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        cancelEditing();
                                      }}
                                      className="rounded p-1 text-muted transition-colors hover:bg-surface-2"
                                      title="Cancel"
                                    >
                                      <X className="h-3.5 w-3.5" />
                                    </button>
                                  </div>
                                ) : (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      startEditing(model);
                                    }}
                                    className="rounded p-1 text-muted transition-colors hover:bg-gold-400/10 hover:text-gold-700 dark:hover:text-gold-300"
                                    title="Edit pricing"
                                  >
                                    <Pencil className="h-3.5 w-3.5" />
                                  </button>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <p className="p-4 text-center text-sm text-muted">
                        No tariff sheet — models are added automatically on first use.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="card">
          <EmptyState
            title="No suppliers on the books"
            description="Add your first LLM provider to begin tracking costs."
            action={{ label: 'Add Provider', onClick: () => setModalOpen(true) }}
          />
        </div>
      )}

      {/* Add Provider Modal */}
      <Modal isOpen={modalOpen} onClose={() => setModalOpen(false)} title="Register a Provider">
        <form onSubmit={handleSubmit} className="space-y-4">
          {createError && (
            <div className="rounded-lg border border-[color:var(--color-danger)] bg-oxblood-500/8 px-4 py-3 text-sm text-oxblood-600 dark:text-oxblood-300">
              {createError}
            </div>
          )}
          <div>
            <label className="label">Provider Name</label>
            <input
              type="text"
              className="input"
              required
              placeholder="e.g., OpenAI Production"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Provider Type</label>
            <select
              className="input"
              value={formData.provider_type}
              onChange={(e) => setFormData({ ...formData, provider_type: e.target.value })}
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="google">Google</option>
              <option value="azure">Azure</option>
              <option value="custom">Custom</option>
            </select>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={() => setModalOpen(false)} className="btn-secondary">
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              Register Provider
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        isOpen={confirmProviderId !== null}
        onClose={() => {
          setConfirmProviderId(null);
          setDeleteError(null);
        }}
        onConfirm={handleDeleteProvider}
        title="Remove provider"
        description="This will deactivate the provider and all its models. Every farthing spent is preserved in the ledger."
        confirmLabel="Remove"
        isLoading={deletingProvider}
      />
    </div>
  );
};

export default Providers;
