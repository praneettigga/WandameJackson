import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Html, Line } from '@react-three/drei';
import * as THREE from 'three';
import { useEditor } from './store';
import {
  SNAP_PX,
  closestOnSegment,
  snapKindLabels,
  snapOpeningOffset,
  snapPoint,
  snapSegments,
  worldPerPixel,
  type SnapResult,
  type SnapSettings,
} from './snapping';
import { addWall, moveNode, moveWall, wallsAt } from './wallGraph';
import { wallLength, type Opening, type Scene, type V2, type Wall } from './scene';

// In-viewport wall, door and window tools. Pointer positions are intersected with the floor
// plane and snapped with a tolerance of SNAP_PX screen pixels at the current zoom.

const FLOOR = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
const OFF: SnapSettings = {
  endpoint: false,
  intersection: false,
  midpoint: false,
  perpendicular: false,
  guide: false,
  angle: false,
  grid: false,
};
const OPENING_DEFAULTS = {
  door: { width: 0.9, height: 2.1, bottom: 0 },
  window: { width: 1.2, height: 1.2, bottom: 0.9 },
};
const Y = 0.02; // draw helpers just above the floor
const v3 = (p: V2, y = Y) => new THREE.Vector3(p[0], y, p[1]);
const fmt = (n: number) => `${n.toFixed(2)} m`;

type Drag =
  | { kind: 'node'; from: V2; exclude: Set<string> }
  | { kind: 'wall'; wall: Wall; grab: V2 }
  | { kind: 'opening'; opening: Opening; wall: Wall };

function useStateRef<T>(initial: T) {
  const [value, setValue] = useState(initial);
  const ref = useRef(value);
  const set = useCallback((next: T | ((prev: T) => T)) => {
    ref.current = typeof next === 'function' ? (next as (prev: T) => T)(ref.current) : next;
    setValue(ref.current);
  }, []);
  return [value, set, ref] as const;
}

function Handle({ position, color, onDown }: { position: V2; color: string; onDown: (e: ThreeEvent<PointerEvent>) => void }) {
  const ref = useRef<THREE.Mesh>(null);
  const height = useThree((s) => s.size.height);
  useFrame(({ camera }) => {
    if (!ref.current || !(camera instanceof THREE.PerspectiveCamera)) return;
    // Constant ~7 px radius regardless of zoom.
    const s = worldPerPixel(camera.position.distanceTo(ref.current.position), camera.fov, height) * 7;
    ref.current.scale.setScalar(s);
  });
  return (
    <mesh ref={ref} position={v3(position, 0.05)} renderOrder={12} onPointerDown={onDown} userData={{ helper: true }}>
      <sphereGeometry args={[1, 16, 12]} />
      <meshBasicMaterial color={color} depthTest={false} />
    </mesh>
  );
}

function openingCentre(wall: Wall, o: { offset: number; width: number }): V2 {
  const l = wallLength(wall) || 1;
  const t = (o.offset + o.width / 2) / l;
  return [wall.start[0] + (wall.end[0] - wall.start[0]) * t, wall.start[1] + (wall.end[1] - wall.start[1]) * t];
}
function openingEnds(wall: Wall, offset: number, width: number): [THREE.Vector3, THREE.Vector3] {
  const l = wallLength(wall) || 1;
  const p = (t: number) => v3([wall.start[0] + ((wall.end[0] - wall.start[0]) * t) / l, wall.start[1] + ((wall.end[1] - wall.start[1]) * t) / l], 0.06);
  return [p(offset), p(offset + width)];
}

