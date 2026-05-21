/** Shared formatting helpers — previously duplicated across pages. */

export function formatCurrency(value: number | null | undefined, fractionDigits = 2): string {
  const n = typeof value === 'number' && Number.isFinite(value) ? value : 0;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(n);
}

export function formatNumber(value: number | null | undefined): string {
  const n = typeof value === 'number' && Number.isFinite(value) ? value : 0;
  return new Intl.NumberFormat('en-US').format(n);
}

export function formatCompact(value: number | null | undefined): string {
  const n = typeof value === 'number' && Number.isFinite(value) ? value : 0;
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(n);
}

export function timeAgo(input: string | Date | null | undefined): string {
  if (!input) return '—';
  const then = typeof input === 'string' ? new Date(input) : input;
  const seconds = Math.floor((Date.now() - then.getTime()) / 1000);
  if (Number.isNaN(seconds)) return '—';
  if (seconds < 45) return 'just now';

  const table: [number, string][] = [
    [31536000, 'yr'],
    [2592000, 'mo'],
    [604800, 'wk'],
    [86400, 'day'],
    [3600, 'hr'],
    [60, 'min'],
  ];
  for (const [limit, name] of table) {
    if (seconds >= limit) {
      const v = Math.floor(seconds / limit);
      return `${v} ${name}${v === 1 ? '' : 's'} ago`;
    }
  }
  return 'just now';
}

export type EnvVariant = 'prod' | 'staging' | 'dev' | 'default';

export function envVariant(environment: string | null | undefined): EnvVariant {
  const env = (environment ?? '').toLowerCase();
  if (env.includes('prod')) return 'prod';
  if (env.includes('stag')) return 'staging';
  if (env.includes('dev')) return 'dev';
  return 'default';
}
