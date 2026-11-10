import { useShallow } from 'zustand/react/shallow';
import { snapKindLabels, type SnapKind } from '../snapping';
import { useEditor } from '../store';
import { UnitSelect } from './controls';

/** Transform modes, measure, architecture tools, undo/redo and snapping above the viewport. */
export function ViewportToolbar({ disabled }: { disabled: boolean }) {
  const state = useEditor(
    useShallow((s) => ({
      scene: s.scene,
      workspace: s.workspace,
      mode: s.mode,
      measure: s.measure,
      frame: s.frame,
      tool: s.tool,
      wallAlign: s.wallAlign,
      past: s.past,
      future: s.future,
      undo: s.undo,
      redo: s.redo,
      snap: s.snap,
      snapSettings: s.snapSettings,
    })),
  );
  return (
      <div className="viewport-toolbar">
        <div className="tool-group">
          {(
            [
              ['translate', '↔', 'Move', 'G'],
              ['rotate', '↻', 'Rotate', 'R'],
              ['scale', '⤢', 'Resize', 'S'],
            ] as const
          ).map(([mode, icon, label, key]) => (
            <button
              key={mode}
              title={`${label} (${key})`}
              aria-label={label}
              disabled={!state.scene || state.workspace === 'Explore'}
              className={state.mode === mode && !state.measure ? 'active' : ''}
              aria-pressed={state.mode === mode && !state.measure}
              onClick={() => useEditor.setState({ mode, measure: false })}
            >
              {icon}
              <span>{label}</span>
            </button>
          ))}
          <button
            title="Frame selected (F)"
            aria-label="Frame selected"
            disabled={!state.scene}
            onClick={() => useEditor.setState({ frame: state.frame + 1 })}
          >
            ⊡
          </button>
          <button
            title="Measure two surface points"
            className={state.measure ? 'active' : ''}
            aria-pressed={state.measure}
            disabled={!state.scene || state.workspace === 'Explore'}
            onClick={() =>
              useEditor.setState({ measure: !state.measure, measures: [], tool: 'select' })
            }
          >
            ⌁<span>Measure</span>
          </button>
          <UnitSelect label="Measurement unit" />
        </div>
        <div className="tool-group" aria-label="Architecture tools">
          {(
            [
              ['wall', '▭', 'Wall', 'W'],
              ['door', '◫', 'Door', 'D'],
              ['window', '▤', 'Window', 'N'],
            ] as const
          ).map(([tool, icon, label, key]) => (
            <button
              key={tool}
              title={`${label} tool (${key}) · snaps to walls; snap distance adapts to zoom`}
              aria-label={`${label} tool`}
              disabled={!state.scene || state.workspace === 'Explore'}
              className={state.tool === tool ? 'active' : ''}
              aria-pressed={state.tool === tool}
              onClick={() =>
                useEditor.setState({
                  tool: state.tool === tool ? 'select' : tool,
                  measure: false,
                  workspace: 'Edit',
                })
              }
            >
              {icon}
              <span>{label}</span>
            </button>
          ))}
          <button
            title="Furniture turns its back to a wall when it snaps flush"
            className={state.wallAlign ? 'active' : ''}
            aria-pressed={state.wallAlign}
            disabled={!state.scene}
            onClick={() => useEditor.setState({ wallAlign: !state.wallAlign })}
          >
            ⊥<span>Align</span>
          </button>
        </div>
        <div className="tool-group">
          <button
            aria-label="Undo"
            title="Undo (Ctrl+Z)"
            disabled={!state.past.length || disabled}
            onClick={state.undo}
          >
            ↶
          </button>
          <button
            aria-label="Redo"
            title="Redo (Ctrl+Shift+Z)"
            disabled={!state.future.length || disabled}
            onClick={state.redo}
          >
            ↷
          </button>
          <button
            className={state.snap ? 'active' : ''}
            aria-pressed={state.snap}
            onClick={() => useEditor.setState({ snap: !state.snap })}
            title="Zoom-adaptive translation grid (see SNAP readout); 15° rotation; 0.1 scale steps"
          >
            ⌗<span>Snap</span>
          </button>
          <details className="snap-menu">
            <summary title="Choose snap targets" aria-label="Snap targets">
              ▾
            </summary>
            <div className="snap-menu-panel">
              <small>Snap distance is 10 px on screen, so it adapts to zoom.</small>
              {(Object.keys(snapKindLabels) as SnapKind[]).map((kind) => (
                <label key={kind}>
                  <input
                    type="checkbox"
                    checked={state.snapSettings[kind]}
                    onChange={(e) =>
                      useEditor.setState({
                        snapSettings: { ...state.snapSettings, [kind]: e.target.checked },
                      })
                    }
                  />
                  {snapKindLabels[kind]}
                </label>
              ))}
            </div>
          </details>
        </div>
      </div>
  );
}
