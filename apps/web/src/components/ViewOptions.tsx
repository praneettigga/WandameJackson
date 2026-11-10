import { useShallow } from 'zustand/react/shallow';
import { api } from '../api';
import { confidenceColors, originColors, originLabels } from '../geometry';
import { WallsToggle } from '../InfrastructurePanel';
import { useEditor } from '../store';

/** Display toggles under the viewport and their legends. */
export function ViewOptions({
  disabled,
  guarded,
}: {
  disabled: boolean;
  guarded: (action: () => Promise<void>) => Promise<void>;
}) {
  const state = useEditor(
    useShallow((s) => ({
      scene: s.scene,
      roomLabels: s.roomLabels,
      xray: s.xray,
      confidenceMap: s.confidenceMap,
      ceilings: s.ceilings,
      compare: s.compare,
    })),
  );
  return (
      <div className="view-options">
        <label title="Select a room floor or label to edit its name in Properties.">
          <input
            type="checkbox"
            checked={state.roomLabels}
            onChange={(e) => useEditor.setState({ roomLabels: e.target.checked })}
          />
          Room labels
        </label>
        <label>
          <input
            type="checkbox"
            checked={state.xray}
            onChange={(e) =>
              useEditor.setState({
                xray: e.target.checked,
                confidenceMap: e.target.checked ? false : state.confidenceMap,
              })
            }
          />
          Provenance X-Ray
        </label>
        <label>
          <input
            type="checkbox"
            checked={state.confidenceMap}
            onChange={(e) =>
              useEditor.setState({
                confidenceMap: e.target.checked,
                xray: e.target.checked ? false : state.xray,
              })
            }
          />
          Confidence map
        </label>
        <label>
          <input
            type="checkbox"
            checked={state.ceilings}
            onChange={(e) => useEditor.setState({ ceilings: e.target.checked })}
          />
          Ceilings
        </label>
        <WallsToggle compact />
        <button
          disabled={!state.scene || disabled}
          className={state.compare ? 'active' : ''}
          aria-pressed={state.compare}
          onClick={() =>
            void guarded(async () => {
              if (state.compare) useEditor.setState({ compare: false });
              else
                useEditor.setState({
                  sourceScene: await api.getSourceScene(state.scene!.id),
                  compare: true,
                });
            })
          }
        >
          ◫ Compare Original
        </button>
        {state.xray && (
          <div className="origin-legend">
            {Object.entries(originColors).map(([label, color]) => (
              <span key={label}>
                <i style={{ background: color }} />
                {originLabels[label as keyof typeof originLabels]}
              </span>
            ))}
          </div>
        )}
        {state.confidenceMap && (
          <div className="origin-legend">
            {(
              [
                ['high', '≥ 80%'],
                ['medium', '50–79%'],
                ['low', '< 50%'],
                ['none', 'not scored'],
              ] as const
            ).map(([level, range]) => (
              <span key={level}>
                <i style={{ background: confidenceColors[level] }} />
                {level === 'none' ? range : `${level} ${range}`}
              </span>
            ))}
          </div>
        )}
      </div>
  );
}
