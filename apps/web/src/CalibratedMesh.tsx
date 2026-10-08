import { useEffect, useState } from 'react';
import { Matrix4, Vector3 } from 'three';
import { api, type MeshCalibration, type MeshPoint, type MeshResult } from './api';
import { MeshViewport } from './MeshViewport';

const empty: MeshCalibration = { reference: null, floor: null, rotationDegrees: [0, 0, 0] };
export function reconstructionMatrix(mesh: MeshResult) {
  return mesh.reconstructionToWorld
    ? new Matrix4().set(...(mesh.reconstructionToWorld.flat() as Parameters<Matrix4['set']>))
    : new Matrix4();
}

export function CalibratedMesh({
  mesh,
  onChange,
  disabled,
}: {
  mesh: MeshResult;
  onChange: (mesh: MeshResult) => void;
  disabled: boolean;
}) {
  const [mode, setMode] = useState<'scale' | 'floor' | 'measure' | null>(null);
  const [points, setPoints] = useState<MeshPoint[]>([]);
  const [distance, setDistance] = useState('1');
  const [angles, setAngles] = useState<MeshPoint>([0, 0, 0]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const spec = mesh.calibration ?? empty;
  useEffect(() => {
    setMode(null);
    setPoints([]);
    setError('');
    setAngles(mesh.calibration?.rotationDegrees ?? [0, 0, 0]);
    setDistance(String(mesh.calibration?.reference?.distanceMeters ?? 1));
  }, [mesh.jobId, mesh.calibrationRevision]);
  const matrix = reconstructionMatrix(mesh);
  const visiblePoints = points.map(
    (p) => new Vector3(...p).applyMatrix4(matrix).toArray() as MeshPoint,
  );
  const measured =
    visiblePoints.length >= 2
      ? new Vector3(...visiblePoints[0]).distanceTo(new Vector3(...visiblePoints[1]))
      : null;
  const locked = disabled || saving;
  function select(next: typeof mode) {
    setMode(next);
    setPoints([]);
    setError('');
  }
  async function save(next: MeshCalibration) {
    setSaving(true);
    setError('');
    try {
      onChange(
        await api.calibrateMesh(mesh.projectId, {
          reference: next.reference,
          floor: next.floor,
          rotationDegrees: next.rotationDegrees,
          jobId: mesh.jobId,
          expectedRevision: mesh.calibrationRevision ?? null,
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <MeshViewport
        url={api.imageUrl(mesh.meshUrl)}
        units={mesh.units}
        points={visiblePoints}
        floorAligned={!!spec.floor}
        onPick={
          !locked && mode && points.length < (mode === 'floor' ? 3 : 2)
            ? (point) => {
                const raw = new Vector3(...point)
                  .applyMatrix4(matrix.clone().invert())
                  .toArray() as MeshPoint;
                setPoints((current) => [...current, raw]);
              }
            : undefined
        }
      />
      <section className="mesh-calibration" aria-label="Scale and alignment">
        <div className="mesh-calibration-heading">
          <div>
            <strong>Scale & alignment</strong>
            <p className="hint">
              {mesh.units === 'meters'
                ? 'Metric scale saved from your reference distance.'
                : 'Add a known distance to enable metric measurements.'}{' '}
              Changes save automatically when applied.
            </p>
          </div>
          <a
            href={api.imageUrl(mesh.manifestUrl ?? `/api/projects/${mesh.projectId}/mesh`)}
            download
          >
            Export manifest (JSON)
          </a>
        </div>
        <div className="mesh-calibration-tools">
          <button disabled={locked} aria-pressed={mode === 'scale'} onClick={() => select('scale')}>
            Set scale · 2 points
          </button>
          <button disabled={locked} aria-pressed={mode === 'floor'} onClick={() => select('floor')}>
            Align floor · 3 points
          </button>
          <button
            disabled={locked}
            aria-pressed={mode === 'measure'}
            onClick={() => select('measure')}
          >
            Measure · 2 points
          </button>
          {mode && (
            <button disabled={locked} onClick={() => select(null)}>
              Stop picking
            </button>
          )}
        </div>
        {mode && (
          <p role="status">
            {mode === 'floor'
              ? 'Choose three well-spaced points on the floor. The first becomes the origin.'
              : 'Choose two distinct surface points. Drag to orbit between picks.'}{' '}
            {points.length}/{mode === 'floor' ? 3 : 2} selected.
          </p>
        )}
        {mode === 'scale' && (
          <div className="mesh-calibration-tools">
            <label>
              Known distance (meters)
              <input
                aria-label="Known distance (meters)"
                type="number"
                min="0.001"
                step="any"
                value={distance}
                disabled={locked}
                onChange={(e) => setDistance(e.target.value)}
              />
            </label>
            <button
              disabled={
                locked ||
                points.length !== 2 ||
                !Number.isFinite(Number(distance)) ||
                Number(distance) <= 0
              }
              onClick={() =>
                void save({
                  ...spec,
                  reference: {
                    pointA: points[0],
                    pointB: points[1],
                    distanceMeters: Number(distance),
                  },
                })
              }
            >
              Apply scale
            </button>
          </div>
        )}
        {mode === 'floor' && (
          <button
            disabled={locked || points.length !== 3}
            onClick={() =>
              void save({
                ...spec,
                floor: { points: points as [MeshPoint, MeshPoint, MeshPoint], flipNormal: false },
                rotationDegrees: [0, 0, 0],
              })
            }
          >
            Apply floor alignment
          </button>
        )}
        {measured !== null && (
          <p className="mesh-measurement">
            Selected distance:{' '}
            <strong>
              {measured.toFixed(4)} {mesh.units === 'meters' ? 'm' : 'model units (uncalibrated)'}
            </strong>
          </p>
        )}
        <details>
          <summary>Manual orientation correction</summary>
          <div className="mesh-calibration-tools">
            {['Tilt X', 'Heading Y', 'Roll Z'].map((label, i) => (
              <label key={label}>
                {label} (°)
                <input
                  aria-label={`${label} degrees`}
                  type="number"
                  min={-180}
                  max={180}
                  value={angles[i]}
                  disabled={locked}
                  onChange={(e) =>
                    setAngles(
                      (current) =>
                        current.map((v, j) => (i === j ? Number(e.target.value) : v)) as MeshPoint,
                    )
                  }
                />
              </label>
            ))}
            <button
              disabled={locked || angles.some((v) => !Number.isFinite(v) || Math.abs(v) > 180)}
              onClick={() => void save({ ...spec, rotationDegrees: angles })}
            >
              Apply orientation
            </button>
            {spec.floor && (
              <button
                disabled={locked}
                onClick={() =>
                  void save({
                    ...spec,
                    floor: { ...spec.floor!, flipNormal: !spec.floor!.flipNormal },
                  })
                }
              >
                Flip floor up direction
              </button>
            )}
          </div>
        </details>
        {mesh.calibration && (
          <button className="text-button" disabled={locked} onClick={() => void save(empty)}>
            Reset scale & alignment
          </button>
        )}
        {saving && <p role="status">Saving calibrated mesh…</p>}
        {error && <p role="alert">{error}</p>}
        <p className="hint">
          Check another known distance with Measure to assess accuracy. Missing surfaces and
          inferred geometry remain unchanged.
        </p>
      </section>
    </>
  );
}
