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

const formatPrice = (v: number) => `$${v.toFixed(2)}`;

const providerTypeVariant = (type: string) => {
  switch (type.toLowerCase()) {
    case 'openai':
      return 'info' as const;
    case 'anthropic':
      return 'warning' as const;
    case 'google':
      return 'success' as const;
    default:
      return 'default' as const;
  }
};

const Providers: React.FC = () => {
  const { data: providers, loading, error, refetch } = useApi<Provider[]>('/api/v1/providers');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmProviderId, setConfirmProviderId] = useState<number | null>(null);
  const [deletingProvider, setDeletingProvider] = useState(false);
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
      console.error('Failed to create provider:', err);
    }
  };

  const startEditing = (model: Model) => {
    setEditingModel({
      id: model.id,
      inputPrice: model.input_price_per_million.toString(),
      outputPrice: model.output_price_per_million.toString(),
    });
  };

  const cancelEditing = () => {
    setEditingModel(null);
  };

  const handleDeleteProvider = async () => {
    if (confirmProviderId === null) return;
    setDeletingProvider(true);
    try {
      await api.delete(`/api/v1/providers/${confirmProviderId}`);
      refetch();
    } catch (err) {
      console.error('Failed to delete provider:', err);
    } finally {
      setDeletingProvider(false);
      setConfirmProviderId(null);
    }
  };

  const saveEditing = async () => {
    if (!editingModel) return;
    try {
      await api.put(`/api/v1/models/${editingModel.id}`, {
        input_price_per_million: parseFloat(editingModel.inputPrice),
        output_price_per_million: parseFloat(editingModel.outputPrice),
      });
      setEditingModel(null);
      refetch();
    } catch (err) {
      console.error('Failed to save model pricing:', err);
      // Keep editing form open so user can retry
    }
  };

  if (loading) return <LoadingSpinner text="Loading providers..." />;
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
          <h1 className="text-2xl font-bold text-slate-900">Providers</h1>
          <p className="text-sm text-slate-500 mt-1">Manage LLM providers and model pricing</p>
        </div>
        <button onClick={() => setModalOpen(true)} className="btn-primary gap-2">
          <Plus className="h-4 w-4" />
          Add Provider
        </button>
      </div>

      {providers && providers.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {providers.map((provider) => {
            const isExpanded = expandedId === provider.id;
            return (
              <div key={provider.id} className="card overflow-hidden">
                <div
                  className="p-5 cursor-pointer"
                  onClick={() => setExpandedId(isExpanded ? null : provider.id)}
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="text-base font-semibold text-slate-900">{provider.name}</h3>
                      <div className="flex items-center gap-2 mt-2">
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
                      <span className="text-xs text-slate-500">
                        {provider.models.length} model{provider.models.length !== 1 ? 's' : ''}
                      </span>
                      <button
                        onClick={(e) => { e.stopPropagation(); setConfirmProviderId(provider.id); }}
                        className="p-1 rounded text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors"
                        title="Delete provider"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                      {isExpanded ? (
                        <ChevronUp className="h-4 w-4 text-slate-400" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-slate-400" />
                      )}
                    </div>
                  </div>
                </div>

                {isExpanded && (
                  <div className="border-t border-slate-200">
                    {provider.models.length > 0 ? (
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="bg-slate-50">
                            <th className="px-4 py-2 text-left text-xs font-semibold text-slate-600">
                              Model
                            </th>
                            <th className="px-4 py-2 text-left text-xs font-semibold text-slate-600">
                              Input $/M
                            </th>
                            <th className="px-4 py-2 text-left text-xs font-semibold text-slate-600">
                              Output $/M
                            </th>
                            <th className="px-4 py-2 text-right text-xs font-semibold text-slate-600">
                              Actions
                            </th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {provider.models.map((model) => (
                            <tr key={model.id} className="hover:bg-slate-50">
                              <td className="px-4 py-2.5">
                                <div className="flex items-center gap-2">
                                  <span className="font-medium text-slate-700">
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
                                  formatPrice(model.input_price_per_million)
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
                                  formatPrice(model.output_price_per_million)
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
                                      className="p-1 rounded text-emerald-600 hover:bg-emerald-50"
                                    >
                                      <Check className="h-3.5 w-3.5" />
                                    </button>
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        cancelEditing();
                                      }}
                                      className="p-1 rounded text-slate-400 hover:bg-slate-100"
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
                                    className="p-1 rounded text-slate-400 hover:text-teal-600 hover:bg-teal-50"
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
                      <p className="text-sm text-slate-500 p-4 text-center">No models configured</p>
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
            title="No providers"
            description="Add your first LLM provider to start tracking costs."
            action={{ label: 'Add Provider', onClick: () => setModalOpen(true) }}
          />
        </div>
      )}

      <Modal isOpen={modalOpen} onClose={() => setModalOpen(false)} title="Add Provider">
        <form onSubmit={handleSubmit} className="space-y-4">
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
              Add Provider
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        isOpen={confirmProviderId !== null}
        onClose={() => setConfirmProviderId(null)}
        onConfirm={handleDeleteProvider}
        title="Delete provider"
        description="This will deactivate the provider and all its models. Existing cost events will be preserved."
        confirmLabel="Delete provider"
        isLoading={deletingProvider}
      />
    </div>
  );
};

export default Providers;
