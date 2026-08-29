import { Suspense, useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import type { Group, PerspectiveCamera as PerspectiveCameraImpl } from 'three';

import { CanvasSizeSync } from '../KnowledgeGraph/CanvasSizeSync';
import { AmbientField } from '../KnowledgeGraph/AmbientField';
import { CoreParticles } from './CoreParticles';
import { CoreConnections } from './CoreConnections';
import { CoreRings } from './CoreRings';
import { CORE_BG, CORE_FOG_COLOR } from './coreTheme';
import type { CoreQualityConfig } from './coreQuality';
import type { MaiaStateConfig } from './coreState';

interface CoreGroupProps {
  quality: CoreQualityConfig;
  stateConfig: MaiaStateConfig;
  reducedMotion: boolean;
}

// The whole core (particles + connections + rings) breathes together
// as one composed object (success condition 4: "HUD e Core costituiscono
// un unico oggetto visivo") -- a single gentle scale pulse plus a very
// slow overall rotation, not independently drifting pieces.
function CoreGroup({ quality, stateConfig, reducedMotion }: CoreGroupProps) {
  const groupRef = useRef<Group>(null);

  useFrame(({ clock }, delta) => {
    if (!groupRef.current) return;
    if (!reducedMotion) {
      groupRef.current.rotation.y += delta * 0.02 * stateConfig.motionIntensity;
      const t = clock.getElapsedTime();
      const breathe = 1 + Math.sin(t * 0.5) * 0.02 * stateConfig.motionIntensity;
      const target = stateConfig.scalePulse * breathe;
      const current = groupRef.current.scale.x;
      groupRef.current.scale.setScalar(current + (target - current) * 0.05);
    } else {
      groupRef.current.scale.setScalar(stateConfig.scalePulse);
    }
  });

  return (
    <group ref={groupRef}>
      <CoreParticles quality={quality} stateConfig={stateConfig} reducedMotion={reducedMotion} />
      <CoreConnections connectionCount={quality.connectionCount} stateConfig={stateConfig} reducedMotion={reducedMotion} />
      <CoreRings segments={quality.ringSegments} stateConfig={stateConfig} reducedMotion={reducedMotion} />
    </group>
  );
}

// Extremely subtle camera parallax for depth perception (STEP 3: "vera
// profondità") -- tiny amplitude, disabled under reduced motion, never
// a free-look/orbit camera (this is a HUD element, not an explorable
// scene like the Knowledge Graph).
function CameraDrift({ reducedMotion }: { reducedMotion: boolean }) {
  useFrame(({ camera, clock }) => {
    if (reducedMotion) return;
    const t = clock.getElapsedTime();
    const cam = camera as PerspectiveCameraImpl;
    cam.position.x = Math.sin(t * 0.08) * 0.15;
    cam.position.y = 0.3 + Math.cos(t * 0.06) * 0.1;
    cam.lookAt(0, 0, 0);
  });
  return null;
}

interface NeuralCoreSceneProps {
  quality: CoreQualityConfig;
  stateConfig: MaiaStateConfig;
  reducedMotion: boolean;
}

export function NeuralCoreScene({ quality, stateConfig, reducedMotion }: NeuralCoreSceneProps) {
  return (
    <div style={{ position: 'absolute', inset: 0 }}>
      <Canvas
        camera={{ position: [0, 0.3, 8.6], fov: 42 }}
        gl={{ antialias: true }}
        dpr={quality.dpr}
        style={{ background: CORE_BG, width: '100%', height: '100%', display: 'block' }}
      >
        <CanvasSizeSync />
        <color attach="background" args={[CORE_BG]} />
        <fog attach="fog" args={[CORE_FOG_COLOR, 6, 20]} />
        <ambientLight intensity={0.3} />
        <pointLight position={[0, 0, 4]} intensity={0.6 * quality.glowScale} color={stateConfig.color} />
        <AmbientField />
        <Suspense fallback={null}>
          <CoreGroup quality={quality} stateConfig={stateConfig} reducedMotion={reducedMotion} />
        </Suspense>
        <CameraDrift reducedMotion={reducedMotion} />
      </Canvas>
    </div>
  );
}
