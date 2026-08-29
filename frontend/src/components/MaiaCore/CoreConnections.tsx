import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Line } from '@react-three/drei';
import type { Line2 } from 'three-stdlib';

import { generateCoreConnections, generateCorePoints } from './coreAnimation';
import { CORE_RADIUS } from './CoreParticles';
import type { MaiaStateConfig } from './coreState';

interface ConnectionLineProps {
  from: [number, number, number];
  to: [number, number, number];
  color: string;
  pulsePhase: number;
  motionIntensity: number;
  paused: boolean;
}

// Each connection has its own slow, staggered brightness pulse (STEP 4:
// "impulsi occasionali") -- driven by a per-connection phase offset so
// they fire at different times, never in unison, and never as a
// constant idle animation once paused for reduced motion. Mutates the
// line's own material directly via a ref inside `useFrame` -- no React
// state per frame (STEP 10: "nessun setState React a 60 FPS").
function ConnectionLine({ from, to, color, pulsePhase, motionIntensity, paused }: ConnectionLineProps) {
  const lineRef = useRef<Line2>(null);

  useFrame(({ clock }) => {
    const material = lineRef.current?.material;
    if (!material) return;
    if (paused) {
      material.opacity = 0.12;
      return;
    }
    const t = clock.getElapsedTime() * 0.25 * motionIntensity + pulsePhase * Math.PI * 2;
    // Mostly dim, with an occasional gentle brightening -- sin^6 keeps
    // the bright moment short and infrequent rather than a smooth
    // constant breathing line.
    const pulse = Math.pow(Math.max(0, Math.sin(t)), 6);
    material.opacity = 0.08 + pulse * 0.5;
  });

  return <Line ref={lineRef} points={[from, to]} color={color} transparent opacity={0.1} lineWidth={0.6} />;
}

interface CoreConnectionsProps {
  connectionCount: number;
  stateConfig: MaiaStateConfig;
  reducedMotion: boolean;
}

// Sparse internal neural web (STEP 4). Deliberately NOT a fully
// connected mesh, NOT a force graph, NOT a flat constellation -- short
// local arcs between a deterministic subset of the particle cloud's own
// points, giving the impression of neural activity without becoming a
// second Knowledge Graph.
export function CoreConnections({ connectionCount, stateConfig, reducedMotion }: CoreConnectionsProps) {
  const connections = useMemo(() => {
    // Reuses the same deterministic point generator as the medium
    // particle tier so connections visually anchor to real rendered
    // points rather than an independent, disconnected-looking set.
    const anchorPoints = generateCorePoints(160, CORE_RADIUS * 0.85, 2);
    return generateCoreConnections(anchorPoints, connectionCount, CORE_RADIUS * 0.5);
  }, [connectionCount]);

  return (
    <group>
      {connections.map((c, i) => (
        <ConnectionLine
          key={i}
          from={[c.from.x, c.from.y, c.from.z]}
          to={[c.to.x, c.to.y, c.to.z]}
          color={stateConfig.color}
          pulsePhase={c.pulsePhase}
          motionIntensity={stateConfig.motionIntensity}
          paused={reducedMotion}
        />
      ))}
    </group>
  );
}
