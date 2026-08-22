import { useEffect, useMemo, useRef } from 'react';
import { ArrowLeft, Crosshair, Maximize2 } from 'lucide-react';

import { useGraphStore } from '../../lib/graphStore';
import { Button } from '../ui/button';
import { GraphCanvas } from './GraphCanvas';
import { DetailPanel } from './DetailPanel';
import { FilterBar } from './FilterBar';
import { TruncationBanner } from './TruncationBanner';
import { GraphEmptyState, GraphErrorState, GraphLoadingState, GraphNoAccessState } from './GraphStates';
import type { CameraRigHandle } from './CameraRig';

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

  useEffect(() => {
    void loadOverview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keyboard escape deselection (STEP 17).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedNodeId) void selectNode(null);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedNodeId, selectNode]);

  const availableDomains = useMemo(
    () => graph.nodes.filter((n) => n.kind === 'DOMAIN').map((n) => n.label).sort(),
    [graph.nodes],
  );

  const showCanvas = status === 'ready';

  return (
    <div className="dark flex flex-col h-full w-full overflow-hidden" style={{ background: 'var(--color-bg)' }}>
      <div className="flex items-center gap-2">
        {mode.type === 'experience' && (
          <div className="px-4 py-2">
            <Button size="sm" variant="ghost" className="gap-2" onClick={() => void backToOverview()}>
              <ArrowLeft size={14} />
              Back to overview
            </Button>
          </div>
        )}
        <div className="flex-1">
          <FilterBar filters={filters} availableDomains={availableDomains} onChange={setFilters} />
        </div>
      </div>

      {graph.truncated && showCanvas && <TruncationBanner />}

      <div className="relative flex-1 min-h-0 flex">
        <div className="relative flex-1 min-w-0">
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
                  <Maximize2 size={15} />
                </IconButton>
                <IconButton title="Reset view" onClick={() => cameraRef.current?.resetView()}>
                  <Crosshair size={15} />
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
      className="flex items-center justify-center rounded-md"
      style={{
        width: 32,
        height: 32,
        background: 'color-mix(in srgb, var(--color-bg-secondary) 82%, transparent)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text-secondary)',
        backdropFilter: 'blur(6px)',
      }}
    >
      {children}
    </button>
  );
}
