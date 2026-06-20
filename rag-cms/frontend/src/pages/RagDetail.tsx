import {
  ArrowLeft,
  Bot,
  ChevronDown,
  ChevronRight,
  FileText,
  Globe,
  Loader2,
  Play,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  UserPlus,
  Users,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  api,
  type AgentEvent,
  type FileItem,
  type IngestRun,
  type Member,
  type Rag,
  type SearchHit,
} from '../api';
import FileDropzone from '../components/FileDropzone';
import UploadQueue, { type UploadItem } from '../components/UploadQueue';
import { useToast } from '../ToastContext';

type Tab = 'files' | 'search';

const UPLOAD_CONCURRENCY = 3;
let _uploadCounter = 0;
const nextUploadId = () => `u${++_uploadCounter}-${Date.now()}`;

export default function RagDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const [rag, setRag] = useState<Rag | null>(null);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [run, setRun] = useState<IngestRun | null>(null);
  const [tab, setTab] = useState<Tab>('files');

  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'hybrid' | 'dense' | 'sparse'>('hybrid');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [busy, setBusy] = useState(false);

  // Upload UX state
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [autoIndex, setAutoIndex] = useState<boolean>(() => {
    return localStorage.getItem('ragcms.autoIndex') !== '0';
  });
  const wasAnyUploading = useRef(false);

  // Ingest event stream
  const [ingestEvents, setIngestEvents] = useState<AgentEvent[]>([]);
  const ingestEsRef = useRef<EventSource | null>(null);

  // Files-table UX state
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [membersOpen, setMembersOpen] = useState(false);
  const role = rag?.role ?? 'owner';
  const canManage = role === 'owner' || role === 'admin';
  const canAsk = role === 'owner' || role === 'admin' || rag?.member_status === 'active';

  const visibleFiles = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? files.filter((f) => f.filename.toLowerCase().includes(q)) : files;
  }, [files, filter]);
  const visibleFileIds = useMemo(() => visibleFiles.map((f) => f.id), [visibleFiles]);

  async function refresh() {
    if (!id) return;
    try {
      const [r, fs] = await Promise.all([api.getRag(id), api.listFiles(id)]);
      setRag(r);
      setFiles(fs);
      try {
        setRun(await api.getIngestStatus(id));
      } catch {
        setRun(null);
      }
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  useEffect(() => {
    void refresh();
  }, [id]);

  useEffect(() => {
    if (!run) return;
    if (run.status === 'queued' || run.status === 'running') {
      const t = setInterval(() => void refresh(), 1500);
      return () => clearInterval(t);
    }
  }, [run?.status]);

  // Subscribe to ingest event stream while a run is in progress.
  useEffect(() => {
    if (!id || !run || !run.stream_token) return;
    if (run.status !== 'running' && run.status !== 'queued') {
      // Terminal — fetch historical events once for the log view, no SSE.
      void (async () => {
        try {
          const past = await api.getIngestEvents(id, run.id);
          setIngestEvents(past);
        } catch {/* non-fatal */}
      })();
      return;
    }
    // Open SSE
    ingestEsRef.current?.close();
    const url = `/api/rags/${id}/index/runs/${run.id}/stream?token=${encodeURIComponent(run.stream_token)}`;
    const es = new EventSource(url);
    ingestEsRef.current = es;
    es.onmessage = (m) => {
      try {
        const ev = JSON.parse(m.data) as AgentEvent;
        if (ev.type === 'stream_end') {
          es.close();
          ingestEsRef.current = null;
          return;
        }
        setIngestEvents((prev) => [...prev, ev]);
      } catch {/* ignore */}
    };
    es.onerror = () => {
      es.close();
      ingestEsRef.current = null;
    };
    return () => {
      es.close();
      if (ingestEsRef.current === es) ingestEsRef.current = null;
    };
  }, [id, run?.id, run?.stream_token, run?.status]);

  async function onDropFiles(picked: File[]) {
    if (!id || picked.length === 0) return;
    // Pre-create queue items as pending; mark uploading just before the XHR
    const newItems: UploadItem[] = picked.map((f) => ({
      id: nextUploadId(),
      file: f,
      status: 'pending',
      progress: 0,
    }));
    setUploads((prev) => [...prev, ...newItems]);

    const queue = [...newItems];
    let active = 0;

    return new Promise<void>((resolve) => {
      const startNext = () => {
        if (queue.length === 0 && active === 0) {
          resolve();
          return;
        }
        while (active < UPLOAD_CONCURRENCY && queue.length > 0) {
          const item = queue.shift()!;
          active++;
          uploadOne(item).finally(() => {
            active--;
            startNext();
          });
        }
      };
      startNext();
    }).then(() => refresh());
  }

  async function uploadOne(item: UploadItem): Promise<void> {
    if (!id) return;
    setUploads((prev) =>
      prev.map((u) => (u.id === item.id ? { ...u, status: 'uploading', progress: 0 } : u)),
    );
    try {
      await api.uploadOneFile(id, item.file, (loaded, total) => {
        const p = total > 0 ? loaded / total : 0;
        setUploads((prev) => prev.map((u) => (u.id === item.id ? { ...u, progress: p } : u)));
      });
      setUploads((prev) =>
        prev.map((u) => (u.id === item.id ? { ...u, status: 'done', progress: 1 } : u)),
      );
    } catch (e) {
      const msg = (e as Error).message;
      setUploads((prev) =>
        prev.map((u) => (u.id === item.id ? { ...u, status: 'error', error: msg } : u)),
      );
      toast.error(`${item.file.name}: ${msg}`);
    }
  }

  function removeUpload(uid: string) {
    setUploads((prev) => prev.filter((u) => u.id !== uid));
  }

  function clearFinishedUploads() {
    setUploads((prev) => prev.filter((u) => u.status === 'uploading' || u.status === 'pending'));
  }

  async function onIndex(force = false) {
    if (!id) return;
    setBusy(true);
    try {
      setRun(await api.startIngest(id, force));
      setIngestEvents([]);
      toast.info(force ? 'Полная переиндексация запущена' : 'Индексация запущена');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteFile(fileId: string, name: string) {
    if (!id) return;
    if (!confirm(`Удалить файл "${name}"?`)) return;
    try {
      await api.deleteFile(id, fileId);
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(fileId);
        return next;
      });
      toast.success('Файл удалён');
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function onBulkDelete() {
    if (!id || selected.size === 0) return;
    if (!confirm(`Удалить ${selected.size} файл(ов)?`)) return;
    const ids = Array.from(selected);
    setBusy(true);
    try {
      await Promise.all(ids.map((fid) => api.deleteFile(id, fid)));
      setSelected(new Set());
      toast.success(`Удалено: ${ids.length}`);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function toggleSelect(fid: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(fid)) next.delete(fid);
      else next.add(fid);
      return next;
    });
  }

  // Auto-trigger ingestion when uploads finish, if enabled and there's something new.
  useEffect(() => {
    const anyUploading = uploads.some((u) => u.status === 'uploading' || u.status === 'pending');
    const hasDone = uploads.some((u) => u.status === 'done');
    if (wasAnyUploading.current && !anyUploading && hasDone && autoIndex) {
      const notRunning = !run || run.status === 'succeeded' || run.status === 'failed';
      if (notRunning) {
        void onIndex();
      }
    }
    wasAnyUploading.current = anyUploading;
  }, [uploads, autoIndex]); // eslint-disable-line react-hooks/exhaustive-deps

  function persistAutoIndex(v: boolean) {
    setAutoIndex(v);
    localStorage.setItem('ragcms.autoIndex', v ? '1' : '0');
  }

  async function onDeleteRag() {
    if (!id || !rag) return;
    if (!confirm(`Удалить RAG "${rag.name}" безвозвратно?`)) return;
    try {
      await api.deleteRag(id);
      toast.success(`RAG "${rag.name}" удалён`);
      navigate('/');
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!id || !query.trim()) return;
    setBusy(true);
    try {
      const res = await api.search(id, query.trim(), mode, 10);
      setHits(res.hits);
      if (res.hits.length === 0) toast.info('Ничего не найдено');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!rag) {
    return (
      <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Loader2 size={16} className="spinner" /> <span className="muted">Загрузка…</span>
      </div>
    );
  }

  const lang = (rag.settings as { fts_language?: string } | undefined)?.fts_language;
  const isIndexing = run?.status === 'running' || run?.status === 'queued';

  return (
    <div className="col" style={{ gap: 20 }}>
      <div className="hero-block">
        <div className="hero-orb" />
        <Link to="/" className="row" style={{ color: 'var(--muted)', fontSize: 12.5, marginBottom: 8, position: 'relative' }}>
          <ArrowLeft size={14} /> Все RAG'и
        </Link>
        <div className="spread" style={{ position: 'relative' }}>
          <div>
            <div className="row gap-8">
              <h1 style={{ margin: 0 }}>{rag.name}</h1>
              <span className={`badge ${rag.status}`}>{rag.status}</span>
            </div>
            {rag.description && (
              <p className="muted" style={{ margin: '6px 0 0', maxWidth: 720, lineHeight: 1.55 }}>
                {rag.description}
              </p>
            )}
          </div>
          <div className="row gap-8">
            {canManage && <WebSearchToggle rag={rag} onChange={(r) => setRag(r)} />}
            {canManage && (
              <button className="ghost" onClick={() => setMembersOpen(true)}>
                <Users size={14} /> Участники
              </button>
            )}
            <Link to={`/rag/${rag.id}/chat`}>
              <button disabled={rag.status !== 'ready' || canAsk === false}>
                <Bot size={16} /> Чат с агентом
              </button>
            </Link>
            {canManage && (
              <button className="ghost danger" onClick={onDeleteRag}>
                <Trash2 size={14} /> Удалить
              </button>
            )}
          </div>
        </div>
        {role === 'member' && rag.member_status === 'revoked' && (
          <div
            className="row gap-8"
            style={{
              marginTop: 12, padding: '10px 14px',
              background: 'var(--danger-soft)', color: 'var(--danger)',
              border: '1px solid var(--danger-soft)', borderRadius: 'var(--r-md)',
              fontSize: 13,
            }}
          >
            <X size={14} /> Ваш доступ отозван — старые чаты доступны, новые вопросы заблокированы.
          </div>
        )}
        {role === 'member' && rag.member_status === 'active' && (
          <div
            className="row gap-8"
            style={{
              marginTop: 12, padding: '8px 14px',
              background: 'var(--accent-soft)', color: 'var(--text-dim)',
              border: '1px solid rgba(99,102,241,0.18)', borderRadius: 'var(--r-md)',
              fontSize: 12.5,
            }}
          >
            <Users size={13} /> Вы приглашены в этот RAG — можно задавать вопросы агенту. Управление файлами и настройками доступно только владельцу.
          </div>
        )}
      </div>

      <div className="grid cols-4">
        <Kpi label="Файлов" value={files.length} />
        <Kpi
          label="Чанков"
          value={run?.chunks_total ?? 0}
          hint={run ? `последний run · ${run.status}` : 'нет запусков'}
        />
        <Kpi label="Embedding" value={rag.embed_dim} hint={rag.embed_model} />
        <Kpi label="FTS" value={lang ?? 'simple'} />
      </div>

      <ModelsSnapshot rag={rag} />

      <div className="card flush">
        <div className="row" style={{ padding: '6px 6px 0', gap: 4, borderBottom: '1px solid var(--border)' }}>
          <TabBtn active={tab === 'files'} onClick={() => setTab('files')}>
            <FileText size={14} /> Файлы и индексация
          </TabBtn>
          <TabBtn active={tab === 'search'} onClick={() => setTab('search')}>
            <Search size={14} /> Поиск
          </TabBtn>
          <span className="spacer" style={{ flex: 1 }} />
          <span className="subtle mono" style={{ padding: '0 12px' }}>id: {rag.id}</span>
        </div>

        {tab === 'files' && (
          <div className="col gap-12" style={{ padding: 20 }}>
            {canManage && (
              <FileDropzone
                accept=".pdf,.txt,.md,.xlsx"
                disabled={busy}
                onFiles={(picked) => void onDropFiles(picked)}
              />
            )}

            {uploads.length > 0 && (
              <>
                <UploadQueue items={uploads} onRemove={removeUpload} />
                <div className="row gap-12" style={{ justifyContent: 'flex-end' }}>
                  <button className="subtle sm" onClick={clearFinishedUploads}>
                    Очистить завершённые
                  </button>
                </div>
              </>
            )}

            {canManage ? (
              <div className="row gap-12" style={{ flexWrap: 'wrap' }}>
                <button
                  onClick={() => void onIndex(false)}
                  disabled={busy || files.length === 0 || isIndexing}
                >
                  {isIndexing ? (
                    <><Loader2 size={14} className="spinner" /> Индексация…</>
                  ) : (
                    <><Play size={14} /> Запустить индексацию</>
                  )}
                </button>
                <button
                  className="ghost"
                  onClick={() => {
                    if (!confirm('Полная переиндексация: все файлы будут обработаны заново. Продолжить?')) return;
                    void onIndex(true);
                  }}
                  disabled={busy || files.length === 0 || isIndexing}
                  title="Сбросить кеш и проиндексировать все файлы заново"
                >
                  <RotateCcw size={13} /> Force re-index
                </button>
                <label className="row gap-4" style={{ fontSize: 12.5, color: 'var(--text-dim)', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={autoIndex}
                    onChange={(e) => persistAutoIndex(e.target.checked)}
                    style={{ width: 'auto' }}
                  />
                  Авто-индексация после загрузки
                </label>
                <span className="spacer" style={{ flex: 1 }} />
                {run && !isIndexing && (
                  <span className="subtle">
                    last run: <span className={`badge ${run.status}`}>{run.status}</span>
                    {' · '}файлы {run.files_done}/{run.files_total} · чанков {run.chunks_total}
                  </span>
                )}
              </div>
            ) : (
              <div className="subtle" style={{ fontSize: 12.5 }}>
                Файлы только для чтения. Управление доступно владельцу RAG'а.
              </div>
            )}

            <IngestProgress run={run} events={ingestEvents} files={files} />

            {files.length === 0 ? (
              <div className="subtle" style={{ padding: '24px', textAlign: 'center' }}>
                Файлы пока не загружены — перетащите их в область выше.
              </div>
            ) : (
              <FilesTable
                files={files}
                selected={selected}
                onToggle={toggleSelect}
                onToggleAll={(all) => setSelected(all ? new Set(visibleFileIds) : new Set())}
                onDelete={onDeleteFile}
                onBulkDelete={onBulkDelete}
                filter={filter}
                setFilter={setFilter}
                visibleFiles={visibleFiles}
                busy={busy}
                readOnly={!canManage}
              />
            )}
          </div>
        )}

        {tab === 'search' && (
          <div className="col gap-12" style={{ padding: 20 }}>
            <form onSubmit={onSearch} className="col gap-12">
              <div className="field">
                <label>Запрос</label>
                <textarea
                  placeholder="Например: где описаны условия расторжения?"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
              <div className="row gap-8">
                <label className="subtle">Режим:&nbsp;
                  <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
                    <option value="hybrid">hybrid (RRF)</option>
                    <option value="dense">dense (Qdrant)</option>
                    <option value="sparse">sparse (FTS)</option>
                  </select>
                </label>
                <span className="spacer" style={{ flex: 1 }} />
                <button type="submit" disabled={busy || !query.trim()}>
                  {busy ? <Loader2 size={14} className="spinner" /> : <Search size={14} />}
                  Найти
                </button>
              </div>
            </form>

            <div style={{ marginTop: 8 }}>
              {hits.length === 0 ? (
                <div className="subtle" style={{ padding: '24px', textAlign: 'center' }}>
                  Запустите поиск, чтобы увидеть выдачу.
                </div>
              ) : (
                hits.map((h) => (
                  <div key={h.chunk_id} className="hit">
                    <div className="hit-meta">
                      <span style={{ color: 'var(--text)' }}>{h.filename}</span>
                      {h.page_start && (
                        <span>
                          стр. {h.page_start}
                          {h.page_end !== h.page_start && `–${h.page_end}`}
                        </span>
                      )}
                      {h.heading && <span>· {h.heading}</span>}
                      <span className="spacer" style={{ flex: 1 }} />
                      <span className="score">score {h.score.toFixed(4)}</span>
                      {h.dense_score !== null && (
                        <span className="mono" style={{ color: 'var(--muted)' }}>dense {h.dense_score.toFixed(3)}</span>
                      )}
                      {h.sparse_score !== null && (
                        <span className="mono" style={{ color: 'var(--muted)' }}>sparse {h.sparse_score.toFixed(3)}</span>
                      )}
                    </div>
                    <pre>{h.text}</pre>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>

      {membersOpen && (
        <MembersModal rag={rag} onClose={() => setMembersOpen(false)} />
      )}
    </div>
  );
}

function MembersModal({ rag, onClose }: { rag: Rag; onClose: () => void }) {
  const toast = useToast();
  const [members, setMembers] = useState<Member[]>([]);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try {
      setMembers(await api.listMembers(rag.id));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [rag.id]);

  async function onInvite(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    try {
      await api.inviteMember(rag.id, email.trim());
      setEmail('');
      await refresh();
      toast.success('Приглашён');
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onRevoke(m: Member) {
    if (m.is_owner) return;
    if (!confirm(`Отозвать доступ ${m.email}?`)) return;
    try {
      await api.revokeMember(rag.id, m.user_id);
      await refresh();
      toast.success('Доступ отозван');
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function onReactivate(m: Member) {
    if (m.is_owner) return;
    try {
      await api.inviteMember(rag.id, m.email);  // POST reactivates
      await refresh();
      toast.success('Доступ восстановлен');
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 'min(560px, 92vw)' }}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>
            <UserPlus size={16} style={{ marginRight: 8, verticalAlign: -2 }} />
            Участники
          </h2>
          <div className="subtle" style={{ marginTop: 4 }}>
            Приглашённые могут задавать вопросы агенту. Управление файлами остаётся за вами.
          </div>
        </div>
        <form onSubmit={onInvite}>
          <div className="modal-body col gap-12">
            <div className="row gap-8">
              <input
                type="email"
                placeholder="email@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={busy}
                autoFocus
              />
              <button type="submit" disabled={busy || !email.trim()}>
                <UserPlus size={14} /> Пригласить
              </button>
            </div>
            <div className="subtle" style={{ fontSize: 12 }}>
              Пользователь должен существовать в системе. Если его нет — попросите admin'а создать аккаунт.
            </div>

            {loading ? (
              <div className="subtle" style={{ padding: 12, textAlign: 'center' }}>Загрузка…</div>
            ) : (
              <ul className="list">
                {members.map((m) => (
                  <li key={m.user_id}>
                    <div className="row">
                      <div className="grow" style={{ minWidth: 0 }}>
                        <strong>{m.email}</strong>{' '}
                        {m.is_owner ? (
                          <span className="badge accent">owner</span>
                        ) : m.status === 'active' ? (
                          <span className="badge success">active</span>
                        ) : (
                          <span className="badge danger">revoked</span>
                        )}
                        <div className="subtle" style={{ fontSize: 12 }}>
                          {m.is_owner ? '—' : `с ${new Date(m.created_at).toLocaleDateString()}`}
                          {m.revoked_at && ` · отозван ${new Date(m.revoked_at).toLocaleDateString()}`}
                        </div>
                      </div>
                      {!m.is_owner && m.status === 'active' && (
                        <button className="ghost danger sm" onClick={() => onRevoke(m)}>
                          <X size={12} /> Отозвать
                        </button>
                      )}
                      {!m.is_owner && m.status === 'revoked' && (
                        <button className="ghost sm" onClick={() => onReactivate(m)}>
                          <UserPlus size={12} /> Восстановить
                        </button>
                      )}
                    </div>
                  </li>
                ))}
                {members.length === 1 && (
                  <li className="subtle" style={{ textAlign: 'center', padding: 12 }}>
                    Пока никого не приглашали.
                  </li>
                )}
              </ul>
            )}
          </div>
          <div className="modal-foot">
            <button type="button" className="ghost" onClick={onClose}>Закрыть</button>
          </div>
        </form>
      </div>
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="subtle"
      style={{
        background: active ? 'var(--surface-2)' : 'transparent',
        color: active ? 'var(--text)' : 'var(--muted)',
        borderRadius: '10px 10px 0 0',
        borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
        padding: '10px 14px',
        fontWeight: 500,
      }}
    >
      {children}
    </button>
  );
}

function WebSearchToggle({ rag, onChange }: { rag: Rag; onChange: (r: Rag) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const enabled = !!(rag.settings as { web_search_enabled?: boolean } | undefined)?.web_search_enabled;

  async function toggle() {
    setBusy(true);
    try {
      const updated = await api.updateRagSettings(rag.id, { web_search_enabled: !enabled });
      onChange(updated);
      toast.success(`Web-поиск: ${!enabled ? 'включён' : 'выключен'}`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      className={`ghost toggle-pill${enabled ? ' on' : ''}`}
      onClick={toggle}
      disabled={busy}
      title={enabled
        ? 'Web-поиск включён — агент может искать в интернете когда документов не хватает.'
        : 'Web-поиск выключен — агент работает только по загруженным файлам.'}
    >
      <Globe size={14} />
      <span>Web-поиск</span>
      <span className={`switch${enabled ? ' on' : ''}`} aria-hidden>
        <span className="switch-knob" />
      </span>
    </button>
  );
}

function Kpi({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

interface ModelSnapshot {
  preset?: string;
  preset_label?: string;
  llm_model?: string;
  llm_vision_model?: string;
  llm_rerank_model?: string;
  embed_provider?: string;
  embed_model?: string;
  embed_dim?: number;
}

function ModelsSnapshot({ rag }: { rag: Rag }) {
  const models = (rag.settings as { models?: ModelSnapshot } | undefined)?.models;
  if (!models) return null;
  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="spread" style={{ alignItems: 'flex-start' }}>
        <div>
          <div className="row gap-8" style={{ alignItems: 'baseline' }}>
            <div className="label" style={{ fontWeight: 600 }}>Модельный пресет</div>
            {models.preset_label && (
              <span className="badge accent">{models.preset_label}</span>
            )}
          </div>
          <div className="subtle" style={{ marginTop: 6, fontSize: 12 }}>
            Снимок сохранён при создании RAG'а и не меняется при обновлении пресетов.
          </div>
        </div>
      </div>
      <div className="grid cols-4" style={{ marginTop: 12, gap: 10 }}>
        <ModelTile label="Chat / Agent" value={models.llm_model} />
        <ModelTile label="Vision / OCR" value={models.llm_vision_model} />
        <ModelTile label="Rerank" value={models.llm_rerank_model} />
        <ModelTile
          label="Embedding"
          value={models.embed_model}
          hint={
            models.embed_dim
              ? `${models.embed_dim}d${models.embed_provider ? ' · ' + models.embed_provider : ''}`
              : models.embed_provider
          }
        />
      </div>
    </div>
  );
}

function ModelTile({ label, value, hint }: { label: string; value?: string; hint?: string }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div
        className="mono"
        style={{ fontSize: 12.5, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        title={value || ''}
      >
        {value || '—'}
      </div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

function fileExt(name: string): string {
  const m = name.toLowerCase().match(/\.([a-z0-9]+)$/);
  return m ? m[1] : '';
}

function FileTypeBadge({ name }: { name: string }) {
  const ext = fileExt(name);
  const color =
    ext === 'pdf' ? 'var(--danger)' :
    ext === 'xlsx' ? 'var(--success)' :
    ext === 'md' ? 'var(--accent-2)' :
    ext === 'txt' ? 'var(--text-dim)' :
    'var(--muted)';
  return (
    <span className="file-type" data-ext={ext} style={{ color }}>
      <FileText size={14} />
      <span className="mono" style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase' }}>
        {ext || '?'}
      </span>
    </span>
  );
}

/** Progress + collapsible event log for the current ingest run. */
function IngestProgress({
  run,
  events,
  files,
}: {
  run: IngestRun | null;
  events: AgentEvent[];
  files: FileItem[];
}) {
  const [open, setOpen] = useState(true);
  if (!run) return null;
  const isActive = run.status === 'queued' || run.status === 'running';
  const isTerminal = run.status === 'succeeded' || run.status === 'failed';
  // Show panel during active run; for terminal, only show if there are events at all.
  if (!isActive && (!isTerminal || events.length === 0)) return null;

  const filesPct = run.files_total > 0
    ? Math.round((run.files_done / run.files_total) * 100)
    : 0;
  const currentFile = run.current_file_id
    ? files.find((f) => f.id === run.current_file_id)
    : null;
  const stageLabel: Record<string, string> = {
    parsing: 'Парсинг',
    chunking: 'Чанкинг',
    enriching: 'Контекстуальное обогащение',
    embedding: 'Эмбеддинги',
    storing: 'Сохранение в Qdrant',
  };

  return (
    <div className="ingest-panel">
      <div className="ingest-head">
        <div className="row gap-8">
          {isActive ? (
            <Loader2 size={14} className="spinner" style={{ color: 'var(--accent-2)' }} />
          ) : run.status === 'succeeded' ? (
            <Sparkles size={14} style={{ color: 'var(--success)' }} />
          ) : (
            <Sparkles size={14} style={{ color: 'var(--danger)' }} />
          )}
          <span style={{ fontWeight: 600, fontSize: 13.5 }}>
            {isActive
              ? 'Индексация…'
              : run.status === 'succeeded'
                ? 'Индексация завершена'
                : 'Индексация упала'}
          </span>
          <span className={`badge ${run.status}`}>{run.status}</span>
        </div>
        <span className="spacer" style={{ flex: 1 }} />
        <span className="subtle mono">
          файлы {run.files_done}/{run.files_total} · чанков {run.chunks_total}
        </span>
      </div>

      <div className="ingest-bar">
        <div className="ingest-bar-fill" style={{ width: `${filesPct}%` }} />
      </div>

      {isActive && (
        <div className="ingest-current">
          {currentFile ? (
            <>
              <FileTypeBadge name={currentFile.filename} />
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="upload-name">{currentFile.filename}</div>
                <div className="upload-sub">
                  <span className="badge accent">{stageLabel[run.current_stage ?? ''] ?? run.current_stage ?? '—'}</span>
                  {run.current_progress != null && (
                    <span className="mono">{Math.round((run.current_progress || 0) * 100)}%</span>
                  )}
                </div>
                {run.current_progress != null && (
                  <div className="upload-bar" style={{ marginTop: 6 }}>
                    <div
                      className="upload-bar-fill"
                      style={{ width: `${Math.round((run.current_progress || 0) * 100)}%` }}
                    />
                  </div>
                )}
              </div>
            </>
          ) : (
            <span className="subtle">Готовлю файлы…</span>
          )}
        </div>
      )}

      {events.length > 0 && (
        <>
          <button
            className="subtle sm ingest-toggle"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            Лог · {events.length} событий
          </button>
          {open && (
            <div className="event-log">
              {events.map((ev) => (
                <span key={ev.seq} className="ev" style={{ color: ingestEventColor(ev.type) }}>
                  <span className="ts">{ev.ts.slice(11, 19)}</span>
                  {ingestEventLine(ev, files)}
                </span>
              ))}
            </div>
          )}
        </>
      )}

      {run.error && (
        <div className="row" style={{ color: 'var(--danger)', fontSize: 12.5, marginTop: 8 }}>
          {run.error}
        </div>
      )}
    </div>
  );
}

function ingestEventColor(type: string): string {
  switch (type) {
    case 'file_done':
    case 'run_finished':
      return 'var(--success)';
    case 'file_failed':
    case 'run_failed':
      return 'var(--danger)';
    case 'embedding':
    case 'embedding_batch':
    case 'enriching':
    case 'storing':
      return 'var(--accent-2)';
    case 'stale_chunks_dropped':
    case 'ocr_fallback':
      return 'var(--warning)';
    default:
      return 'var(--text-dim)';
  }
}

function ingestEventLine(ev: AgentEvent, files: FileItem[]): string {
  const p = ev.payload as Record<string, unknown>;
  const fname = (id: unknown): string => {
    const f = files.find((x) => x.id === id);
    return f ? f.filename : String(id ?? '');
  };
  switch (ev.type) {
    case 'run_started': {
      const skipped = p.files_skipped_cached as number | undefined;
      return `start: всего ${p.files_total}${skipped ? `, кеш-пропущено ${skipped}` : ''}`;
    }
    case 'file_started':
      return `► ${fname(p.file_id)} (${Math.round((p.size_bytes as number) / 1024)} KB)`;
    case 'stale_chunks_dropped':
      return `   — сброшено ${p.count} старых чанков`;
    case 'parsing':
      return `   parsing`;
    case 'ocr_fallback':
      return `   OCR fallback на стр.${p.page}`;
    case 'chunked':
      return `   ${p.pages} стр. → ${p.chunks} чанков`;
    case 'enriching':
      return `   контекстуальное обогащение (${p.chunks} чанков)`;
    case 'embedding':
      return `   embedding запущен (${p.chunks_total} чанков)`;
    case 'embedding_batch':
      return `   embedding batch ${p.done}/${p.total}`;
    case 'storing':
      return `   storing ${p.chunks} чанков в Qdrant`;
    case 'file_done':
      return `✓ ${fname(p.file_id)} — ${p.chunks} чанков${p.ocr_pages ? `, OCR ${p.ocr_pages} стр.` : ''}`;
    case 'file_failed':
      return `✗ ${fname(p.file_id)} (${p.stage}): ${p.error}`;
    case 'run_finished':
      return `finished: ${p.status}, ${p.files_done} файлов, ${p.chunks_total} чанков, ${p.elapsed_sec}s`;
    case 'run_failed':
      return `run failed: ${p.error}`;
    default:
      return `${ev.type} ${JSON.stringify(ev.payload)}`;
  }
}

interface FilesTableProps {
  files: FileItem[];
  visibleFiles: FileItem[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onToggleAll: (all: boolean) => void;
  onDelete: (id: string, name: string) => void;
  onBulkDelete: () => void;
  filter: string;
  setFilter: (v: string) => void;
  busy: boolean;
  readOnly?: boolean;
}

function FilesTable(p: FilesTableProps) {
  const allVisibleSelected =
    p.visibleFiles.length > 0 && p.visibleFiles.every((f) => p.selected.has(f.id));

  return (
    <div className="col gap-12">
      <div className="row gap-12" style={{ flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: 360 }}>
          <Search
            size={14}
            style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)' }}
          />
          <input
            placeholder="Поиск по имени файла…"
            value={p.filter}
            onChange={(e) => p.setFilter(e.target.value)}
            style={{ paddingLeft: 36 }}
          />
        </div>
        <span className="subtle">
          {p.filter
            ? `${p.visibleFiles.length} из ${p.files.length}`
            : `${p.files.length} файл(ов)`}
        </span>
        <span className="spacer" style={{ flex: 1 }} />
        {!p.readOnly && p.selected.size > 0 && (
          <>
            <span className="subtle">Выбрано: {p.selected.size}</span>
            <button className="ghost danger sm" disabled={p.busy} onClick={p.onBulkDelete}>
              <Trash2 size={13} /> Удалить выбранные
            </button>
          </>
        )}
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              {!p.readOnly && (
                <th style={{ width: 32 }}>
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={(e) => p.onToggleAll(e.target.checked)}
                    style={{ width: 'auto' }}
                    aria-label="Выбрать всё"
                  />
                </th>
              )}
              <th>Файл</th>
              <th>Статус</th>
              <th>Размер</th>
              <th>Страниц</th>
              {!p.readOnly && <th style={{ textAlign: 'right' }}></th>}
            </tr>
          </thead>
          <tbody>
            {p.visibleFiles.map((f) => (
              <tr key={f.id} className={p.selected.has(f.id) ? 'selected' : ''}>
                {!p.readOnly && (
                  <td>
                    <input
                      type="checkbox"
                      checked={p.selected.has(f.id)}
                      onChange={() => p.onToggle(f.id)}
                      style={{ width: 'auto' }}
                      aria-label={`Выбрать ${f.filename}`}
                    />
                  </td>
                )}
                <td>
                  <div className="row gap-8">
                    <FileTypeBadge name={f.filename} />
                    <div className="grow" style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.filename}
                      </div>
                      {f.error && (
                        <div className="subtle" style={{ color: 'var(--danger)' }}>{f.error}</div>
                      )}
                    </div>
                  </div>
                </td>
                <td><span className={`badge ${f.status}`}>{f.status}</span></td>
                <td className="mono">{(f.size_bytes / 1024).toFixed(1)} KB</td>
                <td className="mono">{f.pages ?? '—'}</td>
                {!p.readOnly && (
                  <td>
                    <div className="actions">
                      <button
                        className="icon"
                        title="Удалить"
                        onClick={() => p.onDelete(f.id, f.filename)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
            {p.visibleFiles.length === 0 && (
              <tr>
                <td colSpan={p.readOnly ? 4 : 6} className="subtle" style={{ textAlign: 'center', padding: 20 }}>
                  Ничего не найдено.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
