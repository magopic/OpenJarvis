import { useEffect, useImperativeHandle, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { PerspectiveCamera, Vector3 } from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';

import type { GraphResponse } from '../../types/graph';
import type { LayoutMap } from '../../lib/graphLayout';
import { styleForNode } from '../../lib/graphVisual';

export interface CameraRigHandle {
  resetView: () => void;
  fitGraph: () => void;
}

interface CameraRigProps {
  graph: GraphResponse;
  layout: LayoutMap;
  focusNodeId: string | null;
  handleRef: React.Ref<CameraRigHandle>;
}

const DEFAULT_TARGET = new Vector3(0, 0, 0);
// Only used as the R3F <Canvas> initial camera prop, before any fit has
// ever run (the first real frame immediately fits properly via the
// effect below) -- not used anywhere in the fit math itself.
const DEFAULT_CAMERA_POS = new Vector3(0, 3, 7);

// Keeps the same ~20 degree elevated viewing angle used throughout this
// component, expressed as unit direction so any computed distance can
// be turned into a camera offset whose *magnitude* actually equals that
// distance (an earlier version conflated "dist" with the offset's z
// component, which under-measured the true camera distance and made
// the FOV-based fit math below inaccurate).
const ELEVATION = Math.atan2(0.38, 1);
const OFFSET_DIR = new Vector3(0, Math.sin(ELEVATION), Math.cos(ELEVATION));

const MIN_FIT_DISTANCE = 3;
// FASE 4O.4A fix #5: the theoretical "1/padding = fill%" relationship
// (fix #3's basis for 1.65 -> ~60%) assumed a bounding sphere whose
// surface is densely populated with content all the way to its radius.
// In practice the jittered node cloud is much denser near its center
// than at the single farthest outlier that defines `maxReach` -- most
// visual mass sits well inside that radius -- so a sphere fit exactly
// to the outlier reads as noticeably smaller on screen than the
// formula predicts. Retuned empirically against the user's real
// screenshot (not the theoretical percentage) to a much tighter
// multiplier; only small headroom past the single farthest point,
// which is exactly the point most likely to need it to avoid clipping.
const FIT_PADDING = 1.12;

function offsetForDistance(dist: number): Vector3 {
  return OFFSET_DIR.clone().multiplyScalar(dist);
}

// FASE 4O.4A visual-review fix #1: root cause of "cannot zoom out" /
// "Fit does not stick" was that the frame loop below used to lerp the
// camera toward a fixed target on *every single frame forever* --
// including while the user was actively scrolling the mouse wheel or
// dragging, which OrbitControls answers by moving `camera.position`
// itself. Fixed by only running the lerp while `transitioning` is true
// (set exactly when a focus/fit/reset changes the desired framing,
// cleared once the camera reaches it) -- once settled, OrbitControls
// has full, unfought control of position/target from wheel/drag input.
//
// Fix #3: a second bug compounded on top of that fix -- the no-focus
// branch of the effect below used to snap to a fixed
// `DEFAULT_CAMERA_POS` distance on every layout change (e.g. toggling
// "Include archived") instead of an actual bounds-fit, and a *separate*
// setTimeout-driven auto-fit in `KnowledgeGraphView` only ran once per
// mode -- so most layout changes after the first got the wrong,
// dataset-size-blind distance instead of a real fit. Both are now the
// same single code path: every layout change with no active focus
// calls `computeFit`, so it can never drift from what "Fit graph"
// itself would compute.
export function CameraRig({ graph, layout, focusNodeId, handleRef }: CameraRigProps) {
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  const { camera, size } = useThree();
  const desiredTarget = useRef(new Vector3());
  const desiredCameraOffset = useRef(offsetForDistance(7));
  const transitioning = useRef(false);

  // True graph-bounds fit: accounts for each node's actual rendered
  // extent (core + glow radius, not just its center point), the
  // camera's real field of view, and the viewport's aspect ratio --
  // whichever axis (horizontal/vertical) is tighter is the one that
  // must fit, so a wide-but-short or tall-but-narrow graph still frames
  // correctly (STEP 3).
  const computeFit = (nodeIds: string[]): { center: Vector3; distance: number } => {
    const points = nodeIds.map((id) => layout[id]).filter(Boolean) as { x: number; y: number; z: number }[];
    if (points.length === 0) {
      return { center: DEFAULT_TARGET.clone(), distance: 7 };
    }
    const center = new Vector3();
    for (const p of points) center.add(new Vector3(p.x, p.y, p.z));
    center.divideScalar(points.length);

    let maxReach = 1;
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));
    for (const id of nodeIds) {
      const p = layout[id];
      const node = byId.get(id);
      if (!p || !node) continue;
      const style = styleForNode(node);
      const visualRadius = style.size * style.haloScale;
      const reach = new Vector3(p.x, p.y, p.z).distanceTo(center) + visualRadius;
      if (reach > maxReach) maxReach = reach;
    }

    const padded = maxReach * FIT_PADDING;
    const cam = camera as PerspectiveCamera;
    const vFov = ((cam.fov ?? 50) * Math.PI) / 180;
    const aspect = size.height > 0 ? size.width / size.height : 1;
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * aspect);
    const limitingFov = Math.min(vFov, hFov);
    const distance = Math.max(MIN_FIT_DISTANCE, padded / Math.sin(limitingFov / 2));
    return { center, distance };
  };

  const beginTransition = (center: Vector3, distance: number) => {
    desiredTarget.current.copy(center);
    desiredCameraOffset.current = offsetForDistance(distance);
    transitioning.current = true;
    // A graph much larger than the default framing must still be
    // reachable by zooming out (STEP 2) -- keep the hard cap generous
    // relative to whatever this fit actually needed.
    if (controlsRef.current) {
      controlsRef.current.maxDistance = Math.max(120, distance * 4);
    }
  };

  useEffect(() => {
    if (focusNodeId && layout[focusNodeId]) {
      const p = layout[focusNodeId];
      const { distance } = computeFit(Object.keys(layout));
      beginTransition(new Vector3(p.x, p.y, p.z), Math.max(MIN_FIT_DISTANCE, distance * 0.4));
    } else {
      const { center, distance } = computeFit(Object.keys(layout));
      beginTransition(center, distance);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusNodeId, layout]);

  useImperativeHandle(
    handleRef,
    () => ({
      // Reset and Fit intentionally share the exact same bounds-fitting
      // logic (STEP 4) -- "reset" has no separate canonical view to
      // return to beyond "show the whole currently-loaded graph."
      resetView: () => {
        const { center, distance } = computeFit(Object.keys(layout));
        beginTransition(center, distance);
      },
      fitGraph: () => {
        const { center, distance } = computeFit(Object.keys(layout));
        beginTransition(center, distance);
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [layout, graph],
  );

  useFrame(() => {
    const controls = controlsRef.current;
    if (!controls || !transitioning.current) return;
    controls.target.lerp(desiredTarget.current, 0.08);
    const desiredCameraPos = new Vector3().copy(desiredTarget.current).add(desiredCameraOffset.current);
    camera.position.lerp(desiredCameraPos, 0.08);
    controls.update();
    const settled =
      controls.target.distanceTo(desiredTarget.current) < 0.03 &&
      camera.position.distanceTo(desiredCameraPos) < 0.03;
    if (settled) transitioning.current = false;
  });

  return (
    <OrbitControls
      ref={controlsRef}
      enableDamping
      dampingFactor={0.08}
      minDistance={2}
      maxDistance={120}
      makeDefault
    />
  );
}

export { DEFAULT_CAMERA_POS };
