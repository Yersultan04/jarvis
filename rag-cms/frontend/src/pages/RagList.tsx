import {
  Database,
  Plus,
  Sparkles,
  Trash2,
  Users,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Preset, type Rag } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../ToastContext';

const LANG_OPTIONS = [
  { value: 'simple', label: 'simple (default, language-agnostic)' },
  { value: 'english', label: 'english' },
  { value: 'russian', label: 'russian' },
  { value: 'german', label: 'german' },
  { value: 'french', label: 'french' },
  { value: 'spanish', label: 'spanish' },
];

export default function RagList() {
  const { session } = useAuth();
  const toast = useToast();
  const [rags, setRags] = useState<Rag[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [ftsLanguage, setFtsLanguage] = useState('simple');
  const [presetId, setPresetId] = useState<string>('');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setRags(await api.listRags());
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  useEffect(() => {
    void refresh();
    void api.listPresets().then((p) => {
      setPresets(p);
      if (p.length > 0 && !presetId) setPresetId(p[0].id);
    }).catch(() => {/* non-fatal */});
  }, []);

  useEffect(() => {
    const open = () => setCreateOpen(true);
    window.addEventListener('ragcms:open-create-rag', open as EventListener);
    return () => window.removeEventListener('ragcms:open-create-rag', open as EventListener);
  }, []);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const created = await api.createRag(
        name.trim(),
        description.trim() || undefined,
        ftsLanguage,
        presetId || undefined,
      );
      setName('');
      setDescription('');
      setFtsLanguage('simple');
      setCreateOpen(false);
      toast.success(`RAG "${created.name}" создан`);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: string, name: string) {
    if (!confirm(`Удалить RAG "${name}" со всеми файлами и индексом?`)) return;
    try {
      await api.deleteRag(id);
      toast.success(`RAG "${name}" удалён`);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const isAdmin = session?.user.role === 'admin';
  const ready = rags.filter((r) => r.status === 'ready').length;
  const indexing = rags.filter((r) => r.status === 'indexing').length;
  const failed = rags.filter((r) => r.status === 'failed').length;

  return (
    <div className="col" style={{ gap: 20 }}>
      <div className="hero-block">
        <div className="hero-orb" />
        <div className="spread" style={{ position: 'relative' }}>
          <div>
            <h1>{isAdmin ? 'Все RAG’и' : 'Ваши RAG’и'}</h1>
            <p className="muted" style={{ margin: 0, fontSize: 13.5, maxWidth: 620, lineHeight: 1.55 }}>
              Создавайте изолированные RAG-инстансы: каждый со своим Qdrant-collection,
              файлами, агентом и API.
            </p>
          </div>
          <button onClick={() => setCreateOpen(true)}>
            <Plus size={16} /> Новый RAG
          </button>
        </div>
      </div>

      <div className="grid cols-4">
        <Kpi label="Всего" value={rags.length} icon={<Database size={14} />} />
        <Kpi label="Готовы" value={ready} tone="success" />
        <Kpi label="В индексации" value={indexing} tone="accent" />
        <Kpi label="С ошибками" value={failed} tone={failed > 0 ? 'warning' : undefined} />
      </div>

      {rags.length === 0 ? (
        <div className="card empty-state">
          <div className="empty-halo">
            <Sparkles size={32} style={{ color: 'white' }} />
          </div>
          <h2 style={{ marginTop: 20 }}>Пока ни одного RAG'а</h2>
          <p className="muted" style={{ marginTop: 6, maxWidth: 440, marginLeft: 'auto', marginRight: 'auto' }}>
            Создайте первый, загрузите PDF / TXT / MD и спросите ReAct-агента —
            до 40 шагов, с проверкой цитат.
          </p>
          <button style={{ marginTop: 20 }} onClick={() => setCreateOpen(true)}>
            <Plus size={16} /> Создать первый RAG
          </button>
        </div>
      ) : (
        <div className="tickers">
          {rags.map((r) => {
            const fts = (r.settings as { fts_language?: string } | undefined)?.fts_language;
            const role = r.role ?? (r.owner_id === session?.user.id ? 'owner' : 'none');
            const canDelete = role === 'owner' || role === 'admin';
            return (
              <Link key={r.id} to={`/rag/${r.id}`} className="ticker">
                <div className="head">
                  <div className="name" title={r.name}>{r.name}</div>
                  <span className={`badge ${r.status}`}>{r.status}</span>
                </div>
                <div className="desc">{r.description || 'Без описания'}</div>
                <div className="meta">
                  <span>embed <b>{r.embed_dim}</b></span>
                  {fts && <span>fts <b>{fts}</b></span>}
                  {role === 'member' && (
                    <span
                      className={`badge ${r.member_status === 'revoked' ? 'danger' : 'accent'}`}
                      title={r.member_status === 'revoked' ? 'Доступ отозван' : 'Вы приглашены'}
                    >
                      <Users size={10} /> {r.member_status === 'revoked' ? 'отозван' : 'гость'}
                    </span>
                  )}
                  {role === 'admin' && r.owner_id !== session?.user.id && (
                    <span className="badge">admin-view</span>
                  )}
                </div>
                <div className="spread">
                  <span className="subtle mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {r.id.slice(0, 8)}…
                  </span>
                  {canDelete && (
                    <button
                      className="icon"
                      title="Удалить"
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); void onDelete(r.id, r.name); }}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </Link>
            );
          })}
        </div>
      )}

      {createOpen && (
        <CreateModal
          name={name}
          setName={setName}
          description={description}
          setDescription={setDescription}
          ftsLanguage={ftsLanguage}
          setFtsLanguage={setFtsLanguage}
          presets={presets}
          presetId={presetId}
          setPresetId={setPresetId}
          busy={busy}
          onClose={() => setCreateOpen(false)}
          onSubmit={onCreate}
        />
      )}
    </div>
  );
}

function Kpi({
  label,
  value,
  hint,
  icon,
  tone,
}: {
  label: string;
  value: number | string;
  hint?: string;
  icon?: React.ReactNode;
  tone?: 'accent' | 'success' | 'warning';
}) {
  return (
    <div className={`kpi${tone ? ` ${tone}` : ''}`}>
      <div className="row gap-8">
        <span className="label">{label}</span>
        {icon && <span style={{ color: 'var(--muted)' }}>{icon}</span>}
      </div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

interface CreateProps {
  name: string;
  setName: (v: string) => void;
  description: string;
  setDescription: (v: string) => void;
  ftsLanguage: string;
  setFtsLanguage: (v: string) => void;
  presets: Preset[];
  presetId: string;
  setPresetId: (v: string) => void;
  busy: boolean;
  onClose: () => void;
  onSubmit: (e: React.FormEvent) => void;
}

function CreateModal(p: CreateProps) {
  return (
    <div className="modal-backdrop" onClick={p.onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>Новый RAG</h2>
          <div className="subtle" style={{ marginTop: 4 }}>
            Отдельный изолированный экземпляр: Qdrant-collection, файлы, агент.
          </div>
        </div>
        <form onSubmit={p.onSubmit}>
          <div className="modal-body col gap-12">
            <div className="field">
              <label htmlFor="rag-name">Название</label>
              <input
                id="rag-name"
                placeholder="docs-cdek-2026"
                value={p.name}
                onChange={(e) => p.setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="field">
              <label htmlFor="rag-desc">Описание (опционально)</label>
              <textarea
                id="rag-desc"
                placeholder="Зачем нужен этот RAG, какие документы…"
                value={p.description}
                onChange={(e) => p.setDescription(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="rag-lang">Язык FTS</label>
              <select id="rag-lang" value={p.ftsLanguage} onChange={(e) => p.setFtsLanguage(e.target.value)}>
                {LANG_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              <span className="subtle">Постгрес ts_config для sparse-поиска. Меняется только пересозданием.</span>
            </div>
            {p.presets.length > 0 && (
              <div className="field">
                <label htmlFor="rag-preset">Модельный пресет</label>
                <select
                  id="rag-preset"
                  value={p.presetId}
                  onChange={(e) => p.setPresetId(e.target.value)}
                >
                  {p.presets.map((pr) => (
                    <option key={pr.id} value={pr.id}>{pr.label}</option>
                  ))}
                </select>
                {(() => {
                  const cur = p.presets.find((pr) => pr.id === p.presetId);
                  if (!cur) return null;
                  return (
                    <div className="subtle" style={{ marginTop: 6, lineHeight: 1.5 }}>
                      <div>{cur.description}</div>
                      <div className="mono" style={{ marginTop: 4, fontSize: 11.5 }}>
                        chat <b>{cur.llm_model}</b> · vision <b>{cur.llm_vision_model}</b> ·
                        rerank <b>{cur.llm_rerank_model}</b> · embed <b>{cur.embed_model}</b>{' '}
                        ({cur.embed_dim}d)
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
          <div className="modal-foot">
            <button type="button" className="ghost" onClick={p.onClose}>Отмена</button>
            <button type="submit" disabled={p.busy || !p.name.trim()}>
              {p.busy ? 'Создание…' : 'Создать'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
