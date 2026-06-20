import { AlertCircle, ArrowRight, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { api } from '../api';
import { BrandMark } from '../App';
import { useAuth } from '../AuthContext';

export default function Login() {
  const [bootstrapOpen, setBootstrapOpen] = useState<boolean | null>(null);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const auth = useAuth();
  const location = useLocation();

  useEffect(() => {
    void api
      .registrationStatus()
      .then((s) => {
        setBootstrapOpen(s.open);
        if (s.open) setMode('register');
      })
      .catch(() => setBootstrapOpen(false));
  }, []);

  if (auth.session) {
    const next = (location.state as { from?: string } | null)?.from ?? '/';
    return <Navigate to={next} replace />;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      const res =
        mode === 'login'
          ? await api.login(email.trim(), password)
          : await api.register(email.trim(), password);
      auth.login(res.access_token, res.user);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const showRegister = bootstrapOpen === true;
  const isRegister = mode === 'register';

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-brand">
          <BrandMark />
          <span>rag-cms</span>
        </div>
        <div className="card">
          <div className="col gap-12">
            <div>
              <h1 style={{ marginBottom: 4 }}>
                {isRegister ? 'Создать аккаунт администратора' : 'Вход в платформу'}
              </h1>
              <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                {isRegister
                  ? 'На сервере ещё нет пользователей — этот аккаунт получит роль admin.'
                  : 'Введите email и пароль, выданный администратором.'}
              </p>
            </div>

            <form onSubmit={submit} className="col gap-12">
              <div className="field">
                <label htmlFor="email">Email</label>
                <input
                  id="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={busy}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="password">Пароль</label>
                <input
                  id="password"
                  type="password"
                  autoComplete={isRegister ? 'new-password' : 'current-password'}
                  placeholder={isRegister ? 'минимум 8 символов' : '••••••••'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={busy}
                  required
                />
              </div>
              {error && (
                <div className="row" style={{ color: 'var(--danger)', fontSize: 12.5 }}>
                  <AlertCircle size={14} /> {error}
                </div>
              )}
              <button
                type="submit"
                className="block"
                disabled={busy || !email.trim() || password.length < (isRegister ? 8 : 1)}
              >
                {busy ? 'Подождите…' : isRegister ? 'Создать админа' : 'Войти'}
                {!busy && <ArrowRight size={15} />}
              </button>
            </form>

            <div style={{ height: 1, background: 'var(--border)' }} />

            <div className="subtle" style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <ShieldCheck size={14} style={{ marginTop: 1, flexShrink: 0, color: 'var(--success)' }} />
              {showRegister ? (
                <span>
                  Публичная регистрация закроется сразу после создания первого
                  администратора. Дальше новых пользователей заводит admin.
                </span>
              ) : bootstrapOpen === false ? (
                <span>Регистрация закрыта. Запросите аккаунт у администратора.</span>
              ) : (
                <span>Проверка статуса регистрации…</span>
              )}
            </div>

            {showRegister && (
              <div className="subtle" style={{ textAlign: 'center' }}>
                {isRegister ? (
                  <>
                    Уже есть аккаунт?{' '}
                    <a href="#" onClick={(e) => { e.preventDefault(); setMode('login'); setError(null); }}>
                      Войти
                    </a>
                  </>
                ) : (
                  <>
                    Это первый запуск платформы?{' '}
                    <a href="#" onClick={(e) => { e.preventDefault(); setMode('register'); setError(null); }}>
                      Создать первого администратора
                    </a>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
