const LS_TOKEN = 'ragcms.token';
const LS_USER = 'ragcms.user';

export interface AuthUser {
  id: string;
  email: string;
  role: 'user' | 'admin';
  is_active: boolean;
  created_at: string;
}

export interface AuthSession {
  token: string;
  user: AuthUser;
}

export function loadSession(): AuthSession | null {
  const token = localStorage.getItem(LS_TOKEN);
  const raw = localStorage.getItem(LS_USER);
  if (!token || !raw) return null;
  try {
    return { token, user: JSON.parse(raw) as AuthUser };
  } catch {
    return null;
  }
}

export function saveSession(session: AuthSession): void {
  localStorage.setItem(LS_TOKEN, session.token);
  localStorage.setItem(LS_USER, JSON.stringify(session.user));
}

export function clearSession(): void {
  localStorage.removeItem(LS_TOKEN);
  localStorage.removeItem(LS_USER);
}

export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem(LS_TOKEN);
  return token ? { Authorization: `Bearer ${token}` } : {};
}
