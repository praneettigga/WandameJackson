import { expect, test } from '@playwright/test';
import validator from 'gltf-validator';

test('capture mesh uses shared edit, undo, save, reload, and export tools', async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');
  await page.getByRole('button', { name: /Mode 2/ }).click();
  await page.getByLabel('Saved captures').selectOption('p_editor_scan');
  await page.getByRole('button', { name: 'Edit in 3D workspace', exact: true }).click();
  await expect(page.locator('.viewport canvas')).toBeVisible();
  await expect(page.getByLabel('Position X', { exact: true })).toHaveValue('0');
  await page.getByLabel('Position X', { exact: true }).fill('3');
  await page.getByLabel('Position X', { exact: true }).press('Tab');
  await page.getByLabel('Width', { exact: true }).fill('4');
  await page.getByLabel('Width', { exact: true }).press('Tab');
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(page.getByLabel('Width', { exact: true })).toHaveValue('2');
  await page.getByRole('button', { name: 'Redo', exact: true }).click();
  await expect(page.getByLabel('Width', { exact: true })).toHaveValue('4');
  await page.getByLabel('Rotation Y · degrees').fill('30');
  await page.getByLabel('Rotation Y · degrees').press('Tab');
  await page.getByRole('button', { name: /Seating/ }).click();
  await page.getByRole('button', { name: 'Add Chair', exact: true }).click();
  await expect(page.getByLabel('Position X', { exact: true })).toHaveValue('3');
  await page.getByRole('button', { name: 'Save scene', exact: true }).click();
  const endpoint = 'http://127.0.0.1:8012/api/projects/p_editor_scan/scene';
  await expect.poll(async () => (await (await request.get(endpoint)).json()).revision).toBe(1);
  const saved = await (await request.get(endpoint)).json();
  expect(saved.objects).toHaveLength(2);
  expect(saved.objects[0]).toMatchObject({ position: [3, 0, 0] });
  expect(saved.objects[0].dimensions[0]).toBe(4);
  expect(saved.objects[0].dimensions[1]).toBeCloseTo(0.000001, 10);
  expect(saved.objects[0].dimensions[2]).toBe(2);
  expect(saved.objects[0].rotationY).toBeCloseTo(Math.PI / 6);
  // Reload the entire page to prove the mesh is restored from API assets, not browser memory.
  await page.reload();
  await page.getByRole('button', { name: /Mode 2/ }).click();
  await page.getByLabel('Saved captures').selectOption('p_editor_scan');
  await page.getByRole('button', { name: 'Continue editing in 3D workspace' }).click();
  await expect(page.getByLabel('Width', { exact: true })).toHaveValue('4');
  await expect(page.locator('.viewport canvas')).toBeVisible();
  await page.getByRole('button', { name: 'Reload', exact: true }).click();
  await page.getByRole('button', { name: 'Select Captured room mesh', exact: true }).click();
  await expect(page.getByLabel('Position X', { exact: true })).toHaveValue('3');
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: '↓ GLB', exact: true }).click();
  const stream = await (await downloading).createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  const bytes = Buffer.concat(chunks);
  expect((await validator.validateBytes(new Uint8Array(bytes))).issues.numErrors).toBe(0);
  const size = bytes.readUInt32LE(12);
  const gltf = JSON.parse(bytes.subarray(20, 20 + size).toString());
  expect(gltf.nodes.some((n: { name?: string }) => n.name === 'capture-mesh')).toBe(true);
  expect(errors).toEqual([]);
});

test('classroom preset opens with individually editable walls and furniture', async ({
  page,
  request,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: /Mode 2/ }).click();
  await page.getByLabel('Saved captures').selectOption('p_editor_classroom');
  await page.getByRole('button', { name: 'Edit in 3D workspace', exact: true }).click();
  await expect(page.locator('.viewport canvas')).toBeVisible();
  await page.getByRole('button', { name: 'Select Desk 1.1', exact: true }).click();
  await page.getByLabel('Position X', { exact: true }).fill('2.5');
  await page.getByLabel('Position X', { exact: true }).press('Tab');
  await page.getByRole('button', { name: 'Select front', exact: true }).click();
  await page.getByLabel('Wall height', { exact: true }).fill('3.5');
  await page.getByLabel('Wall height', { exact: true }).press('Tab');
  await page.getByRole('button', { name: 'Save scene', exact: true }).click();
  const endpoint = 'http://127.0.0.1:8012/api/projects/p_editor_classroom/scene';
  await expect.poll(async () => (await (await request.get(endpoint)).json()).revision).toBe(1);
  const saved = await (await request.get(endpoint)).json();
  expect(saved.walls).toHaveLength(4);
  expect(saved.openings).toHaveLength(7);
  expect(saved.walls.find((w: { id: string }) => w.id === 'front').height).toBe(3.5);
  expect(saved.objects.find((o: { name: string }) => o.name === 'Desk 1.1').position[0]).toBe(2.5);
  await page.getByRole('button', { name: 'Reload', exact: true }).click();
  await page.getByRole('button', { name: 'Select front', exact: true }).click();
  await expect(page.getByLabel('Wall height', { exact: true })).toHaveValue('3.5');
});
