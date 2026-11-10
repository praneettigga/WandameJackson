import { useShallow } from 'zustand/react/shallow';
import { api } from '../api';
import type { ApiHealth } from '../hooks/useApiHealth';
import type { InputMode } from '../hooks/useProjectWorkflow';
import { useEditor } from '../store';

export const WORKSPACES = ['Reconstruct', 'Edit', 'Inspect', 'Explore'] as const;
export type WorkspaceName = (typeof WORKSPACES)[number];

/** Brand, project title, save action, workspace tabs and API status. */
export function AppHeader({
  inputMode,
  projectName,
  health,
  saving,
  saveDisabled,
  onSave,
  canOpenWorkspace,
  onWorkspace,
}: {
  inputMode: InputMode;
  projectName: string | undefined;
  health: ApiHealth;
  saving: boolean;
  saveDisabled: boolean;
  onSave: () => void;
  canOpenWorkspace: (name: WorkspaceName) => boolean;
  onWorkspace: (name: WorkspaceName) => void;
}) {
  const state = useEditor(
    useShallow((s) => ({
      workspace: s.workspace,
      dirty: s.dirty,
      assemblyName: s.assembly?.name,
      sceneName: s.scene?.name,
      grouped: !!s.assembly,
    })),
  );
  return (
    <>
      <header className="app-header">
        <span className="brand">
          <span className="brand-mark" aria-hidden="true">
            ▱
          </span>
          ROOMSHIFT
        </span>
        <div className="project-title">
          {inputMode === 'capture'
            ? 'Room capture'
            : (state.assemblyName ?? state.sceneName ?? projectName ?? 'Untitled space')}
          {state.dirty && <span title="Unsaved changes" className="dirty-dot" />}
        </div>
        <div className="header-actions">
          {api.mock && <span className="mock-badge">MOCK DATA</span>}
          <button disabled={saveDisabled} className="primary small" onClick={onSave}>
            {saving ? 'Saving…' : state.grouped ? 'Save project' : 'Save scene'}
          </button>
        </div>
      </header>
      <nav className="workspace-bar" aria-label="Workspaces">
        <div className="workspace-tabs">
          {WORKSPACES.map((name, i) => (
            <button
              key={name}
              aria-label={name}
              aria-current={state.workspace === name ? 'page' : undefined}
              className={state.workspace === name ? 'active' : ''}
              disabled={!canOpenWorkspace(name)}
              onClick={() => onWorkspace(name)}
            >
              <span>0{i + 1}</span>
              {name}
            </button>
          ))}
        </div>
        <div className="workspace-info" aria-live="polite">
          <span className={health === 'API connected' ? 'online-dot' : 'offline-dot'} />
          {api.mock ? 'Local fixture service' : health}
        </div>
      </nav>
    </>
  );
}
