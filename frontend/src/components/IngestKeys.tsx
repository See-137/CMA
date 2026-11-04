import { useCallback, useEffect, useState } from 'react';
import { KeyRound } from 'lucide-react';
import { api, apiErrorMessage } from '../api/client';
import type { IngestKey } from '../types';

/** Keys for agents: they can send events and check budgets, nothing else. */
export default function IngestKeys() {
  const [keys, setKeys] = useState<IngestKey[]>([]);
  const [name, setName] = useState('');
  const [shown, setShown] = useState<IngestKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<IngestKey[]>('/api/v1/keys')
      .then(setKeys)
      .catch((err) => setError(apiErrorMessage(err, 'Could not load keys')));
  }, []);

  useEffect(load, [load]);

  const act = async (path: string, body?: unknown) => {
    setError(null);
    try {
      const result = await api.post<IngestKey>(path, body ?? {});
      setShown(result.key ? result : null);
      setName('');
      load();
    } catch (err) {
      setError(apiErrorMessage(err, 'That did not work. Try again.'));
    }
  };

  return (
    <div className="card-ledger p-6 lg:col-span-2">
      <div className="mb-4 flex items-center gap-2">
        <KeyRound className="h-5 w-5 text-brand-600 dark:text-brand-300" />
        <h2 className="font-display text-base font-semibold text-primary">Ingest Keys</h2>
      </div>
      <p className="mb-4 text-sm text-secondary">
        Give each agent or service its own key. A key can send events and check budgets, and
        nothing else.
      </p>

      {error && <p className="mb-3 text-sm text-oxblood-600 dark:text-oxblood-300">{error}</p>}

      {shown?.key && (
        <div className="mb-4 rounded-lg border border-gold-400/40 bg-gold-400/10 p-3 text-sm">
          <p className="mb-1 text-primary">
            Copy the key for {shown.name} now. It is not shown again.
          </p>
          <code className="block break-all font-mono text-xs text-primary">{shown.key}</code>
        </div>
      )}

      <form
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) act('/api/v1/keys', { name: name.trim() });
        }}
      >
        <input
          className="input flex-1"
          placeholder="Key name, such as research-agent"
          aria-label="Key name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit" className="btn-secondary" disabled={!name.trim()}>
          Create key
        </button>
      </form>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="py-2 pr-4">Name</th>
              <th className="py-2 pr-4">Key</th>
              <th className="py-2 pr-4">Last used</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key.id} className="border-t border-token">
                <td className="py-2 pr-4 text-primary">{key.name}</td>
                <td className="py-2 pr-4 font-mono text-xs text-secondary">{key.prefix}…</td>
                <td className="py-2 pr-4 text-secondary">
                  {key.revoked_at
                    ? 'Revoked'
                    : key.last_used_at
                      ? new Date(key.last_used_at).toLocaleString()
                      : 'Never'}
                </td>
                <td className="py-2 text-right">
                  {!key.revoked_at && (
                    <span className="inline-flex gap-2">
                      <button
                        className="btn-secondary text-xs"
                        onClick={() => act(`/api/v1/keys/${key.id}/rotate`)}
                      >
                        Rotate
                      </button>
                      <button
                        className="btn-secondary text-xs"
                        onClick={() => act(`/api/v1/keys/${key.id}/revoke`)}
                      >
                        Revoke
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
