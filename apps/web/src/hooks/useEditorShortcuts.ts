import { useEffect } from 'react';
import { useEditor } from '../store';

/**
 * Editor keyboard shortcuts (blueprint mode only) and the unsaved-changes guard on page unload.
 * Shortcuts never fire while typing in a field.
 */
export function useEditorShortcuts(inputMode: 'blueprint' | 'capture') {
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (useEditor.getState().hasUnsaved()) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    const key = (e: KeyboardEvent) => {
      if (inputMode !== 'blueprint') return;
      const target = e.target;
      if (
        (target instanceof HTMLElement &&
          target.closest('input, textarea, select, [contenteditable="true"]')) ||
        e.altKey
      )
        return;
      const s = useEditor.getState();
      if (e.key === 'Escape') {
        if (document.pointerLockElement) document.exitPointerLock();
        useEditor.setState({
          workspace: s.workspace === 'Explore' ? 'Edit' : s.workspace,
          selectedId: null,
          measure: false,
          tool: 'select',
        });
        return;
      }
      // Works while walking too: look through the walls at the services.
      if (e.key.toLowerCase() === 'i' && !e.ctrlKey && !e.metaKey && s.scene) {
        useEditor.setState({ seeThrough: !s.seeThrough, serviceFocus: null });
        return;
      }
      if (s.workspace === 'Explore' || s.busy) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) s.redo();
        else s.undo();
        return;
      }
      if (e.ctrlKey || e.metaKey) return;
      if (e.key.toLowerCase() === 'g') useEditor.setState({ mode: 'translate', measure: false });
      if (e.key.toLowerCase() === 'r') useEditor.setState({ mode: 'rotate', measure: false });
      if (e.key.toLowerCase() === 's') useEditor.setState({ mode: 'scale', measure: false });
      const tools = { w: 'wall', d: 'door', n: 'window' } as const;
      const tool = tools[e.key.toLowerCase() as keyof typeof tools];
      if (tool && s.scene) {
        useEditor.setState({ tool: s.tool === tool ? 'select' : tool, measure: false });
        return;
      }
      if (e.key.toLowerCase() === 'f') {
        e.preventDefault();
        useEditor.setState({ frame: s.frame + 1 });
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        s.remove();
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('beforeunload', beforeUnload);
      window.removeEventListener('keydown', key);
    };
  }, [inputMode]);
}
