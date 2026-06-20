import {
  AlertCircle,
  KeyRound,
  Plus,
  ShieldCheck,
  Trash2,
  UserCog,
  UserMinus,
  UserPlus,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../ToastContext';
import type { AuthUser } from '../auth';

export default function AdminUsers() {
  const { session } = useAuth();
  const toast = useToast();
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<'user' | 'admin'>('user');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setUsers(await api.listUsers());
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  if (!session) return <Navigate to="/login" replace />;
  if (session.user.role !== 'admin') {
    return (
      <div className="card">
        <div className="row" style={{ color: 'var(--danger)' }}>
          <AlertCircle size={16} /> Доступ только для администраторов.
        </div>
      </div>
    );
  }

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || password.length < 8) return;
    setBusy(true);
    try {
      const u = await api.createUser(email.trim(), password, role);
      setEmail(''); setPassword(''); setRole('user'); setCreateOpen(false);
      toast.success(`Пользователь ${u.email} создан (${u.role})`);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onToggleActive(u: AuthUser) {
    try {
      await api.updateUser(u.id, { is_active: !u.is_active });
      toast.success(u.is_active ? `${u.email} заблокирован` : `${u.email} разблокирован`);
      await refresh();
    } catch (e) { toast.error((e as Error).message); }
  }
  async function onPromote(u: AuthUser) {
    const next = u.role === 'admin' ? 'user' : 'admin';
    if (!confirm(`Изменить роль ${u.email} на ${next}?`)) return;
    try {
      await api.updateUser(u.id, { role: next });
      toast.success(`Роль ${u.email} → ${next}`);
      await refresh();
    } catch (e) { toast.error((e as Error).message); }
  }
  async function onResetPassword(u: AuthUser) {
    const pwd = prompt(`Новый пароль для ${u.email} (минимум 8):`);
    if (!pwd || pwd.length < 8) return;
    try {
      await api.updateUser(u.id, { password: pwd });
      toast.success('Пароль обновлён');
    } catch (e) { toast.error((e as Error).message); }
  }
  async function onDelete(u: AuthUser) {
    if (!confirm(`Удалить ${u.email}? Все RAG'и пользователя будут удалены.`)) return;
    try {
      await api.deleteUser(u.id);
      toast.success(`${u.email} удалён`);
      await refresh();
    } catch (e) { toast.error((e as Error).message); }
  }

  const admins = users.filter((u) => u.role === 'admin').length;
  const active = users.filter((u) => u.is_active).length;

  return (
    <div className="col" style={{ gap: 20 }}>
      <div className="hero-block">
        <div className="hero-orb" />
        <div className="spread" style={{ position: 'relative' }}>
          <div>
            <h1>Пользователи</h1>
            <p className="muted" style={{ margin: 0, fontSize: 13.5, maxWidth: 620, lineHeight: 1.55 }}>
              Только администраторы создают аккаунты. Публичная регистрация закрыта.
            </p>
          </div>
          <button onClick={() => setCreateOpen(true)}>
            <Plus size={16} /> Добавить пользователя
          </button>
        </div>
      </div>

      <div className="grid cols-3">
        <Kpi label="Всего" value={users.length} />
        <Kpi label="Активны" value={active} tone="success" />
        <Kpi label="Админов" value={admins} tone="accent" icon={<ShieldCheck size={14} />} />
      </div>

      <div className="card flush">
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Роль</th>
                <th>Статус</th>
                <th>Создан</th>
                <th style={{ textAlign: 'right' }}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const isMe = u.id === session.user.id;
                return (
                  <tr key={u.id}>
                    <td>
                      <div style={{ fontWeight: 500 }}>{u.email}</div>
                      {isMe && <span className="badge accent" style={{ marginTop: 2 }}>вы</span>}
                    </td>
                    <td>
                      <span className={`badge ${u.role === 'admin' ? 'accent' : ''}`}>
                        {u.role === 'admin' && <ShieldCheck size={10} />}
                        {u.role}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${u.is_active ? 'success' : 'danger'}`}>
                        {u.is_active ? 'active' : 'disabled'}
                      </span>
                    </td>
                    <td className="mono" style={{ color: 'var(--text-dim)' }}>
                      {new Date(u.created_at).toLocaleString()}
                    </td>
                    <td>
                      <div className="actions">
                        <button className="icon" title="Сменить пароль" onClick={() => onResetPassword(u)}>
                          <KeyRound size={14} />
                        </button>
                        <button
                          className="icon"
                          title={u.role === 'admin' ? 'Снять admin' : 'Сделать admin'}
                          onClick={() => onPromote(u)}
                        >
                          <UserCog size={14} />
                        </button>
                        <button
                          className="icon"
                          title={u.is_active ? 'Заблокировать' : 'Разблокировать'}
                          onClick={() => onToggleActive(u)}
                        >
                          <UserMinus size={14} />
                        </button>
                        {!isMe && (
                          <button className="icon" title="Удалить" onClick={() => onDelete(u)}>
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {createOpen && (
        <div className="modal-backdrop" onClick={() => setCreateOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h2 style={{ margin: 0 }}>
                <UserPlus size={16} style={{ marginRight: 8, verticalAlign: -2 }} />
                Новый пользователь
              </h2>
              <div className="subtle" style={{ marginTop: 4 }}>
                Выдайте временный пароль — пользователь может сменить его позже.
              </div>
            </div>
            <form onSubmit={onCreate}>
              <div className="modal-body col gap-12">
                <div className="field">
                  <label htmlFor="u-email">Email</label>
                  <input id="u-email" type="email" placeholder="user@company.com" value={email}
                    onChange={(e) => setEmail(e.target.value)} autoFocus />
                </div>
                <div className="field">
                  <label htmlFor="u-pwd">Временный пароль</label>
                  <input id="u-pwd" type="text" placeholder="минимум 8 символов" value={password}
                    onChange={(e) => setPassword(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="u-role">Роль</label>
                  <select id="u-role" value={role} onChange={(e) => setRole(e.target.value as 'user' | 'admin')}>
                    <option value="user">user</option>
                    <option value="admin">admin</option>
                  </select>
                </div>
              </div>
              <div className="modal-foot">
                <button type="button" className="ghost" onClick={() => setCreateOpen(false)}>Отмена</button>
                <button type="submit" disabled={busy || !email.trim() || password.length < 8}>
                  {busy ? 'Создание…' : 'Создать'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function Kpi({
  label, value, tone, icon,
}: { label: string; value: number; tone?: 'accent' | 'success' | 'warning'; icon?: React.ReactNode }) {
  return (
    <div className={`kpi${tone ? ` ${tone}` : ''}`}>
      <div className="row gap-8">
        <span className="label">{label}</span>
        {icon && <span style={{ color: 'var(--muted)' }}>{icon}</span>}
      </div>
      <div className="value">{value}</div>
    </div>
  );
}
