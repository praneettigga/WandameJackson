import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { floorsOf, layoutErrors } from '../assembly';
import { completeness } from '../completeness';
import { confidenceLevel, entities, geometryWarnings } from '../scene';
import { editorScenes, useEditor } from '../store';

/** Notices, completeness checks and confidence summary for the review dock (memoised per edit). */
export function useReviewSummary() {
  const { scene, assembly, scenes, floorStates } = useEditor(
    useShallow((s) => ({
      scene: s.scene,
      assembly: s.assembly,
      scenes: s.scenes,
      floorStates: s.floorStates,
    })),
  );
  return useMemo(() => {
    const all = editorScenes({ scene, scenes, floorStates });
    const warnings = [
      ...(scene ? [...scene.reconstruction.warnings, ...geometryWarnings(scene)] : []),
      ...(assembly
        ? [
            'Floors are centered automatically; verify alignment. Slabs assume 0.20 m and buildings start 3 m apart.',
            ...layoutErrors(assembly, all),
            ...floorsOf(assembly)
              .filter((f) => !all[f.projectId])
              .map(
                (f) => `${f.name}: not reconstructed; excluded from 3D geometry and GLB export.`,
              ),
          ]
        : []),
    ];
    const list = scene ? entities(scene) : [];
    const scored = list.filter((e) => e.provenance.confidence !== null);
    const review = scored
      .filter((e) => confidenceLevel(e.provenance.confidence) === 'low')
      .sort((a, b) => a.provenance.confidence! - b.provenance.confidence!);
    return {
      warnings,
      scored,
      review,
      issues: scene ? completeness(scene) : [],
      inferredCount: list.filter((e) => e.provenance.origin === 'inferred').length,
    };
  }, [scene, assembly, scenes, floorStates]);
}
