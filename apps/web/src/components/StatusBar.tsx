import { useShallow } from 'zustand/react/shallow';
import { api } from '../api';
import { floorsOf } from '../assembly';
import type { ApiHealth } from '../hooks/useApiHealth';
import type { InputMode } from '../hooks/useProjectWorkflow';
import { useEditor } from '../store';

export function StatusBar({
  inputMode,
  working,
  health,
}: {
  inputMode: InputMode;
  working: boolean;
  health: ApiHealth;
}) {
  // `dirty` and the floor states are selected so the unsaved indicator stays current.
  const state = useEditor(
    useShallow((s) => ({
      scene: s.scene,
      assembly: s.assembly,
      hasUnsaved: s.hasUnsaved,
      dirty: s.dirty,
      assemblyDirty: s.assemblyDirty,
      floorStates: s.floorStates,
    })),
  );
  return (
    <footer className="status-bar">
      <span>
        <i className={api.mock || health === 'API connected' ? 'online-dot' : 'offline-dot'} />
        {inputMode === 'capture'
          ? 'Capture workspace'
          : working
            ? 'Working…'
            : state.hasUnsaved()
              ? 'Unsaved changes'
              : 'Ready'}
        <b>·</b>
        {inputMode === 'capture'
          ? 'Local project storage'
          : state.scene
            ? `Revision ${state.scene.revision}`
            : 'No scene loaded'}
      </span>
      <span>
        {inputMode === 'capture'
          ? 'Photo/video mesh · Scale & alignment in viewer'
          : state.scene
            ? `${state.scene.rooms.length} rooms / ${state.scene.walls.length} walls / ${state.scene.objects.length} objects`
            : state.assembly
              ? `${state.assembly.buildings.length} buildings / ${floorsOf(state.assembly).length} floors`
              : 'Single floor / Metric / Y up'}
        <b>·</b>RoomShift
      </span>
    </footer>
  );
}
