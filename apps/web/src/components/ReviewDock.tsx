import { useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { api } from '../api';
import { exportAssemblyJson, floorsOf } from '../assembly';
import { download, exportAssemblyGlb, exportGlb } from '../geometry';
import { ConfidenceChip } from '../Inspector';
import { confidenceLevel, exportSceneJson } from '../scene';
import { editorScenes, useEditor } from '../store';
import { useReviewSummary } from '../hooks/useReviewSummary';

/** Notices, completeness checks and confidence summary, plus Scene JSON / GLB export. */
export function ReviewDock({
  disabled,
  guarded,
  notice,
  setNotice,
}: {
  disabled: boolean;
  guarded: (action: () => Promise<void>) => Promise<void>;
  notice: string;
  setNotice: (notice: string) => void;
}) {
  const state = useEditor(
    useShallow((s) => ({
      scene: s.scene,
      assembly: s.assembly,
      scenes: s.scenes,
      floorStates: s.floorStates,
      activeProjectId: s.activeProjectId,
      view: s.view,
      ceilings: s.ceilings,
      workspace: s.workspace,
      select: s.select,
    })),
  );
  const { warnings, scored, review, issues, inferredCount } = useReviewSummary();
  const [exportScope, setExportScope] = useState<'all' | 'floor'>('all');
  return (
    <>
      <div className="dock-title">
        <span>REVIEW & OUTPUT</span>
        <span>
          {warnings.length} {warnings.length === 1 ? 'notice' : 'notices'}
          {issues.length > 0 &&
            ` · ${issues.length} completeness ${issues.length === 1 ? 'check' : 'checks'}`}
        </span>
      </div>
      <div className="dock-body">
        <div className="warnings" aria-live="polite">
          {scored.length > 0 && (
            <div className="confidence-summary">
              <p>
                <span>◔</span>
                {(['high', 'medium', 'low'] as const)
                  .map(
                    (level) =>
                      `${scored.filter((e) => confidenceLevel(e.provenance.confidence) === level).length} ${level}`,
                  )
                  .join(' · ')}{' '}
                confidence · {inferredCount} inferred {inferredCount === 1 ? 'element' : 'elements'}
                . Scores are heuristic evidence strengths, not probabilities.
              </p>
              {review.length > 0 && (
                <div className="review-list">
                  <span>Needs review:</span>
                  {review.map((e) => (
                    <button key={e.id} onClick={() => state.select(e.id)}>
                      {e.id} <ConfidenceChip confidence={e.provenance.confidence} />
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {issues.length > 0 && (
            <div className="completeness" aria-label="Completeness checks">
              {issues.map((issue, i) => (
                <p key={i} className={`issue ${issue.severity}`}>
                  <span>{issue.severity === 'warning' ? '◆' : '◇'}</span>
                  {issue.message}
                  {issue.entityId && (
                    <button onClick={() => state.select(issue.entityId)}>Show</button>
                  )}
                </p>
              ))}
            </div>
          )}
          {warnings.length ? (
            warnings.map((warning, i) => (
              <p key={i}>
                <span>△</span>
                {warning}
              </p>
            ))
          ) : (
            <p>
              <span>○</span>
              {notice || 'Your reconstructed space will appear here.'}
            </p>
          )}
          {notice && warnings.length > 0 && <p className="hint">{notice}</p>}
        </div>
        <div className="export-actions">
          {state.assembly && (
            <label className="field">
              GLB export scope
              <select
                value={exportScope}
                onChange={(e) => setExportScope(e.target.value as 'all' | 'floor')}
              >
                <option value="all">All buildings & floors</option>
                <option value="floor">Active floor</option>
              </select>
            </label>
          )}
          <button
            disabled={!state.scene && !state.assembly}
            onClick={() => {
              if (state.assembly) {
                download(
                  exportAssemblyJson(state.assembly, editorScenes(state)),
                  'application/json',
                  `${state.assembly.id}.assembly.json`,
                );
              } else if (state.scene)
                download(
                  exportSceneJson(state.scene),
                  'application/json',
                  `${state.scene.id}.scene.json`,
                );
            }}
          >
            ↓ Scene JSON
          </button>
          {import.meta.env.DEV && (
            <button
              disabled={!state.scene || !!state.scene.source.capture || disabled}
              title="Download this scene with its blueprint image as an evaluation ground-truth pair (name.png + name.scene.json)"
              onClick={() =>
                void guarded(async () => {
                  if (!state.scene) return;
                  const scene = state.scene;
                  const image = await fetch(api.imageUrl(scene.source.imageUrl)).then((r) => {
                    if (!r.ok)
                      throw new Error(`Could not download the blueprint (HTTP ${r.status}).`);
                    return r.blob();
                  });
                  const ext = scene.source.mimeType === 'image/jpeg' ? 'jpg' : 'png';
                  download(image, scene.source.mimeType, `${scene.id}.${ext}`);
                  download(exportSceneJson(scene), 'application/json', `${scene.id}.scene.json`);
                  setNotice(
                    'Ground-truth pair downloaded. Put both files in services/api/data/gt and run python -m eval.run --set real.',
                  );
                })
              }
            >
              ↓ GT pair
            </button>
          )}
          <button
            disabled={
              (!state.scene && !state.assembly) ||
              disabled ||
              (Boolean(state.assembly) && exportScope === 'floor' && !state.scene)
            }
            onClick={() =>
              void guarded(async () => {
                if (state.assembly) {
                  const floor = floorsOf(state.assembly).find(
                    (f) => f.projectId === state.activeProjectId,
                  );
                  const view = {
                    ...state.view,
                    buildingId: null,
                    mode: exportScope === 'floor' ? ('floor' as const) : ('all' as const),
                    floorId: floor?.id ?? null,
                  };
                  const scenes = editorScenes(state);
                  const missing = floorsOf(state.assembly).filter((f) => !scenes[f.projectId]);
                  download(
                    await exportAssemblyGlb(state.assembly, scenes, view, state.ceilings),
                    'model/gltf-binary',
                    `${state.assembly.id}${exportScope === 'floor' ? '-floor' : ''}.glb`,
                  );
                  setNotice(
                    `GLB uses actual elevations. ${missing.length ? `${missing.length} unreconstructed floor(s) omitted.` : 'All requested floors exported.'}`,
                  );
                } else if (state.scene) {
                  download(
                    await exportGlb(state.scene, state.ceilings || state.workspace === 'Explore'),
                    'model/gltf-binary',
                    `${state.scene.id}.glb`,
                  );
                  setNotice(
                    'GLB exported at metre scale. Scene JSON preserves editable RoomShift semantics.',
                  );
                }
              })
            }
          >
            ↓ GLB
          </button>
          <small>
            JSON preserves editing semantics.
            <br />
            GLB exchanges visible geometry.
          </small>
        </div>
      </div>
    </>
  );
}
