import { useEffect, useState } from 'react';
import {
  confidenceLevel,
  entityById,
  fieldOrigin,
  floorSnap,
  inferredFields,
  wallLength,
  type Entity,
  type Origin,
  type V2,
  type V3,
} from './scene';
import { useEditor } from './store';
import { moveNode, splitWall } from './wallGraph';
import { confidenceColors, originColors, originDescriptions, originLabels } from './geometry';

export function OriginTag({ origin }: { origin: Origin }) {
  return (
    <span
      className="origin-tag"
      title={originDescriptions[origin]}
      style={{ color: originColors[origin], borderColor: originColors[origin] }}
    >
      {originLabels[origin]}
    </span>
  );
}

export function ConfidenceChip({ confidence }: { confidence: number | null }) {
  if (confidence === null) return null;
  const level = confidenceLevel(confidence);
  return (
    <span
      className="confidence-chip"
      style={{ color: confidenceColors[level], borderColor: confidenceColors[level] }}
      title={`${level} confidence: heuristic evidence score from the parser`}
    >
      {level === 'low' && '△ '}
      {Math.round(confidence * 100)}%
    </span>
  );
}

function Meter({ value, label }: { value: number; label?: string }) {
  return (
    <div
      className={label ? 'meter' : 'meter small'}
      role={label ? 'meter' : undefined}
      aria-label={label}
      aria-valuemin={label ? 0 : undefined}
      aria-valuemax={label ? 100 : undefined}
      aria-valuenow={label ? Math.round(value * 100) : undefined}
    >
      <i
        style={{
          width: `${value * 100}%`,
          background: confidenceColors[confidenceLevel(value)],
        }}
      />
    </div>
  );
}

function ConfidencePanel({ entity }: { entity: Entity }) {
  const { confidence, confidenceFactors, origin, userEdited } = entity.provenance;
  const level = confidenceLevel(confidence);
  const unscored = inferredFields(entity);
  return (
    <section className="confidence-panel">
      <h4>
        CONFIDENCE <span className="capitalize">{level === 'none' ? '' : level}</span>
      </h4>
      <div className="kv confidence">
        <span>Detection score</span>
        <b style={{ color: confidenceColors[level] }}>
          {confidence === null
            ? 'Not calibrated / unavailable'
            : `${Math.round(confidence * 100)}%`}
        </b>
      </div>
      {confidence !== null && <Meter value={confidence} label="Detection confidence" />}
      <p className="hint">
        {confidence !== null
          ? "How strongly the drawing supports this element's position and shape. A heuristic score from measured signals, not a calibrated probability."
          : origin === 'user'
            ? 'Placed by you, so nothing about it was inferred.'
            : origin === 'generated'
              ? 'Synthetic or procedural data; no detection took place.'
              : 'The producer did not report a score for this element.'}
      </p>
      {confidenceFactors?.map((f) => (
        <div className="factor" key={f.label}>
          <div className="kv">
            <span>{f.label}</span>
            <b style={{ color: confidenceColors[confidenceLevel(f.score)] }}>
              {Math.round(f.score * 100)}%
            </b>
          </div>
          <Meter value={f.score} />
          <p className="factor-detail">{f.detail}</p>
        </div>
      ))}
      {unscored.length > 0 && (
        <p className="note inferred-note">
          <b>Not read from the drawing:</b> {unscored.join(', ')}. These values are assumed or
          derived, so the score does not cover them. Check them against the real space.
        </p>
      )}
      {userEdited && confidence !== null && (
        <p className="hint">
          The score describes the original reconstruction. Fields you edited are tagged{' '}
          <OriginTag origin="user" />.
        </p>
      )}
    </section>
  );
}

