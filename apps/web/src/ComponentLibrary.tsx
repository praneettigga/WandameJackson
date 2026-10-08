import { useEffect, useRef, useState, type ReactNode } from 'react';
import { components, type V3 } from './scene';
import { useEditor } from './store';
import {
  addCustomComponent,
  disposeScan,
  readScan,
  removeCustomComponent,
  scanAccept,
  scanCategories,
  scanDimensions,
  scanUnits,
  useLibrary,
  type CustomComponent,
  type ScanDraft,
  type ScanUnits,
  type UpAxis,
} from './library';

const glyphs = {
  chevron: <path d="M6 3.5 10.5 8 6 12.5" />,
  chair: <path d="M4.5 2v12M4.5 8.5H12V14" />,
  sofa: (
    <>
      <path d="M3.5 7V5A1.5 1.5 0 0 1 5 3.5h6A1.5 1.5 0 0 1 12.5 5v2" />
      <path d="M2 12V8.5a1 1 0 0 1 2 0V10h8V8.5a1 1 0 0 1 2 0V12zM3 12v1.5M13 12v1.5" />
    </>
  ),
  table: <path d="M1.5 5.5h13M3 5.5V13M13 5.5V13M3 8h10" />,
  bed: (
    <>
      <path d="M2 3v11M2 9h12v5M2 11.5h12" />
      <path d="M3.5 9V7.2c0-.4.3-.7.7-.7h2.6c.4 0 .7.3.7.7V9" />
    </>
  ),
  cabinet: (
    <>
      <rect x="3" y="1.5" width="10" height="12" rx="1" />
      <path d="M8 1.5v12M6.3 7v2M9.7 7v2M4.5 13.5V15M11.5 13.5V15" />
    </>
  ),
  scan: (
    <>
      <path d="M8 1.5l5.5 3.1v6.8L8 14.5l-5.5-3.1V4.6z" />
      <path d="M2.5 4.6 8 7.7l5.5-3.1M8 7.7v6.8" />
    </>
  ),
  plus: <path d="M8 3v10M3 8h10" />,
  close: <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />,
  warning: <path d="M8 2.2 14.3 13.5H1.7zM8 6.6v3.1M8 11.5v.1" />,
};
type Glyph = keyof typeof glyphs;
function Icon({ name }: { name: Glyph }) {
  return (
    <svg className="lib-icon" viewBox="0 0 16 16" aria-hidden="true">
      {glyphs[name]}
    </svg>
  );
}

/** Built-in folders; every built-in component belongs to exactly one. */
export const libraryFolders: {
  id: string;
  label: string;
  icon: Glyph;
  tint: string;
  items: string[];
}[] = [
  {
    id: 'seating',
    label: 'Seating',
    icon: 'sofa',
    tint: '#7fb3c8',
    items: ['chair.basic', 'sofa.basic'],
  },
  { id: 'tables', label: 'Tables', icon: 'table', tint: '#d6ad78', items: ['table.basic'] },
  { id: 'beds', label: 'Beds', icon: 'bed', tint: '#ab98d6', items: ['bed.basic'] },
  { id: 'storage', label: 'Storage', icon: 'cabinet', tint: '#8fbf8a', items: ['cabinet.basic'] },
];
const itemIcons: Record<string, Glyph> = {
  'chair.basic': 'chair',
  'sofa.basic': 'sofa',
  'table.basic': 'table',
  'bed.basic': 'bed',
  'cabinet.basic': 'cabinet',
};
const unitLabels: Record<ScanUnits, string> = {
  m: 'Metres',
  cm: 'Centimetres',
  mm: 'Millimetres',
  in: 'Inches',
};
const metres = (v: number) => String(Number(v.toFixed(2)));
const footprint = (d: V3) => `${metres(d[0])} × ${metres(d[2])} m`;

