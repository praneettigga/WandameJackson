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
  it('renames rooms in Properties, tracks provenance, saves and undoes the name', async () => {
    const original = useEditor.getState().scene!.rooms[0];
    useEditor.getState().select(original.id);
    render(<Inspector />);
    const input = screen.getByLabelText('Room name');
    fireEvent.change(input, { target: { value: '  Home office  ' } });
    fireEvent.blur(input);
    expect(useEditor.getState().scene!.rooms[0].name).toBe('Home office');
    expect(useEditor.getState().scene!.rooms[0].provenance.fieldOrigins.name).toBe('user');
    const api = new MockApi();
    await useEditor.getState().save(api);
    expect((await api.getScene('demo-room')).rooms[0].name).toBe('Home office');
    render(<button onClick={() => useEditor.getState().undo()}>Undo room name</button>);
    fireEvent.click(screen.getByText('Undo room name'));
    expect(screen.getByLabelText('Room name')).toHaveValue(original.name);
    fireEvent.change(screen.getByLabelText('Room name'), { target: { value: '   ' } });
    fireEvent.blur(screen.getByLabelText('Room name'));
    expect(useEditor.getState().scene!.rooms[0].name).toBe(original.name);
  });
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
  it('explains parser confidence factors and flags inferred fields the score does not cover', () => {
    const scene = demoScene();
    Object.assign(scene.walls[0].provenance, {
      origin: 'evidence',
      confidence: 0.42,
      confidenceFactors: [
        { label: 'Stroke coverage', score: 0.9, detail: '90% backed by a solid wall stroke.' },
        { label: 'Junctions', score: 0.2, detail: 'Neither end meets another wall.' },
      ],
    });
    useEditor.getState().load(scene);
    useEditor.getState().select('wall-n');
    render(<Inspector />);
    expect(screen.getByRole('meter', { name: 'Detection confidence' })).toHaveAttribute(
      'aria-valuenow',
      '42',
    );
    expect(screen.getByText('Neither end meets another wall.')).toBeInTheDocument();
    expect(screen.getByText('Not read from the drawing:').parentElement).toHaveTextContent(
      'height, thickness',
    );
    const height = screen.getByLabelText('Wall height').closest('label')!;
    expect(height).toHaveTextContent('inferred');
  });
});
describe('wall topology edits', () => {
  it('commits a drawn wall and the rebuilt rooms as one undo step', async () => {
    const { addWall } = await import('../src/wallGraph');
    expect(useEditor.getState().wallEdit((s) => void addWall(s, [4.5, 1], [4.5, 4]))).toBe(true);
    expect(useEditor.getState().scene!.rooms).toHaveLength(2);
    expect(useEditor.getState().notice).toContain('New room');
    useEditor.getState().undo();
    expect(useEditor.getState().scene!.rooms).toHaveLength(1);
    expect(useEditor.getState().scene!.walls).toHaveLength(4);
  });
  it('deletes walls with their openings and openings on their own', () => {
    useEditor.getState().select('window-1');
    useEditor.getState().remove();
    expect(useEditor.getState().scene!.openings.map((o) => o.id)).toEqual(['door-1']);
    useEditor.getState().select('wall-s');
    window.confirm = () => true;
    useEditor.getState().remove();
    expect(useEditor.getState().scene!.walls).toHaveLength(3);
    expect(useEditor.getState().scene!.openings).toHaveLength(0);
    expect(useEditor.getState().scene!.rooms).toHaveLength(0);
  });
  it('rejects a wall edit that breaks an opening and leaves history unchanged', async () => {
    const { moveNode } = await import('../src/wallGraph');
    expect(useEditor.getState().wallEdit((s) => moveNode(s, [5, 1], [2, 1]))).toBe(false);
    expect(useEditor.getState().error).toMatch(/off its wall/);
    expect(useEditor.getState().past).toHaveLength(0);
  });
});