function NumberField({
  label,
  value,
  onCommit,
  step = 0.1,
  origin,
}: {
  label: string;
  value: number;
  onCommit: (value: number) => void;
  step?: number;
  origin?: Origin;
}) {
  const [draft, setDraft] = useState(String(Number(value.toFixed(4))));
  useEffect(() => setDraft(String(Number(value.toFixed(4)))), [value]);
  return (
    <label className="number-field">
      <span>
        {label}
        {origin && <OriginTag origin={origin} />}
      </span>
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
              <div className="entity-badges">
                <OriginTag origin={entity.provenance.origin} />
                <ConfidenceChip confidence={entity.provenance.confidence} />
              </div>
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
                <div className="field-label">
                  Position · bottom-face centre{' '}
                  <OriginTag origin={fieldOrigin(entity, 'position')} />
                </div>
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
                  origin={fieldOrigin(entity, 'rotationY')}
                  onCommit={(n) => patch('rotationY', (n * Math.PI) / 180)}
                />
                <div className="field-label">
                  Dimensions <OriginTag origin={fieldOrigin(entity, 'dimensions')} />
                </div>
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
                  origin={fieldOrigin(entity, 'height')}
                  onCommit={(n) => patch('height', n)}
                />
                <NumberField
                  label="Wall thickness"
                  value={entity.thickness}
                  origin={fieldOrigin(entity, 'thickness')}
                  step={0.01}
                  onCommit={(n) => patch('thickness', n)}
                />
                <div className="field-label">
                  Centerline <OriginTag origin={fieldOrigin(entity, 'start')} />
                </div>
                {(['start', 'end'] as const).map((end) => (
                  <div className="vector-fields" key={end}>
                    {['X', 'Z'].map((axis, i) => (
                      <NumberField
                        key={axis}
                        label={`${end === 'start' ? 'Start' : 'End'} ${axis}`}
                        value={entity[end][i]}
                        step={state.gridStep}
                        onCommit={(n) => {
                          const to: V2 = [...entity[end]];
                          to[i] = n;
                          state.wallEdit((scene) => moveNode(scene, entity[end], to));
                        }}
                      />
                    ))}
                  </div>
                ))}
                <NumberField
                  label="Length"
                  value={wallLength(entity)}
                  step={state.gridStep}
                  onCommit={(n) => {
                    const l = wallLength(entity);
                    if (n <= 0) return useEditor.setState({ error: 'Length must be positive.' });
                    const to: V2 = [
                      entity.start[0] + ((entity.end[0] - entity.start[0]) / l) * n,
                      entity.start[1] + ((entity.end[1] - entity.start[1]) / l) * n,
                    ];
                    state.wallEdit((scene) => moveNode(scene, entity.end, to));
                  }}
                />
                <div className="button-row">
                  <button
                    onClick={() =>
                      state.wallEdit((scene) => {
                        splitWall(scene, entity.id, [
                          (entity.start[0] + entity.end[0]) / 2,
                          (entity.start[1] + entity.end[1]) / 2,
                        ]);
                      })
                    }
                  >
                    Split
                  </button>
                  <button onClick={state.remove}>Delete</button>
                </div>
                <p className="hint">
                  Drag the amber end handles or the cyan middle handle in the viewport. Rooms are
                  rebuilt from the walls; doors and windows keep their position.
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
                    origin={fieldOrigin(entity, key)}
                    onCommit={(n) => patch(key, n)}
                  />
                ))}
                <p className="hint">
                  Offset starts at wall.start. Doors must have a bottom of 0 m. Drag the handle in
                  the viewport to slide it along the wall.
                </p>
                <button className="wide" onClick={state.remove}>
                  Delete opening
                </button>
              </>
            )}
            {'polygon' in entity && (
              <>
                <label className="field">
                  Room name
                  <input
                    key={entity.name}
                    aria-label="Room name"
                    defaultValue={entity.name}
                    placeholder="Enter a room name"
                    maxLength={80}
                    disabled={state.busy}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') {
                        e.currentTarget.value = entity.name;
                        e.currentTarget.blur();
                      }
                      if (e.key === 'Enter') e.currentTarget.blur();
                    }}
                    onBlur={(e) => {
                      const name = e.target.value.trim();
                      if (!name) {
                        e.target.value = entity.name;
                        return;
                      }
                      if (name !== entity.name) patch('name', name);
                      else e.target.value = name;
                    }}
                  />
                </label>
                <p className="hint">
                  Shown on the 3D floor. Names read from the blueprint can be corrected here;
                  enter your own name when none was detected. Press Enter to apply, then Save to keep it.
                </p>
                <OriginTag origin={fieldOrigin(entity, 'name')} />
                <NumberField
                  label="Ceiling height"
                  value={entity.height}
                  origin={fieldOrigin(entity, 'height')}
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
          <ConfidencePanel entity={entity} />
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
              <OriginTag origin={entity.provenance.origin} />
            </div>
            <p className="hint">{originDescriptions[entity.provenance.origin]}</p>
            <div className="kv">
              <span>Producer</span>
              <code>{entity.provenance.source}</code>
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
                  <OriginTag origin={origin} />
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
