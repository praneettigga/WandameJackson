import { useEditor } from '../store';
import { lengthUnits, type LengthUnit } from '../units';

export function ErrorBanner() {
  const error = useEditor((s) => s.error),
    conflict = useEditor((s) => s.conflict || (!!s.assembly && s.assemblyConflict));
  if (!error) return null;
  return (
    <div className={`error-banner ${conflict ? 'conflict' : ''}`} role="alert">
      <b>{conflict ? 'SAVE CONFLICT' : 'ACTION NEEDED'}</b>
      <span>{error}</span>
      <button aria-label="Dismiss error" onClick={() => useEditor.setState({ error: null })}>
        ×
      </button>
    </div>
  );
}

export function UnitSelect({
  label,
  onChange,
}: {
  label: string;
  onChange?: (from: LengthUnit, to: LengthUnit) => void;
}) {
  const unit = useEditor((s) => s.lengthUnit);
  return (
    <select
      className="unit-select"
      aria-label={label}
      value={unit}
      onChange={(e) => {
        const next = e.target.value as LengthUnit;
        onChange?.(unit, next);
        useEditor.getState().setLengthUnit(next);
      }}
    >
      {(Object.keys(lengthUnits) as LengthUnit[]).map((u) => (
        <option key={u} value={u}>
          {u}
        </option>
      ))}
    </select>
  );
}

export function ResizeHandle({
  side,
  onChange,
}: {
  side: 'left' | 'right';
  onChange: (width: number) => void;
}) {
  return (
    <div
      className="resize-handle"
      role="separator"
      aria-label={`Resize ${side} panel`}
      aria-orientation="vertical"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          const panel =
            side === 'left'
              ? e.currentTarget.previousElementSibling
              : e.currentTarget.nextElementSibling;
          onChange(
            (panel?.getBoundingClientRect().width ?? 260) +
              (e.key === 'ArrowRight' ? 20 : -20) * (side === 'left' ? 1 : -1),
          );
        }
      }}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId))
          onChange(side === 'left' ? e.clientX : window.innerWidth - e.clientX);
      }}
      onPointerUp={(e) => e.currentTarget.releasePointerCapture(e.pointerId)}
    />
  );
}

/** Horizontal splitter above the review dock: dragging it trades viewport height for dock height. */
export function DockResizeHandle({ onChange }: { onChange: (height: number) => void }) {
  const dock = (el: Element) => el.nextElementSibling?.getBoundingClientRect();
  return (
    <div
      className="resize-handle-row"
      role="separator"
      aria-label="Resize bottom panel"
      aria-orientation="horizontal"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'ArrowUp' || e.key === 'ArrowDown')
          onChange((dock(e.currentTarget)?.height ?? 140) + (e.key === 'ArrowUp' ? 20 : -20));
      }}
      onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
      onPointerMove={(e) => {
        const rect = dock(e.currentTarget);
        if (e.currentTarget.hasPointerCapture(e.pointerId) && rect)
          onChange(rect.bottom - e.clientY);
      }}
      onPointerUp={(e) => e.currentTarget.releasePointerCapture(e.pointerId)}
    />
  );
}
