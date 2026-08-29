import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Crosshair, Maximize2, Minimize2, Expand } from 'lucide-react';

import { useGraphStore } from '../../lib/graphStore';
import { Button } from '../ui/button';
import { GraphCanvas } from './GraphCanvas';
import { DetailPanel } from './DetailPanel';
import { FilterBar } from './FilterBar';
import { TruncationBanner } from './TruncationBanner';
import { GraphEmptyState, GraphErrorState, GraphLoadingState, GraphNoAccessState } from './GraphStates';
import type { CameraRigHandle } from './CameraRig';
import { GRAPH_BG } from '../../lib/graphTheme';

export function KnowledgeGraphView() {
  const status = useGraphStore((s) => s.status);
  const errorMessage = useGraphStore((s) => s.errorMessage);
  const graph = useGraphStore((s) => s.graph);
  const layout = useGraphStore((s) => s.layout);
  const filters = useGraphStore((s) => s.filters);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const mode = useGraphStore((s) => s.mode);
  const loadOverview = useGraphStore((s) => s.loadOverview);
  const setFilters = useGraphStore((s) => s.setFilters);
  const selectNode = useGraphStore((s) => s.selectNode);
  const showExperience = useGraphStore((s) => s.showExperience);
  const backToOverview = useGraphStore((s) => s.backToOverview);

  const cameraRef = useRef<CameraRigHandle>(null);
  const [immersive, setImmersive] = useState(false);

  useEffect(() => {
    void loadOverview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // FASE 4O.4A fix #3: a separate setTimeout-driven auto-fit used to
  // live here, gated to fire once per mode -- which meant every layout
  // change after the very first (e.g. toggling "Include archived")
  // fell through to CameraRig's own no-focus fallback, which at the
  // time snapped to a fixed default distance instead of an actual
  // bounds-fit. Removed: `CameraRig` now runs the exact same
  // `computeFit` on every layout change automatically (STEP 3/9), so a
  // second, separately-timed fit call here would only risk racing it.

  // Escape: deselect first if something is selected, otherwise exit
  // immersive mode if active (STEP 12).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (selectedNodeId) void selectNode(null);
      else if (immersive) setImmersive(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedNodeId, immersive, selectNode]);

  const availableDomains = useMemo(
    () => graph.nodes.filter((n) => n.kind === 'DOMAIN').map((n) => n.label).sort(),
    [graph.nodes],
  );

  const showCanvas = status === 'ready';

  return (
    <div
      className={immersive ? 'fixed inset-0 z-[100] flex flex-col h-full w-full overflow-hidden' : 'flex flex-col h-full w-full overflow-hidden'}
      style={{ background: GRAPH_BG }}
    >
      {mode.type === 'experience' && (
        <div className="px-4 py-2 shrink-0">
          <Button size="sm" variant="ghost" className="gap-2" onClick={() => void backToOverview()}>
            <ArrowLeft size={14} />
            Back to overview
          </Button>
        </div>
      )}

      {graph.truncated && showCanvas && <TruncationBanner />}

      <div className="relative flex-1 min-h-0 flex">
        <div className="relative flex-1 min-w-0">
          {/* Filters stay available in every state (including empty) --
              the "Include archived" toggle is exactly what a user needs
              from the empty state, so it must never disappear with it. */}
          {status !== 'loading' && (
            <FilterBar filters={filters} availableDomains={availableDomains} onChange={setFilters} />
          )}

          {status === 'loading' && <GraphLoadingState />}
          {status === 'empty' && <GraphEmptyState onRetry={() => void loadOverview()} />}
          {status === 'no-access' && <GraphNoAccessState />}
          {status === 'error' && <GraphErrorState message={errorMessage ?? 'Unknown error'} onRetry={() => void loadOverview()} />}

          {showCanvas && (
            <>
              <GraphCanvas
                ref={cameraRef}
                graph={graph}
                layout={layout}
                selectedNodeId={selectedNodeId}
                onSelect={(id) => void selectNode(id)}
              />
              <div className="absolute top-3 right-3 flex flex-col gap-2">
                <IconButton title="Fit graph" onClick={() => cameraRef.current?.fitGraph()}>
                  <Maximize2 size={14} />
                </IconButton>
                <IconButton title="Reset view" onClick={() => cameraRef.current?.resetView()}>
                  <Crosshair size={14} />
                </IconButton>
                <IconButton
                  title={immersive ? 'Exit immersive mode' : 'Immersive mode'}
                  onClick={() => setImmersive((v) => !v)}
                >
                  {immersive ? <Minimize2 size={14} /> : <Expand size={14} />}
                </IconButton>
              </div>
            </>
          )}
        </div>

        {showCanvas && selectedNodeId && (
          <DetailPanel
            graph={graph}
            selectedNodeId={selectedNodeId}
            onClose={() => void selectNode(null)}
            onShowExperience={(id) => void showExperience(id)}
          />
        )}
      </div>
    </div>
  );
}

function IconButton({ children, onClick, title }: { children: React.ReactNode; onClick: () => void; title: string }) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className="flex items-center justify-center rounded-full transition-colors"
      style={{
        width: 30,
        height: 30,
        background: 'rgba(10, 18, 28, 0.55)',
        border: '1px solid rgba(120, 190, 220, 0.18)',
        color: '#a9c7d9',
        backdropFilter: 'blur(8px)',
      }}
    >
      {children}
    </button>
  );
}
