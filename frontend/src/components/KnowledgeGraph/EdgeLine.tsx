import { useMemo } from 'react';
import { Line } from '@react-three/drei';

import type { GraphEdge } from '../../types/graph';
import { resolveCssColor, styleForEdge } from '../../lib/graphVisual';
import type { LayoutPosition } from '../../lib/graphLayout';

interface EdgeLineProps {
  edge: GraphEdge;
  from: LayoutPosition;
  to: LayoutPosition;
  dimmed: boolean;
  emphasized: boolean;
}

export function EdgeLine({ edge, from, to, dimmed, emphasized }: EdgeLineProps) {
  const style = useMemo(() => styleForEdge(edge), [edge]);
  const color = useMemo(() => resolveCssColor(style.colorVar), [style.colorVar]);
  const opacity = dimmed ? style.opacity * 0.15 : emphasized ? Math.min(style.opacity * 1.4, 1) : style.opacity;

  return (
    <Line
      points={[
        [from.x, from.y, from.z],
        [to.x, to.y, to.z],
      ]}
      color={color}
      transparent
      opacity={opacity}
      lineWidth={emphasized ? style.width * 1.8 : style.width}
      dashed={style.dashed}
      dashSize={0.35}
      gapSize={0.22}
    />
  );
}
