import React, { useState, useEffect, useCallback } from 'react';
import {
  Settings as SettingsIcon,
  RefreshCw,
  Trash2,
  Download,
  Terminal,
  Info,
  Database,
  CheckCircle,
} from 'lucide-react';
import { api } from '../api/client';

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

const Settings: React.FC = () => {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [logs, setLogs] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(true);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [fullResetConfirmOpen, setFullResetConfirmOpen] = useState(false);
  const [fullResetInput, setFullResetInput] = useState('');
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const data = await api.get<SystemStatus>('/api/v1/admin/status');
      setStatus(data);
    } catch {
      setStatus(null);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const fetchLogs = useCallback(async () => {
    setLogsLoading(true);
    try {
      const data = await api.get<LogsResponse>('/api/v1/admin/logs');
      setLogs(data.lines);
    } catch {
      setLogs(['Failed to load logs.']);
    } finally {
      setLogsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchLogs();
  }, [fetchStatus, fetchLogs]);

  const handleReset = async () => {
    setActionLoading('reset');
    setActionMessage(null);
    try {
      const result = await api.post<{ message: string }>('/api/v1/admin/reset');
      setActionMessage(result.message);
      setResetConfirmOpen(false);
      fetchStatus();
    } catch {
      setActionMessage('Failed to reset monitoring data.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleFullReset = async () => {
    if (fullResetInput !== 'RESET') return;
    setActionLoading('full-reset');
    setActionMessage(null);
    try {
      const result = await api.post<{ message: string }>('/api/v1/admin/reset-full');
      setActionMessage(result.message);
      setFullResetConfirmOpen(false);
      setFullResetInput('');
      fetchStatus();
    } catch {
      setActionMessage('Failed to perform full reset.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleExport = async () => {
    setActionLoading('export');
    setActionMessage(null);
    try {
      const data = await api.get<Record<string, unknown>>('/api/v1/admin/export');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `cma-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setActionMessage('Export downloaded successfully.');
    } catch {
      setActionMessage('Failed to export data.');
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Settings</h1>
        <p className="text-sm text-slate-500 mt-1">System administration and configuration</p>
      </div>

      {/* Action message */}
      {actionMessage && (
        <div className="rounded-lg border border-teal-200 bg-teal-50 px-4 py-3 text-sm text-teal-800">
          {actionMessage}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* System Status Card */}
        <div className="card p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5 text-teal-600" />
              <h2 className="text-base font-semibold text-slate-900">System Status</h2>
            </div>
            <button
              onClick={fetchStatus}
              disabled={statusLoading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${statusLoading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>

          {statusLoading && !status ? (
            <div className="text-sm text-slate-500 py-4 text-center">Loading status...</div>
          ) : status ? (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  <span className="relative flex h-3 w-3">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                  </span>
                  <span className="text-sm font-medium text-emerald-700">Running</span>
                </div>
                <span className="text-xs text-slate-400">v{status.version}</span>
                <span className="text-xs text-slate-400">{status.deployment_name}</span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                <div className="rounded-lg bg-slate-50 px-3 py-2">
                  <p className="text-xs text-slate-500">Total Events</p>
                  <p className="text-lg font-semibold text-slate-900">
                    {status.database.total_events.toLocaleString()}
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 px-3 py-2">
                  <p className="text-xs text-slate-500">Total Agents</p>
                  <p className="text-lg font-semibold text-slate-900">
                    {status.database.total_agents.toLocaleString()}
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 px-3 py-2">
                  <p className="text-xs text-slate-500">Active Budgets</p>
                  <p className="text-lg font-semibold text-slate-900">
                    {status.database.active_budgets.toLocaleString()}
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 px-3 py-2">
                  <p className="text-xs text-slate-500">Unresolved Alerts</p>
                  <p className="text-lg font-semibold text-slate-900">
                    {status.database.unresolved_alerts.toLocaleString()}
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 px-3 py-2">
                  <p className="text-xs text-slate-500">DB Size</p>
                  <p className="text-lg font-semibold text-slate-900">
                    {status.database.size_mb} MB
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-rose-600 py-4 text-center">Failed to load system status.</div>
          )}
        </div>

        {/* Data Management Card */}
        <div className="card p-6">
          <div className="flex items-center gap-2 mb-4">
            <Trash2 className="h-5 w-5 text-rose-600" />
            <h2 className="text-base font-semibold text-slate-900">Data Management</h2>
          </div>

          <div className="space-y-4">
            {/* Export */}
            <div>
              <button
                onClick={handleExport}
                disabled={actionLoading === 'export'}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-teal-600 rounded-lg hover:bg-teal-700 transition-colors disabled:opacity-50"
              >
                <Download className="h-4 w-4" />
                {actionLoading === 'export' ? 'Exporting...' : 'Export Data'}
              </button>
              <p className="text-xs text-slate-500 mt-1">Download all agents and recent events as JSON.</p>
            </div>

            {/* Clear Monitoring Data */}
            <div className="border-t border-slate-100 pt-4">
              {!resetConfirmOpen ? (
                <div>
                  <button
                    onClick={() => setResetConfirmOpen(true)}
                    className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-rose-600 bg-white border border-rose-300 rounded-lg hover:bg-rose-50 transition-colors"
                  >
                    <Trash2 className="h-4 w-4" />
                    Clear Monitoring Data
                  </button>
                  <p className="text-xs text-slate-500 mt-1">
                    Clears all events, agents, budgets, and alerts. Keeps your setup config and providers.
                  </p>
                </div>
              ) : (
                <div className="rounded-lg border border-rose-200 bg-rose-50 p-4">
                  <p className="text-sm font-medium text-rose-800 mb-3">
                    Are you sure? This will delete all monitoring data. Setup config and providers will be preserved.
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleReset}
                      disabled={actionLoading === 'reset'}
                      className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-rose-600 rounded-lg hover:bg-rose-700 transition-colors disabled:opacity-50"
                    >
                      {actionLoading === 'reset' ? 'Clearing...' : 'Yes, Clear Data'}
                    </button>
                    <button
                      onClick={() => setResetConfirmOpen(false)}
                      className="px-4 py-2 text-sm font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Full Reset */}
            <div className="border-t border-slate-100 pt-4">
              {!fullResetConfirmOpen ? (
                <div>
                  <button
                    onClick={() => setFullResetConfirmOpen(true)}
                    className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-rose-600 rounded-lg hover:bg-rose-700 transition-colors"
                  >
                    <Trash2 className="h-4 w-4" />
                    Full Reset
                  </button>
                  <p className="text-xs text-slate-500 mt-1">
                    Deletes everything including setup. You'll need to re-run the setup wizard.
                  </p>
                </div>
              ) : (
                <div className="rounded-lg border border-rose-200 bg-rose-50 p-4">
                  <p className="text-sm font-medium text-rose-800 mb-2">
                    This will delete ALL data including your setup configuration. Type <span className="font-mono font-bold">RESET</span> to confirm.
                  </p>
                  <div className="flex items-center gap-2 mt-3">
                    <input
                      type="text"
                      value={fullResetInput}
                      onChange={(e) => setFullResetInput(e.target.value)}
                      placeholder="Type RESET"
                      className="px-3 py-2 text-sm border border-rose-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-rose-500 w-32"
                    />
                    <button
                      onClick={handleFullReset}
                      disabled={fullResetInput !== 'RESET' || actionLoading === 'full-reset'}
                      className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-rose-600 rounded-lg hover:bg-rose-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {actionLoading === 'full-reset' ? 'Resetting...' : 'Confirm Full Reset'}
                    </button>
                    <button
                      onClick={() => {
                        setFullResetConfirmOpen(false);
                        setFullResetInput('');
                      }}
                      className="px-4 py-2 text-sm font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Application Logs Card */}
        <div className="card p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Terminal className="h-5 w-5 text-emerald-600" />
              <h2 className="text-base font-semibold text-slate-900">Application Logs</h2>
            </div>
            <button
              onClick={fetchLogs}
              disabled={logsLoading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${logsLoading ? 'animate-spin' : ''}`} />
              Refresh Logs
            </button>
          </div>

          <div className="rounded-lg bg-slate-900 p-4 max-h-80 overflow-y-auto">
            {logsLoading && logs.length === 0 ? (
              <p className="text-sm text-slate-400 font-mono">Loading logs...</p>
            ) : (
              <pre className="text-xs text-green-400 font-mono whitespace-pre-wrap break-words leading-relaxed">
                {logs.length > 0 ? logs.join('\n') : 'No logs available.'}
              </pre>
            )}
          </div>
        </div>

        {/* About Card */}
        <div className="card p-6">
          <div className="flex items-center gap-2 mb-4">
            <Info className="h-5 w-5 text-slate-600" />
            <h2 className="text-base font-semibold text-slate-900">About</h2>
          </div>

          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <CheckCircle className="h-5 w-5 text-teal-600" />
              <div>
                <p className="text-sm font-semibold text-slate-900">Cost Monitoring Agent</p>
                <p className="text-xs text-slate-500">Self-hosted observability for multi-agent LLM systems</p>
              </div>
            </div>

            <div className="rounded-lg bg-slate-50 px-4 py-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm text-slate-600">Version</span>
                <span className="text-sm font-medium text-slate-900">0.1.0</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-slate-600">License</span>
                <span className="text-sm font-medium text-slate-900">MIT</span>
              </div>
            </div>

            <div className="pt-2">
              <a
                href="https://github.com/your-org/cma"
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-teal-600 hover:text-teal-700 font-medium"
              >
                Documentation & Source Code
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Settings;
