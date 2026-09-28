import React, { useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Coins,
  ChevronRight,
  ChevronLeft,
  Check,
  Copy,
  Eye,
  EyeOff,
  LogIn,
} from 'lucide-react';
import { api } from '../api/client';
import type { SetupResponse, SetupStatus } from '../types';

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

const STEPS = ['Your Keys', 'The House', 'Providers', 'The Coffer'];

const STEP_SUBTITLES = [
  'Who holds the master key to this counting house?',
  'Name your ledger and choose your local conventions.',
  'Which providers shall we track? Every farthing counts.',
  'Set a daily spending limit. Waste not, want not.',
];

// ── Shared layout wrapper ──────────────────────────────────────────────────────

function WizardShell({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="relative min-h-screen flex items-center justify-center p-4"
      style={{ background: 'var(--color-bg)' }}
    >
      {children}
    </div>
  );
}

// ── Brand mark ────────────────────────────────────────────────────────────────

function BrandMark({ subtitle }: { subtitle: string }) {
  return (
    <div className="mb-8 text-center">
      <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gold-leaf shadow-glow-gold">
        <Coins className="h-7 w-7 text-ink-950" />
      </div>
      <h1 className="font-display text-2xl font-semibold tracking-tight text-primary">
        Open Your Counting House
      </h1>
      <p className="mt-1 text-sm text-muted">{subtitle}</p>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

const SetupWizard: React.FC = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isReconnect, setIsReconnect] = useState(false);

  // Success screen state
  const [completedApiKey, setCompletedApiKey] = useState<string | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [copied, setCopied] = useState(false);

  // Form fields
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [deploymentName, setDeploymentName] = useState('My Deployment');
  const [timezone, setTimezone] = useState('UTC');
  const [currency, setCurrency] = useState('USD');
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set(['openai']));
  const [dailyBudget, setDailyBudget] = useState('50');

  // Check if setup is already done but local key is missing (reconnect flow)
  useEffect(() => {
    api
      .get<SetupStatus>('/api/v1/setup/status')
      .then((status) => {
        if (status.is_complete && !localStorage.getItem('api_key')) {
          setIsReconnect(true);
        }
      })
      .catch(() => {});
  }, []);

  // ── Reconnect: only admin_email + admin_password ─────────────────────────
  // Bug fix: the old code sent deployment_name/timezone/currency as empty stubs.
  // The reconnect endpoint only needs credentials — sending extras caused the
  // server to mis-validate or overwrite config fields.

  const handleReconnect = async () => {
    if (!email || password.length < 6) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.post<SetupResponse>('/api/v1/setup/reconnect', {
        admin_email: email,
        admin_password: password,
      });
      localStorage.setItem('api_key', result.api_key);
      navigate('/', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid credentials');
    } finally {
      setSubmitting(false);
    }
  };

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
          // Non-critical; don't block setup
        }
      }

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
                placeholder="keeper@example.com"
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
              <p className="mt-1.5 text-xs text-muted">
                The master key to your counting house. At least 6 characters.
              </p>
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
                placeholder="My Counting House"
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
          <div className="space-y-3">
            <p className="text-sm text-secondary">
              Select the providers your agents connect to. Scrooge will track every token they
              spend.
            </p>
            {[
              { id: 'openai', label: 'OpenAI', desc: 'GPT-4o, GPT-4, GPT-3.5' },
              { id: 'anthropic', label: 'Anthropic', desc: 'Claude 3.5, Claude 3' },
              { id: 'google', label: 'Google', desc: 'Gemini 1.5 Pro, Flash' },
            ].map((p) => {
              const checked = selectedProviders.has(p.id);
              return (
                <label
                  key={p.id}
                  className={`flex cursor-pointer items-center gap-3 rounded-lg border p-3 transition-colors ${
                    checked
                      ? 'border-brand-600/50 bg-brand-500/8 dark:bg-brand-500/12'
                      : 'border-token hover:border-gold-400/50 hover:bg-surface-2'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => {
                      const next = new Set(selectedProviders);
                      if (e.target.checked) next.add(p.id);
                      else next.delete(p.id);
                      setSelectedProviders(next);
                    }}
                    className="h-4 w-4 rounded border-token accent-brand-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-primary">{p.label}</span>
                    <span className="ml-2 text-xs text-muted">{p.desc}</span>
                  </div>
                </label>
              );
            })}
          </div>
        );

      case 3:
        return (
          <div className="space-y-4">
            <div>
              <label className="label">Daily Budget Limit ({currency})</label>
              <div className="relative">
                <span className="absolute left-3.5 top-1/2 -translate-y-1/2 font-mono text-sm text-muted">
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
              <p className="mt-1.5 text-xs text-muted">
                A prudent clerk always sets a ceiling. You can adjust this limit at any time.
              </p>
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  // ── Success Screen ─────────────────────────────────────────────────────────

  if (completedApiKey) {
    return (
      <WizardShell>
        <div className="w-full max-w-lg animate-fade-in">
          <BrandMark subtitle="The ledger is open. Your key awaits." />

          <div className="card-ledger p-6">
            <div className="mb-5 flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-500/15 text-brand-600 dark:text-brand-300">
                <Check className="h-5 w-5" />
              </div>
              <div>
                <h2 className="font-display text-base font-semibold text-primary">
                  Your Counting House is Open
                </h2>
                <p className="text-xs text-muted">Keep your API key somewhere safe.</p>
              </div>
            </div>

            {/* API Key */}
            <div className="mb-5">
              <label className="label">Your API Key</label>
              <p className="mb-3 text-sm text-secondary">
                You'll need this to connect your agents. It's also available on the{' '}
                <span className="font-medium text-gold">Integrations</span> page, or by signing
                in again.
              </p>
              <div className="flex items-center gap-2 rounded-lg bg-ink-950 p-3.5">
                <code className="flex-1 break-all font-mono text-sm text-brand-300">
                  {showKey
                    ? completedApiKey
                    : `${completedApiKey.slice(0, 8)}${'•'.repeat(20)}${completedApiKey.slice(-4)}`}
                </code>
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    onClick={() => setShowKey((v) => !v)}
                    className="flex h-8 w-8 items-center justify-center rounded-md bg-ink-800 text-ink-400 transition-colors hover:bg-ink-700 hover:text-white"
                    title={showKey ? 'Hide' : 'Show'}
                  >
                    {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                  <button
                    onClick={copyKey}
                    className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                      copied
                        ? 'bg-brand-600 text-white'
                        : 'bg-ink-800 text-ink-400 hover:bg-ink-700 hover:text-white'
                    }`}
                  >
                    {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                </div>
              </div>
            </div>

            {/* Quick start */}
            <div className="mb-5 rounded-lg bg-gold-400/10 px-4 py-3 ring-1 ring-gold-600/20">
              <p className="text-sm font-semibold text-gold-700 dark:text-gold-300">
                Quick Start
              </p>
              <p className="mt-0.5 text-xs text-secondary">
                Head to <span className="font-semibold">Integrations</span> in the sidebar for
                copy-paste code snippets — connect your first agent in under a minute.
              </p>
            </div>

            <button
              onClick={() => navigate('/')}
              className="btn-gold w-full justify-center gap-2"
            >
              Enter the Counting House
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </WizardShell>
    );
  }

  // ── Reconnect Screen ───────────────────────────────────────────────────────

  if (isReconnect) {
    return (
      <WizardShell>
        <div className="w-full max-w-sm animate-fade-in">
          <BrandMark subtitle="Your ledger is set. Present your credentials." />

          <div className="card-ledger p-6">
            <p className="mb-4 text-sm text-secondary">
              Your instance is configured. Sign in with your admin credentials to reclaim your
              session.
            </p>

            {error && (
              <div className="mb-4 rounded-lg border border-[color:var(--color-danger)]/30 bg-oxblood-500/10 px-4 py-3 text-sm text-oxblood-600 dark:text-oxblood-300">
                {error}
              </div>
            )}

            <div className="space-y-4">
              <div>
                <label className="label">Email</label>
                <input
                  type="email"
                  className="input"
                  placeholder="keeper@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleReconnect()}
                />
              </div>
              <div>
                <label className="label">Password</label>
                <input
                  type="password"
                  className="input"
                  placeholder="Your admin password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleReconnect()}
                />
              </div>
              <button
                onClick={handleReconnect}
                disabled={!email || password.length < 6 || submitting}
                className="btn-primary w-full justify-center gap-2"
              >
                <LogIn className="h-4 w-4" />
                {submitting ? 'Checking the ledger…' : 'Enter the Counting House'}
              </button>
            </div>
          </div>
        </div>
      </WizardShell>
    );
  }

  // ── Wizard Steps ───────────────────────────────────────────────────────────

  return (
    <WizardShell>
      <div className="w-full max-w-lg animate-fade-in">
        <BrandMark subtitle={STEP_SUBTITLES[step]} />

        {/* Step indicators */}
        <div className="mb-6">
          <div className="mb-3 flex items-center justify-between">
            {STEPS.map((label, i) => (
              <div key={label} className="flex items-center gap-1.5 text-xs font-medium">
                <div
                  className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold transition-colors ${
                    i < step
                      ? 'bg-brand-600 text-white'
                      : i === step
                        ? 'bg-gold-400 text-ink-950'
                        : 'bg-surface-2 text-muted'
                  }`}
                >
                  {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
                </div>
                <span
                  className={`hidden sm:block ${i <= step ? 'text-primary' : 'text-muted'}`}
                >
                  {label}
                </span>
              </div>
            ))}
          </div>
          {/* Progress track */}
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full bg-gold-400 transition-all duration-300"
              style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
            />
          </div>
        </div>

        {/* Card */}
        <div className="card-ledger p-6">
          <h2 className="mb-0.5 font-display text-lg font-semibold text-primary">
            {STEPS[step]}
          </h2>
          <p className="mb-5 text-sm text-muted">{STEP_SUBTITLES[step]}</p>

          {renderStep()}

          {error && (
            <div className="mt-4 rounded-lg border border-[color:var(--color-danger)]/30 bg-oxblood-500/10 px-3 py-2.5 text-sm text-oxblood-600 dark:text-oxblood-300">
              {error}
            </div>
          )}

          <div className="mt-6 flex items-center justify-between border-t border-token pt-4">
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
                className="btn-gold gap-1.5"
              >
                {submitting ? (
                  <div className="h-4 w-4 animate-spin rounded-full border-2 border-ink-950/30 border-t-ink-950" />
                ) : (
                  <Check className="h-4 w-4" />
                )}
                {submitting ? 'Opening the ledger…' : 'Complete Setup'}
              </button>
            )}
          </div>
        </div>
      </div>
    </WizardShell>
  );
};

export default SetupWizard;
