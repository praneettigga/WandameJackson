import { lazy, Suspense, useState, type CSSProperties } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { api } from './api';
import { AssemblyControls, BlueprintWizard, FloorViews } from './AssemblyPanel';
import { LAST_PROJECT_KEY } from './constants';
import { ErrorBoundary, isWebglError } from './ErrorBoundary';
import { Inspector } from './Inspector';
import { useEditor } from './store';
import { Viewport } from './Viewport';
import { AppHeader, type WorkspaceName } from './components/AppHeader';
import { BlueprintCalibration } from './components/BlueprintCalibration';
import { DockResizeHandle, ErrorBanner, ResizeHandle } from './components/controls';
import { DraftBanner } from './components/DraftBanner';
import { ProjectPicker } from './components/ProjectPicker';
import { ReconstructionPanel } from './components/ReconstructionPanel';
import { ReviewDock } from './components/ReviewDock';
import { SceneExplorer } from './components/SceneExplorer';
import { StatusBar } from './components/StatusBar';
import { ViewOptions } from './components/ViewOptions';
import { ViewportToolbar } from './components/ViewportToolbar';
import { useApiHealth } from './hooks/useApiHealth';
import { useDraftAutosave } from './hooks/useDraftAutosave';
import { useEditorShortcuts } from './hooks/useEditorShortcuts';
import { useProjects } from './hooks/useProjects';
import { useProjectWorkflow } from './hooks/useProjectWorkflow';


// Photo/video capture (and its mesh viewer) loads only when Mode 2 is opened.
const CaptureWorkspace = lazy(() =>
  import('./CaptureWorkspace').then((m) => ({ default: m.CaptureWorkspace })),
);

