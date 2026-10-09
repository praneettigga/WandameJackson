import {
  RULES,
  cachedPlan,
  roomUseLabels,
  serviceInfo,
  serviceKinds,
  type Proposal,
  type RoomUse,
} from './infrastructure';
import { useEditor } from './store';
import { formatLength } from './units';

/** The plan for the active scene and the proposal being shown (the user's pick, else the recommended one). */
export function useInfrastructure() {
  const scene = useEditor((s) => s.scene);
  const uses = useEditor((s) => (scene ? s.roomUses[scene.id] : undefined));
  const chosen = useEditor((s) => s.serviceProposal);
  if (!scene) return { plan: null, proposal: null };
  const plan = cachedPlan(scene, uses);
  const proposal =
    plan.proposals.find((p) => p.strategy.id === chosen) ?? plan.proposals.find((p) => p.recommended) ?? null;
  return { plan, proposal };
}

/** Solid walls (normal view) or see-through walls with the services inside them. */
export function WallsToggle({ compact = false }: { compact?: boolean }) {
  const seeThrough = useEditor((s) => s.seeThrough);
  const disabled = useEditor((s) => !s.scene);
  return (
    <div className={`walls-toggle ${compact ? 'compact' : ''}`} role="group" aria-label="Wall display">
      {!compact && <span>Walls</span>}
      <button
        className={!seeThrough ? 'active' : ''}
        aria-pressed={!seeThrough}
        disabled={disabled}
        title="Normal view: solid walls, services hidden (I)"
        onClick={() => useEditor.setState({ seeThrough: false, serviceFocus: null })}
      >
        ■ Solid
      </button>
      <button
        className={seeThrough ? 'active' : ''}
        aria-pressed={seeThrough}
        disabled={disabled}
        title="See through walls, floors and ceilings to the wiring and plumbing inside (I)"
        onClick={() => useEditor.setState({ seeThrough: true })}
      >
        ▢ See-through
      </button>
    </div>
  );
}

const clashCounts = (p: Proposal) => {
  const warnings = p.clashes.filter((c) => c.severity === 'warning').length;
  const notes = p.clashes.length - warnings;
  return [warnings && `${warnings} ${warnings === 1 ? 'clash' : 'clashes'}`, notes && `${notes} ${notes === 1 ? 'note' : 'notes'}`]
    .filter(Boolean)
    .join(' · ') || 'No clashes';
};

export function InfrastructurePanel() {
  const { plan, proposal } = useInfrastructure();
  const scene = useEditor((s) => s.scene);
  const layers = useEditor((s) => s.serviceLayers);
  const focus = useEditor((s) => s.serviceFocus);
  const unit = useEditor((s) => s.lengthUnit);
  const setRoomUse = useEditor((s) => s.setRoomUse);
  if (!scene || !plan) return null;
  const length = (m: number) => formatLength(m, unit);
  return (
    <section className="services" aria-label="Invisible infrastructure">
      <h4>
        INVISIBLE INFRASTRUCTURE <span>{proposal ? clashCounts(proposal).toUpperCase() : 'NO ROOMS'}</span>
      </h4>
      <WallsToggle />
      {plan.notes.map((note, i) => (
        <p className="hint" key={i}>
          {note}
        </p>
      ))}
      {proposal && (
        <>
          <div className="service-layers">
            {serviceKinds.map((kind) => (
              <label key={kind}>
                <input
                  type="checkbox"
                  checked={layers[kind]}
                  onChange={(e) =>
                    useEditor.setState({ serviceLayers: { ...layers, [kind]: e.target.checked } })
                  }
                />
                <i style={{ background: serviceInfo[kind].color }} />
                {serviceInfo[kind].label}
                <span>{length(proposal.totals[kind])}</span>
              </label>
            ))}
          </div>
          <details className="services-group" open>
            <summary>
              Layouts <span>{plan.proposals.length}</span>
            </summary>
            <div className="proposals" role="radiogroup" aria-label="Infrastructure layouts">
              {[...plan.proposals]
                .sort((a, b) => a.score - b.score)
                .map((p) => {
                  const wiring = p.totals.power + p.totals.lighting;
                  const water = p.totals.cold + p.totals.hot;
                  return (
                    <button
                      key={p.strategy.id}
                      role="radio"
                      aria-checked={p === proposal}
                      className={`proposal ${p === proposal ? 'selected' : ''}`}
                      title={p.strategy.description}
                      onClick={() =>
                        useEditor.setState({
                          serviceProposal: p.recommended ? null : p.strategy.id,
                          serviceFocus: null,
                          seeThrough: true,
                        })
                      }
                    >
                      <b>
                        {p.strategy.name}
                        {p.recommended && <em>Recommended</em>}
                      </b>
                      <span>
                        Wiring {length(wiring)} · Water {length(water)} · Waste {length(p.totals.waste)}
                      </span>
                      <span className={p.clashes.some((c) => c.severity === 'warning') ? 'warn' : 'ok'}>
                        {clashCounts(p)}
                      </span>
                    </button>
                  );
                })}
            </div>
            <p className="hint">{proposal.strategy.description}</p>
          </details>
          <details className="services-group" open>
            <summary>
              Clashes & checks <span>{proposal.clashes.length}</span>
            </summary>
            {proposal.clashes.length ? (
              <ul className="clash-list">
                {proposal.clashes.map((c) => (
                  <li key={c.id} className={`issue ${c.severity} ${c.id === focus ? 'focused' : ''}`}>
                    <span>{c.severity === 'warning' ? '◆' : '◇'}</span>
                    <div>
                      {c.message}
                      <small>{c.suggestion}</small>
                    </div>
                    <button
                      onClick={() => {
                        const s = useEditor.getState();
                        if (c.wallId) s.select(c.wallId);
                        useEditor.setState({
                          seeThrough: true,
                          serviceFocus: c.id,
                          frame: s.frame + (c.wallId ? 1 : 0),
                        });
                      }}
                    >
                      Show
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="hint">No collisions between cables, pipes, openings or furniture.</p>
            )}
          </details>
        </>
      )}
      <details className="services-group">
        <summary>
          Room uses <span>{scene.rooms.length}</span>
        </summary>
        {scene.rooms.map((room) => {
          const assignment = plan.uses[room.id];
          return (
            <label className="field room-use" key={room.id}>
              <span>
                {room.name || room.id}
                {assignment?.source === 'assumed' && <em title="Guessed from size and layout">assumed</em>}
              </span>
              <select
                aria-label={`Use of ${room.name || room.id}`}
                value={assignment?.source === 'user' ? assignment.use : ''}
                onChange={(e) => setRoomUse(room.id, (e.target.value || null) as RoomUse | null)}
              >
                <option value="">
                  Auto ({assignment ? roomUseLabels[assignment.use] : '—'})
                </option>
                {(Object.keys(roomUseLabels) as RoomUse[]).map((use) => (
                  <option key={use} value={use}>
                    {roomUseLabels[use]}
                  </option>
                ))}
              </select>
            </label>
          );
        })}
        <p className="hint">
          Wet rooms decide where pipes go. Uses are kept in this browser and are not saved with the scene.
        </p>
      </details>
      <p className="hint">
        Concept layout from rules of thumb: sockets at {RULES.outletHeight} m, switches at {RULES.switchHeight} m,
        cables and pipes kept {Math.round(RULES.separation * 1000)} mm apart, waste falling 1:50. Not a substitute for a
        qualified electrician or plumber.
      </p>
    </section>
  );
}
