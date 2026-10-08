import { useEffect, useState } from 'react';
import { entityById, floorSnap, wallLength, type V3 } from './scene';
import { useEditor } from './store';
import { originColors } from './geometry';

function NumberField({
  label,
  value,
  onCommit,
  step = 0.1,
}: {
  label: string;
  value: number;
  onCommit: (value: number) => void;
  step?: number;
}) {
  const [draft, setDraft] = useState(String(Number(value.toFixed(4))));
  useEffect(() => setDraft(String(Number(value.toFixed(4)))), [value]);
  return (
    <label className="number-field">
      <span>{label}</span>
      <input
        aria-label={label}
        type="number"
        step={step}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur();
        }}
        onBlur={() => {
          const n = Number(draft);
          if (!draft.trim() || !Number.isFinite(n)) {
            setDraft(String(value));
            useEditor.setState({ error: `${label} must be a finite number.` });
            return;
          }
          if (n !== value) onCommit(n);
          const entity =
            useEditor.getState().scene &&
            entityById(useEditor.getState().scene!, useEditor.getState().selectedId);
          if (entity && useEditor.getState().error) setDraft(String(value));
        }}
      />
    </label>
  );
}
export function Inspector() {
  const state = useEditor();
  const entity = state.scene ? entityById(state.scene, state.selectedId) : null;
  const patch = (field: string, value: unknown) =>
    entity && state.patch(entity.id, { [field]: value });
  return (
    <aside className="inspector panel">
      <div className="panel-heading">
        <span>PROPERTIES</span>
        <span className="muted">◈</span>
      </div>
      {!entity ? (
        <div className="inspector-empty">
          <span>↖</span>
          <h3>Inspect your space</h3>
          <p>
            Select an element in the viewport or scene explorer to see its geometry and provenance.
          </p>
        </div>
      ) : (
        <div className="inspector-content" key={entity.id}>
          <div className="entity-title">
            <span className="entity-icon">
              {'position' in entity
                ? '▧'
                : 'polygon' in entity
                  ? '▱'
                  : 'wallId' in entity
                    ? '◫'
                    : '▥'}
            </span>
            <div>
              <h3>{'name' in entity ? entity.name : 'type' in entity ? entity.type : 'Wall'}</h3>
              <code>{entity.id}</code>
            </div>
          </div>
          <section>
            <h4>
              GEOMETRY <span>m</span>
            </h4>
            {'position' in entity && (
              <>
                <label className="field">
                  Name
                  <input
                    key={entity.name}
                    defaultValue={entity.name}
                    onBlur={(e) => {
                      if (e.target.value !== entity.name) patch('name', e.target.value);
                    }}
                  />
                </label>
                <div className="field-label">Position · bottom-face centre</div>
                <div className="vector-fields">
                  {['X', 'Y', 'Z'].map((axis, i) => (
                    <NumberField
                      key={axis}
                      label={`Position ${axis}`}
                      value={entity.position[i]}
                      onCommit={(n) => {
                        const p: V3 = [...entity.position];
                        p[i] = state.snap ? Math.round(n * 10) / 10 : n;
                        patch('position', p);
                      }}
                    />
                  ))}
                </div>
                <NumberField
                  label="Rotation Y · degrees"
                  value={(entity.rotationY * 180) / Math.PI}
                  step={15}
                  onCommit={(n) => patch('rotationY', (n * Math.PI) / 180)}
                />
                <div className="field-label">Dimensions</div>
                <div className="vector-fields">
                  {['Width', 'Height', 'Depth'].map((axis, i) => (
                    <NumberField
                      key={axis}
                      label={axis}
                      value={entity.dimensions[i]}
                      onCommit={(n) => {
                        const d: V3 = [...entity.dimensions];
                        d[i] = n;
                        patch('dimensions', d);
                      }}
                    />
                  ))}
                </div>
                <button
                  className="wide"
                  onClick={() => patch('position', floorSnap(entity).position)}
                >
                  ↓ Snap to floor
                </button>
                <div className="button-row">
                  <button onClick={state.duplicate}>Duplicate</button>
                  <button onClick={state.remove}>Delete</button>
                </div>
              </>
            )}
            {'start' in entity && (
              <>
                <div className="kv">
                  <span>Centerline length</span>
                  <b>{wallLength(entity).toFixed(3)} m</b>
                </div>
                <NumberField
                  label="Wall height"
                  value={entity.height}
                  onCommit={(n) => patch('height', n)}
                />
                <NumberField
                  label="Wall thickness"
                  value={entity.thickness}
                  step={0.01}
                  onCommit={(n) => patch('thickness', n)}
                />
                <p className="hint">
                  Centerline topology is fixed. Changes must preserve opening fit.
                </p>
              </>
            )}
            {'wallId' in entity && (
              <>
                <div className="kv">
                  <span>Wall</span>
                  <code>{entity.wallId}</code>
                </div>
                {(['offset', 'width', 'height', 'bottom'] as const).map((key) => (
                  <NumberField
                    key={key}
                    label={`Opening ${key}`}
                    value={entity[key]}
                    onCommit={(n) => patch(key, n)}
                  />
                ))}
                <p className="hint">
                  Offset starts at wall.start. Doors must have a bottom of 0 m.
                </p>
              </>
            )}
            {'polygon' in entity && (
              <>
                <NumberField
                  label="Ceiling height"
                  value={entity.height}
                  onCommit={(n) => patch('height', n)}
                />
                <div className="kv">
                  <span>Polygon</span>
                  <b>{entity.polygon.length} vertices</b>
                </div>
                <p className="hint">Floor at Y = 0. Ceiling is derived from room height.</p>
              </>
            )}
          </section>
          <section>
            <h4>
              PROVENANCE{' '}
              <span
                className="color-dot"
                style={{ background: originColors[entity.provenance.origin] }}
              />
            </h4>
            <div className="kv">
              <span>Origin</span>
              <b className="capitalize">{entity.provenance.origin}</b>
            </div>
            <div className="kv">
              <span>Producer</span>
              <code>{entity.provenance.source}</code>
            </div>
            <div className="kv confidence">
              <span>Confidence</span>
              <b>
                {entity.provenance.confidence === null
                  ? 'Not calibrated / unavailable'
                  : `${(entity.provenance.confidence * 100).toFixed(1)}%`}
              </b>
            </div>
            <div className="kv">
              <span>User edited</span>
              <b>{entity.provenance.userEdited ? 'Yes' : 'No'}</b>
            </div>
            <h5>FIELD ORIGINS</h5>
            {Object.entries(entity.provenance.fieldOrigins).length ? (
              Object.entries(entity.provenance.fieldOrigins).map(([field, origin]) => (
                <div className="kv" key={field}>
                  <code>{field}</code>
                  <span style={{ color: originColors[origin] }}>{origin}</span>
                </div>
              ))
            ) : (
              <p className="hint">No field overrides.</p>
            )}
            {entity.provenance.notes.map((note, i) => (
              <p className="note" key={i}>
                {note}
              </p>
            ))}
          </section>
        </div>
      )}
      {state.scene && (
        <section className="parser-info">
          <h4>RECONSTRUCTION</h4>
          <div className="kv">
            <span>Parser</span>
            <code>
              {state.scene.reconstruction.parser.name}@{state.scene.reconstruction.parser.version}
            </code>
          </div>
          <p className="hint">
            {state.scene.reconstruction.parser.checkpoint ?? 'No model checkpoint'} ·{' '}
            {state.scene.reconstruction.parser.license ?? 'No model license reported'}
          </p>
          <div className="kv">
            <span>Scale</span>
            <b>{state.scene.source.calibration.metersPerPixel.toFixed(5)} m/px</b>
          </div>
          <div className="kv">
            <span>Scale source</span>
            <b>
              {state.scene.source.calibration.method === 'printed-dimension'
                ? 'Printed measurement'
                : state.scene.source.calibration.method
                  ? 'Estimated scale'
                  : 'Manual reference'}
            </b>
          </div>
        </section>
      )}
    </aside>
  );
}
