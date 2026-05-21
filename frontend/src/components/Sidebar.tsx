import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  Bot,
  Server,
  Wallet,
  Bell,
  Plug,
  Activity,
  MessageSquare,
  Settings as SettingsIcon,
  LogOut,
  Sun,
  Moon,
  Coins,
} from 'lucide-react';
import { useTheme } from '../contexts/ThemeContext';

interface NavEntry {
  to: string;
  icon: React.FC<{ className?: string }>;
  label: string;
}

const mainNav: NavEntry[] = [
  { to: '/', icon: LayoutDashboard, label: 'The Ledger' },
  { to: '/chat', icon: MessageSquare, label: 'Ask Scrooge' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/providers', icon: Server, label: 'Providers' },
  { to: '/budgets', icon: Wallet, label: 'Coffers' },
  { to: '/alerts', icon: Bell, label: 'Alarms' },
];

const secondaryNav: NavEntry[] = [
  { to: '/integrations', icon: Plug, label: 'Integrations' },
  { to: '/events', icon: Activity, label: 'Transactions' },
  { to: '/settings', icon: SettingsIcon, label: 'Counting House' },
];

function NavItem({ to, icon: Icon, label }: NavEntry) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-150 ${
          isActive
            ? 'bg-gold-400/10 text-gold-200'
            : 'text-emerald-100/55 hover:bg-emerald-100/5 hover:text-emerald-50'
        }`
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-gold-300" />
          )}
          <Icon
            className={`h-[18px] w-[18px] transition-colors ${
              isActive ? 'text-gold-300' : 'text-emerald-100/40 group-hover:text-emerald-100/70'
            }`}
          />
          <span className="tracking-tight">{label}</span>
          {isActive && (
            <span className="ml-auto h-1.5 w-1.5 animate-pulse-subtle rounded-full bg-gold-300" />
          )}
        </>
      )}
    </NavLink>
  );
}

export default function Sidebar() {
  const { theme, toggleTheme } = useTheme();

  const handleLogout = () => {
    localStorage.removeItem('api_key');
    window.location.href = '/setup';
  };

  return (
    <aside className="relative flex w-64 shrink-0 flex-col bg-felt text-emerald-50 border-r border-emerald-950/60">
      {/* engraved gold seam down the right edge */}
      <div className="pointer-events-none absolute inset-y-0 right-0 w-px bg-gradient-to-b from-transparent via-gold-400/40 to-transparent" />

      {/* Wordmark */}
      <div className="flex items-center gap-3 px-5 pb-5 pt-6">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gold-leaf shadow-glow-gold">
          <Coins className="h-[22px] w-[22px] text-emerald-950" />
        </div>
        <div className="leading-none">
          <h1 className="font-display text-xl font-semibold tracking-tight text-gold-100">
            Scrooge
          </h1>
          <p className="mt-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-emerald-100/45">
            Counting House
          </p>
        </div>
      </div>

      <div className="mx-4 h-px bg-gradient-to-r from-transparent via-emerald-100/15 to-transparent" />

      <nav className="flex-1 space-y-0.5 px-3 pt-4">
        {mainNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}

        <div className="pb-2 pt-5">
          <p className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-[0.22em] text-emerald-100/30">
            Records
          </p>
        </div>

        {secondaryNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}
      </nav>

      <div className="space-y-1 border-t border-emerald-100/10 px-3 py-3">
        <button
          onClick={toggleTheme}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-emerald-100/55 transition-all duration-150 hover:bg-emerald-100/5 hover:text-emerald-50"
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          <span>{theme === 'dark' ? 'Daylight' : 'Candlelight'}</span>
        </button>

        <button
          onClick={handleLogout}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-emerald-100/55 transition-all duration-150 hover:bg-oxblood-500/15 hover:text-oxblood-200"
        >
          <LogOut className="h-4 w-4" />
          <span>Close the books</span>
        </button>

        <div className="flex items-center gap-2 px-3 pt-1">
          <span className="h-2 w-2 animate-pulse-subtle rounded-full bg-gold-300" />
          <p className="text-[11px] font-medium text-emerald-100/40">v0.1.0 · Open for business</p>
        </div>
      </div>
    </aside>
  );
}
