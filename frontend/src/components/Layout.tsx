import { Outlet } from 'react-router-dom';
import CredentialBanner from './CredentialBanner';
import Sidebar from './Sidebar';

export default function Layout() {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-7xl px-6 py-8 lg:px-10">
          <CredentialBanner />
          <Outlet />
        </div>
      </main>
    </div>
  );
}
