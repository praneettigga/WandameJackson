import type { ProjectEnvelope } from '../api';

/** Open a project by ID (with suggestions from the API) or reload the current one. */
export function ProjectPicker({
  projectId,
  setProjectId,
  projects,
  onRefresh,
  onOpen,
  onReload,
  canReload,
  disabled,
}: {
  projectId: string;
  setProjectId: (id: string) => void;
  projects: ProjectEnvelope[];
  onRefresh: () => void;
  onOpen: (id: string) => void;
  onReload: () => void;
  canReload: boolean;
  disabled: boolean;
}) {
  return (
    <section className="project-section">
      <h4>PROJECT</h4>
      <label className="field">
        Project ID
        <input
          value={projectId}
          list="project-list"
          placeholder="Project ID from the API"
          onFocus={onRefresh}
          onChange={(e) => setProjectId(e.target.value)}
        />
        <datalist id="project-list">
          {projects.map((p) => (
            <option key={p.project.id} value={p.project.id}>
              {p.project.name} · {new Date(p.project.createdAt).toLocaleDateString()}
              {p.project.hasScene ? '' : ' · not reconstructed'}
            </option>
          ))}
        </datalist>
      </label>
      <div className="button-row">
        <button disabled={!projectId.trim() || disabled} onClick={() => onOpen(projectId.trim())}>
          Open project
        </button>
        <button disabled={!canReload || disabled} onClick={onReload}>
          Reload
        </button>
      </div>
    </section>
  );
}
