import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { demoScene, MockApi } from '../src/api';
import { useEditor } from '../src/store';
import { ErrorBanner } from '../src/App';
import { Inspector } from '../src/Inspector';

beforeEach(() => {
  useEditor.setState({ busy: false, conflict: false, workspace: 'Edit' });
  useEditor.getState().load(demoScene());
});
describe('semantic editor state', () => {
  it('clears conflict, history, measurements and selection when opening an unreconstructed project', () => {
    useEditor.setState({
      conflict: true,
      error: 'Old conflict',
      measure: true,
      measures: [[1, 2, 3]],
      selectedId: 'obj-table-1',
    });
    useEditor.getState().load(null);
    expect(useEditor.getState()).toMatchObject({
      scene: null,
      conflict: false,
      error: null,
      measure: false,
      measures: [],
      selectedId: null,
      workspace: 'Reconstruct',
      past: [],
      future: [],
    });
  });
  it('restores the displayed object name on undo', () => {
    useEditor.getState().select('obj-table-1');
    render(<Inspector />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Renamed table' } });
    fireEvent.blur(screen.getByLabelText('Name'));
    // Use a UI event to flush the state change through React.
    render(<button onClick={() => useEditor.getState().undo()}>Undo name</button>);
    fireEvent.click(screen.getByText('Undo name'));
    expect(screen.getByLabelText('Name')).toHaveValue('Table');
  });
  it('selects only by semantic ID and refuses unknown IDs', () => {
    useEditor.getState().select('obj-table-1');
    expect(useEditor.getState().selectedId).toBe('obj-table-1');
    useEditor.getState().select('unknown');
    expect(useEditor.getState().selectedId).toBeNull();
  });
  it('commits one history entry per completed transform and supports undo/redo', () => {
    useEditor.getState().patch('obj-table-1', { position: [4, 0, 2] });
    expect(useEditor.getState().past).toHaveLength(1);
    useEditor.getState().undo();
    expect(useEditor.getState().scene!.objects[0].position).toEqual([3, 0, 2.5]);
    useEditor.getState().redo();
    expect(useEditor.getState().scene!.objects[0].position).toEqual([4, 0, 2]);
  });
  it('rejects invalid architectural edits atomically', () => {
    useEditor.getState().patch('wall-n', { height: 0.5 });
    expect(useEditor.getState().scene!.walls[0].height).toBe(2.7);
    expect(useEditor.getState().error).toContain('window-1');
    expect(useEditor.getState().past).toHaveLength(0);
  });
  it('adds, duplicates and deletes objects with new user provenance', () => {
    useEditor.getState().add('sofa.basic');
    const id = useEditor.getState().selectedId;
    useEditor.getState().duplicate();
    const duplicate = useEditor.getState().scene!.objects.at(-1)!;
    expect(duplicate.id).not.toBe(id);
    expect(duplicate.provenance.origin).toBe('user');
    useEditor.getState().remove();
    expect(useEditor.getState().scene!.objects).toHaveLength(2);
  });
  it('saves revision and retains current server revision when undoing', async () => {
    const api = new MockApi();
    useEditor.getState().patch('obj-table-1', { rotationY: 1 });
    await useEditor.getState().save(api);
    expect(useEditor.getState().scene!.revision).toBe(1);
    expect(useEditor.getState().dirty).toBe(false);
    useEditor.getState().undo();
    expect(useEditor.getState().scene!.revision).toBe(1);
    await useEditor.getState().save(api);
    expect(useEditor.getState().scene!.revision).toBe(2);
    expect((await api.getSourceScene('demo-room')).revision).toBe(0);
  });
  it('keeps local edits on revision conflict, displays the conflict, and prevents retry overwrite', async () => {
    const api = new MockApi();
    await api.saveScene('demo-room', demoScene());
    useEditor.getState().patch('obj-table-1', { rotationY: 0.7 });
    await useEditor.getState().save(api);
    render(<ErrorBanner />);
    expect(screen.getByRole('alert')).toHaveTextContent('Revision conflict');
    expect(screen.getByRole('alert')).toHaveTextContent('Export Scene JSON');
    expect(useEditor.getState().scene!.objects[0].rotationY).toBe(0.7);
    expect(useEditor.getState().dirty).toBe(true);
    await useEditor.getState().save(api);
    expect((await api.getScene('demo-room')).revision).toBe(1);
  });
  it('synchronizes the inspector, renders null confidence honestly, and commits dimensional resize', () => {
    useEditor.getState().select('obj-table-1');
    render(<Inspector />);
    expect(screen.getByText('Not calibrated / unavailable')).toBeInTheDocument();
    const width = screen.getByLabelText('Width');
    fireEvent.change(width, { target: { value: '1.7' } });
    fireEvent.blur(width);
    expect(useEditor.getState().scene!.objects[0].dimensions[0]).toBe(1.7);
    expect(useEditor.getState().scene!.objects[0].provenance.fieldOrigins.dimensions).toBe('user');
  });
});
