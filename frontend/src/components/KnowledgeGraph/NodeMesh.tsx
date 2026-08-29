import { useMemo, useRef, useState } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import { AdditiveBlending, Color, type Mesh, type Sprite } from 'three';

import type { GraphNode } from '../../types/graph';
import { lifecycleColorOverride, lifecycleOpacity, styleForNode } from '../../lib/graphVisual';
import type { LayoutPosition } from '../../lib/graphLayout';
import { getGlowTexture } from '../../lib/graphGlowTexture';
import { COLOR_COLD_BLUE_DIM } from '../../lib/graphTheme';

const prefersReducedMotion =
  typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

interface NodeMeshProps {
  node: GraphNode;
  position: LayoutPosition;
  selected: boolean;
  emphasized: boolean;
  dimmed: boolean;
  onSelect: (id: string) => void;
}

// FASE 4O.4A visual-review fix #2: the halo used to be a solid,
// uniformly-opaque sphere -- every pixel inside its silhouette shared
// one alpha value, so it read as a flat disc with a hard edge ("large
// dark translucent regions" per review). It's now a camera-facing
// sprite using a radial-gradient texture (bright center, alpha falls
// smoothly to zero -- see `graphGlowTexture.ts`) with additive
// blending, so overlapping glows can only brighten the scene, never
// stack into something darker. Node scale itself also now sits on an
// explicit shared hierarchy (`graphVisual.ts`'s `ENTRY_BASE`) rather
// than large absolute radii -- the network should read first,
// individual nodes second.
// FASE 4O.4A visual-review fix #5 (STEP C): real screenshot review
// found nodes still reading as flat grey/opaque physical balls -- a
// single lit `meshStandardMaterial` sphere under scene lighting looks
// like a shaded ball almost by definition, regardless of emissive
// intensity. Split into two layers: a darker, translucent OUTER shell
// (desaturated toward the shared cold-navy base, low opacity -- a
// "vessel," not a solid object) and a small, fully bright INNER core
// (unlit `meshBasicMaterial`, so it reads as a genuine light source
// rather than something lit *by* the scene) -- plus the existing
// additive glow sprite on top. Every node kind still uses the same
// two-layer construction; only color/size differ per the hierarchy in
// `graphVisual.ts`.
const INNER_CORE_SCALE = 0.42;

// Tiny deterministic hash purely for label placement (never used to
// decide graph structure) -- keeps nearby DOMAIN labels from stacking
// exactly on top of each other without a full collision-avoidance pass.
function labelHash01(id: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 4294967296;
}

export function NodeMesh({ node, position, selected, emphasized, dimmed, onSelect }: NodeMeshProps) {
  const coreRef = useRef<Mesh>(null);
  const glowRef = useRef<Sprite>(null);
  const [hovered, setHovered] = useState(false);
  const style = useMemo(() => styleForNode(node), [node]);
  const glowTexture = useMemo(() => getGlowTexture(), []);
  const colorOverride = lifecycleColorOverride(node);
  const core = useMemo(() => new Color(colorOverride ?? style.coreColor), [colorOverride, style.coreColor]);
  const glowColor = useMemo(() => new Color(colorOverride ?? style.haloColor), [colorOverride, style.haloColor]);
  const shellColor = useMemo(() => core.clone().lerp(new Color(COLOR_COLD_BLUE_DIM), 0.55), [core]);
  const innerColor = useMemo(() => core.clone().multiplyScalar(1.25), [core]);

  const baseOpacity = lifecycleOpacity(node);
  const opacity = dimmed ? baseOpacity * 0.2 : baseOpacity;
  const shellOpacity = (selected ? 0.55 : hovered || emphasized ? 0.48 : 0.4) * opacity;
  const scale = selected ? 1.4 : emphasized || hovered ? 1.15 : 1;
  const showGlow = style.showHalo || selected || emphasized || hovered;
  const glowRadius = style.size * style.haloScale;
  const showLabel = node.kind === 'DOMAIN' || selected || emphasized || hovered;
  const labelOffsetX = node.kind === 'DOMAIN' ? (labelHash01(node.id) - 0.5) * 46 : 0;

  // Domain nuclei breathe very slightly -- calm, bounded, and disabled
  // under prefers-reduced-motion (STEP 13). Nothing else animates
  // per-frame: a settled layout has no ongoing jitter.
  useFrame(({ clock }) => {
    if (prefersReducedMotion || node.kind !== 'DOMAIN' || dimmed) return;
    const t = clock.getElapsedTime();
    const pulse = 1 + Math.sin(t * 0.6 + position.x) * 0.05;
    if (glowRef.current) glowRef.current.scale.setScalar(glowRadius * 2 * pulse);
  });

  return (
    <group position={[position.x, position.y, position.z]}>
      <mesh
        ref={coreRef}
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
        <sphereGeometry args={[style.size, 20, 20]} />
        <meshStandardMaterial
          color={shellColor}
          emissive={shellColor}
          emissiveIntensity={selected ? 0.5 : hovered || emphasized ? 0.35 : 0.2}
          transparent
          opacity={shellOpacity}
          roughness={0.25}
          metalness={0.05}
          depthWrite={false}
        />
      </mesh>

      {/* Small, fully bright, unlit inner core -- reads as an actual
          light source rather than a matte ball shaded by scene lights. */}
      <mesh scale={scale}>
        <sphereGeometry args={[style.size * INNER_CORE_SCALE, 14, 14]} />
        <meshBasicMaterial color={innerColor} transparent opacity={opacity} />
      </mesh>

      {showGlow && (
        <sprite ref={glowRef} scale={[glowRadius * 2, glowRadius * 2, 1]}>
          <spriteMaterial
            map={glowTexture}
            color={glowColor}
            transparent
            opacity={(selected ? 0.55 : emphasized ? 0.4 : 0.28) * (dimmed ? 0.25 : 1)}
            blending={AdditiveBlending}
            depthWrite={false}
          />
        </sprite>
      )}

      {showLabel && (
        <Html distanceFactor={11} center occlude={false} style={{ pointerEvents: 'none' }}>
          <div
            style={{
              fontFamily: "'IBM Plex Mono', 'SF Mono', ui-monospace, monospace",
              // FASE 4O.4A fix #3 (STEP 6): DOMAIN labels bumped back up
              // to "comfortably readable" -- fix #2 had gone too far
              // toward restraint once combined with the camera sitting
              // farther away than intended.
              fontSize: node.kind === 'DOMAIN' ? 13 : 9.5,
              fontWeight: node.kind === 'DOMAIN' ? 600 : 400,
              color: node.kind === 'DOMAIN' ? '#eaf7ff' : '#cfe8f5',
              opacity: dimmed ? 0.18 : node.kind === 'DOMAIN' ? 0.88 : 0.72,
              textShadow: '0 1px 6px rgba(0,0,0,0.9)',
              whiteSpace: 'nowrap',
              // FASE 4O.4A fix #5 (STEP D): a deterministic, id-seeded
              // horizontal offset -- a "massive label engine" isn't
              // needed for V1, just enough to keep nearby DOMAIN labels
              // from stacking directly on top of each other when
              // regions sit close together.
              transform: `translate(${labelOffsetX}px, 15px)`,
              letterSpacing: node.kind === 'DOMAIN' ? '0.07em' : '0.01em',
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
