import { CloudUpload } from 'lucide-react';
import { useRef, useState } from 'react';

interface Props {
  accept?: string;
  disabled?: boolean;
  onFiles: (files: File[]) => void;
  hint?: string;
}

const ACCEPT_DEFAULT = '.pdf,.txt,.md,.xlsx';

export default function FileDropzone({
  accept = ACCEPT_DEFAULT,
  disabled,
  onFiles,
  hint,
}: Props) {
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dragCounter = useRef(0);

  function pick() {
    if (disabled) return;
    inputRef.current?.click();
  }

  function filterAccepted(list: File[]): File[] {
    const exts = accept
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
    if (exts.length === 0) return list;
    return list.filter((f) => {
      const name = f.name.toLowerCase();
      return exts.some((e) => (e.startsWith('.') ? name.endsWith(e) : f.type.startsWith(e)));
    });
  }

  function onDragEnter(e: React.DragEvent) {
    if (disabled) return;
    e.preventDefault();
    dragCounter.current += 1;
    setOver(true);
  }
  function onDragLeave(e: React.DragEvent) {
    if (disabled) return;
    e.preventDefault();
    dragCounter.current = Math.max(0, dragCounter.current - 1);
    if (dragCounter.current === 0) setOver(false);
  }
  function onDragOver(e: React.DragEvent) {
    if (disabled) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }
  function onDrop(e: React.DragEvent) {
    if (disabled) return;
    e.preventDefault();
    dragCounter.current = 0;
    setOver(false);
    const list = Array.from(e.dataTransfer.files || []);
    const filtered = filterAccepted(list);
    if (filtered.length > 0) onFiles(filtered);
  }

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const list = Array.from(e.target.files || []);
    if (list.length > 0) onFiles(list);
    if (inputRef.current) inputRef.current.value = '';
  }

  return (
    <div
      className={`dropzone${over ? ' over' : ''}${disabled ? ' disabled' : ''}`}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onClick={pick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          pick();
        }
      }}
      aria-label="Перетащите файлы или нажмите чтобы выбрать"
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept}
        onChange={onChange}
        disabled={disabled}
        style={{ display: 'none' }}
      />
      <div className="dropzone-icon">
        <CloudUpload size={28} />
      </div>
      <div className="dropzone-title">
        Перетащите файлы сюда
        <span className="muted"> или нажмите чтобы выбрать</span>
      </div>
      <div className="dropzone-hint">
        {hint ?? 'Поддерживаются PDF, XLSX, TXT, MD · можно несколько файлов сразу'}
      </div>
    </div>
  );
}
