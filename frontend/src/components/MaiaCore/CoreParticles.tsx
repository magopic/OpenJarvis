import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { AdditiveBlending, BufferAttribute, type Points } from 'three';

import { generateCorePoints } from './coreAnimation';
import type { CoreQualityConfig } from './coreQuality';
import type { MaiaStateConfig } from './coreState';

const CORE_RADIUS = 1.6;

interface TierProps {
  count: number;
  radiusScale: number;
  size: number;
  opacity: number;
  color: string;
  seedOffset: number;
  rotationSpeed: number;
  motionIntensity: number;
  paused: boolean;
}

// One tier = one draw call (a single `THREE.Points` object). Three
// tiers (small/medium/large) give "variazione controllata delle
// dimensioni" (STEP 3) without a custom shader -- `pointsMaterial`
// only supports one uniform size per object, so distinct sizes need
// distinct tiers rather than a per-particle attribute.
function ParticleTier({ count, radiusScale, size, opacity, color, seedOffset, rotationSpeed, motionIntensity, paused }: TierProps) {
  const ref = useRef<Points>(null);
  const positions = useMemo(
    () => generateCorePoints(count, CORE_RADIUS * radiusScale, seedOffset),
    [count, radiusScale, seedOffset],
  );

  // Whole-tier rotation only -- never per-particle re-randomization, so
  // there is nothing resembling live "activity" beyond a slow, calm
  // idle drift (STEP 3: "movimento molto lento in idle", "nessun
  // Math.random() nel render loop").
  useFrame((_, delta) => {
    if (paused || !ref.current) return;
    ref.current.rotation.y += delta * 0.015 * rotationSpeed * motionIntensity;
    ref.current.rotation.x += delta * 0.007 * rotationSpeed * motionIntensity;
  });

  return (
    <points ref={ref}>
      <bufferGeometry>
        <primitive attach="attributes-position" object={new BufferAttribute(positions, 3)} />
      </bufferGeometry>
      <pointsMaterial
        size={size}
        color={color}
        transparent
        opacity={opacity}
        blending={AdditiveBlending}
        depthWrite={false}
        sizeAttenuation
      />
    </points>
  );
}

interface CoreParticlesProps {
  quality: CoreQualityConfig;
  stateConfig: MaiaStateConfig;
  reducedMotion: boolean;
}

// The volumetric particle core itself (STEP 3). Purely decorative --
// no id, no relation to any Second Brain entry or Graph node.
export function CoreParticles({ quality, stateConfig, reducedMotion }: CoreParticlesProps) {
  const { small, medium, large } = quality.particleCounts;
  // FASE 4O.4B live-review fix: sizes were tuned against no real camera
  // distance and rendered at a fraction of a pixel once actually
  // measured (~0.5-1.7px at this scene's real fov/distance/canvas
  // size) -- effectively invisible. Also switched glow from a pure
  // multiplier (which made IDLE's low glowIntensity fade particles to
  // near-zero) to a base-plus-range formula so even the calmest state
  // stays clearly visible, only dimmer.
  const glowOpacity = (base: number, range: number) => base + range * stateConfig.glowIntensity;
  return (
    <group>
      <ParticleTier
        count={small}
        radiusScale={1.0}
        size={0.09}
        opacity={glowOpacity(0.35, 0.35)}
        color={stateConfig.color}
        seedOffset={1}
        rotationSpeed={1}
        motionIntensity={stateConfig.motionIntensity}
        paused={reducedMotion}
      />
      <ParticleTier
        count={medium}
        radiusScale={0.85}
        size={0.16}
        opacity={glowOpacity(0.45, 0.35)}
        color={stateConfig.color}
        seedOffset={2}
        rotationSpeed={-0.7}
        motionIntensity={stateConfig.motionIntensity}
        paused={reducedMotion}
      />
      <ParticleTier
        count={large}
        radiusScale={0.6}
        size={0.26}
        opacity={glowOpacity(0.55, 0.4)}
        color={stateConfig.color}
        seedOffset={3}
        rotationSpeed={0.45}
        motionIntensity={stateConfig.motionIntensity}
        paused={reducedMotion}
      />
    </group>
  );
}

export { CORE_RADIUS };
