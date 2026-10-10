import { expect, test, type Page } from '@playwright/test';

type V2 = [number, number];
type EditorWindow = Window & {
  __editor: {
    getState: () => {
      scene: {
        walls: { id: string; start: V2; end: V2 }[];
        openings: { id: string; type: string; wallId: string; offset: number }[];
        rooms: { id: string; name: string }[];
        revision: number;
      } | null;
      gridStep: number;
      tool: string;
      selectedId: string | null;
      error: string | null;
    };
  };
  __three: {
    scene: { getObjectByName: (name: string) => { position: { x: number; z: number }; material: { map: { image: HTMLCanvasElement } } } | undefined };
    camera: { position: unknown; updateMatrixWorld: () => void };
    Vector3: new (x: number, y: number, z: number) => { project: (c: unknown) => { x: number; y: number } };
  };
};

const state = (page: Page) =>
  page.evaluate(() => {
    const s = (window as unknown as EditorWindow).__editor.getState();
    return { scene: s.scene, gridStep: s.gridStep, tool: s.tool, error: s.error, selectedId: s.selectedId };
  });

/** Page coordinates of a floor point [x, z]. */
async function toScreen(page: Page, p: V2) {
  const canvas = page.locator('.viewport canvas');
  const box = (await canvas.boundingBox())!;
  const ndc = await page.evaluate(([x, z]) => {
    const t = (window as unknown as EditorWindow).__three;
    t.camera.updateMatrixWorld();
    const v = new t.Vector3(x, 0, z).project(t.camera);
    return { x: v.x, y: v.y };
  }, p);
  return { x: box.x + ((ndc.x + 1) / 2) * box.width, y: box.y + ((1 - ndc.y) / 2) * box.height };
}

async function clickFloor(page: Page, p: V2, offsetPx: [number, number] = [0, 0]) {
  const s = await toScreen(page, p);
  await page.mouse.move(s.x + offsetPx[0], s.y + offsetPx[1], { steps: 3 });
  await page.mouse.down();
  await page.mouse.up();
}

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect.poll(async () => (await state(page)).scene?.walls.length ?? 0).toBe(4);
  await page.waitForFunction(() => Boolean((window as unknown as EditorWindow).__three));
  await page.waitForTimeout(500);
});

test('renders the demo room with WebGL', async ({ page }) => {
  const webgl = await page.evaluate(() => {
    const c = document.querySelector('.viewport canvas') as HTMLCanvasElement | null;
    return Boolean(c && (c.getContext('webgl2') || c.getContext('webgl')));
  });
  expect(webgl).toBe(true);
  await page.screenshot({ path: 'test-results/editor-demo-room.png' });
  // The rendered frame is not a flat colour.
  const shot = await page.locator('.viewport canvas').screenshot();
  expect(new Set(shot.subarray(1000, 60000)).size).toBeGreaterThan(20);
});

test('resizes the review dock with pointer and keyboard while retaining the viewport', async ({ page }) => {
  const splitter = page.getByRole('separator', { name: 'Resize bottom panel' });
  const dock = page.locator('.bottom-dock');
  const height = () => dock.evaluate((el) => el.getBoundingClientRect().height);
  const original = await height();
  const handle = (await splitter.boundingBox())!;
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2, handle.y - 80, { steps: 5 });
  await page.mouse.up();
  await expect.poll(height).toBeGreaterThan(original + 60);
  const enlarged = await height();
  await splitter.focus();
  await page.keyboard.press('ArrowDown');
  await expect.poll(height).toBeCloseTo(enlarged - 20, 0);
  expect((await page.locator('.viewport canvas').boundingBox())!.height).toBeGreaterThan(100);
  await page.screenshot({ path: 'test-results/merged-resizable-dock.png' });
});

test('selects a floor label, renames the room, toggles labels and reloads the saved name', async ({ page }) => {
  const room = (await state(page)).scene!.rooms[0];
  const labelPosition = await page.evaluate((id) => {
    const label = (window as unknown as EditorWindow).__three.scene.getObjectByName(`${id}:label`)!;
    return [label.position.x, label.position.z] as V2;
  }, room.id);
  // Look into the room so the floor label is visible and can be selected.
  await page.evaluate(([x, z]) => {
    const camera = (window as unknown as EditorWindow).__three.camera as {
      position: { set: (x: number, y: number, z: number) => void };
      lookAt: (x: number, y: number, z: number) => void;
      updateMatrixWorld: () => void;
    };
    camera.position.set(x, 11, z + 0.01);
    camera.lookAt(x, 0, z);
    camera.updateMatrixWorld();
  }, labelPosition);
  await clickFloor(page, labelPosition);
  await expect.poll(async () => (await state(page)).selectedId).toBe(room.id);
  await expect(page.getByLabel('Room name', { exact: true })).toBeVisible();
  await page.getByLabel('Room name', { exact: true }).fill('Home Office');
  await page.getByLabel('Room name', { exact: true }).press('Enter');
  await expect.poll(async () => (await state(page)).scene!.rooms[0].name).toBe('Home Office');
  await page.screenshot({ path: 'test-results/room-floor-label.png' });
  await page.getByLabel('Room labels', { exact: true }).uncheck();
  expect(await page.evaluate((id) => Boolean((window as unknown as EditorWindow).__three.scene.getObjectByName(`${id}:label`)), room.id)).toBe(false);
  await page.getByLabel('Room labels', { exact: true }).check();
  await page.getByRole('button', { name: 'Save scene' }).click();
  await expect.poll(async () => (await state(page)).scene!.revision).toBe(1);
  await page.reload();
  await expect.poll(async () => (await state(page)).scene?.rooms[0].name).toBe('Home Office');
});

