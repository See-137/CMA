import React, { useState } from 'react';
import { Plus, CheckCircle, Trash2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import type { Alert, AlertChannel } from '../types';

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

const severityVariant = (severity: string) => {
  switch (severity.toLowerCase()) {
    case 'critical':
      return 'danger' as const;
    case 'warning':
      return 'warning' as const;
    case 'info':
      return 'info' as const;
    default:
      return 'default' as const;
  }
};

const channelTypeVariant = (type: string) => {
  switch (type.toLowerCase()) {
    case 'email':
      return 'info' as const;
    case 'slack':
      return 'success' as const;
    case 'webhook':
      return 'warning' as const;
    default:
      return 'default' as const;
  }
};

const Alerts: React.FC = () => {
  const [tab, setTab] = useState<'history' | 'channels'>('history');
  const {
    data: alerts,
    loading: alertsLoading,
    error: alertsError,
    refetch: refetchAlerts,
  } = useApi<Alert[]>('/api/v1/alerts');
  const {
    data: channels,
    loading: channelsLoading,
    error: channelsError,
    refetch: refetchChannels,
  } = useApi<AlertChannel[]>('/api/v1/alerts/channels');
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmChannelId, setConfirmChannelId] = useState<number | null>(null);
  const [deletingChannel, setDeletingChannel] = useState(false);
  const [configError, setConfigError] = useState('');
  const [formData, setFormData] = useState({
    name: '',
    channel_type: 'email',
    config: '',
  });

  const resolveAlert = async (alertId: number) => {
    try {
      await api.put(`/api/v1/alerts/${alertId}/resolve`);
      refetchAlerts();
    } catch (err) {
      console.error('Failed to resolve alert:', err);
    }
  };

  const handleDeleteChannel = async () => {
    if (confirmChannelId === null) return;
    setDeletingChannel(true);
    try {
      await api.delete(`/api/v1/alerts/channels/${confirmChannelId}`);
      refetchChannels();
    } catch (err) {
      console.error('Failed to delete channel:', err);
    } finally {
      setDeletingChannel(false);
      setConfirmChannelId(null);
    }
  };

  const handleCreateChannel = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      JSON.parse(formData.config);
    } catch {
      setConfigError('Invalid JSON — please check your configuration syntax.');
      return;
    }
    setConfigError('');
    try {
      await api.post('/api/v1/alerts/channels', {
        name: formData.name,
        channel_type: formData.channel_type,
        config: formData.config,
      });
      setModalOpen(false);
      setFormData({ name: '', channel_type: 'email', config: '' });
      refetchChannels();
    } catch (err) {
      console.error('Failed to create channel:', err);
    }
  };

  const alertColumns: Column<Alert>[] = [
    {
      header: 'Type',
      accessor: 'alert_type',
      render: (row) => <span className="font-medium text-slate-900">{row.alert_type}</span>,
    },
    {
      header: 'Severity',
      accessor: 'severity',
      render: (row) => <Badge text={row.severity} variant={severityVariant(row.severity)} />,
    },
    {
      header: 'Message',
      accessor: 'message',
      render: (row) => (
        <span className="text-slate-600 max-w-md truncate block">{row.message}</span>
      ),
    },
    {
      header: 'Time',
      accessor: 'created_at',
      render: (row) => <span className="text-slate-500">{timeAgo(row.created_at)}</span>,
    },
    {
      header: 'Resolved',
      accessor: 'is_resolved',
      render: (row) =>
        row.is_resolved ? (
          <Badge text="Resolved" variant="success" />
        ) : (
          <button
            onClick={(e) => {
              e.stopPropagation();
              resolveAlert(row.id);
            }}
            className="inline-flex items-center gap-1 text-xs font-medium text-teal-600 hover:text-teal-800 transition-colors"
          >
            <CheckCircle className="h-3.5 w-3.5" />
            Resolve
          </button>
        ),
    },
  ];

  const loading = tab === 'history' ? alertsLoading : channelsLoading;
  const error = tab === 'history' ? alertsError : channelsError;

  if (loading) return <LoadingSpinner text={`Loading ${tab}...`} />;
  if (error)
    return (
      <div className="text-center py-12">
        <p className="text-rose-600">{error}</p>
        <button
          onClick={tab === 'history' ? refetchAlerts : refetchChannels}
          className="btn-primary mt-4"
        >
          Retry
        </button>
      </div>
    );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Alerts</h1>
          <p className="text-sm text-slate-500 mt-1">Monitor alerts and notification channels</p>
        </div>
        {tab === 'channels' && (
          <button onClick={() => setModalOpen(true)} className="btn-primary gap-2">
            <Plus className="h-4 w-4" />
            Add Channel
          </button>
        )}
      </div>

      {/* Tab Bar */}
      <div className="border-b border-slate-200">
        <nav className="flex gap-6">
          <button
            onClick={() => setTab('history')}
            className={`pb-3 text-sm font-medium border-b-2 transition-colors ${
              tab === 'history'
                ? 'border-teal-600 text-teal-600'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
          >
            Alert History
          </button>
          <button
            onClick={() => setTab('channels')}
            className={`pb-3 text-sm font-medium border-b-2 transition-colors ${
              tab === 'channels'
                ? 'border-teal-600 text-teal-600'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
          >
            Channels
          </button>
        </nav>
      </div>

      {/* Tab Content */}
      {tab === 'history' && (
        <DataTable<Alert>
          columns={alertColumns}
          data={alerts ?? []}
          emptyTitle="No alerts"
          emptyDescription="No alerts have been triggered yet. Alerts will appear when budget thresholds are reached."
        />
      )}

      {tab === 'channels' && (
        <>
          {channels && channels.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {channels.map((channel) => (
                <div key={channel.id} className="card p-5">
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="text-base font-semibold text-slate-900">{channel.name}</h3>
                      <div className="flex items-center gap-2 mt-2">
                        <Badge
                          text={channel.channel_type}
                          variant={channelTypeVariant(channel.channel_type)}
                        />
                        <Badge
                          text={channel.is_active ? 'Active' : 'Inactive'}
                          variant={channel.is_active ? 'success' : 'default'}
                        />
                      </div>
                    </div>
                    <button
                      onClick={() => setConfirmChannelId(channel.id)}
                      className="p-1.5 rounded text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors"
                      title="Delete channel"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div className="mt-3 pt-3 border-t border-slate-100">
                    <p className="text-xs text-slate-500 font-mono truncate">
                      {channel.config_json}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="card">
              <EmptyState
                title="No channels configured"
                description="Add a notification channel to receive alerts via email, Slack, or webhooks."
                action={{ label: 'Add Channel', onClick: () => setModalOpen(true) }}
              />
            </div>
          )}
        </>
      )}

      <Modal isOpen={modalOpen} onClose={() => setModalOpen(false)} title="Add Channel">
        <form onSubmit={handleCreateChannel} className="space-y-4">
          <div>
            <label className="label">Channel Name</label>
            <input
              type="text"
              className="input"
              required
              placeholder="e.g., Slack #alerts"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Channel Type</label>
            <select
              className="input"
              value={formData.channel_type}
              onChange={(e) => setFormData({ ...formData, channel_type: e.target.value })}
            >
              <option value="email">Email</option>
              <option value="slack">Slack</option>
              <option value="webhook">Webhook</option>
              <option value="pagerduty">PagerDuty</option>
            </select>
          </div>
          <div>
            <label className="label">Configuration (JSON)</label>
            <textarea
              className="input font-mono text-xs"
              rows={4}
              required
              placeholder={
                formData.channel_type === 'email'
                  ? '{"recipients": ["team@example.com"]}'
                  : formData.channel_type === 'slack'
                    ? '{"webhook_url": "https://hooks.slack.com/..."}'
                    : '{"url": "https://..."}'
              }
              value={formData.config}
              onChange={(e) => { setConfigError(''); setFormData({ ...formData, config: e.target.value }); }}
            />
            {configError && <p className="mt-1 text-xs text-rose-600">{configError}</p>}
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={() => setModalOpen(false)} className="btn-secondary">
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              Add Channel
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        isOpen={confirmChannelId !== null}
        onClose={() => setConfirmChannelId(null)}
        onConfirm={handleDeleteChannel}
        title="Delete channel"
        description="This will deactivate the alert channel. No future alerts will be sent to it."
        confirmLabel="Delete channel"
        isLoading={deletingChannel}
      />
    </div>
  );
};

export default Alerts;
