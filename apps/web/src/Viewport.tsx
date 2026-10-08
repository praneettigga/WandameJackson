import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import {
  Grid,
  Line,
  OrbitControls,
  PointerLockControls,
  TransformControls,
} from '@react-three/drei';
import * as THREE from 'three';
import {
  buildSceneGeometry,
  buildAssemblyGeometry,
  collides,
  disposeGeometry,
  pointInRoom,
} from './geometry';
import { useEditor, editorScenes } from './store';
import { floorsOf, placements, type Placement } from './assembly';
import { resizedObject, type Scene, type V3 } from './scene';

function FrameCamera({ root }: { root: THREE.Group }) {
  const camera = useThree((s) => s.camera),
    controls = useThree((s) => s.controls);
  const frame = useEditor((s) => s.frame);
  const workspace = useEditor((s) => s.workspace);
  useEffect(() => {
    if (workspace === 'Explore') return;
    const id = useEditor.getState().selectedId;
    const state = useEditor.getState();
    const active = state.assembly
      ? floorsOf(state.assembly).find((f) => f.projectId === state.activeProjectId)
      : null;
    const scope = active ? (root.getObjectByName(`floor:${active.id}`) ?? root) : root;
    const object = id
      ? (scope.getObjectByName(id) ?? scope.children.find((c) => c.userData.entityId === id))
      : root;
    const bounds = new THREE.Box3().setFromObject(object ?? root);
    if (bounds.isEmpty()) return;
    const center = bounds.getCenter(new THREE.Vector3());
    const distance = Math.max(bounds.getSize(new THREE.Vector3()).length() * 0.85, 2);
    if (camera instanceof THREE.PerspectiveCamera) {
      camera.far = Math.max(500, distance * 10);
      camera.updateProjectionMatrix();
    }
    camera.position.copy(center).add(new THREE.Vector3(distance * 0.85, distance * 0.85, distance));
    camera.lookAt(center);
    if (controls && 'target' in controls) {
      (controls.target as THREE.Vector3).copy(center);
      (controls as unknown as { update(): void }).update();
    }
    // Geometry edits must not reset the user's camera. Frame only on request/workspace exit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, camera, controls, workspace]);
  return null;
}
function FirstPerson({ scene, placement }: { scene: Scene; placement?: Placement }) {
  const camera = useThree((s) => s.camera);
  const keys = useRef(new Set<string>());
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    let spawn: [number, number] | undefined;
    for (const room of scene.rooms) {
      const xs = room.polygon.map((p) => p[0]),
        zs = room.polygon.map((p) => p[1]);
      const minX = Math.min(...xs),
        maxX = Math.max(...xs),
        minZ = Math.min(...zs),
        maxZ = Math.max(...zs);
      for (let i = 1; i < 20 && !spawn; i++)
        for (let j = 1; j < 20 && !spawn; j++) {
          const x = minX + ((maxX - minX) * i) / 20,
            z = minZ + ((maxZ - minZ) * j) / 20;
          if (pointInRoom(x, z, room.polygon) && !collides(scene, x, z)) spawn = [x, z];
        }
      if (spawn) break;
    }
    const start = spawn ?? scene.rooms[0]?.polygon[0] ?? [0, 0];
    const position = new THREE.Vector3(start[0], 1.65, start[1]);
    if (placement)
      position
        .applyAxisAngle(new THREE.Vector3(0, 1, 0), placement.rotationY)
        .add(new THREE.Vector3(...placement.position));
    camera.position.copy(position);
    camera.lookAt(position.clone().add(new THREE.Vector3(1, 0, 1)));
    const down = (e: KeyboardEvent) => {
      if (
        !(e.target instanceof HTMLElement) ||
        !e.target.matches('input, textarea, select, [contenteditable="true"]')
      )
        keys.current.add(e.code);
    };
    const up = (e: KeyboardEvent) => keys.current.delete(e.code);
    const clear = () => keys.current.clear();
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', clear);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', clear);
    };
  }, [camera, scene, placement?.floor.id, placement?.position.join(), placement?.rotationY]);
  useFrame((_, delta) => {
    if (!locked) return;
    const k = keys.current;
    const direction = camera.getWorldDirection(new THREE.Vector3());
    direction.y = 0;
    direction.normalize();
    const right = direction.clone().cross(new THREE.Vector3(0, 1, 0));
    const movement = direction
      .multiplyScalar(Number(k.has('KeyW')) - Number(k.has('KeyS')))
      .add(right.multiplyScalar(Number(k.has('KeyD')) - Number(k.has('KeyA'))));
    if (movement.lengthSq()) movement.normalize().multiplyScalar(Math.min(delta, 0.05) * 2);
    const blocked = (x: number, z: number) => {
      const local = new THREE.Vector3(x, 0, z);
      if (placement)
        local
          .sub(new THREE.Vector3(placement.position[0], 0, placement.position[2]))
          .applyAxisAngle(new THREE.Vector3(0, 1, 0), -placement.rotationY);
      return collides(scene, local.x, local.z);
    };
    if (!blocked(camera.position.x + movement.x, camera.position.z))
      camera.position.x += movement.x;
    if (!blocked(camera.position.x, camera.position.z + movement.z))
      camera.position.z += movement.z;
    camera.position.y = (placement?.elevation ?? 0) + 1.65;
  });
  return (
    <PointerLockControls
      selector="#enter-explore"
      onLock={() => setLocked(true)}
      onUnlock={() => {
        setLocked(false);
        useEditor.setState({ workspace: 'Edit' });
      }}
    />
  );
}
function World() {
  const state = useEditor();
  const scenes = editorScenes(state);
  const activeFloor = state.assembly
    ? floorsOf(state.assembly).find((f) => f.projectId === state.activeProjectId)
    : undefined;
  const view =
    state.workspace === 'Explore' && activeFloor
      ? { ...state.view, mode: 'floor' as const, floorId: activeFloor.id, buildingId: null }
      : state.view;
  const activePlacement = state.assembly
    ? placements(state.assembly, scenes).find((p) => p.floor.projectId === state.activeProjectId)
    : undefined;
  const root = useMemo(
    () =>
      state.assembly
        ? buildAssemblyGeometry(state.assembly, editorScenes(state), view, {
            ceilings: state.ceilings || state.workspace === 'Explore',
            xray: state.xray,
            selectedId: state.selectedId,
            activeProjectId: state.activeProjectId,
          })
        : state.scene
          ? buildSceneGeometry(state.scene, {
              ceilings: state.ceilings || state.workspace === 'Explore',
              xray: state.xray,
              selectedId: state.selectedId,
            })
          : new THREE.Group(),
    [
      state.scene,
      state.scenes,
      state.floorStates,
      state.assembly,
      state.view,
      state.activeProjectId,
      state.ceilings,
      state.workspace,
      state.xray,
      state.selectedId,
    ],
  );
  const ghost = useMemo(
    () =>
      state.sourceScene && state.compare
        ? buildSceneGeometry(state.sourceScene, { ghost: true, ceilings: state.ceilings })
        : null,
    [state.sourceScene, state.compare, state.ceilings],
  );
  if (ghost && activePlacement) {
    const display = state.assembly
      ? placements(state.assembly, scenes, view.mode === 'exploded' ? view.gap : 0).find(
          (p) => p.floor.id === activePlacement.floor.id,
        )!
      : activePlacement;
    ghost.position.set(...display.position);
    ghost.rotation.y = display.rotationY;
  }
  useEffect(() => () => disposeGeometry(root), [root]);
  useEffect(
    () => () => {
      if (ghost) disposeGeometry(ghost);
    },
    [ghost],
  );
  const selected = state.scene?.objects.find((o) => o.id === state.selectedId);
  const activeRoot = activeFloor ? root.getObjectByName(`floor:${activeFloor.id}`) : root;
  const target = selected ? activeRoot?.getObjectByName(selected.id) : undefined;
  const dragging = useRef(false);
  function click(event: ThreeEvent<MouseEvent>) {
    event.stopPropagation();
    if (dragging.current || state.workspace === 'Explore') return;
    if (state.measure) {
      const p = event.point;
      useEditor.setState({
        measures: [...(state.measures.length === 2 ? [] : state.measures), [p.x, p.y, p.z]],
      });
    } else if (state.assembly && event.object.userData.projectId)
      state.activateFloor(event.object.userData.projectId, event.object.userData.entityId);
    else state.select(event.object.userData.entityId ?? null);
  }
  return (
    <>
      <color attach="background" args={['#202629']} />
      <ambientLight intensity={0.9} />
      <hemisphereLight args={['#e1ecef', '#5c635c', 1.5]} />
      <directionalLight position={[3, 9, 5]} intensity={2.2} />
      <primitive object={root} onClick={click} />
      {ghost && <primitive object={ghost} raycast={() => null} />}
      {state.workspace !== 'Explore' && (
        <>
          <Grid
            position={[
              0,
              state.assembly
                ? view.mode === 'floor'
                  ? (activePlacement?.elevation ?? 0) - 0.215
                  : -0.215
                : -0.015,
              0,
            ]}
            args={[100, 100]}
            cellSize={0.1}
            sectionSize={1}
            cellColor="#343d41"
            sectionColor="#4e595e"
            cellThickness={0.4}
            sectionThickness={0.8}
            fadeDistance={35}
            infiniteGrid
          />
          <OrbitControls
            makeDefault
            minDistance={0.4}
            maxDistance={Math.max(
              100,
              new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3()).length() * 3,
            )}
          />
          {target && selected && !state.measure && !state.busy && (
            <TransformControls
              object={target}
              mode={state.mode}
              space={state.mode === 'scale' ? 'local' : 'world'}
              showX={state.mode !== 'rotate'}
              showY
              showZ={state.mode !== 'rotate'}
              translationSnap={state.snap ? 0.1 : null}
              rotationSnap={state.snap ? Math.PI / 12 : null}
              scaleSnap={state.snap ? 0.1 : null}
              onMouseDown={() => {
                dragging.current = true;
              }}
              onMouseUp={() => {
                if (state.mode === 'translate')
                  state.patch(selected.id, { position: target.position.toArray() });
                else if (state.mode === 'rotate') {
                  const forward = new THREE.Vector3(0, 0, 1).applyQuaternion(target.quaternion);
                  state.patch(selected.id, { rotationY: Math.atan2(forward.x, forward.z) });
                } else {
                  const resized = resizedObject(selected, target.scale.toArray() as V3);
                  target.scale.set(1, 1, 1);
                  state.patch(selected.id, { dimensions: resized.dimensions });
                }
                setTimeout(() => {
                  dragging.current = false;
                }, 0);
              }}
            />
          )}
          <FrameCamera root={root} />
        </>
      )}
      {state.workspace === 'Explore' && state.scene && (
        <FirstPerson scene={state.scene} placement={activePlacement} />
      )}
      {state.measures.map((p, i) => (
        <mesh key={i} position={p} userData={{ helper: true }}>
          <sphereGeometry args={[0.035]} />
          <meshBasicMaterial color="#f3bd63" depthTest={false} />
        </mesh>
      ))}
      {state.measures.length === 2 && (
        <Line points={state.measures} color="#f3bd63" lineWidth={2} />
      )}
    </>
  );
}
export function Viewport() {
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    const update = () => setLocked(Boolean(document.pointerLockElement));
    const failure = () =>
      useEditor.setState({
        error:
          'The browser could not start pointer lock. Click Enter first person again, or use a desktop browser with pointer-lock support.',
      });
    document.addEventListener('pointerlockchange', update);
    document.addEventListener('pointerlockerror', failure);
    return () => {
      document.removeEventListener('pointerlockchange', update);
      document.removeEventListener('pointerlockerror', failure);
    };
  }, []);
  const scene = useEditor((s) => s.scene),
    workspace = useEditor((s) => s.workspace),
    measures = useEditor((s) => s.measures);
  const assembly = useEditor((s) => s.assembly),
    memberScenes = useEditor((s) => s.scenes);
  const hasScene = Boolean(scene || (assembly && Object.keys(memberScenes).length));
  const measure = useEditor((s) => s.measure),
    compare = useEditor((s) => s.compare);
  return (
    <div className="viewport">
      <Canvas
        camera={{ position: [8, 7, 9], fov: 48, near: 0.02, far: 500 }}
        dpr={[1, 2]}
        onPointerMissed={() => {
          if (!useEditor.getState().measure) useEditor.getState().select(null);
        }}
      >
        <Suspense fallback={null}>
          <World />
        </Suspense>
      </Canvas>
      <div className="viewport-label">
        <span className="live-dot" /> {workspace === 'Explore' ? 'FIRST PERSON' : 'PERSPECTIVE'}{' '}
        <span className="muted">/ METRES</span>
      </div>
      {!hasScene && (
        <div className="empty-viewport">
          <div className="room-symbol">⌑</div>
          <h2>A plan becomes a place.</h2>
          <p>Upload a blueprint to assign its scale automatically.</p>
          <span>
            01 UPLOAD <b>→</b> 02 CALIBRATE <b>→</b> 03 RECONSTRUCT
          </span>
        </div>
      )}
      {compare && <div className="compare-label">CYAN WIREFRAME · ORIGINAL RECONSTRUCTION</div>}
      {workspace === 'Explore' && (
        <div className="explore-overlay" style={{ visibility: locked ? 'hidden' : 'visible' }}>
          <button id="enter-explore" className="primary">
            Enter first person
          </button>
          <p>WASD to walk · Mouse to look · Esc to return</p>
          <span>Eye level 1.65 m · Simple wall and furniture collision</span>
        </div>
      )}
      {measure && (
        <div className="measure-readout">
          {measures.length === 2
            ? `${new THREE.Vector3(...measures[0]).distanceTo(new THREE.Vector3(...measures[1])).toFixed(3)} m`
            : 'Click two surface points to measure'}
          <small>3D straight-line distance</small>
        </div>
      )}
      <div className="viewport-bottom">
        <span>
          {workspace === 'Explore'
            ? 'WALK / 2 m/s'
            : 'LMB select · Drag to orbit · RMB pan · Scroll zoom'}
        </span>
        <span>
          X <i className="axis-x">━</i> Y <i className="axis-y">━</i> Z <i className="axis-z">━</i>
        </span>
      </div>
    </div>
  );
}
