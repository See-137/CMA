import React from 'react';
import { LucideIcon, TrendingUp, TrendingDown } from 'lucide-react';

interface StatsCardProps {
  title: string;
  value: string;
  change?: number;
  icon: LucideIcon;
  iconColor?: string;
  iconBg?: string;
  delay?: number;
}

const StatsCard: React.FC<StatsCardProps> = ({
  title,
  value,
  change,
  icon: Icon,
  iconColor = 'text-teal-600',
  iconBg = 'bg-teal-50',
  delay = 0,
}) => {
  return (
    <div
      className="card-hover p-6 opacity-0 animate-fade-in group cursor-default"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex items-start justify-between">
        <div
          className={`flex items-center justify-center h-11 w-11 rounded-xl ${iconBg} transition-transform duration-200 group-hover:scale-110`}
        >
          <Icon className={`h-5 w-5 ${iconColor}`} />
        </div>
        {change !== undefined && change !== null && (
          <div
            className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold ${
              change >= 0
                ? 'bg-emerald-50 text-emerald-600'
                : 'bg-rose-50 text-rose-600'
            }`}
          >
            {change >= 0 ? (
              <TrendingUp className="h-3 w-3" />
            ) : (
              <TrendingDown className="h-3 w-3" />
            )}
            <span>{Math.abs(change).toFixed(1)}%</span>
          </div>
        )}
      </div>
      <div className="mt-4">
        <p className="text-[13px] font-medium text-slate-500">{title}</p>
        <p className="mt-1 text-2xl font-bold text-slate-900 tracking-tight">{value}</p>
      </div>
    </div>
  );
};

export default StatsCard;
