import { Suspense, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import Menu from '@mui/material/Menu';
import MenuItem from '@mui/material/MenuItem';
import ListItemIcon from '@mui/material/ListItemIcon';
import Tooltip from '@mui/material/Tooltip';
import { useAuth } from '../hooks/useAuth';
import { useBackend } from '../hooks/useBackend';
import { useColorMode } from '../hooks/useColorMode';
import type { Backend } from '../api/client';
import { TableSkeleton } from '../components/DataPage';
import ErrorBoundary from '../components/ErrorBoundary';

interface NavEntry {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
  /** Roles that may open the page. Omitted means every signed-in customer. */
  roles?: string[];
  /** Label shown to customers when the page means something different to them than to staff. */
  customerLabel?: string;
}

const NAV_SECTIONS: { label: string; customerLabel?: string; items: NavEntry[] }[] = [
  {
    label: 'WORKSPACE',
    items: [
      { to: '/', label: 'Dashboard', icon: '◫', end: true },
      { to: '/payments', label: 'Payments', icon: '↗' },
      { to: '/accounts', label: 'Accounts', icon: '▤' },
      { to: '/transactions', label: 'Transactions', icon: '⇄' },
      { to: '/beneficiaries', label: 'Beneficiaries', icon: '◎' },
    ],
  },
  {
    label: 'PROFILE',
    items: [
      { to: '/kyc', label: 'Verification', icon: '☑' },
      { to: '/notifications', label: 'Notifications', icon: '✉' },
    ],
  },
  {
    label: 'OPERATIONS',
    customerLabel: 'INSIGHTS',
    items: [
      { to: '/fraud', label: 'Fraud cases', icon: '⚑', customerLabel: 'Payment reviews' },
      { to: '/aml', label: 'AML alerts', icon: '⚠', roles: ['admin', 'fraud_analyst'] },
      { to: '/reconciliation', label: 'Reconciliation', icon: '⚖', roles: ['admin', 'finance_user', 'operations_user'] },
      { to: '/reports', label: 'Reports', icon: '▥' },
      { to: '/treasury', label: 'Treasury', icon: '⇣', roles: ['admin', 'finance_user'] },
      { to: '/admin', label: 'Admin', icon: '⚙', roles: ['admin'] },
    ],
  },
];

const ALL_ITEMS = NAV_SECTIONS.flatMap((section) => section.items);

const BACKENDS: { value: Backend; label: string }[] = [
  { value: 'python', label: 'Python' },
  { value: 'dotnet', label: '.NET' },
];

/** The nav entry for a path, and the label of the sub-page beneath it, if any. */
function breadcrumb(pathname: string): { item: NavEntry | null; page: string | null } {
  const item = ALL_ITEMS.find((entry) => (entry.end ? pathname === '/' : pathname.startsWith(entry.to))) ?? null;
  if (!item || item.end) return { item, page: null };
  const rest = pathname.slice(item.to.length).replace(/^\/+|\/+$/g, '');
  if (!rest) return { item, page: null };
  return { item, page: rest === 'new' ? `New ${item.label.replace(/s$/, '').toLowerCase()}` : 'Details' };
}

export function AppLayout() {
  const { auth, customer, roles, logout } = useAuth();
  const { backend, switchBackend } = useBackend();
  const { mode, toggle } = useColorMode();
  const navigate = useNavigate();
  const location = useLocation();
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);

  const email = customer?.email || auth?.customer?.email || 'Signed in';
  const initials = email.slice(0, 2).toUpperCase();
  const isStaff = roles.some((role) => role !== 'customer');
  const navLabel = (entry: { label: string; customerLabel?: string }) =>
    !isStaff && entry.customerLabel ? entry.customerLabel : entry.label;
  const visibleSections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => !item.roles || item.roles.some((role) => roles.includes(role))),
  })).filter((section) => section.items.length > 0);
  const crumb = breadcrumb(location.pathname);

  async function handleLogout() {
    setAnchorEl(null);
    await logout();
    navigate('/login', { replace: true });
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">F</span>
          <span className="brand-name">fincore</span>
        </div>
        <nav className="sidebar-nav" aria-label="Main">
          {visibleSections.map((section) => (
            <div key={section.label}>
              <div className="nav-section-label">{navLabel(section)}</div>
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
                  title={navLabel(item)}
                >
                  <span className="nav-icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  <span className="nav-text">{navLabel(item)}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="avatar">{initials}</span>
          <div>
            <strong>{email}</strong>
            <small>{isStaff ? roles.filter((role) => role !== 'customer').join(' · ') : 'Customer'}</small>
          </div>
        </div>
      </aside>

      <section className="main-panel">
        <header className="topbar">
          <div className="breadcrumbs">
            Workspace <span>/</span>{' '}
            {crumb.item && crumb.page ? (
              <>
                <Link to={crumb.item.to}>{navLabel(crumb.item)}</Link> <span>/</span> <b>{crumb.page}</b>
              </>
            ) : (
              <b>{crumb.item ? navLabel(crumb.item) : 'Overview'}</b>
            )}
          </div>
          <div className="topbar-right">
            <span className="environment">
              <i /> LOCAL
            </span>
            <div className="backend-control topbar-backend">
              <label id="backend-label">Active backend</label>
              <div className="backend-switch" role="radiogroup" aria-labelledby="backend-label" data-backend={backend}>
                <span className="thumb" aria-hidden="true" />
                {BACKENDS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={backend === option.value}
                    className={backend === option.value ? 'on' : undefined}
                    onClick={() => switchBackend(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <Tooltip title={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              <button
                type="button"
                className="theme-toggle"
                onClick={toggle}
                aria-label={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              >
                <span className="sun" aria-hidden="true">☀</span>
                <span className="moon" aria-hidden="true">☾</span>
              </button>
            </Tooltip>
            <Tooltip title={email}>
              <button className="icon-button" onClick={(e) => setAnchorEl(e.currentTarget)} aria-label="Account menu">
                {(email[0] ?? 'F').toUpperCase()}
              </button>
            </Tooltip>
            <Menu anchorEl={anchorEl} open={anchorEl !== null} onClose={() => setAnchorEl(null)}>
              <MenuItem disabled>
                <ListItemIcon>
                  <span style={{ fontSize: 12, color: 'var(--muted)' }}>{email}</span>
                </ListItemIcon>
              </MenuItem>
              <MenuItem onClick={() => void handleLogout()}>
                <ListItemIcon>⎋</ListItemIcon>Sign out
              </MenuItem>
            </Menu>
          </div>
        </header>
        {/* Keyed by route so the entrance animation replays and the error boundary resets on
            every navigation; a page that throws never takes the sidebar or other pages down. */}
        <div className="content page-enter" key={location.pathname}>
          <ErrorBoundary>
            <Suspense fallback={<TableSkeleton rows={5} />}>
              <Outlet />
            </Suspense>
          </ErrorBoundary>
        </div>
        <footer className="app-footer">
          FINCORE · LOCAL DEVELOPMENT <span>{backend === 'dotnet' ? '.NET · ASP.NET CORE' : 'PYTHON · FASTAPI'}</span>
        </footer>
      </section>
    </main>
  );
}

export default AppLayout;
