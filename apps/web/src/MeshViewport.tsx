import { useEffect, useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Bounds, OrbitControls, Html, Line, useBounds } from '@react-three/drei';
import type { MeshPoint } from './api';
import * as THREE from 'three';
import { ErrorBoundary } from './ErrorBoundary';

function dispose(object: THREE.Object3D) {
  object.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      child.geometry.dispose();
      (Array.isArray(child.material) ? child.material : [child.material]).forEach(
        (m: THREE.Material) => {
          // Textured scans hold large images on the GPU; free them with the material.
          for (const value of Object.values(m))
            if (value instanceof THREE.Texture) value.dispose();
          m.dispose();
        },
      );
    }
  });
}
/** Re-frames the camera on the mesh whenever `token` changes (the "Fit mesh" button). */
function Refit({ token }: { token: number }) {
  const bounds = useBounds();
  useEffect(() => {
    if (token) bounds.refresh().clip().fit();
  }, [token, bounds]);
  return null;
}
function RenderReady({ onReady }: { onReady: () => void }) {
  const frames = useRef(0);
  useFrame(() => {
    if (++frames.current === 2) onReady();
  });
  return null;
}
export function MeshViewport({
  url,
  units = 'uncalibrated',
  points = [],
  onPick,
  floorAligned = false,
}: {
  url: string;
  units?: 'uncalibrated' | 'meters';
  points?: MeshPoint[];
  onPick?: (point: MeshPoint) => void;
  floorAligned?: boolean;
}) {
  const [object, setObject] = useState<THREE.Group | null>(null);
  const [error, setError] = useState('');
  const [wireframe, setWireframe] = useState(false);
  const [frame, setFrame] = useState(0);
  const [rendered, setRendered] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let loaded: THREE.Group | null = null;
    setError('');
    setObject(null);
    setRendered(false);
    void (async () => {
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error(`Mesh download failed (${response.status}).`);
        const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
        const gltf = await new GLTFLoader().parseAsync(await response.arrayBuffer(), '');
        loaded = gltf.scene;
        if (controller.signal.aborted) dispose(loaded);
        else setObject(loaded);
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      controller.abort();
      if (loaded) dispose(loaded);
    };
  }, [url]);
  useEffect(() => {
    object?.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        (Array.isArray(child.material) ? child.material : [child.material]).forEach((m) => {
          m.wireframe = wireframe;
          m.needsUpdate = true;
        });
      }
    });
  }, [object, wireframe]);
  return (
    <div className="mesh-viewport" data-rendered={rendered} data-picking={!!onPick}>
      <div className="mesh-view-controls">
        <button onClick={() => setFrame((v) => v + 1)}>
          Fit mesh
        </button>
        <button aria-pressed={wireframe} onClick={() => setWireframe(!wireframe)}>
          Wireframe
        </button>
      </div>
      {error ? (
        <div className="mesh-load-state" role="alert">
          {error}
        </div>
      ) : !object ? (
        <div className="mesh-load-state" role="status">
          Loading colored mesh…
        </div>
      ) : (
        <ErrorBoundary
          resetKey={url}
          fallback={(e) => (
            <div className="mesh-load-state" role="alert">
              Viewer unavailable: {e.message}. You can still download the GLB.
            </div>
          )}
        >
          <Canvas camera={{ position: [2, 1.5, 3], fov: 50 }} dpr={[1, 1.5]}>
            <color attach="background" args={['#171f22']} />
            <ambientLight intensity={1.4} />
            <directionalLight position={[5, 8, 5]} intensity={2} />
            <Bounds fit clip observe margin={1.25}>
              <primitive
                object={object}
                dispose={null}
                onClick={(event: {
                  delta: number;
                  point: THREE.Vector3;
                  stopPropagation: () => void;
                }) => {
                  if (onPick && event.delta <= 4) {
                    event.stopPropagation();
                    onPick(event.point.toArray() as MeshPoint);
                  }
                }}
              />
              <Refit token={frame} />
            </Bounds>
            {points.length > 1 && <Line points={points} color="#ffcb70" lineWidth={2} />}
            {points.map((point, i) => (
              <Html key={i} position={point} center style={{ pointerEvents: 'none' }}>
                <span className="mesh-point-label">{i + 1}</span>
              </Html>
            ))}
            {floorAligned && <axesHelper args={[units === 'meters' ? 1 : 0.25]} />}
            <OrbitControls makeDefault enableDamping />
            <RenderReady onReady={() => setRendered(true)} />
          </Canvas>
          {!rendered && (
            <div className="mesh-load-state mesh-render-loading" role="status">
              Loading colored mesh…
            </div>
          )}
        </ErrorBoundary>
      )}
      <div className="mesh-view-caption">
        {onPick ? 'Click a surface to select a point · ' : ''}Drag to orbit · Scroll to zoom ·{' '}
        {units === 'meters' ? 'Meters · user calibrated' : 'Uncalibrated scale'}
      </div>
    </div>
  );
}
