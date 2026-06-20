import {
  Bot,
  Database,
  LogOut,
  Moon,
  Plus,
  Search,
  Sun,
  Users,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type Rag } from './api';
import { useAuth } from './AuthContext';
import { useTheme } from './ThemeContext';

interface Command {
  id: string;
  label: string;
  group: string;
  icon: React.ReactNode;
  hint?: string;
  keywords?: string;
  run: () => void;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenCreateRag: () => void;
}

export function CommandPalette({ open, onClose, onOpenCreateRag }: Props) {
  const [query, setQuery] = useState('');
  const [rags, setRags] = useState<Rag[]>([]);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const { session, logout } = useAuth();
  const { theme, toggle: toggleTheme } = useTheme();

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActive(0);
    inputRef.current?.focus();
    api.listRags().then(setRags).catch(() => setRags([]));
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const base: Command[] = [
      {
        id: 'home',
        label: 'Дашборд',
        group: 'Навигация',
        icon: <Database size={14} />,
        run: () => { navigate('/'); onClose(); },
      },
      {
        id: 'new-rag',
        label: 'Создать новый RAG',
        group: 'Действия',
        icon: <Plus size={14} />,
        hint: 'N',
        run: () => { navigate('/'); onOpenCreateRag(); onClose(); },
      },
      {
        id: 'toggle-theme',
        label: theme === 'dark' ? 'Светлая тема' : 'Тёмная тема',
        group: 'Внешний вид',
        icon: theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />,
        run: () => { toggleTheme(); onClose(); },
      },
    ];
    if (session?.user.role === 'admin') {
      base.push({
        id: 'admin-users',
        label: 'Пользователи (admin)',
        group: 'Навигация',
        icon: <Users size={14} />,
        run: () => { navigate('/admin/users'); onClose(); },
      });
    }
    for (const r of rags) {
      base.push({
        id: `rag-${r.id}`,
        label: r.name,
        group: 'Перейти к RAG',
        icon: <Database size={14} />,
        hint: r.status,
        keywords: r.description || '',
        run: () => { navigate(`/rag/${r.id}`); onClose(); },
      });
      if (r.status === 'ready') {
        base.push({
          id: `chat-${r.id}`,
          label: `Чат · ${r.name}`,
          group: 'Чат с агентом',
          icon: <Bot size={14} />,
          run: () => { navigate(`/rag/${r.id}/chat`); onClose(); },
        });
      }
    }
    base.push({
      id: 'logout',
      label: 'Выйти',
      group: 'Сессия',
      icon: <LogOut size={14} />,
      run: () => { logout(); navigate('/login', { replace: true }); onClose(); },
    });
    return base;
  }, [rags, session, theme, navigate, onClose, onOpenCreateRag, toggleTheme, logout]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) =>
      c.label.toLowerCase().includes(q) ||
      c.group.toLowerCase().includes(q) ||
      (c.keywords || '').toLowerCase().includes(q),
    );
  }, [commands, query]);

  useEffect(() => { setActive(0); }, [query]);

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      filtered[active]?.run();
    } else if (e.key === 'Escape') {
      onClose();
    }
  }

  if (!open) return null;

  let lastGroup = '';
  return (
    <div className="cmdk-backdrop" onClick={onClose}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Command palette">
        <div style={{ position: 'relative' }}>
          <Search
            size={14}
            style={{ position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)' }}
          />
          <input
            ref={inputRef}
            className="cmdk-input"
            placeholder="Команда или RAG…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKey}
            style={{ paddingLeft: 38 }}
          />
        </div>
        <div className="cmdk-list">
          {filtered.length === 0 && (
            <div style={{ padding: 16, color: 'var(--muted)', fontSize: 13 }}>
              Ничего не найдено.
            </div>
          )}
          {filtered.map((c, i) => {
            const showGroup = c.group !== lastGroup;
            lastGroup = c.group;
            return (
              <div key={c.id}>
                {showGroup && <div className="cmdk-group">{c.group}</div>}
                <div
                  className={`cmdk-item${i === active ? ' active' : ''}`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => c.run()}
                >
                  <span className="cmdk-icon">{c.icon}</span>
                  <span>{c.label}</span>
                  {c.hint && <span className="cmdk-meta">{c.hint}</span>}
                </div>
              </div>
            );
          })}
        </div>
        <div
          style={{
            padding: '8px 14px',
            borderTop: '1px solid var(--border)',
            display: 'flex',
            gap: 12,
            color: 'var(--muted)',
            fontSize: 11,
          }}
        >
          <span><span className="kbd">↑</span> <span className="kbd">↓</span> навигация</span>
          <span><span className="kbd">↵</span> выбрать</span>
          <span><span className="kbd">esc</span> закрыть</span>
        </div>
      </div>
    </div>
  );
}
