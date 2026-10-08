import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useEditor } from '../src/store';
import App from '../src/App';

vi.mock('../src/Viewport', () => ({
  Viewport: () => <div data-testid="viewport">Semantic 3D viewport</div>,
}));
vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api')>();
  return { ...actual, api: new actual.MockApi() };
});
beforeEach(() => {
  localStorage.clear();
  useEditor.setState({
    scene: null,
    dirty: false,
    error: null,
    busy: false,
    conflict: false,
    past: [],
    future: [],
    workspace: 'Reconstruct',
    selectedId: null,
    compare: false,
    sourceScene: null,
  });
});
describe('application integration without WebGL', () => {
  it('boots mock mode with its honest badge, synchronizes explorer and inspector, adds, undoes and saves', async () => {
    render(<App />);
    expect(screen.getByText('MOCK DATA')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Select Table' })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Select Table' }));
    expect(screen.getByLabelText('Width')).toHaveValue(1.2);
    fireEvent.change(screen.getByLabelText('Width'), { target: { value: '1.6' } });
    fireEvent.blur(screen.getByLabelText('Width'));
    expect(useEditor.getState().scene!.objects[0].dimensions[0]).toBe(1.6);
    fireEvent.click(screen.getByRole('button', { name: 'Add Chair' }));
    expect(useEditor.getState().scene!.objects).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(useEditor.getState().scene!.objects).toHaveLength(1);
    const revision = useEditor.getState().scene!.revision;
    fireEvent.click(screen.getByRole('button', { name: 'Save scene' }));
    await waitFor(() => expect(useEditor.getState().scene!.revision).toBe(revision + 1));
    expect(useEditor.getState().dirty).toBe(false);
  });
  it('does not intercept typing shortcuts, but supports selection transforms and history shortcuts outside inputs', async () => {
    render(<App />);
    await screen.findByRole('button', { name: 'Select Table' });
    fireEvent.click(screen.getByRole('button', { name: 'Select Table' }));
    const field = screen.getByLabelText('Width');
    fireEvent.keyDown(field, { key: 'Delete' });
    expect(useEditor.getState().scene!.objects).toHaveLength(1);
    fireEvent.keyDown(window, { key: 'r' });
    expect(useEditor.getState().mode).toBe('rotate');
    fireEvent.keyDown(window, { key: 's' });
    expect(useEditor.getState().mode).toBe('scale');
    fireEvent.keyDown(window, { key: 'Delete' });
    expect(useEditor.getState().scene!.objects).toHaveLength(0);
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(useEditor.getState().scene!.objects).toHaveLength(1);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(useEditor.getState().selectedId).toBeNull();
  });
  it('retrieves the immutable original for a clearly labelled comparison toggle', async () => {
    render(<App />);
    await screen.findByRole('button', { name: 'Select Table' });
    fireEvent.click(screen.getByRole('button', { name: /Compare Original/ }));
    await waitFor(() => expect(useEditor.getState().compare).toBe(true));
    expect(useEditor.getState().sourceScene!.revision).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: /Compare Original/ }));
    await waitFor(() => expect(useEditor.getState().compare).toBe(false));
  });
  it('performs two-point visual calibration and mock job polling, then opens the fixture', async () => {
    render(<App />);
    await screen.findByRole('button', { name: 'Select Table' });
    fireEvent.click(screen.getByRole('button', { name: 'Reconstruct' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reset reference points' }));
    const preview = screen.getByAltText('Blueprint for two-point calibration').parentElement!;
    vi.spyOn(preview, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      width: 600,
      height: 600,
      right: 600,
      bottom: 600,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    fireEvent.click(preview, { clientX: 100, clientY: 150 });
    fireEvent.click(preview, { clientX: 300, clientY: 150 });
    expect(screen.getByText('50.0, 50.0 px')).toBeInTheDocument();
    expect(screen.getByText('150.0, 50.0 px')).toBeInTheDocument();
    expect(screen.getByText('0.020000')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load synthetic reconstruction →' }));
    await waitFor(() => expect(useEditor.getState().workspace).toBe('Edit'), { timeout: 4000 });
    expect(useEditor.getState().scene!.reconstruction.parser.name).toBe('fixture');
    expect(
      screen.getByText('Synthetic fixture loaded. No image analysis was performed.'),
    ).toBeInTheDocument();
  });
});
