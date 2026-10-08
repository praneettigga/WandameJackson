import { beforeEach, describe, expect, it } from 'vitest';
import { demoScene } from '../src/api';
import { clearDraft, readDraft, restorableDraft, writeDraft } from '../src/draft';

beforeEach(() => localStorage.clear());
describe('local draft autosave', () => {
  it('offers an edited draft for the same revision', () => {
    const edited = demoScene();
    edited.objects[0].position = [2, 0, 2];
    writeDraft(edited);
    const offer = restorableDraft(demoScene());
    expect(offer?.stale).toBe(false);
    expect(offer?.draft.scene.objects[0].position).toEqual([2, 0, 2]);
  });
  it('drops a draft identical to the loaded scene', () => {
    writeDraft(demoScene());
    expect(restorableDraft(demoScene())).toBeNull();
    expect(readDraft('demo-room')).toBeNull();
  });
  it('marks drafts based on an older server revision as stale', () => {
    const edited = demoScene();
    edited.objects[0].position = [2, 0, 2];
    writeDraft(edited);
    expect(restorableDraft({ ...demoScene(), revision: 3 })?.stale).toBe(true);
  });
  it('ignores corrupt drafts', () => {
    localStorage.setItem('roomshift.draft.demo-room', '{"scene": {"nope": 1}}');
    expect(readDraft('demo-room')).toBeNull();
    clearDraft('demo-room');
  });
});
