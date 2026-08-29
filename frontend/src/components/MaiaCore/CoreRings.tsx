import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { BufferAttribute, type Group, type LineBasicMaterial } from 'three';

import type { MaiaStateConfig } from './coreState';

// Builds a segmented ring (arcs with gaps, STEP 5: "segmenti, tacche,
// gap") as vertex pairs for a single `THREE.LineSegments` draw call --
// not one React element per tick. `gapEvery` skips one slice in every
// N to create the segmented look; `tickEvery` adds a short outward
// radial tick on top of a kept slice (the "tacche" / status indicators
// STEP 5 asks for on the tick ring specifically).
function buildRingVertices(radius: number, segments: number, gapEvery: number, tickEvery: number, tickLength: number): Float32Array {
  const verts: number[] = [];
  const arcPointsPerSlice = 3;
  for (let i = 0; i < segments; i++) {
    if (gapEvery > 0 && i % gapEvery === 0) continue; // gap
    const a0 = (i / segments) * Math.PI * 2;
    const a1 = ((i + 0.82) / segments) * Math.PI * 2; // slice doesn't span the full step -> visible gap
    for (let p = 0; p < arcPointsPerSlice - 1; p++) {
      const t0 = a0 + ((a1 - a0) * p) / (arcPointsPerSlice - 1);
      const t1 = a0 + ((a1 - a0) * (p + 1)) / (arcPointsPerSlice - 1);
      verts.push(Math.cos(t0) * radius, Math.sin(t0) * radius, 0, Math.cos(t1) * radius, Math.sin(t1) * radius, 0);
    }
    if (tickEvery > 0 && i % tickEvery === 0) {
      const mid = (a0 + a1) / 2;
      const x = Math.cos(mid);
      const y = Math.sin(mid);
      verts.push(x * radius, y * radius, 0, x * (radius + tickLength), y * (radius + tickLength), 0);
    }
  }
  return new Float32Array(verts);
}

interface RingProps {
  radius: number;
  segments: number;
  gapEvery: number;
  tickEvery: number;
  tickLength: number;
  color: string;
  opacity: number;
  rotationSpeed: number;
  motionIntensity: number;
  paused: boolean;
  breathe: boolean;
}

function Ring({ radius, segments, gapEvery, tickEvery, tickLength, color, opacity, rotationSpeed, motionIntensity, paused, breathe }: RingProps) {
  const groupRef = useRef<Group>(null);
  const matRef = useRef<LineBasicMaterial>(null);
  const vertices = useMemo(
    () => buildRingVertices(radius, segments, gapEvery, tickEvery, tickLength),
    [radius, segments, gapEvery, tickEvery, tickLength],
  );

  useFrame(({ clock }, delta) => {
    if (groupRef.current && !paused) {
      groupRef.current.rotation.z += delta * 0.05 * rotationSpeed * motionIntensity;
    }
    if (matRef.current && breathe) {
      const t = clock.getElapsedTime();
      const variation = paused ? 0 : Math.sin(t * 0.4 * motionIntensity) * 0.15;
      matRef.current.opacity = opacity + opacity * variation;
    }
  });

  return (
    // FASE 4O.4B live-review fix: rings were built in the XY plane but
    // rotated flat (X=90 deg) as if lying on the ground under the core,
    // like a saucer -- viewed from this scene's near-front-on camera
    // that collapses a circle into a thin horizontal line. A HUD
    // reticle needs to face the viewer; a small tilt keeps some depth
    // without flattening it away.
    <group ref={groupRef} rotation={[0.12, 0, 0]}>
      <lineSegments>
        <bufferGeometry>
          <primitive attach="attributes-position" object={new BufferAttribute(vertices, 3)} />
        </bufferGeometry>
        <lineBasicMaterial ref={matRef} color={color} transparent opacity={opacity} />
      </lineSegments>
    </group>
  );
}

interface CoreRingsProps {
  segments: number;
  stateConfig: MaiaStateConfig;
  reducedMotion: boolean;
}

// 3-4 concentric HUD rings around the core (STEP 5). Independent slow
// rotations, segmented/ticked geometry -- never plain CSS circles,
// never a videogame-fast spin.
export function CoreRings({ segments, stateConfig, reducedMotion }: CoreRingsProps) {
  const { color, motionIntensity } = stateConfig;
  return (
    <group>
      {/* Ring 1 -- inner energy ring: tight gaps, fast-feeling but still slow absolute speed */}
      <Ring
        radius={2.1}
        segments={segments}
        gapEvery={5}
        tickEvery={0}
        tickLength={0}
        color={color}
        opacity={0.55}
        rotationSpeed={1.1}
        motionIntensity={motionIntensity}
        paused={reducedMotion}
        breathe
      />
      {/* Ring 2 -- segmented operational ring, counter-rotating */}
      <Ring
        radius={2.55}
        segments={Math.round(segments * 0.66)}
        gapEvery={4}
        tickEvery={0}
        tickLength={0}
        color={color}
        opacity={0.35}
        rotationSpeed={-0.7}
        motionIntensity={motionIntensity}
        paused={reducedMotion}
        breathe={false}
      />
      {/* Ring 3 -- status/tick ring: sparse arcs with small radial ticks */}
      <Ring
        radius={2.95}
        segments={Math.round(segments * 0.4)}
        gapEvery={3}
        tickEvery={6}
        tickLength={0.08}
        color={color}
        opacity={0.45}
        rotationSpeed={0.45}
        motionIntensity={motionIntensity}
        paused={reducedMotion}
        breathe
      />
      {/* Ring 4 -- outer sparse telemetry ring: very sparse, very slow */}
      <Ring
        radius={3.35}
        segments={Math.round(segments * 0.3)}
        gapEvery={2}
        tickEvery={0}
        tickLength={0}
        color={color}
        opacity={0.2}
        rotationSpeed={-0.22}
        motionIntensity={motionIntensity}
        paused={reducedMotion}
        breathe={false}
      />
    </group>
  );
}
