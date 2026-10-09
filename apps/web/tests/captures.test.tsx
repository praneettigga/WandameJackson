import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CaptureWorkspace } from '../src/CaptureWorkspace';
import { api, HttpApi, pollJob, type Job } from '../src/api';

vi.mock('../src/MeshViewport', () => ({
  MeshViewport: ({ url }: { url: string }) => <div data-testid="mesh-viewer">{url}</div>,
}));
beforeEach(() => {
  vi.spyOn(api, 'reconstructionCapabilities').mockResolvedValue({
    ready: true,
    code: 'READY',
    message: 'Worker ready.',
  });
});
const job: Job = {
  id: 'j_1',
  projectId: 'p_capture',
  status: 'succeeded',
  kind: 'capture-preparation',
  stage: 'ready',
  progress: 1,
  sceneUrl: null,
  inputManifestUrl: '/api/projects/p_capture/capture-input',
  error: null,
  createdAt: '',
  updatedAt: '',
};
const envelope = {
  project: { id: 'p_capture', name: 'My room', createdAt: '', hasScene: false },
  image: null,
  source: { kind: 'video' as const },
  captureJobId: 'j_1',
  inputManifestUrl: job.inputManifestUrl!,
};

describe('capture ingestion UI', () => {
  it('uploads and reviews selected frames without claiming a mesh', async () => {
    vi.spyOn(api, 'listCaptures').mockResolvedValue([envelope]);
    vi.spyOn(api, 'getCapture').mockResolvedValue(envelope);
    vi.spyOn(api, 'createCapture').mockResolvedValue({ ...envelope, job });
    vi.spyOn(api, 'getCaptureInput').mockResolvedValue({
      schemaVersion: '1.0.0',
      projectId: 'p_capture',
      originals: [{ id: 'source_0000', filename: 'room.mp4' }],
      rejected: [],
      warnings: ['Quality checks are heuristics.'],
      frames: [
        {
          id: 'frame_0000',
          sourceId: 'source_0000',
          url: '/frame.png',
          timestampSeconds: 1.5,
          width: 640,
          height: 480,
        },
      ],
    });
    render(<CaptureWorkspace />);
    fireEvent.change(screen.getByLabelText('Choose capture files'), {
      target: { files: [new File(['video'], 'room.mp4')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Upload & prepare views' }));
    await screen.findByText('1 views ready for reconstruction');
    expect(screen.getByRole('button', { name: 'Reconstruct mesh →' })).toBeEnabled();
    expect(screen.getByText(/room.mp4 · 1.5s/)).toBeInTheDocument();
    expect(api.createCapture).toHaveBeenCalledWith('video', expect.any(Array));
    fireEvent.click(screen.getByText(/Capture evidence & quality/));
    expect(screen.getByRole('link', { name: 'Open input manifest (JSON)' })).toBeInTheDocument();
  });
  it('rejects too few photos before uploading', async () => {
    vi.spyOn(api, 'listCaptures').mockResolvedValue([]);
    const create = vi.spyOn(api, 'createCapture');
    render(<CaptureWorkspace />);
    fireEvent.change(screen.getByLabelText('Capture type'), { target: { value: 'photo-set' } });
    fireEvent.change(screen.getByLabelText('Choose capture files'), {
      target: { files: [new File(['photo'], 'room.jpg')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Upload & prepare views' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose 20–40 photos');
    expect(create).not.toHaveBeenCalled();
  });
  it('reopens a failed capture with an actionable error', async () => {
    vi.spyOn(api, 'listCaptures').mockResolvedValue([{ ...envelope, inputManifestUrl: null }]);
    vi.spyOn(api, 'getCapture').mockResolvedValue({ ...envelope, inputManifestUrl: null });
    vi.spyOn(api, 'getJob').mockResolvedValue({
      job: {
        ...job,
        status: 'failed',
        error: { code: 'LOW_OVERLAP', message: 'Retake with more overlap.', details: null },
      },
    });
    render(<CaptureWorkspace />);
    await screen.findByRole('option', { name: /My room/ });
    fireEvent.change(screen.getByLabelText('Saved captures'), { target: { value: 'p_capture' } });
    expect(await screen.findByRole('alert')).toHaveTextContent('Retake with more overlap.');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Retry preparation' })).toBeEnabled(),
    );
  });
});

it('accepts prepared inputs without treating them as scenes', async () => {
  expect(await pollJob(api, job, () => {})).toEqual(job);
  await expect(pollJob(api, { ...job, inputManifestUrl: null }, () => {})).rejects.toThrow(
    'no persisted result',
  );
});

it('keeps captures out of the blueprint list', async () => {
  const transport = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ projects: [envelope] })));
  const http = new HttpApi('http://localhost:8000', transport);
  expect(await http.listProjects()).toEqual({ projects: [] });
});

it('reopens an existing mesh and keeps its export available after a failed rerun', async () => {
  const mesh = {
    schemaVersion: '1.0.0' as const,
    jobId: 'j_mesh',
    inputJobId: 'j_1',
    projectId: 'p_capture',
    meshUrl: '/mesh.glb',
    diagnosticUrl: '/mesh.ply',
    units: 'uncalibrated' as const,
    cameras: [],
    statistics: { vertices: 400, triangles: 200, executionSeconds: 12, endToEndSeconds: 15 },
    warnings: ['Uncalibrated.'],
  };
  const prepared = {
    schemaVersion: '1.0.0' as const,
    projectId: 'p_capture',
    originals: [],
    rejected: [],
    warnings: [],
    frames: [
      {
        id: 'f_1',
        url: '/image.png',
        sourceId: 's_1',
        timestampSeconds: null,
        width: 640,
        height: 480,
      },
    ],
  };
  vi.spyOn(api, 'listCaptures').mockResolvedValue([{ ...envelope, meshManifestUrl: '/mesh' }]);
  vi.spyOn(api, 'getCapture').mockResolvedValue({ ...envelope, meshManifestUrl: '/mesh' });
  vi.spyOn(api, 'getCaptureInput').mockResolvedValue(prepared);
  vi.spyOn(api, 'getMesh').mockResolvedValue(mesh);
  vi.spyOn(api, 'getJob').mockResolvedValue({ job });
  vi.spyOn(api, 'reconstructMesh').mockResolvedValue({
    job: {
      ...job,
      kind: 'mesh-reconstruction',
      status: 'failed',
      error: { code: 'GPU_OUT_OF_MEMORY', message: 'Not enough VRAM.', details: null },
    },
  });
  render(<CaptureWorkspace />);
  await screen.findByRole('option', { name: /My room/ });
  fireEvent.change(screen.getByLabelText('Saved captures'), { target: { value: 'p_capture' } });
  expect(await screen.findByTestId('mesh-viewer')).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Reconstruct again' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Reconstruct again' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Not enough VRAM.');
  expect(screen.getByRole('link', { name: '↓ Export GLB' })).toHaveAttribute(
    'href',
    'http://127.0.0.1:8000/mesh.glb',
  );
  expect(screen.getByTestId('mesh-viewer')).toBeInTheDocument();
});

it('shows an explanatory capture guide and disables reconstruction while GPU is unavailable', async () => {
  vi.spyOn(api, 'reconstructionCapabilities').mockResolvedValue({
    ready: false,
    code: 'GPU_UNAVAILABLE',
    message: 'Restore the NVIDIA driver.',
  });
  vi.spyOn(api, 'listCaptures').mockResolvedValue([]);
  render(<CaptureWorkspace />);
  expect(screen.getByRole('img', { name: /Capture guide/ })).toBeInTheDocument();
  await screen.findByText('Restore the NVIDIA driver.');
  expect(screen.getByRole('button', { name: 'Reconstruct mesh →' })).toBeDisabled();
});
