import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CalibratedMesh } from '../src/CalibratedMesh';
import { api, type MeshResult } from '../src/api';

vi.mock('../src/MeshViewport', () => ({
  MeshViewport: ({ onPick }: { onPick?: (p: number[]) => void }) => (
    <>
      <button onClick={() => onPick?.([2, 0, 0])}>Pick A</button>
      <button onClick={() => onPick?.([4, 0, 0])}>Pick B</button>
      <button onClick={() => onPick?.([2, 0, 2])}>Pick C</button>
    </>
  ),
}));
const mesh: MeshResult = {
  schemaVersion: '1.1.0',
  projectId: 'p_test',
  jobId: 'j_test',
  inputJobId: 'j_input',
  units: 'meters',
  meshUrl: '/mesh.glb',
  diagnosticUrl: '/raw.ply',
  calibrationRevision: 'c_old',
  reconstructionToWorld: [
    [2, 0, 0, 2],
    [0, 2, 0, 0],
    [0, 0, 2, 0],
    [0, 0, 0, 1],
  ],
  calibration: {
    reference: { pointA: [0, 0, 0], pointB: [1, 0, 0], distanceMeters: 2 },
    floor: null,
    rotationDegrees: [0, 0, 0],
  },
  cameras: [],
  warnings: [],
  statistics: { vertices: 121, triangles: 200, executionSeconds: 10, endToEndSeconds: 12 },
};

it('converts picks back to original coordinates and measures in displayed metric units', async () => {
  const save = vi
    .spyOn(api, 'calibrateMesh')
    .mockResolvedValue({ ...mesh, calibrationRevision: 'c_new' });
  const changed = vi.fn();
  render(<CalibratedMesh mesh={mesh} onChange={changed} disabled={false} />);
  fireEvent.click(screen.getByText('Set scale · 2 points'));
  fireEvent.click(screen.getByText('Pick A'));
  fireEvent.click(screen.getByText('Pick B'));
  expect(screen.getByText('2.0000 m')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Known distance (meters)'), { target: { value: '5' } });
  fireEvent.click(screen.getByText('Apply scale'));
  await screen.findByText('Saving calibrated mesh…');
  expect(save).toHaveBeenCalledWith('p_test', {
    jobId: 'j_test',
    expectedRevision: 'c_old',
    reference: { pointA: [0, 0, 0], pointB: [1, 0, 0], distanceMeters: 5 },
    floor: null,
    rotationDegrees: [0, 0, 0],
  });
});

it('sends a floor reference without dropping scale and displays save conflicts', async () => {
  const save = vi
    .spyOn(api, 'calibrateMesh')
    .mockRejectedValue(new Error('Reopen this capture before saving.'));
  render(<CalibratedMesh mesh={mesh} onChange={vi.fn()} disabled={false} />);
  fireEvent.click(screen.getByText('Align floor · 3 points'));
  fireEvent.click(screen.getByText('Pick A'));
  fireEvent.click(screen.getByText('Pick B'));
  fireEvent.click(screen.getByText('Pick C'));
  fireEvent.click(screen.getByText('Apply floor alignment'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Reopen this capture');
  expect(save).toHaveBeenCalledWith(
    'p_test',
    expect.objectContaining({
      reference: mesh.calibration!.reference,
      floor: {
        points: [
          [0, 0, 0],
          [1, 0, 0],
          [0, 0, 1],
        ],
        flipNormal: false,
      },
    }),
  );
});

it('does not label uncalibrated distances as meters', () => {
  render(
    <CalibratedMesh
      mesh={{ ...mesh, units: 'uncalibrated', calibration: null, reconstructionToWorld: undefined }}
      onChange={vi.fn()}
      disabled={false}
    />,
  );
  fireEvent.click(screen.getByText('Measure · 2 points'));
  fireEvent.click(screen.getByText('Pick A'));
  fireEvent.click(screen.getByText('Pick B'));
  expect(screen.getByText('2.0000 model units (uncalibrated)')).toBeInTheDocument();
});
