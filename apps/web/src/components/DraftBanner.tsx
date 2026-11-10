import type { restorableDraft } from '../draft';
import { clearDraft } from '../draft';
import { download } from '../geometry';
import { sceneSchema } from '../scene';
import { useEditor } from '../store';

type DraftOffer = ReturnType<typeof restorableDraft>;

/** Offers unsaved local edits found for the loaded scene: restore, download or discard. */
export function DraftBanner({
  draftOffer,
  setDraftOffer,
  onError,
}: {
  draftOffer: DraftOffer;
  setDraftOffer: (offer: DraftOffer) => void;
  onError: (error: unknown) => void;
}) {
  const scene = useEditor((s) => s.scene);
  if (!draftOffer || scene?.id !== draftOffer.draft.scene.id) return null;
  const { draft, stale } = draftOffer;
  const savedAt = new Date(draft.savedAt).toLocaleString();
  return (
    <div className="draft-banner" role="status">
      <b>Unsaved draft</b>
      <span>
        {stale
          ? `Local edits from ${savedAt} are based on revision ${draft.baseRevision}, but the server now has revision ${scene.revision}. Restoring them would conflict; download them as JSON instead.`
          : `Local edits from ${savedAt} were not saved.`}
      </span>
      {stale ? (
        <button
          onClick={() =>
            download(
              JSON.stringify(draft.scene, null, 2),
              'application/json',
              `${draft.scene.id}-draft.scene.json`,
            )
          }
        >
          Download draft
        </button>
      ) : (
        <button
          className="primary small"
          onClick={() => {
            const current = useEditor.getState().scene;
            const parsed = sceneSchema.safeParse(draft.scene);
            if (!current || !parsed.success || current.revision !== draft.baseRevision) {
              onError(
                new Error('This draft no longer matches the loaded scene; download it as JSON instead.'),
              );
              setDraftOffer({ ...draftOffer, stale: true });
              return;
            }
            useEditor.setState({ scene: parsed.data, dirty: true, past: [current], future: [] });
            setDraftOffer(null);
          }}
        >
          Restore
        </button>
      )}
      <button
        onClick={() => {
          clearDraft(draft.scene.id);
          setDraftOffer(null);
        }}
      >
        Discard
      </button>
    </div>
  );
}
