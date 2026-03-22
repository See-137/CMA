import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  Bot,
  Server,
  Wallet,
  Bell,
  Plug,
  Activity,
  DollarSign,
  Settings as SettingsIcon,
} from 'lucide-react';

const mainNav = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/providers', icon: Server, label: 'Providers' },
  { to: '/budgets', icon: Wallet, label: 'Budgets' },
  { to: '/alerts', icon: Bell, label: 'Alerts' },
];

const secondaryNav = [
  { to: '/integrations', icon: Plug, label: 'Integrations' },
  { to: '/events', icon: Activity, label: 'Events' },
  { to: '/settings', icon: SettingsIcon, label: 'Settings' },
];

const NavItem: React.FC<{ to: string; icon: React.FC<{ className?: string }>; label: string }> = ({
  to,
  icon: Icon,
  label,
}) => (
  <NavLink
    to={to}
    end={to === '/'}
    className={({ isActive }) =>
      `group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${
        isActive
          ? 'bg-teal-500/15 text-teal-400 shadow-sm shadow-teal-500/10'
          : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
      }`
    }
  >
    {({ isActive }) => (
      <>
        <div
          className={`flex items-center justify-center h-8 w-8 rounded-lg transition-all duration-150 ${
            isActive
              ? 'bg-teal-500/20 text-teal-400'
              : 'text-slate-500 group-hover:text-slate-300'
          }`}
        >
          <Icon className="h-[18px] w-[18px]" />
        </div>
        <span>{label}</span>
        {isActive && (
          <div className="ml-auto w-1.5 h-1.5 rounded-full bg-teal-400 animate-pulse-subtle" />
        )}
      </>
    )}
  </NavLink>
);

const Sidebar: React.FC = () => {
  return (
    <aside className="flex flex-col w-64 min-h-screen bg-slate-950 text-white shrink-0 border-r border-slate-800/50">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5">
        <div className="flex items-center justify-center h-10 w-10 rounded-xl bg-gradient-to-br from-teal-400 to-teal-600 shadow-glow-teal">
          <DollarSign className="h-5 w-5 text-white" />
        </div>
        <div>
          <h1 className="text-base font-extrabold tracking-tight bg-gradient-to-r from-teal-300 to-teal-500 bg-clip-text text-transparent">
            CMA
          </h1>
          <p className="text-[11px] text-slate-500 font-medium tracking-wide">Cost Monitor</p>
        </div>
      </div>

      {/* Divider */}
      <div className="mx-4 h-px bg-gradient-to-r from-transparent via-slate-700/50 to-transparent" />

      {/* Main Nav */}
      <nav className="flex-1 px-3 pt-4 space-y-0.5">
        {mainNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}

        {/* Section divider */}
        <div className="pt-4 pb-2">
          <div className="mx-2 h-px bg-slate-800/60" />
          <p className="mt-3 mb-1 px-3 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
            System
          </p>
        </div>

        {secondaryNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-slate-800/50">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse-subtle" />
          <p className="text-[11px] text-slate-500 font-medium">v0.1.0 &middot; Running</p>
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
