import { forwardRef, Suspense } from 'react';
import { Canvas } from '@react-three/fiber';

import type { GraphResponse } from '../../types/graph';
import type { LayoutMap } from '../../lib/graphLayout';
import { GRAPH_BG, GRAPH_FOG_NEAR_COLOR, COLOR_CYAN, COLOR_COLD_BLUE } from '../../lib/graphTheme';
import { GraphScene } from './GraphScene';
import { AmbientField } from './AmbientField';
import { CanvasSizeSync } from './CanvasSizeSync';
import { CameraRig, DEFAULT_CAMERA_POS, type CameraRigHandle } from './CameraRig';

interface GraphCanvasProps {
  graph: GraphResponse;
  layout: LayoutMap;
  selectedNodeId: string | null;
  onSelect: (id: string | null) => void;
}

// FASE 4O.4A: the MAIA graph environment is always this near-black
// deep-blue space, regardless of the surrounding app's light/dark
// theme -- hardcoded from `graphTheme.ts` rather than resolved from
// `--color-*` CSS variables (the earlier CSS-var approach silently
// rendered a white canvas whenever the app theme was light, since it
// read `document.documentElement`, not this component's own subtree).
export const GraphCanvas = forwardRef<CameraRigHandle, GraphCanvasProps>(
  ({ graph, layout, selectedNodeId, onSelect }, ref) => {
    return (
      // FASE 4O.4A visual-review fix #4 (root cause of "graph far too
      // small"): confirmed via direct DOM inspection, not guessed --
      // the actual <canvas> element R3F renders had no width/height at
      // all (attribute or CSS), sitting at the browser's literal
      // default of 300x150px, while its own wrapper div correctly
      // measured the full container. R3F's <canvas> is *always* just
      // `style={{display:'block'}}` by design (see its source) -- it
      // relies entirely on `react-use-measure`'s ResizeObserver state
      // reporting a nonzero size before it ever calls the renderer's
      // `setSize()`. That measurement never produced a nonzero value
      // here, so `setSize()` never ran, and the whole 3D scene rendered
      // into a tiny corner -- everything else people saw was just the
      // page's own background color, not empty 3D space. Not a camera-
      // distance problem at all. `CanvasSizeSync` (rendered inside
      // `<Canvas>` below) fixes this directly, independent of that
      // measurement path. The absolutely-positioned wrapper here is a
      // secondary belt-and-braces measure, giving the canvas an
      // unambiguous box outside the flex layout algorithm.
      <div style={{ position: 'absolute', inset: 0 }}>
        <Canvas
          camera={{ position: DEFAULT_CAMERA_POS.toArray(), fov: 50 }}
          gl={{ antialias: true }}
          onPointerMissed={() => onSelect(null)}
          style={{ background: GRAPH_BG, width: '100%', height: '100%', display: 'block' }}
        >
          <CanvasSizeSync />
          <color attach="background" args={[GRAPH_BG]} />
          {/* Retuned for the FASE 4O.4A fix #2 compact composition (domain
              sphere radius ~4.2 vs the previous 8) -- the fog range shrinks
              with it so it still reads as restrained depth, not a wall. */}
          <fog attach="fog" args={[GRAPH_FOG_NEAR_COLOR, 7, 26]} />
          <ambientLight intensity={0.4} />
          <pointLight position={[0, 6, 6]} intensity={0.55} color={COLOR_CYAN} />
          <pointLight position={[-6, -3, -4]} intensity={0.2} color={COLOR_COLD_BLUE} />
          <AmbientField />
          <Suspense fallback={null}>
            <GraphScene
              graph={graph}
              layout={layout}
              selectedNodeId={selectedNodeId}
              onSelect={(id) => onSelect(id)}
            />
          </Suspense>
          <CameraRig graph={graph} layout={layout} focusNodeId={selectedNodeId} handleRef={ref} />
        </Canvas>
      </div>
    );
  },
);

GraphCanvas.displayName = 'GraphCanvas';
