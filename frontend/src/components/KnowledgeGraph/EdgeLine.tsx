import { useMemo } from 'react';
import { Line } from '@react-three/drei';

import type { GraphEdge } from '../../types/graph';
import { styleForEdge } from '../../lib/graphVisual';
import type { LayoutPosition } from '../../lib/graphLayout';

interface EdgeLineProps {
  edge: GraphEdge;
  from: LayoutPosition;
  to: LayoutPosition;
  dimmed: boolean;
  emphasized: boolean;
}

// FASE 4O.4A: thin, low-intensity by default, brighter only when
// relevant to the current selection -- edges are visually subordinate
// to nodes, never a rigid diagram grid. No continuous per-frame
// animation here; opacity/width only change in response to selection
// state (a React re-render), never an idle tween.
export function EdgeLine({ edge, from, to, dimmed, emphasized }: EdgeLineProps) {
  const style = useMemo(() => styleForEdge(edge), [edge]);
  const opacity = dimmed ? style.opacity * 0.12 : emphasized ? Math.min(style.opacity * 1.5, 1) : style.opacity;

  return (
    <Line
      points={[
        [from.x, from.y, from.z],
        [to.x, to.y, to.z],
      ]}
      color={style.color}
      transparent
      opacity={opacity}
      lineWidth={emphasized ? style.width * 2 : style.width}
      dashed={style.dashed}
      dashSize={0.3}
      gapSize={0.2}
    />
  );
}
