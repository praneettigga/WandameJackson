import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import validator from 'gltf-validator';

// This synthetic plane exercises the actual exporter, glTF validator and WebGL viewer.
// It is deliberately not a reconstruction-quality benchmark or product fallback.
test('loads, orbits and downloads the exported colored mesh', async ({ page }) => {
  const root = resolve('../..');
  const temp = mkdtempSync(`${tmpdir()}/roomshift-mesh-e2e-`);
  try {
    execFileSync(`${root}/services/api/.venv/bin/python`, ['-c', `
import sys, numpy as np
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(`${root}/services/reconstruction`)})
from geometry import write_glb
v=np.array([[x/10,y/10,0] for y in range(11) for x in range(11)])
t=[]
for y in range(10):
 for x in range(10):
  a=y*11+x; t.extend([[a,a+1,a+11],[a+1,a+12,a+11]])
write_glb(Path(${JSON.stringify(`${temp}/mesh.glb`)}),v,t,np.tile([.6,.8,.4],(len(v),1)),np.tile([0,0,1],(len(v),1)))
`]);
    const glb = readFileSync(`${temp}/mesh.glb`);
    const report = await validator.validateBytes(new Uint8Array(glb));
    expect(report.issues.numErrors).toBe(0);
    const project = { project: { id: 'p_test', name: 'Test capture', createdAt: '', hasScene: false }, image: null,
      source: { kind: 'video' }, captureJobId: null, meshJobId: null, inputManifestUrl: '/input', meshManifestUrl: '/mesh', hasMesh: true };
    const manifest = { schemaVersion: '1.0.0', projectId: 'p_test', jobId: 'j_mesh', inputJobId: 'j_input', units: 'uncalibrated',
      meshUrl: '/api/test-mesh.glb', diagnosticUrl: '/diagnostic', cameras: [], warnings: ['Synthetic test geometry.'],
      statistics: { vertices: 121, triangles: 200, executionSeconds: 1, endToEndSeconds: 2 } };
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      const headers = { 'Access-Control-Allow-Origin': '*' };
      if (path === '/api/test-mesh.glb') return route.fulfill({ contentType: 'model/gltf-binary', body: glb, headers: { ...headers, 'Content-Disposition': 'attachment; filename="test-mesh.glb"' } });
      if (path === '/api/frame.png') return route.fulfill({ contentType: 'image/png', body: readFileSync(`${root}/contracts/fixtures/room.png`), headers });
      const body = path === '/api/health' ? { status: 'ok', schemaVersion: '0.1.0' }
        : path === '/api/projects' ? { projects: [project] }
        : path === '/api/reconstruction/capabilities' ? { ready: true, code: 'READY', message: 'Test worker' }
        : path.endsWith('/capture-input') ? { schemaVersion: '1.0.0', projectId: 'p_test', jobId: 'j_input', frames: [{ id: 'frame_0', sourceId: 'src', url: '/api/frame.png', timestampSeconds: 0 }], originals: [{ id: 'src', filename: 'test.mp4' }], rejected: [], warnings: [] }
        : path.endsWith('/mesh') ? manifest : project;
      await route.fulfill({ json: body, headers });
    });
    await page.goto('/');
    await page.getByRole('button', { name: 'Mode 2 · Photos & video' }).click();
    await page.getByLabel('Saved captures').selectOption('p_test');
    await expect(page.locator('.mesh-viewport canvas')).toBeVisible();
    await expect(page.getByText('Loading colored mesh…')).toBeHidden();
    await page.getByRole('button', { name: 'Wireframe' }).click();
    await expect(page.getByRole('button', { name: 'Wireframe' })).toHaveAttribute('aria-pressed', 'true');
    const box = (await page.locator('.mesh-viewport canvas').boundingBox())!;
    await page.mouse.move(box.x + box.width/2, box.y + box.height/2);
    await page.mouse.down(); await page.mouse.move(box.x + box.width/2 + 100, box.y + box.height/2 + 40, {steps:10}); await page.mouse.up();
    await page.getByRole('button', { name: 'Fit mesh' }).click();
    const downloading = page.waitForEvent('download');
    await page.getByRole('link', { name: '↓ Export GLB' }).click();
    expect((await downloading).suggestedFilename()).toBe('test-mesh.glb');
    await page.screenshot({ path: '/tmp/roomshift-mesh-viewer.png' });
  } finally { rmSync(temp, { recursive: true, force: true }); }
});
