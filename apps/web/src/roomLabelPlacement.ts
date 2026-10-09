import type { SceneObject, V2 } from './scene';

/** Find a roomy interior point, including for concave rooms whose centroid is outside. */
export function roomLabelPlacement(polygon: V2[], objects: SceneObject[] = []) {
  const xs = polygon.map((p) => p[0]),
    zs = polygon.map((p) => p[1]);
  const minX = Math.min(...xs),
    maxX = Math.max(...xs);
  const minZ = Math.min(...zs),
    maxZ = Math.max(...zs);
  function clearance(x: number, z: number) {
    let inside = false,
      distance = Infinity;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const a = polygon[j],
        b = polygon[i];
      if (a[1] > z !== b[1] > z && x < ((b[0] - a[0]) * (z - a[1])) / (b[1] - a[1]) + a[0])
        inside = !inside;
      const dx = b[0] - a[0],
        dz = b[1] - a[1];
      const t = Math.max(
        0,
        Math.min(1, ((x - a[0]) * dx + (z - a[1]) * dz) / (dx * dx + dz * dz || 1)),
      );
      distance = Math.min(distance, Math.hypot(x - a[0] - t * dx, z - a[1] - t * dz));
    }
    if (!inside) return -distance;
    for (const object of objects) {
      const dx = x - object.position[0],
        dz = z - object.position[2];
      const c = Math.cos(object.rotationY),
        s = Math.sin(object.rotationY);
      const qx = Math.abs(c * dx - s * dz) - object.dimensions[0] / 2;
      const qz = Math.abs(s * dx + c * dz) - object.dimensions[2] / 2;
      distance = Math.min(
        distance,
        Math.hypot(Math.max(qx, 0), Math.max(qz, 0)) + Math.min(Math.max(qx, qz), 0),
      );
    }
    return distance;
  }
  // A label is four times wider than it is deep. Fit that footprint directly;
  // a circular clearance would make labels tiny beside furniture.
  const centerX = (minX + maxX) / 2, centerZ = (minZ + maxZ) / 2;
  for (const width of [2.4, 2, 1.6, 1.3, 1, 0.8, 0.6, 0.4]) {
    let best: { x: number; z: number; score: number } | null = null;
    for (let i = 0; i <= 40; i++) for (let j = 0; j <= 40; j++) {
      const x = minX + ((maxX - minX) * i) / 40;
      const z = minZ + ((maxZ - minZ) * j) / 40;
      if (clearance(x, z) <= 0.06) continue;
      let fits = true;
      for (const dx of [-0.5, -0.25, 0, 0.25, 0.5]) {
        for (const dz of [-0.5, 0, 0.5]) {
          if (clearance(x + dx * width, z + dz * width / 4) <= 0.06) {
            fits = false; break;
          }
        }
        if (!fits) break;
      }
      if (!fits) continue;
      const score = clearance(x, z) - 0.05 * Math.hypot(x - centerX, z - centerZ);
      if (!best || score > best.score) best = { x, z, score };
    }
    if (best) return { position: [best.x, 0.025, best.z] as [number, number, number], width };
  }
  let x = (minX + maxX) / 2,
    z = (minZ + maxZ) / 2,
    radius = clearance(x, z);
  // Coarse search followed by local refinement; bounded work even on huge plans.
  let left = minX,
    right = maxX,
    top = minZ,
    bottom = maxZ;
  for (let pass = 0; pass < 4; pass++) {
    const sx = (right - left) / 24,
      sz = (bottom - top) / 24;
    for (let i = 0; i <= 24; i++)
      for (let j = 0; j <= 24; j++) {
        const px = left + i * sx,
          pz = top + j * sz,
          d = clearance(px, pz);
        if (d > radius) {
          x = px;
          z = pz;
          radius = d;
        }
      }
    left = x - sx;
    right = x + sx;
    top = z - sz;
    bottom = z + sz;
  }
  return {
    position: [x, 0.025, z] as [number, number, number],
    width: Math.min(2.4, Math.max(0, radius) * 1.6),
  };
}
