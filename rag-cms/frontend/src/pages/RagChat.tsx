import {
  ArrowLeft,
  Bot,
  Copy,
  ExternalLink,
  FileText,
  Loader2,
  MessageSquarePlus,
  Pencil,
  Quote,
  Send,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useParams } from 'react-router-dom';
import {
  api,
  type AgentCitation,
  type AgentEvent,
  type ChatSessionDetail,
  type ChatSessionRun,
  type ChatSessionSummary,
  type Rag,
} from '../api';
import MarkdownAnswer from '../components/MarkdownAnswer';
import { useToast } from '../ToastContext';

const TERMINAL = new Set(['succeeded', 'escalated', 'failed']);

export default function RagChat() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const [rag, setRag] = useState<Rag | null>(null);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [active, setActive] = useState<ChatSessionDetail | null>(null);
  const [runEvents, setRunEvents] = useState<Record<string, AgentEvent[]>>({});
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const esRefs = useRef<Map<string, EventSource>>(new Map());
  const threadBottom = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  async function loadRag() {
    if (!id) return;
    try {
      setRag(await api.getRag(id));
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function loadSessions() {
    if (!id) return;
    try {
      setSessions(await api.listChatSessions(id));
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function openSession(sid: string) {
    if (!id) return;
    try {
      const detail = await api.getChatSession(id, sid);
      setActive(detail);
      setRunEvents({});
      // subscribe to any still-running runs in this session
      for (const r of detail.runs) {
        if (!TERMINAL.has(r.status) && r.stream_token) {
          subscribeRunStream(r.id, r.stream_token);
        }
      }
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  function subscribeRunStream(runId: string, token: string) {
    if (!id) return;
    if (esRefs.current.has(runId)) return;
    const url = `/api/rags/${id}/agent/runs/${runId}/stream?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    esRefs.current.set(runId, es);
    es.onmessage = (m) => {
      try {
        const ev = JSON.parse(m.data) as AgentEvent;
        if (ev.type === 'stream_end') {
          es.close();
          esRefs.current.delete(runId);
          void refreshActiveSession();
          return;
        }
        setRunEvents((prev) => {
          const cur = prev[runId] || [];
          return { ...prev, [runId]: [...cur, ev] };
        });
      } catch {/* ignore */}
    };
    es.onerror = () => {
      es.close();
      esRefs.current.delete(runId);
      void refreshActiveSession();
    };
  }

  async function refreshActiveSession() {
    if (!id || !active) return;
    try {
      const detail = await api.getChatSession(id, active.id);
      setActive(detail);
      await loadSessions();
    } catch {/* ignore */}
  }

  useEffect(() => {
    void loadRag();
    void loadSessions();
    return () => {
      for (const es of esRefs.current.values()) es.close();
      esRefs.current.clear();
    };
  }, [id]);

  useEffect(() => {
    threadBottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [active?.runs.length, Object.values(runEvents).map((e) => e.length).join(',')]);

  // Fallback poll: while there's an active run in the open session whose status
  // is still pending, re-fetch the session every 2s. SSE may drop silently
  // (proxy, sleep, retry exhausted) — without this the UI would lie forever.
  useEffect(() => {
    if (!id || !active) return;
    const hasActive = active.runs.some((r) => !TERMINAL.has(r.status));
    if (!hasActive) return;
    const t = setInterval(() => {
      void (async () => {
        try {
          const detail = await api.getChatSession(id, active.id);
          setActive(detail);
        } catch {/* ignore — will retry on next tick */}
      })();
    }, 2000);
    return () => clearInterval(t);
  }, [id, active?.id, active?.runs.map((r) => r.status).join(',')]);

  function newChat() {
    setActive(null);
    setRunEvents({});
    setQuery('');
    composerRef.current?.focus();
  }

  async function onAsk(e: React.FormEvent) {
    e.preventDefault();
    if (!id || !query.trim()) return;
    if (rag && rag.status !== 'ready') {
      toast.warning('Дождитесь окончания индексации (status=ready).');
      return;
    }
    setBusy(true);
    const q = query.trim();
    try {
      const started = await api.startAgentRun(id, q, active?.id);
      setQuery('');
      // Refresh session detail so we see the new run + ensure sessions list updates
      const detail = await api.getChatSession(id, started.session_id);
      setActive(detail);
      await loadSessions();
      subscribeRunStream(started.id, started.stream_token);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onComposerKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void onAsk(e as unknown as React.FormEvent);
    }
  }

  async function onRenameSession(s: ChatSessionSummary) {
    if (!id) return;
    const title = prompt('Новое название чата:', s.title);
    if (!title || title.trim() === s.title) return;
    try {
      await api.renameChatSession(id, s.id, title.trim());
      await loadSessions();
      if (active?.id === s.id) await refreshActiveSession();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function onDeleteSession(s: ChatSessionSummary) {
    if (!id) return;
    if (!confirm(`Удалить чат "${s.title}" со всеми сообщениями?`)) return;
    try {
      await api.deleteChatSession(id, s.id);
      if (active?.id === s.id) setActive(null);
      await loadSessions();
      toast.success('Чат удалён');
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  // Render the chat through a portal so it anchors to document.body, with
  // inline-styles so no CSS rule (extension, cached file, weird containing
  // block) can override the layout.
  return createPortal(
    <div
      className="chat-page"
      style={{
        position: 'fixed',
        top: 56,
        left: 64,
        right: 0,
        bottom: 0,
        display: 'flex',
        flexDirection: 'column',
        padding: '18px 28px',
        gap: 12,
        boxSizing: 'border-box',
        overflow: 'hidden',
        background: 'var(--bg)',
        zIndex: 1,
      }}
    >
      <div className="chat-page-head">
        <Link
          to={`/rag/${id}`}
          className="row"
          style={{ color: 'var(--muted)', fontSize: 12.5, marginBottom: 4 }}
        >
          <ArrowLeft size={14} /> {rag?.name ?? '…'}
        </Link>
        <div className="spread">
          <h1 style={{ margin: 0, fontSize: 22 }}>Чат с агентом</h1>
          <button className="ghost" onClick={newChat}>
            <MessageSquarePlus size={14} /> Новый чат
          </button>
        </div>
      </div>

      <div className="chat-shell">
        <aside className="chat-history">
          <div className="chat-history-head">Чаты</div>
          <div className="chat-history-list">
            {sessions.length === 0 ? (
              <div className="subtle" style={{ padding: 12 }}>Чатов ещё нет.</div>
            ) : (
              sessions.map((s) => (
                <div
                  key={s.id}
                  className={`chat-history-item${active?.id === s.id ? ' active' : ''}`}
                  onClick={() => void openSession(s.id)}
                >
                  <div className="q">{s.title}</div>
                  <div className="row gap-4" style={{ marginTop: 6, fontSize: 11, color: 'var(--muted)' }}>
                    <span>{s.last_run_at ? new Date(s.last_run_at).toLocaleString() : '—'}</span>
                    <span className="spacer" style={{ flex: 1 }} />
                    <button
                      className="icon"
                      style={{ width: 22, height: 22 }}
                      title="Переименовать"
                      onClick={(e) => { e.stopPropagation(); void onRenameSession(s); }}
                    >
                      <Pencil size={11} />
                    </button>
                    <button
                      className="icon"
                      style={{ width: 22, height: 22 }}
                      title="Удалить чат"
                      onClick={(e) => { e.stopPropagation(); void onDeleteSession(s); }}
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </aside>

        <section className="chat-main">
          <div className="chat-thread">
            {!active ? (
              <EmptyState onSeed={(q) => { setQuery(q); composerRef.current?.focus(); }} />
            ) : active.runs.length === 0 ? (
              <div className="subtle" style={{ padding: 16, textAlign: 'center' }}>
                Чат пустой — задайте первый вопрос.
              </div>
            ) : (
              active.runs.map((r) => (
                <RunTurn key={r.id} run={r} events={runEvents[r.id] || []} />
              ))
            )}
            <div ref={threadBottom} />
          </div>

          {rag?.role === 'member' && rag?.member_status === 'revoked' ? (
            <div
              className="chat-composer"
              style={{
                color: 'var(--danger)',
                fontSize: 13,
                justifyContent: 'center',
                background: 'var(--danger-soft)',
              }}
            >
              Ваш доступ к этому RAG отозван. Новые вопросы недоступны, но старая история открыта.
            </div>
          ) : (
            <form className="chat-composer" onSubmit={onAsk}>
              <textarea
                ref={composerRef}
                placeholder={
                  active
                    ? 'Продолжите этот чат… (Ctrl/⌘+Enter — отправить)'
                    : 'Начните новый чат… (Ctrl/⌘+Enter — отправить)'
                }
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onComposerKey}
                disabled={busy}
              />
              <button type="submit" disabled={busy || !query.trim()}>
                <Send size={15} />
              </button>
            </form>
          )}
        </section>
      </div>
    </div>,
    document.body,
  );
}

function RunTurn({ run, events }: { run: ChatSessionRun; events: AgentEvent[] }) {
  const { id: ragId } = useParams<{ id: string }>();
  const toast = useToast();
  const [preview, setPreview] = useState<AgentCitation | null>(null);
  function copyAnswer() {
    if (!run.answer) return;
    void navigator.clipboard.writeText(run.answer);
    toast.success('Ответ скопирован');
  }
  function copyWithSources() {
    if (!run.answer) return;
    const lines = [run.answer, ''];
    if (run.citations.length > 0) {
      lines.push('Источники:');
      run.citations.forEach((c, i) => {
        const page = c.page_start
          ? c.page_end === c.page_start
            ? `стр. ${c.page_start}`
            : `стр. ${c.page_start}–${c.page_end}`
          : '';
        lines.push(`[${i + 1}] ${c.filename}${page ? ' · ' + page : ''}`);
        lines.push(`    «${c.quote}»`);
      });
    }
    void navigator.clipboard.writeText(lines.join('\n'));
    toast.success('Ответ + источники скопированы');
  }

  const running = !TERMINAL.has(run.status);

  return (
    <div className="turn">
      {/* User message — compact bubble on the right */}
      <div className="msg msg-user">
        <div className="msg-bubble">{run.query}</div>
      </div>

      {/* Assistant message — no bubble, text flows; bot avatar on the left */}
      <div className="msg msg-assistant">
        <div className="msg-avatar"><Bot size={16} /></div>
        <div className="msg-body">
          <div className="msg-head">
            <span className="msg-author">Агент</span>
            <span className={`badge ${run.status}`}>{run.status}</span>
            {run.confidence !== null && (
              <span className="mono subtle">conf {run.confidence.toFixed(2)}</span>
            )}
            <span className="spacer" style={{ flex: 1 }} />
            {run.answer && (
              <>
                <button className="icon" title="Скопировать ответ" onClick={copyAnswer} style={{ width: 26, height: 26 }}>
                  <Copy size={12} />
                </button>
                <button
                  className="subtle sm"
                  title="Скопировать ответ с источниками"
                  onClick={copyWithSources}
                  style={{ padding: '4px 8px', fontSize: 11 }}
                >
                  <Copy size={11} /> +источники
                </button>
              </>
            )}
          </div>

          {run.answer ? (
            <MarkdownAnswer
              text={run.answer}
              citations={run.citations}
              onCitationClick={(c) => setPreview(c)}
            />
          ) : running ? (
            <AgentProgress run={run} events={events} />
          ) : (
            <div style={{ color: 'var(--danger)' }}>—</div>
          )}

          {events.length > 0 && <EventLog events={events} compact={!!run.answer} />}

          {run.citations && run.citations.length > 0 && (
            <div className="citations">
              {run.citations.map((c, i) => (
                <CitationCard
                  key={c.chunk_id}
                  ragId={ragId}
                  runId={run.id}
                  citation={c}
                  index={i + 1}
                  onPreview={() => setPreview(c)}
                />
              ))}
            </div>
          )}

          {preview && ragId && (
            <FilePreviewModal
              ragId={ragId}
              fileId={preview.file_id}
              filename={preview.filename}
              page={preview.page_start ?? null}
              onClose={() => setPreview(null)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function CitationCard({
  ragId,
  runId,
  citation,
  index,
  onPreview,
}: {
  ragId: string | undefined;
  runId: string;
  citation: AgentCitation;
  index: number;
  onPreview: () => void;
}) {
  const pageStr = citation.page_start
    ? citation.page_end === citation.page_start
      ? `стр. ${citation.page_start}`
      : `стр. ${citation.page_start}–${citation.page_end}`
    : null;

  return (
    <div id={`cite-${runId}-${index}`} className="citation">
      <div className="cite-meta">
        <Quote size={11} style={{ display: 'inline', marginRight: 4 }} />
        <strong>[{index}]</strong>{' '}
        <button
          className="cite-filename"
          onClick={() => { if (ragId) onPreview(); }}
          title="Открыть документ"
        >
          {citation.filename}
        </button>
        {pageStr && <> · {pageStr}</>}
      </div>
    </div>
  );
}

function FilePreviewModal({
  ragId,
  fileId,
  filename,
  page,
  onClose,
}: {
  ragId: string;
  fileId: string;
  filename: string;
  page: number | null;
  onClose: () => void;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [mime, setMime] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let urlToRevoke: string | null = null;
    void (async () => {
      try {
        const { url, mime } = await api.fetchFileBlob(ragId, fileId);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        urlToRevoke = url;
        setBlobUrl(url);
        setMime(mime);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
      if (urlToRevoke) URL.revokeObjectURL(urlToRevoke);
    };
  }, [ragId, fileId]);

  // Escape to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const isPdf = mime === 'application/pdf' || filename.toLowerCase().endsWith('.pdf');
  const iframeSrc = blobUrl && isPdf && page
    ? `${blobUrl}#page=${page}&zoom=page-fit`
    : blobUrl ?? '';

  const content = (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal file-preview-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head row gap-8" style={{ alignItems: 'center' }}>
          <FileText size={16} style={{ color: 'var(--accent-2)' }} />
          <div style={{ fontWeight: 600, fontSize: 14, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={filename}>
            {filename}
          </div>
          {page && <span className="badge accent">стр. {page}</span>}
          <span className="spacer" style={{ flex: 1 }} />
          {blobUrl && (
            <a
              className="subtle sm"
              href={blobUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{ padding: '4px 8px', fontSize: 11, textDecoration: 'none' }}
              title="Открыть в новой вкладке"
            >
              <ExternalLink size={11} /> В новой вкладке
            </a>
          )}
          <button className="icon" onClick={onClose} title="Закрыть (Esc)">
            <X size={14} />
          </button>
        </div>
        <div className="file-preview-body">
          {error ? (
            <div style={{ padding: 24, color: 'var(--danger)' }}>Ошибка: {error}</div>
          ) : !blobUrl ? (
            <div className="row gap-8" style={{ padding: 24, color: 'var(--muted)' }}>
              <Loader2 size={14} className="spinner" /> Загружаю документ…
            </div>
          ) : isPdf ? (
            <iframe
              src={iframeSrc}
              title={filename}
              style={{ width: '100%', height: '100%', border: 0 }}
            />
          ) : (
            <div style={{ padding: 24 }}>
              <p className="subtle" style={{ marginBottom: 12 }}>
                Превью для этого типа файла не поддерживается в браузере.
              </p>
              <a
                href={blobUrl}
                download={filename}
                style={{ color: 'var(--accent-2)' }}
              >
                Скачать {filename}
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(content, document.body);
}

function truncate(s: string, n: number): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

/** Human-readable label for the agent's *current* activity, derived from the latest event. */
function describeCurrent(events: AgentEvent[]): string {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    const p = ev.payload as Record<string, unknown>;
    switch (ev.type) {
      case 'thought':
        return `Думаю: ${truncate(String(p.thought ?? ''), 110)}`;
      case 'tool_call': {
        const args = p.args as Record<string, unknown> | undefined;
        const q = args && typeof args.query === 'string' ? `"${truncate(args.query, 50)}"` : '';
        return `Вызываю ${String(p.tool)}${q ? ' ' + q : '…'}`;
      }
      case 'observation':
        return `Получил ответ от ${String(p.tool)}${p.summary ? ': ' + truncate(String(p.summary), 80) : ''}`;
      case 'grounding_report':
        return `Проверяю цитаты: ${p.grounded}/${p.total} подтверждены`;
      case 'final_rejected':
        return 'Финальный ответ отклонён без цитат — переделываю';
      case 'tool_blocked':
        return `Заблокировал повтор: ${String(p.tool)}`;
      case 'router_decision':
        return `Маршрут: ${String(p.kind)} → ${String(p.tool)}`;
      case 'run_started':
        return `Стартую (бюджет: ${p.max_steps} шагов)`;
    }
  }
  return 'Подключаюсь к агенту…';
}

function AgentProgress({ run, events }: { run: ChatSessionRun; events: AgentEvent[] }) {
  let step = 0;
  for (let i = events.length - 1; i >= 0; i--) {
    const p = events[i].payload as { step?: number };
    if (typeof p.step === 'number') { step = p.step; break; }
  }
  const max = run.max_steps || 40;

  let pool: number | null = null;
  for (let i = events.length - 1; i >= 0; i--) {
    const p = events[i].payload as { pool_size?: number };
    if (typeof p.pool_size === 'number') { pool = p.pool_size; break; }
  }

  const current = describeCurrent(events);

  // Until the first step arrives, show a warm-up state with an indeterminate
  // shimmer — a 0/40 bar reads as "stuck" when really we're just waiting for
  // the first LLM call (10–20s on gpt-oss-120b via OpenRouter).
  if (step === 0) {
    return (
      <div className="ingest-panel" style={{ marginTop: 4 }}>
        <div className="ingest-head">
          <div className="row gap-8" style={{ alignItems: 'center' }}>
            <Loader2 size={14} className="spinner" style={{ color: 'var(--accent-2)' }} />
            <span style={{ fontWeight: 600, fontSize: 13.5 }}>Запускаю агента…</span>
          </div>
        </div>
        <div className="ingest-bar indeterminate" />
        <div className="subtle" style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.5 }}>
          Первый LLM-вызов обычно ~10–20 секунд. Бар появится с первого шага.
        </div>
      </div>
    );
  }

  const pct = Math.min(100, Math.round((step / Math.max(1, max)) * 100));

  return (
    <div className="ingest-panel" style={{ marginTop: 4 }}>
      <div className="ingest-head">
        <div className="row gap-8" style={{ alignItems: 'center' }}>
          <Loader2 size={14} className="spinner" style={{ color: 'var(--accent-2)' }} />
          <span style={{ fontWeight: 600, fontSize: 13.5 }}>
            Шаг {step} <span className="subtle">из {max}</span>
          </span>
          {pool !== null && (
            <span className="badge accent" title="Найдено релевантных фрагментов">
              pool {pool}
            </span>
          )}
        </div>
        <span className="spacer" style={{ flex: 1 }} />
        <span className="subtle mono" style={{ fontSize: 11.5 }}>{pct}%</span>
      </div>
      <div className="ingest-bar">
        <div className="ingest-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="subtle" style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.5 }}>
        {current}
      </div>
    </div>
  );
}

function EventLog({ events, compact }: { events: AgentEvent[]; compact: boolean }) {
  const [open, setOpen] = useState(!compact);
  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary style={{ cursor: 'pointer', color: 'var(--muted)', fontSize: 12, marginTop: 10 }}>
        <Sparkles size={11} style={{ display: 'inline', marginRight: 4 }} />
        Trace · {events.length} шагов
      </summary>
      <div className="event-log">
        {events.map((ev) => (
          <span key={ev.seq} className="ev" style={{ color: rowColor(ev.type) }}>
            <span className="ts">{ev.ts.slice(11, 19)}</span>
            {rowLine(ev)}
          </span>
        ))}
      </div>
    </details>
  );
}

function rowColor(type: string): string {
  switch (type) {
    case 'tool_call':
    case 'router_decision':
    case 'grounding_report':
      return 'var(--accent-2)';
    case 'final_answer':
    case 'run_finished':
      return 'var(--success)';
    case 'parse_error':
    case 'final_rejected':
    case 'tool_blocked':
    case 'budget_exhausted':
    case 'run_failed':
    case 'escalated':
      return 'var(--danger)';
    case 'thought':
      return 'var(--text)';
    default:
      return 'var(--text-dim)';
  }
}

function rowLine(ev: AgentEvent): string {
  const p = ev.payload as Record<string, unknown>;
  switch (ev.type) {
    case 'run_started':
      return `start (max_steps=${p.max_steps})`;
    case 'router_decision':
      return `router: kind=${p.kind} → ${p.tool} (conf ${(p.confidence as number).toFixed(2)}, via ${p.via})`;
    case 'router_failed':
      return `router failed: ${p.error}`;
    case 'thought':
      return `[${p.step}] думаю (${p.kind}): ${p.thought}`;
    case 'tool_call':
      return `[${p.step}] → ${p.tool}(${JSON.stringify(p.args)})`;
    case 'observation':
      return `[${p.step}] ← ${p.tool}: ${p.summary} (pool=${p.pool_size})`;
    case 'grounding_report': {
      const g = p as { grounded: number; total: number; fraction: number; adjusted_confidence: number };
      return `[${p.step}] grounding ${g.grounded}/${g.total} (conf→${g.adjusted_confidence.toFixed(2)})`;
    }
    case 'final_answer':
      return `[${p.step}] ✓ финальный ответ (${(p.citations as unknown[]).length} цитат, conf=${(p.confidence as number).toFixed(2)})`;
    case 'escalated':
      return `[${p.step}] ⚠ эскалация: ${p.reason}`;
    case 'parse_error':
      return `[${p.step}] LLM-вывод не парсится: ${p.error}`;
    case 'tool_blocked':
      return `[${p.step}] заблокирован повтор ${p.tool}: ${p.reason}`;
    case 'budget_exhausted':
      return `[${p.step}] бюджет шагов исчерпан (pool=${p.pool_size})`;
    case 'run_finished':
      return `finished: ${p.status}, ${p.steps_used} шагов, ${p.elapsed_sec}s`;
    case 'run_failed':
      return `run failed: ${p.error}`;
    default:
      return `${ev.type} ${JSON.stringify(ev.payload)}`;
  }
}

function EmptyState({ onSeed }: { onSeed: (q: string) => void }) {
  const seeds = [
    'О чём эти документы? Опиши основные темы.',
    'Перечисли ключевые факты, имена, даты и цифры.',
    'Что в документах самое важное для меня?',
  ];
  return (
    <div style={{ textAlign: 'center', padding: '40px 16px 24px', color: 'var(--muted)' }}>
      <div className="empty-halo" style={{ width: 60, height: 60 }}>
        <Sparkles size={26} style={{ color: 'white' }} />
      </div>
      <h2 style={{ marginTop: 18, color: 'var(--text)' }}>С чего начнём?</h2>
      <p style={{ marginTop: 4 }}>
        Задайте любой вопрос по загруженным документам. Каждый чат сохраняет историю — можно продолжать диалог.
      </p>
      <div className="col gap-8" style={{ maxWidth: 540, margin: '24px auto 0' }}>
        {seeds.map((s) => (
          <button key={s} className="ghost" onClick={() => onSeed(s)} style={{ justifyContent: 'flex-start' }}>
            <Sparkles size={13} /> {s}
          </button>
        ))}
      </div>
    </div>
  );
}

