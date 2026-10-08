import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CaptureWorkspace } from '../src/CaptureWorkspace';
import { api, HttpApi, pollJob, type Job } from '../src/api';

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
    expect(screen.getByText(/3D mesh reconstruction is not available yet/)).toBeInTheDocument();
    expect(screen.getByText(/room.mp4 · 1.5s/)).toBeInTheDocument();
    expect(api.createCapture).toHaveBeenCalledWith('video', expect.any(Array));
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
