import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { ThemeProvider } from './contexts/ThemeContext';
import { api } from './api/client';
import type { SetupStatus } from './types';
import Layout from './components/Layout';
import LoadingSpinner from './components/LoadingSpinner';
import SetupWizard from './pages/SetupWizard';
import Dashboard from './pages/Dashboard';
import Agents from './pages/Agents';
import AgentDetail from './pages/AgentDetail';
import Providers from './pages/Providers';
import Budgets from './pages/Budgets';
import Alerts from './pages/Alerts';
import Events from './pages/Events';
import Integrations from './pages/Integrations';
import Settings from './pages/Settings';
import Chat from './pages/Chat';

const SetupGuard: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [checking, setChecking] = useState(true);
  const [setupComplete, setSetupComplete] = useState(false);

  // Check once on mount — not on every route change
  useEffect(() => {
    const checkSetup = async () => {
      try {
        const status = await api.get<SetupStatus>('/api/v1/setup/status');
        const hasLocalKey = !!localStorage.getItem('api_key');
        const ready = status.is_complete && hasLocalKey;
        setSetupComplete(ready);
        if (!ready) {
          navigate('/setup', { replace: true });
        }
      } catch {
        navigate('/setup', { replace: true });
      } finally {
        setChecking(false);
      }
    };
    checkSetup();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (checking) {
    return (
      <div className="flex items-center justify-center h-screen">
        <LoadingSpinner text="Checking setup status..." />
      </div>
    );
  }

  // If setup is complete AND user has a local API key, redirect away from /setup
  if (setupComplete && location.pathname === '/setup') {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
};

const App: React.FC = () => {
  return (
    <ThemeProvider>
    <BrowserRouter>
      <SetupGuard>
        <Routes>
          <Route path="/setup" element={<SetupWizard />} />
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/agents/:id" element={<AgentDetail />} />
            <Route path="/providers" element={<Providers />} />
            <Route path="/budgets" element={<Budgets />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/integrations" element={<Integrations />} />
            <Route path="/events" element={<Events />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </SetupGuard>
    </BrowserRouter>
    </ThemeProvider>
  );
};

export default App;
