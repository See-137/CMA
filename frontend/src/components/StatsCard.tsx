import { LucideIcon, TrendingUp, TrendingDown } from 'lucide-react';

interface StatsCardProps {
  title: string;
  value: string;
  change?: number;
  icon: LucideIcon;
  delay?: number;
  /** Lower spend is good for cost metrics; flip the colour semantics. */
  invertChange?: boolean;
}

export default function StatsCard({
  title,
  value,
  change,
  icon: Icon,
  delay = 0,
  invertChange = false,
}: StatsCardProps) {
  const hasChange = change !== undefined && change !== null;
  const up = (change ?? 0) >= 0;
  const good = invertChange ? !up : up;

  return (
    <div
      className="card-hover group animate-fade-in cursor-default p-6 opacity-0"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex items-start justify-between">
        <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-brand-500/12 text-brand-600 transition-transform duration-200 group-hover:scale-110 dark:text-brand-300">
          <Icon className="h-5 w-5" />
        </div>
        {hasChange && (
          <div
            className={`flex items-center gap-1 rounded-full px-2 py-1 text-xs font-semibold numeral ${
              good
                ? 'bg-brand-500/12 text-brand-700 dark:text-brand-300'
                : 'bg-oxblood-500/12 text-oxblood-600 dark:text-oxblood-300'
            }`}
          >
            {up ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
            <span>{Math.abs(change as number).toFixed(1)}%</span>
          </div>
        )}
      </div>
      <div className="mt-4">
        <p className="eyebrow">{title}</p>
        <p className="numeral mt-1.5 text-[26px] font-semibold leading-none text-primary">
          {value}
        </p>
      </div>
    </div>
  );
}
