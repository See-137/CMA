import React, { useState, useEffect, useCallback } from 'react';
import {
  RefreshCw,
  Trash2,
  Download,
  Terminal,
  Info,
  Database,
  CheckCircle,
  Lock,
  KeyRound,
} from 'lucide-react';
import { api, ApiError } from '../api/client';
import Modal from '../components/Modal';

interface SystemStatus {
  status: string;
  version: string;
  deployment_name: string;
  uptime_check: string;
  database: {
    size_mb: number;
    total_events: number;
    total_agents: number;
    active_budgets: number;
    unresolved_alerts: number;
  };
}

interface LogsResponse {
  lines: string[];
}

// ── Admin password gate ────────────────────────────────────────────────────────
// All /admin/* endpoints (except /status) now require X-Admin-Password.
// We prompt once per action; we never cache the password in state long-term.

interface AdminPasswordModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (password: string) => void;
  title: string;
  description: string;
  submitLabel: string;
  isLoading: boolean;
  error: string | null;
}

function AdminPasswordModal({
  isOpen,
  onClose,
  onSubmit,
  title,
  description,
  submitLabel,
  isLoading,
  error,
}: AdminPasswordModalProps) {
  const [password, setPassword] = useState('');

  // Reset on open/close
  useEffect(() => {
    if (!isOpen) setPassword('');
  }, [isOpen]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (password) onSubmit(password);
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <p className="text-sm text-secondary">{description}</p>

        {error && (
          <div className="rounded-lg border border-[color:var(--color-danger)]/30 bg-oxblood-500/10 px-4 py-3 text-sm text-oxblood-600 dark:text-oxblood-300">
            {error}
          </div>
        )}

        <div>
          <label className="label">
            <Lock className="mr-1.5 inline h-3 w-3" />
            Admin Password
          </label>
          <input
            type="password"
            className="input"
            placeholder="Enter your admin password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
          />
        </div>

        <div className="flex justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} className="btn-secondary" disabled={isLoading}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn-danger"
            disabled={!password || isLoading}
          >
            {isLoading ? `${submitLabel}...` : submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── Stat chip ──────────────────────────────────────────────────────────────────

function StatChip({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-surface-2 px-3 py-2.5">
      <p className="eyebrow mb-0.5">{label}</p>
      <p className="numeral text-lg font-semibold text-primary">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

const Settings: React.FC = () => {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [logs, setLogs] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsUnlocked, setLogsUnlocked] = useState(false);

  // Data management inline confirm state
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [fullResetInput, setFullResetInput] = useState('');
  const [fullResetOpen, setFullResetOpen] = useState(false);

  // Action state
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<{ text: string; tone: 'ok' | 'err' } | null>(
    null,
  );

  // Admin password modal
  type PendingAction = 'reset' | 'full-reset' | 'export' | 'logs';
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [pwdError, setPwdError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const data = await api.get<SystemStatus>('/api/v1/admin/status');
      setStatus(data);
    } catch {
      // The backend may be momentarily busy (e.g. just after a reset) — retry
      // once before declaring it unreachable, so a brief hiccup doesn't flash
      // a scary "Failed" right after a successful action.
      await new Promise((r) => setTimeout(r, 800));
      try {
        const data = await api.get<SystemStatus>('/api/v1/admin/status');
        setStatus(data);
      } catch {
        setStatus(null);
      }
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const fetchLogs = useCallback(async (password: string) => {
    setLogsLoading(true);
    try {
      const data = await api.get<LogsResponse>('/api/v1/admin/logs', undefined, {
        'X-Admin-Password': password,
      });
      setLogs(data.lines);
      setLogsUnlocked(true);
      setPendingAction(null);
      setPwdError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setPwdError('Incorrect admin password. Try again.');
      } else {
        setLogs(['Failed to load logs.']);
        setPendingAction(null);
      }
    } finally {
      setLogsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleAdminSubmit = async (password: string) => {
    if (!pendingAction) return;
    setActionLoading(pendingAction);
    setPwdError(null);

    if (pendingAction === 'logs') {
      await fetchLogs(password);
      setActionLoading(null);
      return;
    }

    try {
      const adminHeader = { 'X-Admin-Password': password };

      if (pendingAction === 'reset') {
        const result = await api.post<{ message: string }>(
          '/api/v1/admin/reset',
          undefined,
          adminHeader,
        );
        setActionMessage({ text: result.message, tone: 'ok' });
        setResetConfirmOpen(false);
        setPendingAction(null);
        fetchStatus();
      } else if (pendingAction === 'full-reset') {
        const result = await api.post<{ message: string }>(
          '/api/v1/admin/reset-full',
          undefined,
          adminHeader,
        );
        setActionMessage({ text: result.message, tone: 'ok' });
        setFullResetOpen(false);
        setFullResetInput('');
        setPendingAction(null);
        fetchStatus();
      } else if (pendingAction === 'export') {
        const data = await api.get<Record<string, unknown>>(
          '/api/v1/admin/export',
          undefined,
          adminHeader,
        );
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `cma-export-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        setActionMessage({ text: 'Ledger exported to your coffers.', tone: 'ok' });
        setPendingAction(null);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setPwdError('Incorrect admin password. The vault remains sealed.');
      } else {
        setActionMessage({ text: 'Operation failed. Check the logs.', tone: 'err' });
        setPendingAction(null);
      }
    } finally {
      setActionLoading(null);
    }
  };

  const openAction = (action: PendingAction) => {
    setPwdError(null);
    setPendingAction(action);
  };

  const closeModal = () => {
    setPendingAction(null);
    setPwdError(null);
  };

  const modalConfig: Record<
    PendingAction,
    { title: string; description: string; submitLabel: string }
  > = {
    reset: {
      title: 'Clear Monitoring Data',
      description:
        'This will delete all events, agents, budgets, and alerts. Your setup config and providers are preserved. Enter your admin password to proceed.',
      submitLabel: 'Clear Data',
    },
    'full-reset': {
      title: 'Full Reset — Burn the Ledger',
      description:
        'This destroys everything — events, agents, budgets, and your setup configuration. You will need to run the setup wizard again. There is no undo. Enter your admin password to proceed.',
      submitLabel: 'Full Reset',
    },
    export: {
      title: 'Export Ledger',
      description:
        'Download all agents and recent events as a JSON file. Enter your admin password to authorise the export.',
      submitLabel: 'Export',
    },
    logs: {
      title: 'View Application Logs',
      description:
        'The log viewer requires your admin password. Enter it once to unlock the log pane.',
      submitLabel: 'Unlock Logs',
    },
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <header>
        <p className="eyebrow">Counting House</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
          Administration
        </h1>
        <p className="mt-1 text-sm text-muted">
          System status, data management, and application logs.
        </p>
      </header>

      {/* Action message */}
      {actionMessage && (
        <div
          className={`rounded-lg border px-4 py-3 text-sm animate-fade-in ${
            actionMessage.tone === 'ok'
              ? 'border-brand-600/30 bg-brand-500/10 text-brand-700 dark:text-brand-300'
              : 'border-[color:var(--color-danger)]/30 bg-oxblood-500/10 text-oxblood-600 dark:text-oxblood-300'
          }`}
        >
          {actionMessage.text}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* ── System Status ─────────────────────────────────────────────── */}
        <div className="card-ledger p-6">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5 text-gold" />
              <h2 className="font-display text-base font-semibold text-primary">System Status</h2>
            </div>
            <button
              onClick={fetchStatus}
              disabled={statusLoading}
              className="btn-secondary px-3 py-1.5 text-xs"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${statusLoading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>

          {statusLoading && !status ? (
            <p className="py-4 text-center text-sm text-muted">Consulting the ledger…</p>
          ) : status ? (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <span className="relative flex h-3 w-3">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75" />
                  <span className="relative inline-flex h-3 w-3 rounded-full bg-brand-500" />
                </span>
                <span className="text-sm font-medium text-brand-600 dark:text-brand-300">
                  Running
                </span>
                <span className="numeral text-xs text-muted">v{status.version}</span>
                <span className="text-xs text-muted">{status.deployment_name}</span>
              </div>

              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <StatChip label="Total Events" value={status.database.total_events} />
                <StatChip label="Agents" value={status.database.total_agents} />
                <StatChip label="Active Budgets" value={status.database.active_budgets} />
                <StatChip label="Open Alerts" value={status.database.unresolved_alerts} />
                <StatChip label="DB Size" value={`${status.database.size_mb} MB`} />
              </div>
            </div>
          ) : (
            <p className="py-4 text-center text-sm text-oxblood-600 dark:text-oxblood-300">
              Failed to reach the counting house.
            </p>
          )}
        </div>

        {/* ── Data Management ───────────────────────────────────────────── */}
        <div className="card-ledger p-6">
          <div className="mb-4 flex items-center gap-2">
            <Trash2 className="h-5 w-5 text-oxblood-500 dark:text-oxblood-300" />
            <h2 className="font-display text-base font-semibold text-primary">Data Management</h2>
          </div>

          <div className="space-y-5">
            {/* Export */}
            <div>
              <button
                onClick={() => openAction('export')}
                disabled={actionLoading === 'export'}
                className="btn-secondary gap-2"
              >
                <Download className="h-4 w-4" />
                {actionLoading === 'export' ? 'Exporting…' : 'Export Ledger'}
              </button>
              <p className="mt-1.5 text-xs text-muted">
                Download all agents and recent events as JSON.
              </p>
            </div>

            <div className="hairline" />

            {/* Clear Monitoring Data */}
            <div>
              {!resetConfirmOpen ? (
                <div>
                  <button
                    onClick={() => setResetConfirmOpen(true)}
                    className="btn-secondary gap-2 text-oxblood-600 dark:text-oxblood-300"
                  >
                    <Trash2 className="h-4 w-4" />
                    Clear Monitoring Data
                  </button>
                  <p className="mt-1.5 text-xs text-muted">
                    Clears all events, agents, budgets, and alerts. Keeps your setup and providers.
                  </p>
                </div>
              ) : (
                <div className="rounded-lg border border-[color:var(--color-danger)]/30 bg-oxblood-500/10 p-4">
                  <p className="mb-3 text-sm font-medium text-oxblood-700 dark:text-oxblood-300">
                    All monitoring data will be erased. Setup config and providers are preserved.
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => openAction('reset')}
                      disabled={actionLoading === 'reset'}
                      className="btn-danger"
                    >
                      {actionLoading === 'reset' ? 'Clearing…' : 'Clear Data'}
                    </button>
                    <button
                      onClick={() => setResetConfirmOpen(false)}
                      className="btn-secondary"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>

            <div className="hairline" />

            {/* Full Reset */}
            <div>
              {!fullResetOpen ? (
                <div>
                  <button
                    onClick={() => setFullResetOpen(true)}
                    className="btn-danger"
                  >
                    <Trash2 className="h-4 w-4" />
                    Full Reset
                  </button>
                  <p className="mt-1.5 text-xs text-muted">
                    Destroys everything, including setup. Requires re-running the wizard.
                  </p>
                </div>
              ) : (
                <div className="rounded-lg border border-[color:var(--color-danger)]/30 bg-oxblood-500/10 p-4">
                  <p className="mb-2 text-sm font-medium text-oxblood-700 dark:text-oxblood-300">
                    This destroys <em>everything</em> including setup. Type{' '}
                    <span className="numeral font-bold">RESET</span> to confirm.
                  </p>
                  <div className="mt-3 flex items-center gap-2">
                    <input
                      type="text"
                      value={fullResetInput}
                      onChange={(e) => setFullResetInput(e.target.value)}
                      placeholder="Type RESET"
                      className="input w-32"
                    />
                    <button
                      onClick={() => {
                        if (fullResetInput === 'RESET') openAction('full-reset');
                      }}
                      disabled={fullResetInput !== 'RESET' || actionLoading === 'full-reset'}
                      className="btn-danger"
                    >
                      {actionLoading === 'full-reset' ? 'Resetting…' : 'Full Reset'}
                    </button>
                    <button
                      onClick={() => {
                        setFullResetOpen(false);
                        setFullResetInput('');
                      }}
                      className="btn-secondary"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ── Application Logs ──────────────────────────────────────────── */}
        <div className="card-ledger p-6 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Terminal className="h-5 w-5 text-gold" />
              <h2 className="font-display text-base font-semibold text-primary">
                Application Logs
              </h2>
            </div>
            {logsUnlocked ? (
              <button
                onClick={() => {
                  setPendingAction('logs');
                  setPwdError(null);
                }}
                disabled={logsLoading}
                className="btn-secondary px-3 py-1.5 text-xs"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${logsLoading ? 'animate-spin' : ''}`} />
                Refresh
              </button>
            ) : (
              <button
                onClick={() => openAction('logs')}
                className="btn-secondary px-3 py-1.5 text-xs gap-1.5"
              >
                <KeyRound className="h-3.5 w-3.5" />
                Unlock Logs
              </button>
            )}
          </div>

          {!logsUnlocked ? (
            <div className="flex flex-col items-center justify-center gap-3 py-10">
              <Lock className="h-8 w-8 text-muted" />
              <p className="text-sm text-muted">
                Logs require admin authentication. Click "Unlock Logs" to proceed.
              </p>
            </div>
          ) : (
            <div className="max-h-80 overflow-y-auto rounded-lg bg-ink-950 p-4">
              {logsLoading ? (
                <p className="font-mono text-sm text-muted">Fetching the day's entries…</p>
              ) : (
                <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-brand-300">
                  {logs.length > 0 ? logs.join('\n') : 'No entries in the log.'}
                </pre>
              )}
            </div>
          )}
        </div>

        {/* ── About ─────────────────────────────────────────────────────── */}
        <div className="card p-6 lg:col-span-2">
          <div className="mb-4 flex items-center gap-2">
            <Info className="h-5 w-5 text-muted" />
            <h2 className="font-display text-base font-semibold text-primary">About</h2>
          </div>

          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <CheckCircle className="h-5 w-5 text-brand-500" />
              <div>
                <p className="text-sm font-semibold text-primary">Cost Monitoring Agent</p>
                <p className="text-xs text-muted">
                  Self-hosted observability for multi-agent LLM systems
                </p>
              </div>
            </div>

            <div className="rounded-lg bg-surface-2 px-4 py-3">
              <div className="flex items-center justify-between py-1">
                <span className="text-sm text-secondary">Version</span>
                <span className="numeral text-sm font-medium text-primary">0.1.0</span>
              </div>
              <div className="hairline my-1" />
              <div className="flex items-center justify-between py-1">
                <span className="text-sm text-secondary">License</span>
                <span className="text-sm font-medium text-primary">MIT</span>
              </div>
            </div>

            <a
              href="https://github.com/your-org/cma"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-gold hover:underline"
            >
              Documentation &amp; Source Code
            </a>
          </div>
        </div>
      </div>

      {/* Admin password modal — shared by all gated actions */}
      {pendingAction && (
        <AdminPasswordModal
          isOpen
          onClose={closeModal}
          onSubmit={handleAdminSubmit}
          title={modalConfig[pendingAction].title}
          description={modalConfig[pendingAction].description}
          submitLabel={modalConfig[pendingAction].submitLabel}
          isLoading={actionLoading === pendingAction}
          error={pwdError}
        />
      )}
    </div>
  );
};

export default Settings;