const OPEN_KEY = 'roomshift.libraryFolders';
function storedOpen(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(OPEN_KEY) ?? '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function Folder({
  id,
  label,
  icon,
  tint,
  count,
  open,
  onToggle,
  children,
}: {
  id: string;
  label: string;
  icon: Glyph;
  tint: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <li>
      <button
        className="lib-folder"
        aria-expanded={open}
        aria-controls={`library-${id}`}
        onClick={onToggle}
      >
        <span className="lib-chevron">
          <Icon name="chevron" />
        </span>
        <span className="lib-folder-icon" style={{ color: tint }}>
          <Icon name={icon} />
        </span>
        <span className="lib-label">{label}</span>
        <span className="lib-count">{count}</span>
      </button>
      <ul id={`library-${id}`} className="lib-children" hidden={!open}>
        {children}
      </ul>
    </li>
  );
}

function ImportForm({
  draft,
  onCancel,
  onAdded,
}: {
  draft: ScanDraft;
  onCancel: () => void;
  onAdded: (item: CustomComponent, persisted: boolean) => void;
}) {
  const [name, setName] = useState(draft.name);
  const [category, setCategory] = useState<string>('other');
  const [units, setUnits] = useState(draft.units);
  const [upAxis, setUpAxis] = useState(draft.upAxis);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const dimensions = scanDimensions(draft.size, units, upAxis);
  return (
    <form
      className="lib-import"
      aria-label="Custom component details"
      onSubmit={(e) => {
        e.preventDefault();
        setSaving(true);
        setError('');
        addCustomComponent(draft, { name, category, units, upAxis }).then(
          ({ item, persisted }) => onAdded(item, persisted),
          (err: unknown) => {
            setError(err instanceof Error ? err.message : String(err));
            setSaving(false);
          },
        );
      }}
    >
      <div className="lib-import-file">
        <Icon name="scan" />
        <span title={draft.fileName}>{draft.fileName}</span>
        <small>
          {draft.data.byteLength < 1024 * 1024
            ? `${Math.ceil(draft.data.byteLength / 1024)} KB`
            : `${(draft.data.byteLength / 1024 / 1024).toFixed(1)} MB`}
        </small>
      </div>
      <label className="field">
        Name
        <input value={name} maxLength={80} required onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        Category
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {scanCategories.map((c) => (
            <option key={c} value={c}>
              {c[0].toUpperCase() + c.slice(1)}
            </option>
          ))}
        </select>
      </label>
      <div className="lib-import-row">
        <label className="field">
          File units
          <select value={units} onChange={(e) => setUnits(e.target.value as ScanUnits)}>
            {(Object.keys(scanUnits) as ScanUnits[]).map((u) => (
              <option key={u} value={u}>
                {unitLabels[u]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Up axis
          <select value={upAxis} onChange={(e) => setUpAxis(e.target.value as UpAxis)}>
            <option value="y">Y up</option>
            <option value="z">Z up</option>
          </select>
        </label>
      </div>
      <div className="lib-size" role="status">
        <span>SIZE · W × H × D</span>
        <b>{dimensions.map(metres).join(' × ')} m</b>
      </div>
      <p className="hint">
        Wrong size? Change the units. Lying on its side? Switch the up axis. Kept in this browser
        only; saved scenes store its size, so other devices show a box.
      </p>
      {error && (
        <p className="lib-error" role="alert">
          {error}
        </p>
      )}
      <div className="button-row">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="primary" disabled={saving || !name.trim()}>
          {saving ? 'Adding…' : 'Add to library'}
        </button>
      </div>
    </form>
  );
}

export function ComponentLibrary({ disabled }: { disabled: boolean }) {
  const hasScene = useEditor((s) => Boolean(s.scene));
  const add = useEditor((s) => s.add);
  const custom = useLibrary((s) => s.items);
  const persistent = useLibrary((s) => s.persistent);
  const [open, setOpen] = useState(() => new Set(storedOpen()));
  useEffect(() => {
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify([...open]));
    } catch {
      // Remembering folders is a convenience; nothing breaks without it.
    }
  }, [open]);
  const toggle = (id: string, force?: boolean) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (force ?? !next.has(id)) next.add(id);
      else next.delete(id);
      return next;
    });
  const fileRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<ScanDraft | null>(null);
  const [reading, setReading] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const canAdd = hasScene && !disabled;
  const remove = (c: CustomComponent) => {
    const placed =
      useEditor.getState().scene?.objects.filter((o) => o.componentId === c.id).length ?? 0;
    if (
      !window.confirm(
        `Remove ${c.name} from the library?` +
          (placed
            ? ` ${placed} placed ${placed === 1 ? 'copy keeps its' : 'copies keep their'} size but will show as ${placed === 1 ? 'a box' : 'boxes'}.`
            : ''),
      )
    )
      return;
    void removeCustomComponent(c.id).then(() =>
      setMessage({ text: `${c.name} removed from the library.` }),
    );
  };
  return (
    <section className="library">
      <h4>
        COMPONENT LIBRARY <span>LOCAL</span>
      </h4>
      <p className="hint">Open a folder, then click an item to place it</p>
      <ul className="lib-tree" aria-label="Component library">
        {libraryFolders.map((folder) => {
          const items = components.filter((c) => folder.items.includes(c.id));
          return (
            <Folder
              key={folder.id}
              {...folder}
              count={items.length}
              open={open.has(folder.id)}
              onToggle={() => toggle(folder.id)}
            >
              {items.map((c) => (
                <li key={c.id}>
                  <button
                    className="lib-item"
                    aria-label={`Add ${c.name}`}
                    title={`Add ${c.name} to the scene`}
                    disabled={!canAdd}
                    onClick={() => add(c.id)}
                  >
                    <Icon name={itemIcons[c.id] ?? 'scan'} />
                    <span className="lib-label">{c.name}</span>
                    <small>{footprint(c.dimensions)}</small>
                  </button>
                </li>
              ))}
            </Folder>
          );
        })}
        <Folder
          id="custom"
          label="Custom"
          icon="scan"
          tint="#e3b170"
          count={custom.length}
          open={open.has('custom')}
          onToggle={() => toggle('custom')}
        >
          {custom.length === 0 && <li className="lib-empty">No custom components yet</li>}
          {custom.map((c) => (
            <li key={c.id} className="lib-custom">
              <button
                className="lib-item"
                aria-label={`Add ${c.name}`}
                title={c.error ?? `${c.fileName} · ${c.category}`}
                disabled={!canAdd}
                onClick={() => add(c.id)}
              >
                <Icon name={c.error ? 'warning' : 'scan'} />
                <span className="lib-label">{c.name}</span>
                <small>{footprint(c.dimensions)}</small>
              </button>
              <button
                className="lib-remove"
                aria-label={`Remove ${c.name} from the library`}
                title="Remove from library"
                onClick={() => remove(c)}
              >
                <Icon name="close" />
              </button>
            </li>
          ))}
        </Folder>
        <li>
          <button
            className="lib-add"
            disabled={reading || Boolean(draft)}
            onClick={() => fileRef.current?.click()}
          >
            <Icon name="plus" />
            <span className="lib-label">{reading ? 'Reading file…' : 'Add custom component'}</span>
          </button>
        </li>
      </ul>
      <input
        ref={fileRef}
        type="file"
        accept={scanAccept}
        className="sr-only"
        aria-label="Custom component file"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (!file) return;
          setReading(true);
          setMessage(null);
          readScan(file)
            .then(setDraft, (err: unknown) =>
              setMessage({ text: err instanceof Error ? err.message : String(err), error: true }),
            )
            .finally(() => setReading(false));
        }}
      />
      {draft && (
        <ImportForm
          draft={draft}
          onCancel={() => {
            disposeScan(draft.object);
            setDraft(null);
          }}
          onAdded={(item, persisted) => {
            setDraft(null);
            toggle('custom', true);
            setMessage({
              text: persisted
                ? `${item.name} added to Custom. Click it to place it in the scene.`
                : `${item.name} added for this session only; this browser could not store it.`,
            });
          }}
        />
      )}
      {message && (
        <p
          className={message.error ? 'lib-error' : 'lib-message'}
          role={message.error ? 'alert' : 'status'}
        >
          {message.text}
        </p>
      )}
      {!persistent && (
        <p className="note">
          This browser is not storing custom components, so they last until the page reloads.
        </p>
      )}
    </section>
  );
}
