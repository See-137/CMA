import React from 'react';

type BadgeVariant = 'success' | 'warning' | 'danger' | 'info' | 'default';

interface BadgeProps {
  text: string;
  variant?: BadgeVariant;
}

const variantClasses: Record<BadgeVariant, string> = {
  success: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  warning: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  danger: 'bg-rose-50 text-rose-700 ring-rose-600/20',
  info: 'bg-teal-50 text-teal-700 ring-teal-600/20',
  default: 'bg-slate-50 text-slate-700 ring-slate-600/20',
};

const Badge: React.FC<BadgeProps> = ({ text, variant = 'default' }) => {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${variantClasses[variant]}`}
    >
      {text}
    </span>
  );
};

export default Badge;
