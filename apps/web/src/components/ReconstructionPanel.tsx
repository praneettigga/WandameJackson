import { useRef } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { api } from '../api';
import { MAX_BLUEPRINT_BYTES, MAX_BLUEPRINTS_PER_BATCH } from '../constants';
import type { ProjectWorkflow } from '../hooks/useProjectWorkflow';
import { useEditor } from '../store';
import { fromMeters, toMeters } from '../units';
import { UnitSelect } from './controls';

/** Left panel in the Reconstruct workspace: 01 source blueprint, 02 scale, 03 reconstruct. */
export function ReconstructionPanel({
  workflow,
  disabled,
  onOpenWizard,
}: {
  workflow: ProjectWorkflow;
  disabled: boolean;
  /** Several blueprints, or adding to a project, go through the grouping wizard. */
  onOpenWizard: (files: File[], addToCurrent: boolean) => void;
}) {
  const state = useEditor(useShallow((s) => ({ assembly: s.assembly, lengthUnit: s.lengthUnit })));
  const fileRef = useRef<HTMLInputElement>(null);
  const addFiles = useRef(false);
  const {
    project,
    points,
    setPoints,
    distance,
    setDistance,
    scaleMode,
    setScaleMode,
    automaticScale,
    findingScale,
    scaleError,
    findScale,
    height,
    setHeight,
    thickness,
    setThickness,
    scale,
    job,
    working,
    cancelling,
    showBlueprint,
    setShowBlueprint,
    error,
    guarded,
    discard,
    reconstruct,
    cancelJobs,
    uploadBlueprint,
  } = workflow;
  return (
    <>
      <div className="panel-heading">
        RECONSTRUCTION <span>01—03</span>
      </div>
      <section>
        <h4>
          <span className="step">01</span> SOURCE BLUEPRINT
        </h4>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept="image/png,image/jpeg"
          className="sr-only"
          aria-label="Upload blueprint"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = '';
            if (!files.length) return;
            if (files.length > MAX_BLUEPRINTS_PER_BATCH) {
              error(new Error('Select at most 20 blueprints per upload batch.'));
              return;
            }
            if (
              files.some(
                (f) =>
                  !['image/png', 'image/jpeg'].includes(f.type) || f.size > MAX_BLUEPRINT_BYTES,
              )
            ) {
              error(new Error('Choose PNG/JPEG images, each 20 MB or smaller.'));
              return;
            }
            if (files.length > 1 || state.assembly || addFiles.current) {
              if (!addFiles.current && !state.assembly && !discard()) return;
              onOpenWizard(files, addFiles.current);
              return;
            }
            if (discard()) void uploadBlueprint(files[0]);
          }}
        />
        <button
          className="upload-zone"
          disabled={disabled}
          onClick={() => {
            addFiles.current = Boolean(state.assembly);
            fileRef.current?.click();
          }}
        >
          <span>↥</span>
          <b>
            {state.assembly
              ? 'Add blueprints'
              : project
                ? 'Replace blueprint'
                : 'Choose blueprints'}
          </b>
          <small>PNG / JPG · up to 20 files · 20 MB each</small>
        </button>
        {project && (
          <>
            {!state.assembly && (
              <button
                className="wide"
                disabled={disabled}
                onClick={() => {
                  addFiles.current = true;
                  fileRef.current?.click();
                }}
              >
                Add blueprints / floors
              </button>
            )}
            <p className="hint">
              {project.project.name}
              <br />
              {project.image.width} × {project.image.height} px
            </p>
            <button className="wide" onClick={() => setShowBlueprint(!showBlueprint)}>
              {showBlueprint ? 'Show 3D viewport' : 'Show blueprint'}
            </button>
          </>
        )}
        {api.mock && (
          <p className="mock-note">
            Synthetic contract fixture. Upload and reconstruct simulate the workflow; no AI or image
            parsing runs.
          </p>
        )}
      </section>
      <section>
        <h4>
          <span className="step">02</span> SET THE SCALE
        </h4>
        <label className="field">
          Scale method
          <select
            value={scaleMode}
            disabled={disabled}
            onChange={(e) => {
              const mode = e.target.value as 'auto' | 'manual';
              setScaleMode(mode);
              if (mode === 'auto' && project && !automaticScale)
                void guarded(() => findScale(project.project.id));
            }}
          >
            <option value="auto">Automatic (default)</option>
            <option value="manual">Manual reference</option>
          </select>
        </label>
        {scaleMode === 'auto' ? (
          <>
            <p className="hint" role="status">
              {findingScale
                ? 'Reading blueprint measurements…'
                : !project
                  ? 'Upload a blueprint to assign its scale automatically.'
                  : automaticScale?.method === 'printed-dimension'
                    ? 'Using a printed measurement'
                    : automaticScale
                      ? api.mock
                        ? 'Synthetic fixture scale'
                        : 'Estimated scale · no verified measurement'
                      : 'Scale preview unavailable. Reconstruct space will calculate the scale automatically.'}
            </p>
            {scaleError && <p className="hint">{scaleError}</p>}
            {automaticScale?.notes?.map((note, i) => (
              <p className="hint" key={i}>
                {note}
              </p>
            ))}
            {project && !automaticScale && !findingScale && (
              <button
                disabled={disabled}
                onClick={() => void guarded(() => findScale(project.project.id))}
              >
                Retry automatic scale
              </button>
            )}
          </>
        ) : (
          <>
            <p className="hint">Click two endpoints of a known measurement in the blueprint.</p>
            <div className="point-list">
              {[0, 1].map((i) => (
                <div key={i}>
                  <span>{i === 0 ? 'A' : 'B'}</span>
                  <code>
                    {points[i]
                      ? `${points[i][0].toFixed(1)}, ${points[i][1].toFixed(1)} px`
                      : 'Select a point'}
                  </code>
                </div>
              ))}
            </div>
            <label className="field">
              Known distance
              <span className="unit-input">
                <input
                  aria-label="Known distance"
                  type="number"
                  min="0.001"
                  step={{ m: 0.1, cm: 1, mm: 10, ft: 0.5, in: 1 }[state.lengthUnit]}
                  value={distance}
                  onChange={(e) => setDistance(e.target.value)}
                />
                <UnitSelect
                  label="Known distance unit"
                  onChange={(from, to) => {
                    if (distance !== '' && Number.isFinite(Number(distance)))
                      setDistance(String(fromMeters(toMeters(Number(distance), from), to)));
                  }}
                />
              </span>
            </label>
            <button
              className="text-button"
              disabled={!points.length || disabled}
              onClick={() => setPoints([])}
            >
              Reset reference points
            </button>
          </>
        )}
        <div className="scale-result">
          <span>METRES / PIXEL</span>
          <b>{scale ? scale.toFixed(6) : '—'}</b>
        </div>
      </section>
      <section>
        <h4>
          <span className="step">03</span> RECONSTRUCT
        </h4>
        <label className="field">
          Wall height (m)
          <input
            type="number"
            min="0.1"
            max="20"
            step="0.1"
            placeholder="Assumed: 2.7 m"
            value={height}
            onChange={(e) => setHeight(e.target.value)}
          />
        </label>
        <p className="hint">
          Leave height blank to assume 2.7 m. Ceilings are inferred from room heights.
        </p>
        <label className="field">
          Wall thickness (m)
          <input
            type="number"
            min="0.01"
            step="0.01"
            placeholder="Auto / measured"
            value={thickness}
            onChange={(e) => setThickness(e.target.value)}
          />
        </label>
        <button
          className="primary wide"
          disabled={!project || (scaleMode === 'manual' && !scale) || disabled}
          onClick={() => void reconstruct()}
        >
          {working
            ? 'Working…'
            : api.mock
              ? 'Load synthetic reconstruction →'
              : state.assembly
                ? 'Reconstruct buildings & floors →'
                : 'Reconstruct space →'}
        </button>
        {job && (
          <div className="job-progress" role="status">
            <progress value={job.progress} max={1} />
            <span>
              {job.cancelRequested ? 'cancelling' : job.status} · {Math.round(job.progress * 100)}%
            </span>
            {(job.status === 'queued' || job.status === 'running') && !job.cancelRequested && (
              <button className="small" disabled={cancelling} onClick={() => void cancelJobs(job)}>
                {cancelling ? 'Cancelling…' : state.assembly ? 'Cancel all' : 'Cancel'}
              </button>
            )}
          </div>
        )}
      </section>
    </>
  );
}
