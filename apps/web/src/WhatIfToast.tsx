import { useEffect, useRef, useState } from 'react';
import { useEditor } from './store';
import { whatIf } from './whatIf';
import type { Scene } from './scene';

/** Small bottom-left note describing the impact of the latest edit. Hides itself after a few seconds. */
export function WhatIfToast() {
  const scene = useEditor((s) => s.scene);
  const uses = useEditor((s) => (s.scene ? s.roomUses[s.scene.id] : undefined));
  const previous = useRef<Scene | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  useEffect(() => {
    const before = previous.current;
    previous.current = scene;
    // Only edits within one scene; loading or switching scenes is not a change to explain.
    if (!before || !scene || before.id !== scene.id || before === scene) return;
    const next = whatIf(before, scene, uses);
    if (next.length) setLines((current) => (current.join('\n') === next.join('\n') ? current : next));
  }, [scene, uses]);
  useEffect(() => {
    if (!lines.length) return;
    const timer = setTimeout(() => setLines([]), 8000);
    return () => clearTimeout(timer);
  }, [lines]);
  if (!lines.length) return null;
  return (
    <div className="what-if" role="status" aria-label="Impact of your change">
      <b>WHAT CHANGED</b>
      {lines.map((line) => (
        <p key={line}>{line}</p>
      ))}
      <button aria-label="Dismiss" onClick={() => setLines([])}>
        ×
      </button>
    </div>
  );
}
