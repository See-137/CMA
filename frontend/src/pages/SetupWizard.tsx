import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { DollarSign, ChevronRight, ChevronLeft, Check, Copy, Eye, EyeOff } from 'lucide-react';
import { api } from '../api/client';
import type { SetupResponse } from '../types';

const TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Berlin',
  'Europe/Moscow',
  'Asia/Tokyo',
  'Asia/Shanghai',
  'Asia/Kolkata',
  'Australia/Sydney',
];

const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'CHF'];

const STEPS = ['Admin Account', 'Deployment Config', 'Providers', 'Budget'];

const SetupWizard: React.FC = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Success screen state
  const [completedApiKey, setCompletedApiKey] = useState<string | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [copied, setCopied] = useState(false);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [deploymentName, setDeploymentName] = useState('My Deployment');
  const [timezone, setTimezone] = useState('UTC');
  const [currency, setCurrency] = useState('USD');
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set(['openai']));
  const [dailyBudget, setDailyBudget] = useState('50');

  const canNext = (): boolean => {
    switch (step) {
      case 0:
        return email.length > 0 && password.length >= 6;
      case 1:
        return deploymentName.length > 0;
      case 2:
        return selectedProviders.size > 0;
      case 3:
        return Number(dailyBudget) > 0;
      default:
        return false;
    }
  };

  const copyKey = useCallback(() => {
    if (!completedApiKey) return;
    navigator.clipboard.writeText(completedApiKey).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [completedApiKey]);

  const handleComplete = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.post<SetupResponse>('/api/v1/setup', {
        admin_email: email,
        admin_password: password,
        deployment_name: deploymentName,
        timezone,
        currency,
      });
      localStorage.setItem('api_key', result.api_key);

      // Create the daily budget from step 4
      if (Number(dailyBudget) > 0) {
        try {
          await api.post('/api/v1/budgets', {
            name: 'Daily Global Limit',
            scope: 'global',
            period: 'daily',
            limit_amount: Number(dailyBudget),
            alert_thresholds: [50, 75, 90, 100],
            control_action: 'alert',
          });
        } catch {
          // Budget creation is non-critical, don't block setup
        }
      }

      // Show success screen instead of redirecting immediately
      setCompletedApiKey(result.api_key);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup failed');
    } finally {
      setSubmitting(false);
    }
  };

  const renderStep = () => {
    switch (step) {
      case 0:
        return (
          <div className="space-y-4">
            <div>
              <label className="label">Email Address</label>
              <input
                type="email"
                className="input"
                placeholder="admin@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Password</label>
              <input
                type="password"
                className="input"
                placeholder="Min. 6 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <p className="mt-1 text-xs text-slate-500">Must be at least 6 characters</p>
            </div>
          </div>
        );
      case 1:
        return (
          <div className="space-y-4">
            <div>
              <label className="label">Deployment Name</label>
              <input
                type="text"
                className="input"
                placeholder="My Deployment"
                value={deploymentName}
                onChange={(e) => setDeploymentName(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Timezone</label>
              <select
                className="input"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              >
                {TIMEZONES.map((tz) => (
                  <option key={tz} value={tz}>
                    {tz}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Currency</label>
              <select
                className="input"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              >
                {CURRENCIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          </div>
        );
      case 2:
        return (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              Select the providers your agents connect to. CMA will track costs for these.
            </p>
            {[
              { id: 'openai', label: 'OpenAI', desc: 'GPT-4o, GPT-4, GPT-3.5' },
              { id: 'anthropic', label: 'Anthropic', desc: 'Claude 3.5, Claude 3' },
              { id: 'google', label: 'Google', desc: 'Gemini 1.5 Pro, Flash' },
            ].map((p) => (
              <label key={p.id} className="flex items-center gap-3 p-3 rounded-lg border border-slate-200 hover:border-teal-300 hover:bg-teal-50/30 cursor-pointer transition-colors">
                <input
                  type="checkbox"
                  checked={selectedProviders.has(p.id)}
                  onChange={(e) => {
                    const next = new Set(selectedProviders);
                    if (e.target.checked) next.add(p.id);
                    else next.delete(p.id);
                    setSelectedProviders(next);
                  }}
                  className="h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500"
                />
                <div>
                  <span className="text-sm font-medium text-slate-900">{p.label}</span>
                  <span className="text-xs text-slate-500 ml-2">{p.desc}</span>
                </div>
              </label>
            ))}
          </div>
        );
      case 3:
        return (
          <div className="space-y-4">
            <div>
              <label className="label">Daily Budget Limit ({currency})</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm">
                  $
                </span>
                <input
                  type="number"
                  className="input pl-7"
                  placeholder="50.00"
                  min="1"
                  step="0.01"
                  value={dailyBudget}
                  onChange={(e) => setDailyBudget(e.target.value)}
                />
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Set a daily spending limit for cost protection. You can adjust this later.
              </p>
            </div>
          </div>
        );
      default:
        return null;
    }
  };

  // ── Success Screen ──────────────────────────────────────────────────────────

  if (completedApiKey) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
        <div className="w-full max-w-lg">
          <div className="flex items-center justify-center gap-3 mb-8">
            <div className="flex items-center justify-center h-11 w-11 rounded-xl bg-emerald-600">
              <Check className="h-6 w-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-slate-900">Setup Complete!</h1>
              <p className="text-sm text-slate-500">Your CMA instance is ready</p>
            </div>
          </div>

          <div className="card p-6 space-y-5">
            {/* API Key Section */}
            <div>
              <h2 className="text-base font-semibold text-slate-900 mb-1">Your API Key</h2>
              <p className="text-sm text-slate-500 mb-3">
                Save this key — you'll need it to connect your agents. You can also find it
                later on the <span className="font-medium text-teal-600">Integrations</span> page
                or by logging in again.
              </p>
              <div className="bg-slate-900 rounded-lg p-4">
                <div className="flex items-center justify-between gap-3">
                  <code className="text-green-400 font-mono text-sm break-all flex-1">
                    {showKey ? completedApiKey : completedApiKey.slice(0, 8) + '\u2022'.repeat(20) + completedApiKey.slice(-4)}
                  </code>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => setShowKey((v) => !v)}
                      className="inline-flex items-center justify-center h-8 w-8 rounded-md bg-slate-700 text-slate-300 hover:bg-slate-600 hover:text-white transition-colors"
                      title={showKey ? 'Hide' : 'Show'}
                    >
                      {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                    <button
                      onClick={copyKey}
                      className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                        copied
                          ? 'bg-emerald-600 text-white'
                          : 'bg-slate-700 text-slate-300 hover:bg-slate-600 hover:text-white'
                      }`}
                    >
                      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                      {copied ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Quick start hint */}
            <div className="bg-teal-50 rounded-lg p-4">
              <p className="text-sm font-medium text-teal-900 mb-1">Quick Start</p>
              <p className="text-xs text-teal-700">
                Go to <span className="font-semibold">Integrations</span> in the sidebar for
                copy-paste code snippets to connect your agents in under a minute.
              </p>
            </div>

            {/* Go to Dashboard */}
            <button
              onClick={() => navigate('/')}
              className="btn-primary w-full justify-center gap-2"
            >
              Go to Dashboard
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Wizard Steps ────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="w-full max-w-lg">
        {/* Logo */}
        <div className="flex items-center justify-center gap-3 mb-8">
          <div className="flex items-center justify-center h-11 w-11 rounded-xl bg-teal-600">
            <DollarSign className="h-6 w-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-slate-900">CMA Setup</h1>
            <p className="text-sm text-slate-500">Cost Monitoring Agent</p>
          </div>
        </div>

        {/* Progress Bar */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-2">
            {STEPS.map((label, i) => (
              <div key={label} className="flex items-center gap-2 text-xs font-medium">
                <div
                  className={`flex items-center justify-center h-6 w-6 rounded-full text-xs font-bold transition-colors ${
                    i < step
                      ? 'bg-teal-600 text-white'
                      : i === step
                        ? 'bg-teal-600 text-white'
                        : 'bg-slate-200 text-slate-500'
                  }`}
                >
                  {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
                </div>
                <span
                  className={`hidden sm:block ${
                    i <= step ? 'text-slate-900' : 'text-slate-400'
                  }`}
                >
                  {label}
                </span>
              </div>
            ))}
          </div>
          <div className="h-1.5 bg-slate-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-teal-600 rounded-full transition-all duration-300"
              style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
            />
          </div>
        </div>

        {/* Card */}
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-slate-900 mb-1">{STEPS[step]}</h2>
          <p className="text-sm text-slate-500 mb-6">
            {step === 0 && 'Create your admin account to get started.'}
            {step === 1 && 'Configure your deployment settings.'}
            {step === 2 && 'Select which LLM providers your agents use. This helps CMA show relevant pricing.'}
            {step === 3 && 'Set a daily budget to protect against runaway costs.'}
          </p>

          {renderStep()}

          {error && (
            <div className="mt-4 p-3 rounded-lg bg-rose-50 text-rose-700 text-sm">{error}</div>
          )}

          <div className="flex items-center justify-between mt-6 pt-4 border-t border-slate-200">
            <button
              onClick={() => setStep((s) => s - 1)}
              disabled={step === 0}
              className="btn-secondary gap-1.5"
            >
              <ChevronLeft className="h-4 w-4" />
              Back
            </button>

            {step < STEPS.length - 1 ? (
              <button
                onClick={() => setStep((s) => s + 1)}
                disabled={!canNext()}
                className="btn-primary gap-1.5"
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </button>
            ) : (
              <button
                onClick={handleComplete}
                disabled={!canNext() || submitting}
                className="btn-primary gap-1.5"
              >
                {submitting ? (
                  <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                ) : (
                  <Check className="h-4 w-4" />
                )}
                Complete Setup
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default SetupWizard;
