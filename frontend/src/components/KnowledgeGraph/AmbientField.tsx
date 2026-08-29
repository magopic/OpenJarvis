import { useMemo } from 'react';
import { AdditiveBlending, BufferAttribute } from 'three';

// FASE 4O.4A visual-review fix #2 (STEP 8): pure background atmosphere,
// not graph data -- these points are never GraphNodes, carry no id, are
// never clickable/selectable, and are positioned well outside the
// graph's own radius so they read as distant depth cues, never as
// business-data nodes. Static (no per-frame animation, no twinkle) and
// seeded by a fixed index-based formula -- deterministic like the rest
// of the layout, not `Math.random()` -- so it never flickers or
// reshuffles between renders.
const COUNT = 180;
const FIELD_RADIUS = 22;

function hash01(seed: number): number {
  let h = 0x811c9dc5 ^ seed;
  h = Math.imul(h, 0x01000193);
  h ^= h >>> 13;
  h = Math.imul(h, 0x01000193);
  return (h >>> 0) / 4294967296;
}

export function AmbientField() {
  const positions = useMemo(() => {
    const arr = new Float32Array(COUNT * 3);
    for (let i = 0; i < COUNT; i++) {
      const theta = hash01(i * 3 + 1) * Math.PI * 2;
      const cosPhi = hash01(i * 3 + 2) * 2 - 1;
      const sinPhi = Math.sqrt(Math.max(0, 1 - cosPhi * cosPhi));
      const r = FIELD_RADIUS * (0.6 + 0.4 * hash01(i * 3 + 3));
      arr[i * 3] = r * sinPhi * Math.cos(theta);
      arr[i * 3 + 1] = r * cosPhi * 0.6;
      arr[i * 3 + 2] = r * sinPhi * Math.sin(theta);
    }
    return arr;
  }, []);

  return (
    <points renderOrder={-1}>
      <bufferGeometry>
        <primitive attach="attributes-position" object={new BufferAttribute(positions, 3)} />
      </bufferGeometry>
      <pointsMaterial
        size={0.06}
        color="#5b8ba8"
        transparent
        opacity={0.35}
        blending={AdditiveBlending}
        depthWrite={false}
        sizeAttenuation
      />
    </points>
  );
}
