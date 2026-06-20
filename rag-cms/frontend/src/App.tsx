import {
  LayoutDashboard,
  LogOut,
  Moon,
  Search,
  Sun,
  Users,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useMatch, useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { CommandPalette } from './CommandPalette';
import { useTheme } from './ThemeContext';

const CREATE_RAG_EVENT = 'ragcms:open-create-rag';

export function openCreateRag() {
  window.dispatchEvent(new CustomEvent(CREATE_RAG_EVENT));
}

export default function App() {
  const { session, logout } = useAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();

  const isAuthRoute = location.pathname.startsWith('/login');
  const [cmdkOpen, setCmdkOpen] = useState(false);

  // Global shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const inField = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
      const mod = e.metaKey || e.ctrlKey;

      if (mod && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setCmdkOpen((v) => !v);
      } else if (!inField && !mod && e.key.toLowerCase() === 'n') {
        if (!session) return;
        e.preventDefault();
        if (location.pathname !== '/') navigate('/');
        // Defer to ensure RagList mounted before dispatching
        setTimeout(() => openCreateRag(), 30);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate, location.pathname, session]);

  const openCmdk = useCallback(() => setCmdkOpen(true), []);

  if (isAuthRoute || !session) {
    return (
      <div className="shell no-sidebar">
        <div className="topbar">
          <BrandMark />
          <span className="brand-text">rag-cms</span>
          <span className="muted" style={{ fontSize: 13 }}>multi-tenant agentic RAG platform</span>
          <span className="spacer" />
          <button
            className="icon"
            title={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
            onClick={toggleTheme}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
        <main className="main"><Outlet /></main>
      </div>
    );
  }

  return (
    <div
      className="shell"
      style={{
        display: 'grid',
        gridTemplateColumns: '64px minmax(0, 1fr)',
        gridTemplateRows: '56px 1fr',
        gridTemplateAreas: '"sidebar topbar" "sidebar main"',
        minHeight: '100vh',
      }}
    >
      <aside
        className="sidebar"
        style={{ width: 64, minWidth: 64, maxWidth: 64, gridArea: 'sidebar' }}
      >
        <div className="brand">
          <BrandMark />
        </div>
        <nav>
          <NavLink to="/" end className={navClass} title="RAG'и">
            <LayoutDashboard size={18} /> <span className="nav-label">RAG'и</span>
          </NavLink>
          {session.user.role === 'admin' && (
            <NavLink to="/admin/users" className={navClass} title="Пользователи">
              <Users size={18} /> <span className="nav-label">Пользователи</span>
            </NavLink>
          )}
        </nav>
        <div className="sidebar-footer">
          <div className="avatar" title={`${session.user.email} (${session.user.role})`}>
            {session.user.email.slice(0, 1).toUpperCase()}
          </div>
          <button
            className="icon"
            title="Выйти"
            onClick={() => { logout(); navigate('/login', { replace: true }); }}
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      <div className="topbar">
        <Breadcrumbs />
        <span className="spacer" />
        <button
          type="button"
          className="searchbar"
          onClick={openCmdk}
          title="Командная палитра (⌘/Ctrl+K)"
        >
          <Search size={14} className="searchbar-icon" />
          <span className="searchbar-label">Найти RAG, чат, команду…</span>
          <span className="kbd">⌘K</span>
        </button>
        <button
          className="icon"
          onClick={toggleTheme}
          title={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
        >
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>

      <main className="main"><Outlet /></main>

      <CommandPalette
        open={cmdkOpen}
        onClose={() => setCmdkOpen(false)}
        onOpenCreateRag={openCreateRag}
      />
    </div>
  );
}

function navClass({ isActive }: { isActive: boolean }) {
  return 'nav-item' + (isActive ? ' active' : '');
}

export function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
      </svg>
    </div>
  );
}

function Breadcrumbs() {
  const loc = useLocation();
  const ragMatch = useMatch('/rag/:id');
  const chatMatch = useMatch('/rag/:id/chat');
  const adminMatch = useMatch('/admin/users');

  if (loc.pathname === '/' || loc.pathname === '') {
    return <div className="crumbs">Дашборд</div>;
  }
  if (chatMatch) {
    return (
      <div className="crumbs">
        <NavLink to="/" style={{ color: 'inherit' }}>Дашборд</NavLink>
        {' / '}
        <NavLink to={`/rag/${chatMatch.params.id}`} style={{ color: 'inherit' }}>RAG</NavLink>
        {' / Чат с агентом'}
      </div>
    );
  }
  if (ragMatch) {
    return (
      <div className="crumbs">
        <NavLink to="/" style={{ color: 'inherit' }}>Дашборд</NavLink>
        {' / RAG'}
      </div>
    );
  }
  if (adminMatch) {
    return <div className="crumbs">Admin / Пользователи</div>;
  }
  return null;
}
