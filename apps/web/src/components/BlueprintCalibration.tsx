import { useEffect, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { api, type ProjectEnvelope } from '../api';
import { imagePoint, type V2 } from '../scene';
import { formatLength, toMeters, type LengthUnit } from '../units';

/** The uploaded blueprint with the two-point scale reference drawn over it. */
export function BlueprintCalibration({
  project,
  points,
  setPoints,
  distance,
  unit,
  manual,
  disabled,
}: {
  project: ProjectEnvelope;
  points: V2[];
  setPoints: (p: V2[]) => void;
  distance: string;
  unit: LengthUnit;
  manual: boolean;
  disabled: boolean;
}) {
  const [imageError, setImageError] = useState(false);
  const [hover, setHover] = useState<V2 | null>(null);
  useEffect(() => setImageError(false), [project]);
  const toImage = (e: ReactMouseEvent<HTMLDivElement>) =>
    imagePoint(
      e.clientX,
      e.clientY,
      e.currentTarget.getBoundingClientRect(),
      project.image.width,
      project.image.height,
    );
  const w = project.image.width;
  const ghost = points.length === 1 ? hover : null;
  const ends = ghost ? [points[0], ghost] : points.length === 2 ? points : null;
  const marks: [V2, string, boolean][] = points.map((p, i) => [p, i === 0 ? 'A' : 'B', false]);
  if (ghost) marks.push([ghost, 'B', true]);
  return (
    <div className="calibration-view">
      <div className="viewport-label">
        <span className="live-dot" /> BLUEPRINT <span className="muted">/ ORIGINAL PIXELS</span>
      </div>
      <div
        className="calibration-image"
        onClick={(e) => {
          const p = toImage(e);
          if (p && manual && !disabled && !imageError) {
            setPoints(points.length >= 2 ? [p] : [...points, p]);
            setHover(null);
          }
        }}
        onMouseMove={(e) => setHover(points.length === 1 && !imageError ? toImage(e) : null)}
        onMouseLeave={() => setHover(null)}
      >
        <img
          src={api.imageUrl(project.image.url)}
          alt="Blueprint for two-point calibration"
          onError={() => setImageError(true)}
        />
        <svg
          viewBox={`0 0 ${project.image.width} ${project.image.height}`}
          preserveAspectRatio="xMidYMid meet"
          aria-hidden="true"
        >
          {ends && (
            <>
              <line
                x1={ends[0][0]}
                y1={ends[0][1]}
                x2={ends[1][0]}
                y2={ends[1][1]}
                stroke="#e99f39"
                strokeWidth={w / 400}
                strokeDasharray={ghost ? `${w / 120} ${w / 200}` : undefined}
              />
              <text
                x={(ends[0][0] + ends[1][0]) / 2}
                y={(ends[0][1] + ends[1][1]) / 2 - w / 50}
                textAnchor="middle"
                fontSize={w / 50}
                fontWeight="bold"
                fill="#f0c487"
                stroke="#171a1c"
                strokeWidth={w / 250}
                paintOrder="stroke"
              >
                {Math.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1]).toFixed(1)} px
                {!ghost && distance && Number(distance) > 0
                  ? ` = ${formatLength(toMeters(Number(distance), unit), unit)}`
                  : ''}
              </text>
            </>
          )}
          {marks.map(([[x, y], label, isGhost], i) => (
            <g key={i} opacity={isGhost ? 0.65 : 1}>
              <circle
                cx={x}
                cy={y}
                r={w / 65}
                fill="#f3bd63"
                stroke="#24292b"
                strokeWidth={w / 600}
              />
              <text
                x={x}
                y={y}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={w / 55}
                fontWeight="bold"
                fill="#171a1c"
              >
                {label}
              </text>
            </g>
          ))}
        </svg>
      </div>
      {imageError && (
        <div role="alert" className="image-error">
          Blueprint could not be loaded. Check the API URL and image endpoint.
        </div>
      )}
      <div className="calibration-caption">
        {!manual
          ? 'Automatic scale selected. Choose Manual reference to set your own measurement.'
          : points.length === 0
            ? 'Click the first end of a known distance.'
            : points.length === 1
              ? 'Now click the other end.'
              : 'Reference set. Enter the real-world distance on the left.'}
        <span>
          {project.image.width} × {project.image.height} px · Image is unmodified
        </span>
      </div>
    </div>
  );
}
