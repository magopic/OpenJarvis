// MAIA Knowledge Graph — shared additive glow texture (FASE 4O.4A
// visual-review fix #2).
//
// The previous halo was a solid, uniformly-opaque sphere -- every
// pixel inside its silhouette had the same alpha, so it rendered as a
// flat disc with a hard circular edge ("geometry", not "light"). This
// generates one small radial-gradient texture (bright center, alpha
// falls smoothly to zero at the edge) at runtime -- no external image
// asset -- shared by every glow sprite in the scene via one cached
// `THREE.CanvasTexture`. Combined with additive blending in
// `NodeMesh.tsx`, overlapping glows can only brighten the scene, never
// stack into a darker translucent region.
import { CanvasTexture } from 'three';

let cached: CanvasTexture | null = null;

export function getGlowTexture(): CanvasTexture {
  if (cached) return cached;
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(0.22, 'rgba(255,255,255,0.55)');
    gradient.addColorStop(0.5, 'rgba(255,255,255,0.14)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
  }
  cached = new CanvasTexture(canvas);
  return cached;
}
