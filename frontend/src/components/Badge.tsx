type BadgeVariant = 'success' | 'warning' | 'danger' | 'info' | 'default';

interface BadgeProps {
  text: string;
  variant?: BadgeVariant;
}

const variantClasses: Record<BadgeVariant, string> = {
  success: 'bg-brand-500/12 text-brand-700 ring-brand-600/25 dark:text-brand-300',
  warning: 'bg-gold-400/15 text-gold-700 ring-gold-600/30 dark:text-gold-300',
  danger: 'bg-oxblood-500/12 text-oxblood-700 ring-oxblood-600/25 dark:text-oxblood-300',
  info: 'bg-brand-500/10 text-brand-700 ring-brand-600/20 dark:text-brand-300',
  default: 'bg-ink-500/10 text-secondary ring-ink-500/20',
};

export default function Badge({ text, variant = 'default' }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ring-inset ${variantClasses[variant]}`}
    >
      {text}
    </span>
  );
}
