// MAIA Neural Core — deterministic particle/connection generation
// (FASE 4O.4B STEP 3/4).
//
// Every position here is computed once from a fixed index-seeded hash,
// never `Math.random()` -- re-mounting the component produces the same
// cloud every time (STEP 3: "comportamento deterministico quando
// possibile"). These particles and connections are DECORATIVE: they
// carry no id tying them to a Second Brain entry or Graph node, and
// nothing here is ever passed to or read from the Knowledge Graph.

// FNV-1a, matching the same small deterministic hash already used in
// `KnowledgeGraph/graphLayout.ts` and `AmbientField.tsx`.
function hash01(seed: number): number {
  let h = 0x811c9dc5 ^ seed;
  h = Math.imul(h, 0x01000193);
  h ^= h >>> 13;
  h = Math.imul(h, 0x01000193);
  h ^= h << 7;
  return ((h >>> 0) % 4294967296) / 4294967296;
}

export interface CorePoint {
  x: number;
  y: number;
  z: number;
}

/**
 * Volumetric spherical particle cloud, denser toward the center (STEP
 * 3: "densità maggiore verso il centro"): radius is biased toward zero
 * by squaring the uniform sample, then points are distributed evenly
 * over the sphere's solid angle so there's no polar clumping.
 */
export function generateCorePoints(count: number, radius: number, seedOffset: number): Float32Array {
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const s = i + seedOffset * 100000;
    const theta = hash01(s * 2 + 1) * Math.PI * 2;
    const cosPhi = hash01(s * 2 + 2) * 2 - 1;
    const sinPhi = Math.sqrt(Math.max(0, 1 - cosPhi * cosPhi));
    const rFrac = hash01(s * 2 + 3);
    const r = radius * rFrac * rFrac; // bias toward center
    positions[i * 3] = r * sinPhi * Math.cos(theta);
    positions[i * 3 + 1] = r * cosPhi;
    positions[i * 3 + 2] = r * sinPhi * Math.sin(theta);
  }
  return positions;
}

export interface CoreConnection {
  from: CorePoint;
  to: CorePoint;
  /** Deterministic phase offset (0-1) for this connection's pulse timing. */
  pulsePhase: number;
}

/**
 * A sparse subset of short local connections between nearby points --
 * deliberately NOT a fully-connected mesh (STEP 4: "non collegare tutte
 * le particelle" / must not read as a web, database graph, or force
 * graph). Each connection picks a random-but-deterministic anchor point
 * and links it to one of its few nearest neighbors within a small
 * radius, producing short local arcs rather than long cross-core lines.
 */
export function generateCoreConnections(points: Float32Array, count: number, maxLinkDistance: number): CoreConnection[] {
  const n = points.length / 3;
  if (n === 0) return [];
  const connections: CoreConnection[] = [];
  for (let i = 0; i < count; i++) {
    const anchorIdx = Math.floor(hash01(i * 7 + 1) * n) % n;
    const ax = points[anchorIdx * 3];
    const ay = points[anchorIdx * 3 + 1];
    const az = points[anchorIdx * 3 + 2];

    // Search a small deterministic sample of candidate indices (not the
    // whole cloud) for the nearest one within range -- keeps this O(1)
    // per connection instead of O(n).
    let bestIdx = -1;
    let bestDist = Infinity;
    for (let k = 0; k < 8; k++) {
      const candidateIdx = Math.floor(hash01(i * 7 + 100 + k) * n) % n;
      if (candidateIdx === anchorIdx) continue;
      const dx = points[candidateIdx * 3] - ax;
      const dy = points[candidateIdx * 3 + 1] - ay;
      const dz = points[candidateIdx * 3 + 2] - az;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (dist < bestDist && dist < maxLinkDistance) {
        bestDist = dist;
        bestIdx = candidateIdx;
      }
    }
    if (bestIdx === -1) continue;
    connections.push({
      from: { x: ax, y: ay, z: az },
      to: { x: points[bestIdx * 3], y: points[bestIdx * 3 + 1], z: points[bestIdx * 3 + 2] },
      pulsePhase: hash01(i * 7 + 999),
    });
  }
  return connections;
}
