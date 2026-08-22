import { useMemo } from 'react';

import type { GraphResponse } from '../../types/graph';
import type { LayoutMap } from '../../lib/graphLayout';
import { NodeMesh } from './NodeMesh';
import { EdgeLine } from './EdgeLine';

interface GraphSceneProps {
  graph: GraphResponse;
  layout: LayoutMap;
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
}

// On selection, directly-connected nodes/edges stay at full presence and
// everything else quiets down (STEP 8.3/8.4) -- computed fresh from the
// currently-loaded edges, never a separate "importance" concept.
function relatedNodeIds(graph: GraphResponse, selectedId: string | null): Set<string> {
  if (!selectedId) return new Set();
  const related = new Set<string>([selectedId]);
  for (const edge of graph.edges) {
    if (edge.source === selectedId) related.add(edge.target);
    if (edge.target === selectedId) related.add(edge.source);
  }
  return related;
}

export function GraphScene({ graph, layout, selectedNodeId, onSelect }: GraphSceneProps) {
  const related = useMemo(() => relatedNodeIds(graph, selectedNodeId), [graph, selectedNodeId]);
  const hasSelection = selectedNodeId !== null;

  return (
    <group>
      {graph.edges.map((edge) => {
        const from = layout[edge.source];
        const to = layout[edge.target];
        if (!from || !to) return null;
        const touchesSelection = edge.source === selectedNodeId || edge.target === selectedNodeId;
        const bothRelated = related.has(edge.source) && related.has(edge.target);
        return (
          <EdgeLine
            key={edge.id}
            edge={edge}
            from={from}
            to={to}
            emphasized={touchesSelection}
            dimmed={hasSelection && !bothRelated}
          />
        );
      })}
      {graph.nodes.map((node) => {
        const position = layout[node.id];
        if (!position) return null;
        return (
          <NodeMesh
            key={node.id}
            node={node}
            position={position}
            selected={node.id === selectedNodeId}
            emphasized={hasSelection && related.has(node.id) && node.id !== selectedNodeId}
            dimmed={hasSelection && !related.has(node.id)}
            onSelect={onSelect}
          />
        );
      })}
    </group>
  );
}
