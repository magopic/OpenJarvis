import { useMemo, useRef, useState } from 'react';
import { Html } from '@react-three/drei';
import type { Mesh } from 'three';
import { Color } from 'three';

import type { GraphNode } from '../../types/graph';
import { lifecycleOpacity, resolveCssColor, styleForNode, type NodeShape } from '../../lib/graphVisual';
import type { LayoutPosition } from '../../lib/graphLayout';

function NodeGeometry({ shape, size }: { shape: NodeShape; size: number }) {
  switch (shape) {
    case 'icosahedron':
      return <icosahedronGeometry args={[size, 0]} />;
    case 'octahedron':
      return <octahedronGeometry args={[size, 0]} />;
    case 'tetrahedron':
      return <tetrahedronGeometry args={[size, 0]} />;
    case 'box':
      return <boxGeometry args={[size * 1.3, size * 1.3, size * 1.3]} />;
    case 'cone':
      return <coneGeometry args={[size * 0.85, size * 1.7, 6]} />;
    case 'cylinder':
      return <cylinderGeometry args={[size * 0.7, size * 0.7, size * 1.4, 10]} />;
    case 'sphere':
    default:
      return <sphereGeometry args={[size, 20, 20]} />;
  }
}

interface NodeMeshProps {
  node: GraphNode;
  position: LayoutPosition;
  selected: boolean;
  emphasized: boolean;
  dimmed: boolean;
  onSelect: (id: string) => void;
}

export function NodeMesh({ node, position, selected, emphasized, dimmed, onSelect }: NodeMeshProps) {
  const meshRef = useRef<Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const style = useMemo(() => styleForNode(node), [node]);
  const color = useMemo(() => new Color(resolveCssColor(style.colorVar)), [style.colorVar]);
  const baseOpacity = lifecycleOpacity(node);
  const opacity = dimmed ? baseOpacity * 0.22 : baseOpacity;
  const scale = selected ? 1.35 : emphasized || hovered ? 1.15 : 1;
  const showLabel = node.kind === 'DOMAIN' || selected || hovered;

  return (
    <group position={[position.x, position.y, position.z]}>
      <mesh
        ref={meshRef}
        scale={scale}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(node.id);
        }}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHovered(true);
          document.body.style.cursor = 'pointer';
        }}
        onPointerOut={() => {
          setHovered(false);
          document.body.style.cursor = 'auto';
        }}
      >
        <NodeGeometry shape={style.shape} size={style.size} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={selected ? style.glow * 1.6 : hovered ? style.glow * 1.2 : style.glow}
          transparent
          opacity={opacity}
          roughness={0.35}
          metalness={0.15}
        />
      </mesh>
      {showLabel && (
        <Html distanceFactor={12} center occlude={false} style={{ pointerEvents: 'none' }}>
          <div
            style={{
              fontFamily: 'var(--font-hud)',
              fontSize: node.kind === 'DOMAIN' ? 12 : 10,
              color: node.kind === 'DOMAIN' ? 'var(--color-accent-amber)' : 'var(--color-text)',
              opacity: dimmed ? 0.25 : 0.92,
              textShadow: '0 1px 4px rgba(0,0,0,0.85)',
              whiteSpace: 'nowrap',
              transform: 'translateY(18px)',
              letterSpacing: node.kind === 'DOMAIN' ? '0.04em' : 'normal',
              textTransform: node.kind === 'DOMAIN' ? 'uppercase' : 'none',
            }}
          >
            {node.label}
          </div>
        </Html>
      )}
    </group>
  );
}
