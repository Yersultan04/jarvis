import { AlertCircle, CheckCircle2, FileText, Loader2, X } from 'lucide-react';

export type UploadStatus = 'pending' | 'uploading' | 'done' | 'error';

export interface UploadItem {
  id: string;
  file: File;
  status: UploadStatus;
  progress: number;  // 0..1
  error?: string;
}

interface Props {
  items: UploadItem[];
  onRemove?: (id: string) => void;
}

export default function UploadQueue({ items, onRemove }: Props) {
  if (items.length === 0) return null;
  return (
    <div className="upload-queue">
      <div className="upload-queue-head">
        Очередь загрузки <span className="muted">· {items.length}</span>
      </div>
      <div className="upload-queue-list">
        {items.map((it) => (
          <UploadRow key={it.id} item={it} onRemove={onRemove} />
        ))}
      </div>
    </div>
  );
}

function UploadRow({ item, onRemove }: { item: UploadItem; onRemove?: (id: string) => void }) {
  const ext = item.file.name.split('.').pop()?.toLowerCase() ?? '';
  const sizeKb = item.file.size / 1024;
  const sizeText = sizeKb < 1024 ? `${sizeKb.toFixed(1)} KB` : `${(sizeKb / 1024).toFixed(2)} MB`;
  const pct = Math.round(item.progress * 100);

  let statusEl: React.ReactNode = null;
  if (item.status === 'pending') statusEl = <span className="muted" style={{ fontSize: 11 }}>ожидание…</span>;
  else if (item.status === 'uploading')
    statusEl = (
      <span style={{ color: 'var(--accent-2)', fontSize: 11, display: 'inline-flex', gap: 4 }}>
        <Loader2 size={12} className="spinner" /> {pct}%
      </span>
    );
  else if (item.status === 'done')
    statusEl = (
      <span style={{ color: 'var(--success)', fontSize: 11, display: 'inline-flex', gap: 4 }}>
        <CheckCircle2 size={12} /> готово
      </span>
    );
  else
    statusEl = (
      <span style={{ color: 'var(--danger)', fontSize: 11, display: 'inline-flex', gap: 4 }}>
        <AlertCircle size={12} /> ошибка
      </span>
    );

  return (
    <div className={`upload-item ${item.status}`}>
      <div className="upload-icon" data-ext={ext}>
        <FileText size={14} />
      </div>
      <div className="upload-meta">
        <div className="upload-name" title={item.file.name}>{item.file.name}</div>
        <div className="upload-sub">
          <span className="mono">{sizeText}</span>
          <span>·</span>
          {statusEl}
          {item.error && <span style={{ color: 'var(--danger)' }} title={item.error}>· {item.error.slice(0, 80)}</span>}
        </div>
        <div className="upload-bar">
          <div
            className="upload-bar-fill"
            style={{
              width: item.status === 'done' ? '100%' : item.status === 'error' ? '100%' : `${pct}%`,
            }}
          />
        </div>
      </div>
      {onRemove && item.status !== 'uploading' && (
        <button
          className="icon"
          title="Убрать из очереди"
          onClick={() => onRemove(item.id)}
          aria-label="Убрать"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