export default function App() {
  const workflow = useProjectWorkflow();
  const {
    inputMode,
    setInputMode,
    project,
    projectId,
    setProjectId,
    points,
    setPoints,
    distance,
    scaleMode,
    automaticScale,
    working,
    saving,
    notice,
    setNotice,
    showBlueprint,
    setShowBlueprint,
    draftOffer,
    setDraftOffer,
    error,
    guarded,
    discard,
    loadProject,
    loadCaptureScene,
    reconstructGroup,
    save,
  } = workflow;
  const { health, ensureReady } = useApiHealth();
  const { projects, refresh: refreshProjects } = useProjects();
  useDraftAutosave();
  useEditorShortcuts(inputMode);
  const state = useEditor(
    useShallow((s) => ({
      scene: s.scene,
      assembly: s.assembly,
      workspace: s.workspace,
      busy: s.busy,
      conflict: s.conflict,
      lengthUnit: s.lengthUnit,
    })),
  );
  const [wizard, setWizard] = useState<{ files: File[]; addToCurrent: boolean } | null>(null);
  const [captureMeshId, setCaptureMeshId] = useState<string | null>(null);
  const [captureBusy, setCaptureBusy] = useState(false);
  const [leftWidth, setLeftWidth] = useState(260),
    [rightWidth, setRightWidth] = useState(286);
  const [dockHeight, setDockHeight] = useState<number | null>(null);

  const disabled = working || state.busy;
  const renderBlueprint = state.workspace === 'Reconstruct' && project && showBlueprint;
  const openCaptureInEditor = async (id: string) => {
    if (!discard()) return;
    await guarded(async () => loadCaptureScene(await api.openCaptureScene(id)));
  };
  const selectWorkspace = (name: WorkspaceName) => {
    if (document.pointerLockElement) document.exitPointerLock();
    if (inputMode === 'capture' && name !== 'Reconstruct') {
      const id = captureMeshId ?? state.scene?.id;
      if (!id) return;
      void (async () => {
        if (state.scene?.id === id && state.scene.source.capture) setInputMode('blueprint');
        else await openCaptureInEditor(id);
        useEditor.setState({ workspace: name, measure: false });
      })();
      return;
    }
    useEditor.setState({ workspace: name, measure: false });
  };

  return (
    <div
      className={`app-shell ${inputMode === 'capture' ? 'capture-mode' : ''}`}
      style={
        { '--left-panel': `${leftWidth}px`, '--right-panel': `${rightWidth}px` } as CSSProperties
      }
    >
      <AppHeader
        inputMode={inputMode}
        projectName={project?.project.name}
        health={health}
        saving={saving}
        saveDisabled={
          (!state.scene && !state.assembly) || disabled || (!state.assembly && state.conflict)
        }
        onSave={() => void save()}
        canOpenWorkspace={(name) =>
          name === 'Reconstruct' || (inputMode === 'capture' && !!captureMeshId) || !!state.scene
        }
        onWorkspace={selectWorkspace}
      />
      <ErrorBanner />
      <DraftBanner draftOffer={draftOffer} setDraftOffer={setDraftOffer} onError={error} />
      <div className="input-mode" role="group" aria-label="Input mode">
        {(['blueprint', 'capture'] as const).map((mode) => (
          <button
            key={mode}
            aria-pressed={inputMode === mode}
            disabled={disabled || captureBusy}
            onClick={() => {
              if (document.pointerLockElement) document.exitPointerLock();
              setInputMode(mode);
            }}
          >
            {mode === 'blueprint' ? 'Mode 1 · Blueprint' : 'Mode 2 · Photos & video'}
          </button>
        ))}
      </div>
      {inputMode === 'capture' && (
        <Suspense fallback={<p className="hint">Loading capture workspace…</p>}>
          <CaptureWorkspace
            onMeshReady={setCaptureMeshId}
            onBusyChange={setCaptureBusy}
            onOpenEditor={openCaptureInEditor}
          />
        </Suspense>
      )}
      <main
        className="editor-layout"
        style={inputMode === 'capture' ? { display: 'none' } : undefined}
      >
        <aside className="left-panel panel">
          {state.assembly && (
            <AssemblyControls onReconstruct={(ids) => void reconstructGroup(ids)} />
          )}
          {state.workspace === 'Reconstruct' && state.scene?.source.capture ? (
            <section className="project-section">
              <h4>CAPTURE PROJECT</h4>
              <p>
                This scene uses a saved capture snapshot. Edit it here with the shared 3D tools.
              </p>
              <button onClick={() => setInputMode('capture')}>
                Photos & video · source and calibration
              </button>
              <p className="hint">
                Reconstruction and calibration changes do not overwrite saved editor work.
              </p>
            </section>
          ) : state.workspace === 'Reconstruct' ? (
            <ReconstructionPanel
              workflow={workflow}
              disabled={disabled}
              onOpenWizard={(files, addToCurrent) => {
                setWizard({ files, addToCurrent });
                // The wizard is modal: block editor actions until it closes.
                useEditor.setState({ busy: true });
              }}
            />
          ) : (
            <SceneExplorer disabled={disabled} />
          )}
          <ProjectPicker
            projectId={projectId}
            setProjectId={setProjectId}
            projects={projects}
            onRefresh={() => void refreshProjects()}
            onOpen={(id) => {
              if (discard()) void guarded(() => loadProject(id));
            }}
            canReload={!!state.scene}
            onReload={() => {
              if (discard()) void guarded(() => loadProject(state.assembly?.id ?? state.scene!.id));
            }}
            disabled={disabled}
          />
        </aside>
        <ResizeHandle
          side="left"
          onChange={(width) => setLeftWidth(Math.max(220, Math.min(420, width)))}
        />
        <div className="center-panel">
          <FloorViews />
          <ViewportToolbar disabled={disabled} />
          <div className="viewport-host">
            {renderBlueprint ? (
              <BlueprintCalibration
                project={project}
                points={
                  scaleMode === 'auto' && automaticScale?.method === 'printed-dimension'
                    ? [automaticScale.pointA, automaticScale.pointB]
                    : scaleMode === 'manual'
                      ? points
                      : []
                }
                setPoints={setPoints}
                distance={distance}
                unit={state.lengthUnit}
                manual={scaleMode === 'manual'}
                disabled={disabled}
              />
            ) : (
              <ErrorBoundary
                resetKey={state.scene?.id ?? state.assembly?.id ?? null}
                fallback={(e, reset) => (
                  <div className="empty-viewport" role="alert">
                    <h2>Viewport unavailable</h2>
                    <p>{e.message}</p>
                    <p>
                      {isWebglError(e)
                        ? 'Enable WebGL in your browser. Scene inspection and JSON export remain available.'
                        : 'Scene inspection and JSON export remain available.'}
                    </p>
                    <button onClick={reset}>Try again</button>
                  </div>
                )}
              >
                <Viewport paused={inputMode === 'capture'} />
              </ErrorBoundary>
            )}
          </div>
          <ViewOptions disabled={disabled} guarded={guarded} />
          <DockResizeHandle
            onChange={(h) => setDockHeight(Math.max(40, Math.min(window.innerHeight * 0.7, h)))}
          />
          <div
            className="bottom-dock"
            style={
              dockHeight === null
                ? undefined
                : { height: dockHeight, minHeight: 0, maxHeight: 'none' }
            }
          >
            <ReviewDock
              disabled={disabled}
              guarded={guarded}
              notice={notice}
              setNotice={setNotice}
            />
          </div>
        </div>
        <ResizeHandle
          side="right"
          onChange={(width) => setRightWidth(Math.max(240, Math.min(440, width)))}
        />
        <Inspector />
      </main>
      {wizard && (
        <BlueprintWizard
          files={wizard.files}
          existing={state.assembly}
          previous={wizard.addToCurrent && !state.assembly ? project : null}
          ensureApiReady={ensureReady}
          onCancel={() => {
            setWizard(null);
            useEditor.setState({ busy: false });
          }}
          onComplete={(envelope) => {
            useEditor.getState().adoptAssembly(envelope);
            setProjectId(envelope.assembly.id);
            localStorage.setItem(LAST_PROJECT_KEY, envelope.assembly.id);
            setWizard(null);
            setShowBlueprint(true);
            setNotice('Grouped project created. Choose Reconstruct buildings & floors to begin.');
          }}
        />
      )}
      <StatusBar inputMode={inputMode} working={working} health={health} />
    </div>
  );
}
