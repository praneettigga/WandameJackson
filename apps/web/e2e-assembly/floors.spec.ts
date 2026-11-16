import { expect, test, type Page } from '@playwright/test';
import { resolve } from 'node:path';

const fixtures = resolve('../../contracts/fixtures');

type Summary = {
  error: string | null;
  floors: { id: string; name: string; offset: [number, number]; projectId: string }[];
  scenes: Record<string, { rooms: number; objects: (string | null)[]; mpp: number }>;
};
type EditorState = {
  error: string | null;
  busy: boolean;
  hasUnsaved: () => boolean;
  assembly: {
    buildings: {
      floors: { id: string; name: string; offset: [number, number]; projectId: string }[];
    }[];
  } | null;
  scenes: Record<
    string,
    {
      rooms: unknown[];
      objects: { componentId: string | null }[];
      source: { calibration: { metersPerPixel: number } };
    }
  >;
};
type EditorWindow = Window & { __editor: { getState: () => EditorState } };
const editor = (page: Page) =>
  page.evaluate(() => {
    const s = (window as unknown as EditorWindow).__editor.getState();
    return { busy: s.busy, unsaved: s.hasUnsaved() };
  });
const summary = (page: Page) =>
  page.evaluate((): Summary => {
    const s = (window as unknown as EditorWindow).__editor.getState();
    return {
      error: s.error,
      floors: (s.assembly?.buildings ?? []).flatMap((b) =>
        b.floors.map((f) => ({ id: f.id, name: f.name, offset: f.offset, projectId: f.projectId })),
      ),
      scenes: Object.fromEntries(
        Object.entries(s.scenes ?? {}).map(([id, scene]) => [
          id,
          {
            rooms: scene.rooms.length,
            objects: scene.objects.map((o) => o.componentId),
            mpp: scene.source.calibration.metersPerPixel,
          },
        ]),
      ),
    };
  });

async function waitForScenes(page: Page, count: number) {
  await expect
    .poll(async () => Object.keys((await summary(page)).scenes).length, { timeout: 150_000 })
    .toBeGreaterThanOrEqual(count);
  await expect.poll(async () => (await editor(page)).busy).toBe(false);
}

test('two floors of one building: reconstruct, align, duplicate, remove, save, reopen and export', async ({
  page,
}) => {
  await page.goto('/');
  await page
    .locator('input[type=file]')
    .setInputFiles([resolve(fixtures, 'floor-1.png'), resolve(fixtures, 'floor-4.png')]);
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Floors of the same building' }).click();
  await dialog.getByRole('button', { name: 'Create grouped project' }).click();
  await page.getByRole('button', { name: /Reconstruct buildings/ }).click();
  await waitForScenes(page, 2);

  let s = await summary(page);
  expect(s.error).toBeNull();
  expect(s.floors.map((f) => f.name)).toEqual(['floor-1', 'floor-4']);
  for (const floor of s.floors) {
    const scene = s.scenes[floor.projectId];
    // The printed "12.0 m" is read as "120m"; the wall thickness check repairs it to 0.01 m/px.
    expect(scene.mpp).toBeCloseTo(0.01, 3);
    expect(scene.objects).toContain('stairs.u');
    expect(scene.rooms).toBeGreaterThanOrEqual(10);
  }
  // Same sheet, same stair core: the floors already line up and nothing needs moving.
  await page.getByRole('button', { name: 'Auto-align floors' }).click();
  await expect(page.getByText(/floor-4: outlines overlap \d+%/)).toBeVisible();
  s = await summary(page);
  expect(Math.hypot(...s.floors[1].offset)).toBeLessThan(0.15);

  await page.getByRole('combobox').filter({ hasText: 'Exploded view' }).selectOption('exploded');
  await page.screenshot({ path: 'test-results/assembly-exploded.png' });
  await page.getByRole('combobox').filter({ hasText: 'Exploded view' }).selectOption('all');

  // Duplicate the top floor (a repeated floor) and remove it again.
  await page
    .getByRole('button', { name: /floor-4/ })
    .first()
    .click();
  await page.getByRole('button', { name: 'Duplicate floor' }).click();
  await expect.poll(async () => (await summary(page)).floors.length).toBe(3);
  s = await summary(page);
  expect(s.floors[2].name).toBe('floor-4 copy');
  expect(s.scenes[s.floors[2].projectId].objects).toContain('stairs.u');
  await page.getByRole('button', { name: 'Remove floor' }).click();
  await expect.poll(async () => (await summary(page)).floors.length).toBe(2);

  await page.getByRole('button', { name: 'Save project' }).click();
  await expect.poll(async () => (await editor(page)).unsaved).toBe(false);

  await page.reload();
  await page.getByRole('button', { name: 'Open project' }).click();
  await waitForScenes(page, 2);
  s = await summary(page);
  expect(s.floors.map((f) => f.name)).toEqual(['floor-1', 'floor-4']);

  for (const name of ['↓ Scene JSON', '↓ GLB']) {
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name }).last().click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^a_[a-z0-9]+\.(assembly\.json|glb)$/);
  }
});