test('grid step follows zoom', async ({ page }) => {
  const canvas = page.locator('.viewport canvas');
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const before = (await state(page)).gridStep;
  for (let i = 0; i < 40; i++) await page.mouse.wheel(0, -400);
  await expect.poll(async () => (await state(page)).gridStep).toBeLessThan(before);
  for (let i = 0; i < 80; i++) await page.mouse.wheel(0, 600);
  await expect.poll(async () => (await state(page)).gridStep).toBeGreaterThan(before);
  await expect(page.locator('.snap-readout')).toContainText('SNAP');
});

test('draws a wall that snaps to existing endpoints and splits the room', async ({ page }) => {
  await page.keyboard.press('w');
  await expect.poll(async () => (await state(page)).tool).toBe('wall');
  // Start a few pixels off the wall-n/wall-e corner side, end near wall-s: both snap onto walls.
  await clickFloor(page, [4.5, 1], [3, 2]);
  await clickFloor(page, [4.5, 4], [-2, 3]);
  await page.screenshot({ path: 'test-results/editor-wall-drawn.png' });
  await page.keyboard.press('Escape');
  const s = (await state(page)).scene!;
  expect(s.rooms).toHaveLength(2);
  const drawn = s.walls.filter((w) => !['wall-n', 'wall-e', 'wall-s', 'wall-w'].includes(w.id));
  const added = drawn.find((w) => Math.abs(w.start[0] - w.end[0]) < 1e-6 && Math.abs(w.start[1] - w.end[1]) > 2);
  expect(added, JSON.stringify(drawn)).toBeTruthy();
  expect([added!.start[1], added!.end[1]].sort()).toEqual([1, 4]);
  // Undo removes the wall and the extra room in one step.
  await page.keyboard.press('Control+z');
  await expect.poll(async () => (await state(page)).scene!.rooms.length).toBe(1);
});

test('places a door on a wall and exports GLB', async ({ page }) => {
  await page.keyboard.press('d');
  const before = (await state(page)).scene!.openings.length;
  await clickFloor(page, [1, 2.5]);
  await expect.poll(async () => (await state(page)).scene!.openings.length).toBe(before + 1);
  const door = (await state(page)).scene!.openings.at(-1)!;
  expect(door).toMatchObject({ type: 'door', wallId: 'wall-w' });
  await page.keyboard.press('Escape');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '↓ GLB' }).click();
  const file = await (await download).createReadStream();
  const chunks: Buffer[] = [];
  for await (const c of file) chunks.push(c as Buffer);
  const glb = Buffer.concat(chunks);
  expect(glb.subarray(0, 4).toString()).toBe('glTF');
});

test('saves wall edits and reloads them', async ({ page }) => {
  await page.keyboard.press('w');
  await clickFloor(page, [4.5, 1]);
  await clickFloor(page, [4.5, 4]);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Save scene' }).click();
  await expect.poll(async () => (await state(page)).scene!.revision).toBe(1);
  await page.reload();
  await expect.poll(async () => (await state(page)).scene?.rooms.length ?? 0).toBe(2);
});

test('drags a wall corner handle and the room follows', async ({ page }) => {
  await page.getByText('wall-e', { exact: true }).click();
  const from = await toScreen(page, [5, 4]);
  const to = await toScreen(page, [5.5, 4]);
  await page.mouse.move(from.x, from.y, { steps: 2 });
  await page.mouse.down();
  await page.mouse.move(to.x + 2, to.y + 1, { steps: 8 });
  await page.mouse.up();
  const s = (await state(page)).scene!;
  const e = s.walls.find((w) => w.id === 'wall-e')!;
  expect(e.end[0]).toBeGreaterThan(5.2);
  // Snapped to the zoom-dependent grid, not left at the raw pointer position.
  const step = (await state(page)).gridStep;
  expect(Math.abs(e.end[0] / step - Math.round(e.end[0] / step))).toBeLessThan(1e-6);
  expect(s.walls.find((w) => w.id === 'wall-s')!.start).toEqual(e.end);
  await page.screenshot({ path: 'test-results/editor-corner-dragged.png' });
});

test('restores an unsaved draft after a refresh', async ({ page }) => {
  page.on('dialog', (d) => void d.accept());
  await page.keyboard.press('w');
  await clickFloor(page, [4.5, 1]);
  await clickFloor(page, [4.5, 4]);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(1200); // autosave debounce
  await page.reload();
  await expect(page.locator('.draft-banner')).toContainText('were not saved');
  await page.getByRole('button', { name: 'Restore' }).click();
  await expect.poll(async () => (await state(page)).scene!.rooms.length).toBe(2);
});

test('slides a door across a junction onto the next collinear wall', async ({ page }) => {
  // Split wall-s at x = 2 by drawing a partition; the door stays on the first piece.
  await page.keyboard.press('w');
  await clickFloor(page, [2, 1]);
  await clickFloor(page, [2, 4]);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page.getByText('door / door-1').click();
  const from = await toScreen(page, [3.55, 4]);
  const to = await toScreen(page, [1.5, 4]);
  await page.mouse.move(from.x, from.y, { steps: 2 });
  await page.mouse.down();
  await page.mouse.move(to.x + 1, to.y, { steps: 10 });
  await page.mouse.up();
  const s = (await state(page)).scene!;
  const door = s.openings.find((o) => o.id === 'door-1')!;
  expect(door.wallId).not.toBe('wall-s');
  const host = s.walls.find((w) => w.id === door.wallId)!;
  expect(Math.max(host.start[0], host.end[0])).toBeCloseTo(2);
  expect((await state(page)).error).toBeNull();
});
