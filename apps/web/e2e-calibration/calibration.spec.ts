import { test, expect } from '@playwright/test';
import validator from 'gltf-validator';
import { Matrix4, Vector3 } from 'three';

test('real surface picking, calibration API, export, floor alignment and reload agree', async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const endpoint = 'http://127.0.0.1:8012/api/projects/p_calibration/mesh';
  await page.goto('/');
  await page.getByRole('button', { name: 'Mode 2 · Photos & video' }).click();
  await page.getByLabel('Saved captures').selectOption('p_calibration');
  await expect(page.locator('.mesh-viewport')).toHaveAttribute('data-rendered', 'true');
  // Bounds animates camera fitting; click only after it settles.
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: 'Set scale · 2 points' }).click();
  const canvas = page.locator('.mesh-viewport canvas');
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2 - 35, box.y + box.height / 2);
  await page.mouse.click(box.x + box.width / 2 + 35, box.y + box.height / 2);
  await expect(page.getByRole('button', { name: 'Apply scale' })).toBeEnabled();
  await page.getByLabel('Known distance (meters)').fill('3');
  await page.getByRole('button', { name: 'Apply scale' }).click();
  await expect(
    page.getByText('Metric scale saved from your reference distance.', { exact: false }),
  ).toBeVisible();
  const scaled = await (await request.get(endpoint)).json();
  const matrix = new Matrix4().set(
    ...(scaled.reconstructionToWorld.flat() as Parameters<Matrix4['set']>),
  );
  const a = new Vector3(...scaled.calibration.reference.pointA).applyMatrix4(matrix);
  const b = new Vector3(...scaled.calibration.reference.pointB).applyMatrix4(matrix);
  expect(a.distanceTo(b)).toBeCloseTo(3, 6);
  const download = await request.get(`http://127.0.0.1:8012${scaled.meshUrl}`);
  const bytes = await download.body();
  expect((await validator.validateBytes(new Uint8Array(bytes))).issues.numErrors).toBe(0);
  const jsonSize = bytes.readUInt32LE(12);
  const doc = JSON.parse(bytes.subarray(20, 20 + jsonSize).toString());
  const pos = doc.accessors[0],
    view = doc.bufferViews[pos.bufferView];
  const first = new Vector3(
    ...([0, 1, 2].map((i) => bytes.readFloatLE(28 + jsonSize + view.byteOffset + i * 4)) as [
      number,
      number,
      number,
    ]),
  );
  expect(first.distanceTo(new Vector3(-1, -1, 0).applyMatrix4(matrix))).toBeLessThan(1e-5);
  await page.reload();
  await page.getByRole('button', { name: 'Mode 2 · Photos & video' }).click();
  await page.getByLabel('Saved captures').selectOption('p_calibration');
  await expect(
    page.getByText('Metric scale saved from your reference distance.', { exact: false }),
  ).toBeVisible();
  await expect(page.locator('.mesh-viewport')).toHaveAttribute('data-rendered', 'true');
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: 'Align floor · 3 points' }).click();
  const floorBox = (await canvas.boundingBox())!;
  for (const [x, y] of [
    [-35, 0],
    [35, 0],
    [0, 35],
  ])
    await page.mouse.click(
      floorBox.x + floorBox.width / 2 + x,
      floorBox.y + floorBox.height / 2 + y,
    );
  await expect(page.getByRole('button', { name: 'Apply floor alignment' })).toBeEnabled();
  await page.getByRole('button', { name: 'Apply floor alignment' }).click();
  await expect(page.getByRole('button', { name: 'Stop picking' })).toHaveCount(0);
  const aligned = await (await request.get(endpoint)).json();
  const floorMatrix = new Matrix4().set(
    ...(aligned.reconstructionToWorld.flat() as Parameters<Matrix4['set']>),
  );
  for (const p of aligned.calibration.floor.points)
    expect(new Vector3(...p).applyMatrix4(floorMatrix).y).toBeCloseTo(0, 6);
  expect(aligned.calibration.reference).toEqual(scaled.calibration.reference);
  const manifest = await (await request.get(`http://127.0.0.1:8012${aligned.manifestUrl}`)).json();
  expect(manifest.reconstructionToWorld).toEqual(aligned.reconstructionToWorld);
  await expect(page.locator('.mesh-viewport')).toHaveAttribute('data-rendered', 'true');
  await page.screenshot({ path: '/tmp/roomshift-milestone3.png', fullPage: true });
  expect(errors).toEqual([]);
});
