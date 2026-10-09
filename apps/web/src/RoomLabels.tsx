import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { ThreeEvent } from '@react-three/fiber';
import type { Room, Scene, SceneObject } from './scene';
import { roomLabelPlacement } from './roomLabelPlacement';

function FloorLabel({
  room,
  objects,
  projectId,
  interactive,
  onClick,
}: {
  room: Room;
  objects: SceneObject[];
  projectId: string;
  interactive: boolean;
  onClick: (e: ThreeEvent<MouseEvent>) => void;
}) {
  const placement = useMemo(
    () => roomLabelPlacement(room.polygon, objects),
    [room.polygon, objects],
  );
  const texture = useMemo(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 1024;
    canvas.height = 256;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = 'rgba(24, 34, 38, 0.88)';
    ctx.beginPath();
    ctx.roundRect(0, 0, 1024, 256, 40);
    ctx.fill();
    ctx.strokeStyle = '#9ebcb7';
    ctx.lineWidth = 6;
    ctx.strokeRect(12, 12, 1000, 232);
    const name = room.name.trim() || 'Unnamed room';
    let size = 78;
    ctx.font = `600 ${size}px system-ui, sans-serif`;
    while (ctx.measureText(name).width > 920 && size > 12) {
      size -= 2;
      ctx.font = `600 ${size}px system-ui, sans-serif`;
    }
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#f5f7ed';
    ctx.fillText(name, 512, 128, 920);
    const map = new THREE.CanvasTexture(canvas);
    map.colorSpace = THREE.SRGBColorSpace;
    return map;
  }, [room.name]);
  useEffect(() => () => texture.dispose(), [texture]);
  if (!placement.width) return null;
  return (
    <mesh
      name={`${room.id}:label`}
      position={placement.position}
      rotation={[-Math.PI / 2, 0, 0]}
      userData={{ entityId: room.id, projectId }}
      raycast={interactive ? undefined : () => null}
      onClick={interactive ? onClick : undefined}
    >
      <planeGeometry args={[placement.width, placement.width / 4]} />
      <meshBasicMaterial map={texture} transparent depthWrite={false} />
    </mesh>
  );
}

export function RoomLabels({
  scene,
  interactive,
  onClick,
}: {
  scene: Scene;
  interactive: boolean;
  onClick: (e: ThreeEvent<MouseEvent>) => void;
}) {
  return (
    <>
      {scene.rooms.map((room) => (
        <FloorLabel
          key={room.id}
          room={room}
          objects={scene.objects}
          projectId={scene.id}
          interactive={interactive}
          onClick={onClick}
        />
      ))}
    </>
  );
}
