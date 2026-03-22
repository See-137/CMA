import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
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

const SetupGuard: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [checking, setChecking] = useState(true);
  const [setupComplete, setSetupComplete] = useState(false);

  useEffect(() => {
    const checkSetup = async () => {
      try {
        const status = await api.get<SetupStatus>('/api/v1/setup/status');
        setSetupComplete(status.is_complete);
        if (!status.is_complete && location.pathname !== '/setup') {
          navigate('/setup', { replace: true });
        }
      } catch {
        // If setup check fails, assume setup is needed
        if (location.pathname !== '/setup') {
          navigate('/setup', { replace: true });
        }
      } finally {
        setChecking(false);
      }
    };
    checkSetup();
  }, [navigate, location.pathname]);

  if (checking) {
    return (
      <div className="flex items-center justify-center h-screen">
        <LoadingSpinner text="Checking setup status..." />
      </div>
    );
  }

  // If setup is complete and user is on /setup, redirect to dashboard
  if (setupComplete && location.pathname === '/setup') {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
};

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <SetupGuard>
        <Routes>
          <Route path="/setup" element={<SetupWizard />} />
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
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
  );
};

export default App;
