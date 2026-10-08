import { useEffect, useRef, useState } from 'react';
import { api, type AssemblyEnvelope, type ProjectEnvelope } from './api';
import { useEditor, editorScenes } from './store';
import {
  floorsOf,
  newBuilding,
  newFloor,
  minimumStoryHeight,
  placements,
  layoutErrors,
  type Assembly,
  type Building,
  type Floor,
} from './assembly';

export function BlueprintWizard({
  files,
  existing,
  previous,
  ensureApiReady,
  onComplete,
  onCancel,
}: {
  files: File[];
  existing: Assembly | null;
  previous: ProjectEnvelope | null;
  ensureApiReady: () => Promise<void>;
  onComplete: (result: AssemblyEnvelope) => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus();
  }, []);
  const [mode, setMode] = useState<'floors' | 'buildings' | null>(null);
  const [name, setName] = useState(existing?.name ?? 'Building project');
  const [buildings, setBuildings] = useState<Building[]>(() =>
    existing
      ? structuredClone(existing.buildings)
      : previous
        ? [newBuilding('Building 1', [newFloor(previous.project.id, 'Ground floor')])]
        : [],
  );
  const [rows, setRows] = useState(() =>
    files.map((file) => ({
      file,
      floorName: file.name.replace(/\.[^.]+$/, ''),
      buildingId: '',
      storyHeight: '',
    })),
  );
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState('');
  const [uploaded, setUploaded] = useState<Record<number, ProjectEnvelope>>({});
  function choose(value: 'floors' | 'buildings') {
    setMode(value);
    const base = existing
      ? structuredClone(existing.buildings)
      : previous
        ? [newBuilding('Building 1', [newFloor(previous.project.id, 'Ground floor')])]
        : [];
    if (value === 'floors') {
      const building = base[0] ?? newBuilding('Building 1');
      if (!base.length) base.push(building);
      setRows(rows.map((r) => ({ ...r, buildingId: building.id })));
    } else {
      const added = rows.map((r, i) => newBuilding(`Building ${base.length + i + 1}`));
      base.push(...added);
      setRows(rows.map((r, i) => ({ ...r, buildingId: added[i].id })));
    }
    setBuildings(base);
  }
  async function submit() {
    setBusy(true);
    setMessage('');
    const complete = { ...uploaded };
    try {
      if (
        !name.trim() ||
        !mode ||
        buildings.some(
          (b) => !b.name.trim() && (b.floors.length || rows.some((r) => r.buildingId === b.id)),
        ) ||
        rows.some(
          (r) =>
            !r.buildingId ||
            !r.floorName.trim() ||
            (r.storyHeight &&
              (!Number.isFinite(Number(r.storyHeight)) || Number(r.storyHeight) < 0.3)),
        )
      )
        throw new Error('Set building assignments, floor names, and valid floor heights.');
      await ensureApiReady();
      for (const [index, row] of rows.entries()) {
        if (complete[index]) continue;
        setMessage(`Uploading ${index + 1} of ${rows.length}: ${row.file.name}`);
        complete[index] = await api.createProject(row.file, row.floorName, true);
        setUploaded({ ...complete });
      }
      const next = buildings
        .map((b) => ({
          ...b,
          floors: [
            ...b.floors,
            ...rows.flatMap((r, i) =>
              r.buildingId === b.id
                ? [
                    {
                      ...newFloor(complete[i].project.id, r.floorName.trim()),
                      storyHeight: r.storyHeight ? Number(r.storyHeight) : null,
                    },
                  ]
                : [],
            ),
          ],
        }))
        .filter((b) => b.floors.length);
      const result = existing
        ? await api.saveAssembly({ ...existing, name: name.trim(), buildings: next })
        : await api.createAssembly({ name: name.trim(), buildings: next });
      onComplete(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-shade">
      <section
        className="blueprint-wizard"
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-label="Group blueprints"
        onKeyDown={(e) => {
          if (e.key === 'Escape' && !busy) onCancel();
          if (e.key !== 'Tab') return;
          const controls = Array.from(
            dialog.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled), input:not(:disabled), select:not(:disabled)',
            ) ?? [],
          );
          const first = controls[0],
            last = controls.at(-1);
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }}
      >
        <h2>How do these blueprints fit together?</h2>
        <p>{files.length} images selected. Every image keeps its own automatic scale.</p>
        <div className="group-choice">
          <button
            disabled={busy}
            className={mode === 'floors' ? 'active' : ''}
            onClick={() => choose('floors')}
          >
            Floors of the same building
          </button>
          <button
            disabled={busy}
            className={mode === 'buildings' ? 'active' : ''}
            onClick={() => choose('buildings')}
          >
            Multiple buildings & floors
          </button>
        </div>
        {mode && (
          <>
            <label className="field">
              Project name
              <input value={name} disabled={busy} onChange={(e) => setName(e.target.value)} />
            </label>
            <div className="wizard-buildings">
              {buildings.map((b) => (
                <label className="field" key={b.id}>
                  Building name
                  <input
                    aria-label={`Name ${b.name}`}
                    disabled={busy}
                    value={b.name}
                    onChange={(e) =>
                      setBuildings(
                        buildings.map((x) => (x.id === b.id ? { ...x, name: e.target.value } : x)),
                      )
                    }
                  />
                </label>
              ))}
            </div>
            {mode === 'buildings' && (
              <p className="hint">
                Each blueprint starts in its own building. Use its Building menu to group multiple
                blueprints as floors in the same building.
              </p>
            )}
            <div className="blueprint-rows">
              {rows.map((row, i) => (
                <div className="blueprint-row" key={row.file.name + i}>
                  <FilePreview file={row.file} />
                  <div>
                    <b>{row.file.name}</b>
                    <small>{completeLabel(uploaded[i])}</small>
                    <label className="field">
                      Building
                      <select
                        disabled={busy}
                        value={row.buildingId}
                        onChange={(e) =>
                          setRows(
                            rows.map((r, j) =>
                              j === i ? { ...r, buildingId: e.target.value } : r,
                            ),
                          )
                        }
                      >
                        {buildings.map((b) => (
                          <option value={b.id} key={b.id}>
                            {b.name || 'Unnamed building'}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      Floor name
                      <input
                        disabled={busy}
                        value={row.floorName}
                        onChange={(e) =>
                          setRows(
                            rows.map((r, j) => (j === i ? { ...r, floorName: e.target.value } : r)),
                          )
                        }
                      />
                    </label>
                    <label className="field">
                      Floor height (m)
                      <input
                        type="number"
                        min="0.3"
                        step="0.1"
                        placeholder="Auto: wall height + 0.20 m slab"
                        disabled={busy}
                        value={row.storyHeight}
                        onChange={(e) =>
                          setRows(
                            rows.map((r, j) =>
                              j === i ? { ...r, storyHeight: e.target.value } : r,
                            ),
                          )
                        }
                      />
                    </label>
                  </div>
                  <div className="floor-order">
                    <button
                      aria-label={`Move ${row.floorName} up`}
                      disabled={busy || Boolean(Object.keys(uploaded).length) || i === 0}
                      onClick={() => {
                        const next = [...rows];
                        [next[i - 1], next[i]] = [next[i], next[i - 1]];
                        setRows(next);
                        setUploaded({});
                      }}
                    >
                      ↑
                    </button>
                    <button
                      aria-label={`Move ${row.floorName} down`}
                      disabled={
                        busy || Boolean(Object.keys(uploaded).length) || i === rows.length - 1
                      }
                      onClick={() => {
                        const next = [...rows];
                        [next[i + 1], next[i]] = [next[i], next[i + 1]];
                        setRows(next);
                        setUploaded({});
                      }}
                    >
                      ↓
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <button
              disabled={busy}
              onClick={() =>
                setBuildings([...buildings, newBuilding(`Building ${buildings.length + 1}`)])
              }
            >
              Add building group
            </button>
            <p className="hint">
              Order within each building runs from bottom to top. Alignment is centered
              automatically and can be adjusted. Slabs are assumed to be 0.20 m thick.
            </p>
          </>
        )}
        {message && <p role="status">{message}</p>}
        <div className="button-row">
          <button disabled={busy} onClick={onCancel}>
            Cancel
          </button>
          <button className="primary" disabled={busy || !mode} onClick={() => void submit()}>
            {busy ? 'Uploading…' : 'Create grouped project'}
          </button>
        </div>
      </section>
    </div>
  );
}
function completeLabel(project?: ProjectEnvelope) {
  return project ? 'Uploaded' : 'Ready to upload';
}
function FilePreview({ file }: { file: File }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (typeof URL.createObjectURL === 'function') {
      const url = URL.createObjectURL(file);
      setUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    const reader = new FileReader();
    reader.onload = () => setUrl(String(reader.result));
    reader.readAsDataURL(file);
    return () => {
      reader.onload = null;
      if (reader.readyState === FileReader.LOADING) reader.abort();
    };
  }, [file]);
  return <img src={url || undefined} alt={`Preview ${file.name}`} />;
}

export function AssemblyControls({ onReconstruct }: { onReconstruct: (ids?: string[]) => void }) {
  const s = useEditor();
  if (!s.assembly) return null;
  const scenes = editorScenes(s),
    floors = floorsOf(s.assembly);
  const active = floors.find((f) => f.projectId === s.activeProjectId);
  const building = s.assembly.buildings.find((b) => b.floors.some((f) => f.id === active?.id));
  function changeFloor(update: (floor: Floor) => void) {
    s.updateAssembly((a) => {
      const f = floorsOf(a).find((f) => f.id === active?.id);
      if (f) update(f);
    });
  }
  const errors = layoutErrors(s.assembly, scenes);
  return (
    <section className="assembly-panel">
      <h4>BUILDINGS & FLOORS</h4>
      <label className="field">
        Grouped project name
        <input
          value={s.assembly.name}
          disabled={s.busy}
          onChange={(e) =>
            s.updateAssembly((a) => {
              a.name = e.target.value;
            })
          }
        />
      </label>
      {s.assembly.buildings.map((b) => (
        <div className="building-tree" key={b.id}>
          <b>{b.name}</b>
          {b.floors.map((f, i) => (
            <button
              className={`floor-row ${f.id === active?.id ? 'active' : ''}`}
              key={f.id}
              disabled={s.busy}
              onClick={() => s.activateFloor(f.projectId)}
            >
              <span>{i === 0 ? 'G' : i}</span>
              <span>
                {f.name}
                <small>
                  {s.jobs[f.projectId]?.status ??
                    (scenes[f.projectId] ? 'Ready' : 'Awaiting reconstruction')}
                  {s.floorStates[f.projectId]?.dirty ||
                  (f.projectId === s.activeProjectId && s.dirty)
                    ? ' · edited'
                    : ''}
                </small>
              </span>
            </button>
          ))}
        </div>
      ))}
      {active && building && (
        <>
          <label className="field">
            Building name
            <input
              value={building.name}
              disabled={s.busy}
              onChange={(e) =>
                s.updateAssembly((a) => {
                  a.buildings.find((b) => b.id === building.id)!.name = e.target.value;
                })
              }
            />
          </label>
          <label className="field">
            Floor name
            <input
              value={active.name}
              disabled={s.busy}
              onChange={(e) =>
                changeFloor((f) => {
                  f.name = e.target.value;
                })
              }
            />
          </label>
          <label className="field">
            Move floor to building
            <select
              value={building.id}
              disabled={s.busy}
              onChange={(e) =>
                s.updateAssembly((a) => {
                  const source = a.buildings.find((b) => b.id === building.id)!;
                  const index = source.floors.findIndex((f) => f.id === active.id);
                  const [floor] = source.floors.splice(index, 1);
                  a.buildings.find((b) => b.id === e.target.value)!.floors.push(floor);
                  a.buildings = a.buildings.filter((b) => b.floors.length);
                })
              }
            >
              {s.assembly.buildings.map((b) => (
                <option value={b.id} key={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <div className="button-row">
            {([-1, 1] as const).map((direction) => (
              <button
                key={direction}
                disabled={
                  s.busy ||
                  (direction < 0
                    ? building.floors[0].id === active.id
                    : building.floors.at(-1)!.id === active.id)
                }
                onClick={() =>
                  s.updateAssembly((a) => {
                    const b = a.buildings.find((b) => b.id === building.id)!;
                    const i = b.floors.findIndex((f) => f.id === active.id);
                    [b.floors[i], b.floors[i + direction]] = [b.floors[i + direction], b.floors[i]];
                  })
                }
              >
                {direction < 0 ? 'Move floor down' : 'Move floor up'}
              </button>
            ))}
          </div>
          <label className="field">
            Floor-to-floor height (m)
            <input
              disabled={s.busy}
              type="number"
              min="0.3"
              step="0.1"
              placeholder={`Auto: ${minimumStoryHeight(scenes[active.projectId]).toFixed(2)} m`}
              value={active.storyHeight ?? ''}
              onChange={(e) =>
                changeFloor((f) => {
                  f.storyHeight = e.target.value === '' ? null : Number(e.target.value);
                })
              }
            />
          </label>
          <small>
            Elevation{' '}
            {placements(s.assembly, scenes)
              .find((p) => p.floor.id === active.id)
              ?.elevation.toFixed(2)}{' '}
            m · inferred 0.20 m slab
          </small>
          <h4>ALIGNMENT</h4>
          <p className="hint">
            Centered automatically. Adjust the assembled view to line up walls.
          </p>
          <button disabled={s.busy} onClick={() => s.setView({ mode: 'all' })}>
            Show assembled view
          </button>
          {(['Floor', 'Building'] as const).map((kind) => (
            <div key={kind}>
              <b>{kind} placement</b>
              {[0, 1, 2].map((index) => {
                const item = kind === 'Floor' ? active : building;
                return (
                  <label className="field" key={index}>
                    {kind}{' '}
                    {index === 0
                      ? 'X offset (m)'
                      : index === 1
                        ? 'Z offset (m)'
                        : 'rotation (degrees)'}
                    <input
                      type="number"
                      step={index === 2 ? '1' : '0.1'}
                      disabled={s.busy || s.view.mode === 'exploded'}
                      value={
                        index === 2
                          ? +((item.rotationY * 180) / Math.PI).toFixed(3)
                          : item.offset[index]
                      }
                      onChange={(e) =>
                        s.updateAssembly((a) => {
                          const target =
                            kind === 'Floor'
                              ? floorsOf(a).find((f) => f.id === active.id)!
                              : a.buildings.find((b) => b.id === building.id)!;
                          if (index === 2)
                            target.rotationY = (Number(e.target.value) * Math.PI) / 180;
                          else target.offset[index] = Number(e.target.value);
                        })
                      }
                    />
                  </label>
                );
              })}
            </div>
          ))}
          <button
            disabled={s.busy}
            onClick={() =>
              changeFloor((f) => {
                f.offset = [0, 0];
                f.rotationY = 0;
              })
            }
          >
            Reset floor alignment
          </button>
          {s.jobs[active.projectId]?.error && (
            <p role="alert">{s.jobs[active.projectId].error!.message}</p>
          )}
          <button
            className="wide"
            disabled={s.busy}
            onClick={() => onReconstruct([active.projectId])}
          >
            {scenes[active.projectId] ? 'Reconstruct this floor' : 'Retry this floor'}
          </button>
        </>
      )}
      {errors.map((error) => (
        <p role="alert" key={error}>
          {error}
        </p>
      ))}
    </section>
  );
}
export function FloorViews() {
  const s = useEditor();
  if (!s.assembly) return null;
  const buildings = s.assembly.buildings;
  const building = buildings.find((b) => b.id === s.view.buildingId);
  const floors = building?.floors ?? floorsOf(s.assembly);
  const disabled = s.busy || s.workspace === 'Explore';
  const selected = floors.find((f) => f.id === s.view.floorId);
  const selectFloor = (id: string) => {
    const floor = floors.find((f) => f.id === id);
    if (!floor) return;
    s.activateFloor(floor.projectId);
    s.setView({ mode: 'floor', floorId: id });
  };
  return (
    <div className="floor-view-bar" aria-label="Floor viewing controls">
      <label>
        Building
        <select
          disabled={disabled}
          value={s.view.buildingId ?? ''}
          onChange={(e) => {
            const id = e.target.value || null;
            const b = buildings.find((b) => b.id === id);
            if (b && !b.floors.some((f) => f.projectId === s.activeProjectId))
              s.activateFloor(
                b.floors.find((f) => editorScenes(s)[f.projectId])?.projectId ??
                  b.floors[0].projectId,
              );
            s.setView({ buildingId: id, mode: 'all', floorId: null });
          }}
        >
          <option value="">All buildings</option>
          {buildings.map((b) => (
            <option value={b.id} key={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        View
        <select
          disabled={disabled}
          value={s.view.mode}
          onChange={(e) => {
            const mode = e.target.value as 'all' | 'floor' | 'exploded';
            if (mode === 'floor') selectFloor(selected?.id ?? floors[0].id);
            else s.setView({ mode });
          }}
        >
          <option value="all">All floors</option>
          <option value="floor">Selected floor</option>
          <option value="exploded">Exploded view</option>
        </select>
      </label>
      {s.view.mode === 'floor' && (
        <>
          <label>
            Floor
            <select
              disabled={disabled}
              value={s.view.floorId ?? ''}
              onChange={(e) => selectFloor(e.target.value)}
            >
              {floors.map((f) => (
                <option value={f.id} key={f.id}>
                  {buildings.find((b) => b.floors.some((x) => x.id === f.id))?.name} / {f.name}
                </option>
              ))}
            </select>
          </label>
          {[-1, 1].map((d) => (
            <button
              key={d}
              aria-label={d < 0 ? 'Previous floor' : 'Next floor'}
              disabled={
                disabled ||
                !selected ||
                (d < 0 ? floors[0].id === selected.id : floors.at(-1)!.id === selected.id)
              }
              onClick={() => selectFloor(floors[floors.indexOf(selected!) + d].id)}
            >
              {d < 0 ? '←' : '→'}
            </button>
          ))}
        </>
      )}
      {s.view.mode === 'exploded' && (
        <label>
          Visual gap (m)
          <input
            type="number"
            min="0"
            max="20"
            step="0.5"
            value={s.view.gap}
            disabled={disabled}
            onChange={(e) => s.setView({ gap: Math.max(0, Math.min(20, Number(e.target.value))) })}
          />
        </label>
      )}
      {s.workspace === 'Explore' && (
        <small>Walking on the active floor at its actual elevation</small>
      )}
    </div>
  );
}
