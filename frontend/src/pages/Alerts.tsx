import React, { useState } from 'react';
import { Plus, CheckCircle, Trash2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api, ApiError } from '../api/client';
import DataTable, { Column } from '../components/DataTable';
import Badge from '../components/Badge';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import { timeAgo } from '../utils/format';
import type { Alert, AlertChannel } from '../types';

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
  const [resolveError, setResolveError] = useState<string | null>(null);
  const [channelDeleteError, setChannelDeleteError] = useState<string | null>(null);
  const [channelCreateError, setChannelCreateError] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    channel_type: 'email',
    config: '',
  });

  const resolveAlert = async (alertId: number) => {
    setResolveError(null);
    try {
      await api.put(`/api/v1/alerts/${alertId}/resolve`);
      refetchAlerts();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? (err.body as { detail?: string })?.detail ?? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to resolve alert';
      setResolveError(msg);
    }
  };

  const handleDeleteChannel = async () => {
    if (confirmChannelId === null) return;
    setDeletingChannel(true);
    setChannelDeleteError(null);
    try {
      await api.delete(`/api/v1/alerts/channels/${confirmChannelId}`);
      refetchChannels();
      setConfirmChannelId(null);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? (err.body as { detail?: string })?.detail ?? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to delete channel';
      setChannelDeleteError(msg);
    } finally {
      setDeletingChannel(false);
    }
  };

  const handleCreateChannel = async (e: React.FormEvent) => {
    e.preventDefault();
    setConfigError('');
    setChannelCreateError(null);
    try {
      JSON.parse(formData.config);
    } catch {
      setConfigError('Invalid JSON — please check your configuration syntax.');
      return;
    }
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
      const msg =
        err instanceof ApiError
          ? (err.body as { detail?: string })?.detail ?? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to create channel';
      setChannelCreateError(msg);
    }
  };

  const alertColumns: Column<Alert>[] = [
    {
      header: 'Type',
      accessor: 'alert_type',
      render: (row) => (
        <span className="font-medium text-primary font-display">{row.alert_type}</span>
      ),
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
        <span className="text-secondary max-w-md truncate block">{row.message}</span>
      ),
    },
    {
      header: 'Time',
      accessor: 'created_at',
      render: (row) => (
        <span className="numeral text-xs text-muted">{timeAgo(row.created_at)}</span>
      ),
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
            className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400 transition-colors"
          >
            <CheckCircle className="h-3.5 w-3.5" />
            Resolve
          </button>
        ),
    },
  ];

  const loading = tab === 'history' ? alertsLoading : channelsLoading;
  const loadError = tab === 'history' ? alertsError : channelsError;

  if (loading) return <LoadingSpinner text={tab === 'history' ? 'Scanning the alarm bell...' : 'Loading channels...'} />;
  if (loadError)
    return (
      <div className="card-ledger mx-auto max-w-md p-8 text-center">
        <p className="font-display text-lg text-oxblood-600 dark:text-oxblood-300">{loadError}</p>
        <button
          onClick={tab === 'history' ? refetchAlerts : refetchChannels}
          className="btn-primary mt-4"
        >
          Try again
        </button>
      </div>
    );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Watchkeeping</p>
          <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
            Alerts
          </h1>
          <p className="mt-1 text-sm text-muted">
            When the coffers run low, someone must ring the bell. These are the bells.
          </p>
        </div>
        {tab === 'channels' && (
          <button onClick={() => { setChannelCreateError(null); setModalOpen(true); }} className="btn-gold gap-2">
            <Plus className="h-4 w-4" />
            Add Channel
          </button>
        )}
      </header>

      {/* Resolve error notice */}
      {resolveError && (
        <div className="rounded-lg border border-oxblood-600/30 bg-oxblood-500/10 px-4 py-3">
          <p className="text-sm text-oxblood-600 dark:text-oxblood-300">{resolveError}</p>
        </div>
      )}

      {/* Tab Bar */}
      <div className="border-b border-[color:var(--color-border)]">
        <nav className="flex gap-6">
          {(['history', 'channels'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors capitalize ${
                tab === t
                  ? 'border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300'
                  : 'border-transparent text-muted hover:text-secondary'
              }`}
            >
              {t === 'history' ? 'Alert History' : 'Channels'}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab Content */}
      {tab === 'history' && (
        <DataTable<Alert>
          columns={alertColumns}
          data={alerts ?? []}
          getRowKey={(row) => row.id}
          emptyTitle="Not a peep"
          emptyDescription="No alerts have been triggered yet. Budget thresholds will ring the bell when reached."
        />
      )}

      {tab === 'channels' && (
        <>
          {channelDeleteError && (
            <div className="rounded-lg border border-oxblood-600/30 bg-oxblood-500/10 px-4 py-3">
              <p className="text-sm text-oxblood-600 dark:text-oxblood-300">{channelDeleteError}</p>
            </div>
          )}
          {channels && channels.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {channels.map((channel) => (
                <div key={channel.id} className="card-ledger p-5">
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="font-display text-base font-semibold text-primary">
                        {channel.name}
                      </h3>
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
                      onClick={() => { setChannelDeleteError(null); setConfirmChannelId(channel.id); }}
                      className="p-1.5 rounded text-muted hover:text-oxblood-600 hover:bg-oxblood-500/10 transition-colors"
                      title="Delete channel"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div className="mt-3 pt-3 border-t border-[color:var(--color-border)]">
                    <p className="numeral text-xs text-muted truncate">
                      {channel.config_json}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="card">
              <EmptyState
                title="Silence on all channels"
                description="Add a notification channel to receive alerts via Slack, webhooks, or email."
                action={{ label: 'Add Channel', onClick: () => setModalOpen(true) }}
              />
            </div>
          )}
        </>
      )}

      <Modal
        isOpen={modalOpen}
        onClose={() => { setModalOpen(false); setChannelCreateError(null); setConfigError(''); }}
        title="Add Notification Channel"
      >
        <form onSubmit={handleCreateChannel} className="space-y-4">
          {channelCreateError && (
            <div className="rounded-lg border border-oxblood-600/30 bg-oxblood-500/10 px-4 py-3">
              <p className="text-sm text-oxblood-600 dark:text-oxblood-300">{channelCreateError}</p>
            </div>
          )}
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
              onChange={(e) => {
                setConfigError('');
                setChannelCreateError(null);
                setFormData({ ...formData, config: e.target.value });
              }}
            />
            {configError && (
              <p className="mt-1 text-xs text-oxblood-600 dark:text-oxblood-300">{configError}</p>
            )}
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => { setModalOpen(false); setChannelCreateError(null); setConfigError(''); }}
              className="btn-secondary"
            >
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
        onClose={() => { setConfirmChannelId(null); setChannelDeleteError(null); }}
        onConfirm={handleDeleteChannel}
        title="Silence this channel?"
        description="This will deactivate the alert channel. No future dispatches will be sent through it."
        confirmLabel="Delete channel"
        isLoading={deletingChannel}
      />
    </div>
  );
};

export default Alerts;
