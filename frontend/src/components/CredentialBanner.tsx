import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { CredentialStatus } from '../types';

/** Shown while this browser still uses the original shared key: it stops
 *  working for management on the date below, and logging in moves off it. */
export default function CredentialBanner() {
  const [status, setStatus] = useState<CredentialStatus | null>(null);

  useEffect(() => {
    api
      .get<CredentialStatus>('/api/v1/keys/status')
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  if (status?.role !== 'shared_key') return null;
  const until = status.shared_key_full_access_until
    ? new Date(status.shared_key_full_access_until).toLocaleDateString()
    : 'soon';

  return (
    <div className="mb-6 rounded-lg border border-gold-400/40 bg-gold-400/10 px-4 py-3 text-sm text-primary">
      This browser uses the shared API key, which loses management access on {until}. Log out and
      log in with your admin password, and give agents ingest keys under Settings.
    </div>
  );
}
