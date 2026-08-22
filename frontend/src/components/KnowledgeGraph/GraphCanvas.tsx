import { forwardRef, Suspense } from 'react';
import { Canvas } from '@react-three/fiber';

import type { GraphResponse } from '../../types/graph';
import type { LayoutMap } from '../../lib/graphLayout';
import { resolveCssColor } from '../../lib/graphVisual';
import { GraphScene } from './GraphScene';
import { CameraRig, DEFAULT_CAMERA_POS, type CameraRigHandle } from './CameraRig';

interface GraphCanvasProps {
  graph: GraphResponse;
  layout: LayoutMap;
  selectedNodeId: string | null;
  onSelect: (id: string | null) => void;
}

export const GraphCanvas = forwardRef<CameraRigHandle, GraphCanvasProps>(
  ({ graph, layout, selectedNodeId, onSelect }, ref) => {
    const bg = resolveCssColor('--color-bg', '#0a0a0b');
    const fogColor = resolveCssColor('--color-bg-secondary', '#121214');
    const accent = resolveCssColor('--color-accent', '#22d3ee');

    return (
      <Canvas
        camera={{ position: DEFAULT_CAMERA_POS.toArray(), fov: 50 }}
        gl={{ antialias: true }}
        onPointerMissed={() => onSelect(null)}
        style={{ background: bg }}
      >
        <color attach="background" args={[bg]} />
        <fog attach="fog" args={[fogColor, 24, 90]} />
        <ambientLight intensity={0.55} />
        <pointLight position={[0, 20, 20]} intensity={0.6} color={accent} />
        <pointLight position={[-20, -10, -10]} intensity={0.25} color={accent} />
        <Suspense fallback={null}>
          <GraphScene
            graph={graph}
            layout={layout}
            selectedNodeId={selectedNodeId}
            onSelect={(id) => onSelect(id)}
          />
        </Suspense>
        <CameraRig layout={layout} focusNodeId={selectedNodeId} handleRef={ref} />
      </Canvas>
    );
  },
);

GraphCanvas.displayName = 'GraphCanvas';
