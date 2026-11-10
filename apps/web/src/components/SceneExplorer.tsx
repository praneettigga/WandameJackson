import { useShallow } from 'zustand/react/shallow';
import { ComponentLibrary } from '../ComponentLibrary';
import { originColors, originLabels } from '../geometry';
import { InfrastructurePanel } from '../InfrastructurePanel';
import { ConfidenceChip } from '../Inspector';
import { entities } from '../scene';
import { useEditor } from '../store';

/** Entity tree of the loaded scene, services planner and component library (left panel). */
export function SceneExplorer({ disabled }: { disabled: boolean }) {
  const state = useEditor(
    useShallow((s) => ({
      scene: s.scene,
      selectedId: s.selectedId,
      select: s.select,
    })),
  );
  return (
    <>
      <div className="panel-heading">
        SCENE EXPLORER <span>{state.scene ? entities(state.scene).length : 0}</span>
      </div>
      <div className="scene-tree">
        {state.scene &&
          (
            [
              ['Rooms', state.scene.rooms, '▱'],
              ['Walls', state.scene.walls, '▥'],
              ['Openings', state.scene.openings, '◫'],
              ['Objects', state.scene.objects, '▧'],
            ] as const
          ).map(([title, list, icon]) => (
            <div className="tree-group" key={title}>
              <h4>
                ⌄ {title}
                <span>{list.length}</span>
              </h4>
              {list.map((entity) => (
                <button
                  key={entity.id}
                  aria-label={`Select ${'name' in entity ? entity.name : entity.id}`}
                  className={`tree-item ${state.selectedId === entity.id ? 'selected' : ''}`}
                  onClick={() => state.select(entity.id)}
                >
                  <span>{icon}</span>
                  <span>
                    {'name' in entity
                      ? entity.name
                      : 'type' in entity
                        ? `${entity.type} / ${entity.id}`
                        : entity.id}
                  </span>
                  <ConfidenceChip confidence={entity.provenance.confidence} />
                  <i
                    title={originLabels[entity.provenance.origin]}
                    style={{ background: originColors[entity.provenance.origin] }}
                  />
                </button>
              ))}
            </div>
          ))}
      </div>
      <InfrastructurePanel />
      <ComponentLibrary disabled={disabled} />
    </>
  );
}
