import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { AlertCircle, CheckCircle2, Info, X, AlertTriangle } from 'lucide-react';

export type ToastKind = 'success' | 'error' | 'info' | 'warning';

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  ttl: number;
}

interface ToastState {
  push: (kind: ToastKind, message: string, ttl?: number) => void;
  success: (msg: string, ttl?: number) => void;
  error: (msg: string, ttl?: number) => void;
  info: (msg: string, ttl?: number) => void;
  warning: (msg: string, ttl?: number) => void;
}

const Ctx = createContext<ToastState | null>(null);

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((kind: ToastKind, message: string, ttl = 5000) => {
    const id = nextId++;
    setToasts((prev) => [...prev, { id, kind, message, ttl }]);
    if (ttl > 0) {
      window.setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, ttl);
    }
  }, []);

  const value = useMemo<ToastState>(
    () => ({
      push,
      success: (m, ttl) => push('success', m, ttl),
      error: (m, ttl) => push('error', m, ttl ?? 8000),
      info: (m, ttl) => push('info', m, ttl),
      warning: (m, ttl) => push('warning', m, ttl),
    }),
    [push],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {toasts.map((t) => (
          <ToastView key={t.id} t={t} onClose={() => dismiss(t.id)} />
        ))}
      </div>
    </Ctx.Provider>
  );
}

function ToastView({ t, onClose }: { t: Toast; onClose: () => void }) {
  const Icon = t.kind === 'success' ? CheckCircle2
    : t.kind === 'error' ? AlertCircle
    : t.kind === 'warning' ? AlertTriangle
    : Info;
  const color =
    t.kind === 'success' ? 'var(--success)' :
    t.kind === 'error' ? 'var(--danger)' :
    t.kind === 'warning' ? 'var(--warning)' :
    'var(--accent-2)';
  return (
    <div className={`toast ${t.kind}`} role="status">
      <Icon size={16} style={{ color, flexShrink: 0, marginTop: 1 }} />
      <div className="toast-msg">{t.message}</div>
      <button className="toast-close" onClick={onClose} aria-label="Закрыть">
        <X size={14} />
      </button>
    </div>
  );
}

export function useToast(): ToastState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useToast must be inside <ToastProvider>');
  return v;
}

/** Best-effort access for non-React code (e.g. fetch wrapper).
 *  We expose a tiny singleton bridge through window so api.ts can poke toasts. */
declare global {
  interface Window { __ragcmsToast?: ToastState | null }
}

export function BindToastBridge() {
  const t = useToast();
  useEffect(() => {
    window.__ragcmsToast = t;
    return () => { window.__ragcmsToast = null; };
  }, [t]);
  return null;
}