export function WallTools({ scene }: { scene: Scene }) {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const controls = useThree((s) => s.controls) as unknown as { enabled: boolean } | null;
  const tool = useEditor((s) => s.tool);
  const selectedId = useEditor((s) => s.selectedId);
  // Native event handlers read the refs, so a pointerup that arrives before React re-renders
  // still sees the latest hover/placement (found by the browser tests).
  const [hover, setHover, hoverRef] = useStateRef<SnapResult | null>(null);
  const [chain, setChain, chainRef] = useStateRef<V2[]>([]);
  const [typed, setTyped, typedRef] = useStateRef('');
  const [drag, setDrag, dragRef] = useStateRef<Drag | null>(null);
  const [placement, setPlacement, placementRef] = useStateRef<{ wall: Wall; offset: number } | null>(null);
  const keys = useRef({ alt: false, shift: false });
  const down = useRef<[number, number] | null>(null);
  const raycaster = useMemo(() => new THREE.Raycaster(), []);

  const selectedWall = scene.walls.find((w) => w.id === selectedId);
  const selectedOpening = scene.openings.find((o) => o.id === selectedId);

  function floor(e: { clientX: number; clientY: number }) {
    const rect = gl.domElement.getBoundingClientRect();
    raycaster.setFromCamera(
      new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1),
      camera,
    );
    const hit = raycaster.ray.intersectPlane(FLOOR, new THREE.Vector3());
    if (!hit || !(camera instanceof THREE.PerspectiveCamera)) return null;
    const wpp = worldPerPixel(camera.position.distanceTo(hit), camera.fov, rect.height);
    return { p: [hit.x, hit.z] as V2, tolerance: SNAP_PX * wpp };
  }
  function snap(raw: V2, tolerance: number, anchor: V2 | null, exclude?: Set<string>) {
    const s = useEditor.getState();
    return snapPoint(raw, {
      segments: snapSegments(s.scene!.walls),
      tolerance,
      gridStep: s.gridStep,
      settings: s.snap && !keys.current.alt ? s.snapSettings : OFF,
      anchor,
      orthogonal: keys.current.shift,
      exclude,
    });
  }
  function nearestWall(p: V2, tolerance: number) {
    let best: { wall: Wall; d: number; t: number } | null = null;
    for (const wall of useEditor.getState().scene!.walls) {
      const c = closestOnSegment(p, wall.start, wall.end);
      const d = Math.hypot(c.point[0] - p[0], c.point[1] - p[1]);
      if (d <= Math.max(tolerance, wall.thickness / 2) && (!best || d < best.d)) best = { wall, d, t: c.t };
    }
    return best;
  }
  function openingOffset(wall: Wall, t: number, width: number, tolerance: number, ignore?: string) {
    const s = useEditor.getState();
    const neighbours = s.scene!.openings.filter((o) => o.wallId === wall.id && o.id !== ignore);
    return snapOpeningOffset(t * wallLength(wall) - width / 2, width, wallLength(wall), neighbours, tolerance, s.gridStep, 0, s.snap && !keys.current.alt).offset;
  }

  function place(point: V2) {
    const s = useEditor.getState();
    const chain = chainRef.current;
    const anchor = chain.at(-1);
    if (!anchor) {
      setChain([point]);
      return;
    }
    const ok = s.wallEdit((sc) => {
      addWall(sc, anchor, point);
    });
    if (!ok) return;
    const closed = chain.length > 1 && Math.hypot(point[0] - chain[0][0], point[1] - chain[0][1]) < 1e-3;
    setChain(closed ? [] : [...chain, point]);
    setTyped('');
  }

  // Pointer handling on the canvas element; works for drags that leave the handle mesh.
  useEffect(() => {
    const el = gl.domElement;
    const move = (e: PointerEvent) => {
      const tool = useEditor.getState().tool;
      const drag = dragRef.current,
        hover = hoverRef.current,
        placement = placementRef.current,
        chain = chainRef.current;
      keys.current = { alt: e.altKey, shift: e.shiftKey };
      const hit = floor(e);
      if (!hit) return;
      if (drag) {
        if (drag.kind === 'node') setHover(snap(hit.p, hit.tolerance, null, drag.exclude));
        else if (drag.kind === 'wall') setHover({ point: hit.p, kind: null, guides: [] });
        else {
          // Slide along the host wall or onto a collinear wall that continues it past a junction.
          const host = drag.wall;
          const line = (w: Wall) => {
            const cross = (q: V2) =>
              (host.end[0] - host.start[0]) * (q[1] - host.start[1]) - (host.end[1] - host.start[1]) * (q[0] - host.start[0]);
            const l = wallLength(host) || 1;
            return Math.abs(cross(w.start)) / l < 1e-3 && Math.abs(cross(w.end)) / l < 1e-3;
          };
          const candidates = useEditor.getState().scene!.walls.filter((w) => w.id === host.id || line(w));
          let best: { wall: Wall; t: number; d: number } | null = null;
          for (const w of candidates) {
            const c = closestOnSegment(hit.p, w.start, w.end);
            const d = Math.hypot(c.point[0] - hit.p[0], c.point[1] - hit.p[1]);
            if (wallLength(w) >= drag.opening.width && (!best || d < best.d - 1e-9)) best = { wall: w, t: c.t, d };
          }
          const target = best ?? { wall: host, t: closestOnSegment(hit.p, host.start, host.end).t };
          const offset = openingOffset(target.wall, target.t, drag.opening.width, hit.tolerance, drag.opening.id);
          setPlacement({ wall: target.wall, offset });
        }
        return;
      }
      if (tool === 'wall') setHover(snap(hit.p, hit.tolerance, chain.at(-1) ?? null));
      else if (tool === 'door' || tool === 'window') {
        const near = nearestWall(hit.p, hit.tolerance * 2);
        setPlacement(near ? { wall: near.wall, offset: openingOffset(near.wall, near.t, OPENING_DEFAULTS[tool].width, hit.tolerance) } : null);
      }
    };
    const pointerDown = (e: PointerEvent) => {
      down.current = [e.clientX, e.clientY];
    };
    const up = (e: PointerEvent) => {
      const s = useEditor.getState();
      const tool = useEditor.getState().tool;
      const drag = dragRef.current,
        hover = hoverRef.current,
        placement = placementRef.current,
        chain = chainRef.current;

      if (drag) {
        const hit = floor(e);
        if (drag.kind === 'node' && hover) s.wallEdit((sc) => moveNode(sc, drag.from, hover.point));
        if (drag.kind === 'wall' && hit) {
          const l = wallLength(drag.wall) || 1;
          const n: V2 = [-(drag.wall.end[1] - drag.wall.start[1]) / l, (drag.wall.end[0] - drag.wall.start[0]) / l];
          let offset = (hit.p[0] - drag.grab[0]) * n[0] + (hit.p[1] - drag.grab[1]) * n[1];
          if (s.snap && !e.altKey) offset = Math.round(offset / s.gridStep) * s.gridStep;
          if (Math.abs(offset) > 1e-6) s.wallEdit((sc) => moveWall(sc, drag.wall.id, offset));
        }
        if (drag.kind === 'opening' && placement) {
          const offset = Math.round(placement.offset * 1e4) / 1e4;
          if (placement.wall.id !== drag.opening.wallId)
            s.patch(drag.opening.id, { wallId: placement.wall.id, offset });
          else if (Math.abs(offset - drag.opening.offset) > 1e-6) s.patch(drag.opening.id, { offset });
        }
        setDrag(null);
        setHover(null);
        setPlacement(null);
        if (controls) controls.enabled = true;
        return;
      }
      const start = down.current;
      down.current = null;
      if (e.button !== 0 || !start || Math.hypot(e.clientX - start[0], e.clientY - start[1]) > 5) return;
      if (tool === 'wall') {
        const hit = floor(e);
        if (hit) place(snap(hit.p, hit.tolerance, chain.at(-1) ?? null).point);
      } else if ((tool === 'door' || tool === 'window') && placement) {
        const d = OPENING_DEFAULTS[tool];
        const height = Math.min(d.height, placement.wall.height - d.bottom);
        if (height <= 0) {
          useEditor.setState({ error: `The wall is too low for a ${tool}.` });
          return;
        }
        const id = `${tool}-${crypto.randomUUID().slice(0, 8)}`;
        const ok = s.commit((sc) => {
          sc.openings.push({
            id,
            type: tool,
            wallId: placement.wall.id,
            offset: Math.round(placement.offset * 1e4) / 1e4,
            width: Math.min(d.width, wallLength(placement.wall)),
            height,
            bottom: d.bottom,
            provenance: {
              origin: 'user',
              confidence: null,
              source: 'user',
              userEdited: false,
              fieldOrigins: { offset: 'user', width: 'user', height: 'user', bottom: 'user' },
              notes: [`Placed by the user on ${placement.wall.id}.`],
            },
          });
        });
        if (ok) useEditor.setState({ selectedId: id });
      }
    };
    const leave = () => {
      if (!dragRef.current) {
        setHover(null);
        setPlacement(null);
      }
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerdown', pointerDown);
    window.addEventListener('pointerup', up);
    el.addEventListener('pointerleave', leave);
    return () => {
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerdown', pointerDown);
      window.removeEventListener('pointerup', up);
      el.removeEventListener('pointerleave', leave);
    };
  });

  // Keyboard: typed length, Enter, Esc. Captured before the app-level shortcuts.
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && e.target.closest('input, textarea, select, [contenteditable="true"]')) return;
      keys.current = { alt: e.altKey, shift: e.shiftKey };
      const tool = useEditor.getState().tool;
      const chain = chainRef.current,
        typed = typedRef.current,
        hover = hoverRef.current;
      if (tool === 'select') return;
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
        if (chain.length) {
          setChain([]);
          setTyped('');
        } else useEditor.setState({ tool: 'select' });
        setHover(null);
        setPlacement(null);
        return;
      }
      if (tool !== 'wall' || !chain.length) return;
      if (/^[0-9.]$/.test(e.key)) {
        e.stopImmediatePropagation();
        setTyped((t) => t + e.key);
      } else if (e.key === 'Backspace' && typed) {
        e.stopImmediatePropagation();
        e.preventDefault();
        setTyped((t) => t.slice(0, -1));
      } else if (e.key === 'Enter') {
        e.stopImmediatePropagation();
        const anchor = chain.at(-1)!;
        const length = Number(typed);
        if (typed && Number.isFinite(length) && length > 0) {
          const target = hover?.point ?? [anchor[0] + 1, anchor[1]];
          const d = Math.hypot(target[0] - anchor[0], target[1] - anchor[1]) || 1;
          place([anchor[0] + ((target[0] - anchor[0]) / d) * length, anchor[1] + ((target[1] - anchor[1]) / d) * length]);
        } else {
          setChain([]);
        }
        setTyped('');
      }
    };
    const keyUp = (e: KeyboardEvent) => {
      keys.current = { alt: e.altKey, shift: e.shiftKey };
    };
    window.addEventListener('keydown', key, { capture: true });
    window.addEventListener('keyup', keyUp);
    return () => {
      window.removeEventListener('keydown', key, { capture: true });
      window.removeEventListener('keyup', keyUp);
    };
  });

  useEffect(() => {
    setChain([]);
    setTyped('');
    setHover(null);
    setPlacement(null);
  }, [tool]);

  function startDrag(next: Drag) {
    return (e: ThreeEvent<PointerEvent>) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      if (controls) controls.enabled = false;
      setDrag(next);
    };
  }

  const anchor = chain.at(-1);
  const preview: [THREE.Vector3, THREE.Vector3][] = [];
  if (drag?.kind === 'node' && hover)
    for (const w of scene.walls)
      for (const [end, other] of [
        [w.start, w.end],
        [w.end, w.start],
      ] as const)
        if (Math.hypot(end[0] - drag.from[0], end[1] - drag.from[1]) < 1e-3) preview.push([v3(other), v3(hover.point)]);
  if (drag?.kind === 'wall' && hover) {
    const l = wallLength(drag.wall) || 1;
    const n: V2 = [-(drag.wall.end[1] - drag.wall.start[1]) / l, (drag.wall.end[0] - drag.wall.start[0]) / l];
    const off = (hover.point[0] - drag.grab[0]) * n[0] + (hover.point[1] - drag.grab[1]) * n[1];
    preview.push([v3([drag.wall.start[0] + n[0] * off, drag.wall.start[1] + n[1] * off]), v3([drag.wall.end[0] + n[0] * off, drag.wall.end[1] + n[1] * off])]);
  }
  if (tool === 'wall' && anchor && hover) preview.push([v3(anchor), v3(hover.point)]);
  for (let i = 0; i < chain.length - 1; i++) preview.push([v3(chain[i]), v3(chain[i + 1])]);

  const label =
    tool === 'wall' && anchor && hover
      ? `${typed ? `${typed} m ↵` : fmt(Math.hypot(hover.point[0] - anchor[0], hover.point[1] - anchor[1]))} · ${Math.round(
          (Math.atan2(-(hover.point[1] - anchor[1]), hover.point[0] - anchor[0]) * 180) / Math.PI,
        )}°`
      : hover?.kind
        ? snapKindLabels[hover.kind]
        : null;

  return (
    <group>
      {preview.map(([a, b], i) => (
        <Line key={i} points={[a, b]} color="#f3bd63" lineWidth={2.5} dashed={i === 0 && !drag} dashSize={0.1} gapSize={0.06} depthTest={false} renderOrder={11} />
      ))}
      {hover?.guides.map(([a, b], i) => (
        <Line key={`g${i}`} points={[v3(a), v3(b)]} color="#66d9e8" lineWidth={1} dashed dashSize={0.05} gapSize={0.05} depthTest={false} renderOrder={11} />
      ))}
      {chain.map((p, i) => (
        <mesh key={`c${i}`} position={v3(p)} renderOrder={12}>
          <sphereGeometry args={[0.04]} />
          <meshBasicMaterial color="#f3bd63" depthTest={false} />
        </mesh>
      ))}
      {hover && (tool === 'wall' || drag?.kind === 'node') && (
        <group position={v3(hover.point)}>
          <mesh renderOrder={12}>
            <sphereGeometry args={[0.03]} />
            <meshBasicMaterial color={hover.kind && hover.kind !== 'grid' ? '#66d9e8' : '#f3bd63'} depthTest={false} />
          </mesh>
          {label && (
            <Html center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
              <div className={`snap-glyph snap-${hover.kind ?? 'free'}`}>{label}</div>
            </Html>
          )}
        </group>
      )}
      {placement && (
        <Line
          points={openingEnds(placement.wall, placement.offset, drag?.kind === 'opening' ? drag.opening.width : OPENING_DEFAULTS[tool === 'window' ? 'window' : 'door'].width)}
          color={tool === 'window' || (drag?.kind === 'opening' && drag.opening.type === 'window') ? '#9dcfd1' : '#c4a477'}
          lineWidth={6}
          depthTest={false}
          renderOrder={12}
        />
      )}
      {tool === 'select' && !drag && selectedWall && (
        <>
          {(['start', 'end'] as const).map((end) => (
            <Handle
              key={end}
              position={selectedWall[end]}
              color="#f3bd63"
              onDown={startDrag({ kind: 'node', from: selectedWall[end], exclude: new Set(wallsAt(scene.walls, selectedWall[end])) })}
            />
          ))}
          <Handle
            position={[(selectedWall.start[0] + selectedWall.end[0]) / 2, (selectedWall.start[1] + selectedWall.end[1]) / 2]}
            color="#66d9e8"
            onDown={(e) => {
              const grab = floor(e.nativeEvent)?.p ?? ([e.point.x, e.point.z] as V2);
              startDrag({ kind: 'wall', wall: selectedWall, grab })(e);
            }}
          />
        </>
      )}
      {tool === 'select' && !drag && selectedOpening && (() => {
        const wall = scene.walls.find((w) => w.id === selectedOpening.wallId);
        return wall ? (
          <Handle position={openingCentre(wall, selectedOpening)} color="#c4a477" onDown={startDrag({ kind: 'opening', opening: selectedOpening, wall })} />
        ) : null;
      })()}
    </group>
  );
}
