import { useEffect, useImperativeHandle, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { Vector3 } from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';

import type { LayoutMap } from '../../lib/graphLayout';

export interface CameraRigHandle {
  resetView: () => void;
  fitGraph: () => void;
}

interface CameraRigProps {
  layout: LayoutMap;
  focusNodeId: string | null;
  handleRef: React.Ref<CameraRigHandle>;
}

const DEFAULT_TARGET = new Vector3(0, 0, 0);
const DEFAULT_CAMERA_POS = new Vector3(0, 14, 34);

// Smooth, bounded camera motion only -- lerp toward a target each frame,
// never a scripted cinematic path (STEP 7: "do not use aggressive
// cinematic camera movement").
export function CameraRig({ layout, focusNodeId, handleRef }: CameraRigProps) {
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  const { camera } = useThree();
  const desiredTarget = useRef(new Vector3());
  const desiredCameraOffset = useRef(new Vector3(0, 6, 16));

  useEffect(() => {
    if (focusNodeId && layout[focusNodeId]) {
      const p = layout[focusNodeId];
      desiredTarget.current.set(p.x, p.y, p.z);
    } else {
      desiredTarget.current.copy(DEFAULT_TARGET);
    }
  }, [focusNodeId, layout]);

  useImperativeHandle(
    handleRef,
    () => ({
      resetView: () => {
        desiredTarget.current.copy(DEFAULT_TARGET);
        desiredCameraOffset.current.set(0, 6, 16);
      },
      fitGraph: () => {
        const positions = Object.values(layout);
        if (positions.length === 0) return;
        const center = positions.reduce(
          (acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y, z: acc.z + p.z }),
          { x: 0, y: 0, z: 0 },
        );
        center.x /= positions.length;
        center.y /= positions.length;
        center.z /= positions.length;
        const maxDist = Math.max(
          1,
          ...positions.map((p) => Math.hypot(p.x - center.x, p.y - center.y, p.z - center.z)),
        );
        desiredTarget.current.set(center.x, center.y, center.z);
        const dist = Math.max(18, maxDist * 2.2);
        desiredCameraOffset.current.set(0, dist * 0.4, dist);
      },
    }),
    [layout],
  );

  useFrame(() => {
    const controls = controlsRef.current;
    if (!controls) return;
    controls.target.lerp(desiredTarget.current, 0.08);
    const desiredCameraPos = new Vector3()
      .copy(desiredTarget.current)
      .add(desiredCameraOffset.current);
    camera.position.lerp(desiredCameraPos, 0.05);
    controls.update();
  });

  return (
    <OrbitControls
      ref={controlsRef}
      enableDamping
      dampingFactor={0.08}
      minDistance={4}
      maxDistance={90}
      makeDefault
    />
  );
}

export { DEFAULT_CAMERA_POS };
