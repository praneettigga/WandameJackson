import { sceneSchema, type Scene } from './scene';

// Unsaved edits are mirrored to this browser's localStorage so a refresh or crash does not lose
// them. A draft is only offered back for the same project and server revision it was based on;
// it never overwrites the server by itself.

export type Draft = { scene: Scene; baseRevision: number; savedAt: string };
const key = (projectId: string) => `roomshift.draft.${projectId}`;

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

export function writeDraft(scene: Scene) {
  try {
    storage()?.setItem(
      key(scene.id),
      JSON.stringify({ scene, baseRevision: scene.revision, savedAt: new Date().toISOString() }),
    );
  } catch {
    /* Quota or privacy mode: autosave is best effort. */
  }
}

export function readDraft(projectId: string): Draft | null {
  try {
    const raw = storage()?.getItem(key(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Draft;
    return { ...parsed, scene: sceneSchema.parse(parsed.scene) };
  } catch {
    clearDraft(projectId);
    return null;
  }
}

export function clearDraft(projectId: string) {
  try {
    storage()?.removeItem(key(projectId));
  } catch {
    /* ignore */
  }
}

/**
 * A draft worth offering for the loaded scene. `stale` means the server moved on since the draft was
 * taken, so restoring it would conflict; the UI offers a JSON download instead.
 */
export function restorableDraft(loaded: Scene): { draft: Draft; stale: boolean } | null {
  const draft = readDraft(loaded.id);
  if (!draft) return null;
  if (draft.baseRevision === loaded.revision && JSON.stringify(draft.scene) === JSON.stringify(loaded)) {
    clearDraft(loaded.id);
    return null;
  }
  return { draft, stale: draft.baseRevision !== loaded.revision };
}
