import { useEffect } from 'react';
import { clearDraft, writeDraft } from '../draft';
import type { Scene } from '../scene';
import { useEditor } from '../store';

/** Mirrors unsaved edits to a local draft (debounced); clears it once saved or discarded. */
export function useDraftAutosave() {
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pending: Scene | null = null;
    const flush = () => {
      clearTimeout(timer);
      if (pending) writeDraft(pending);
      pending = null;
    };
    const unsubscribe = useEditor.subscribe((s, prev) => {
      const switched = !!prev.scene && prev.scene.id !== s.scene?.id;
      if (switched) {
        const outgoing = prev.scene!.id;
        // A floor switch stashes the outgoing scene: keep its draft. Loading over it discards it.
        if (s.floorStates[outgoing]?.dirty) flush();
        else {
          if (pending?.id === outgoing) {
            clearTimeout(timer);
            pending = null;
          }
          if (prev.dirty) clearDraft(outgoing);
        }
      }
      // Background floors saved by a grouped save.
      for (const [id, floor] of Object.entries(prev.floorStates))
        if (floor.dirty && s.floorStates[id] && !s.floorStates[id].dirty) clearDraft(id);
      if (s.scene && s.dirty && s.scene !== prev.scene) {
        clearTimeout(timer);
        pending = s.scene;
        timer = setTimeout(flush, 800);
      } else if (!switched && prev.dirty && !s.dirty && prev.scene) {
        // Same scene went clean: it was saved or discarded.
        clearTimeout(timer);
        pending = null;
        clearDraft(prev.scene.id);
      }
    });
    return () => {
      flush();
      unsubscribe();
    };
  }, []);
}
