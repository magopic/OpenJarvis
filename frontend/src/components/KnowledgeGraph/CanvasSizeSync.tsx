import { useEffect } from 'react';
import { useThree } from '@react-three/fiber';
import type { PerspectiveCamera } from 'three';

// FASE 4O.4A visual-review fix #4: investigated a candidate root cause
// for "graph far too small" -- R3F's <Canvas> never sets explicit
// width/height on the actual <canvas> element itself; it relies on
// `react-use-measure` observing its wrapper div and, once that reports
// a nonzero size, calling the renderer's `setSize()`. In this
// automated test tab that measurement was reproducibly stuck at the
// browser's literal canvas default (300x150px) even though the wrapper
// div itself measured the full container correctly -- but this could
// not be confirmed as the cause of what the user's own browser shows,
// since the same automated tab also cannot composite frames for
// screenshots in this environment (a separate, known limitation), so
// the two may share a cause specific to non-visible/non-composited
// tabs rather than affecting a normally-open browser window.
//
// Kept as a defensive, low-risk safeguard regardless: it measures the
// canvas's own parent directly via `getBoundingClientRect()` (never
// trusting react-use-measure's state) and explicitly drives
// `gl.setSize()` + the camera's aspect/projection matrix, on mount and
// on every subsequent resize via its own `ResizeObserver`. If R3F's own
// sizing already matches, `applySize`'s early-return guard makes this a
// no-op; if it doesn't, this corrects it. Touches no graph data, no
// layout positions, no visual grammar.
export function CanvasSizeSync() {
  const { gl, camera, size, set } = useThree();

  useEffect(() => {
    const canvas = gl.domElement;
    const target = canvas.parentElement;
    if (!target) return;

    const applySize = (width: number, height: number) => {
      if (width <= 0 || height <= 0) return;
      if (Math.abs(width - size.width) < 0.5 && Math.abs(height - size.height) < 0.5) return;
      gl.setSize(width, height);
      const cam = camera as PerspectiveCamera;
      if (typeof cam.aspect === 'number') {
        cam.aspect = width / height;
        cam.updateProjectionMatrix();
      }
      set({ size: { width, height, top: 0, left: 0 } });
    };

    const rect = target.getBoundingClientRect();
    applySize(rect.width, rect.height);

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        applySize(width, height);
      }
    });
    observer.observe(target);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gl, camera, set]);

  return null;
}
