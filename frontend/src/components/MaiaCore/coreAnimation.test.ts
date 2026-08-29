import { describe, expect, it } from 'vitest';
import { generateCoreConnections, generateCorePoints } from './coreAnimation';

describe('generateCorePoints', () => {
  it('is deterministic across repeated calls (STEP 3)', () => {
    const a = generateCorePoints(120, 1.6, 1);
    const b = generateCorePoints(120, 1.6, 1);
    expect(Array.from(a)).toEqual(Array.from(b));
  });

  it('produces the requested point count', () => {
    const points = generateCorePoints(50, 1.6, 2);
    expect(points.length).toBe(50 * 3);
  });

  it('keeps every point within the given radius', () => {
    const radius = 2.0;
    const points = generateCorePoints(200, radius, 3);
    for (let i = 0; i < points.length; i += 3) {
      const d = Math.hypot(points[i], points[i + 1], points[i + 2]);
      expect(d).toBeLessThanOrEqual(radius + 1e-9);
    }
  });

  it('biases density toward the center (mean radius well under the max)', () => {
    const radius = 2.0;
    const points = generateCorePoints(400, radius, 4);
    let total = 0;
    let n = 0;
    for (let i = 0; i < points.length; i += 3) {
      total += Math.hypot(points[i], points[i + 1], points[i + 2]);
      n++;
    }
    const mean = total / n;
    // A uniform-volume (not density-biased) sphere would average ~0.75*radius;
    // the center-biased distribution should sit well below that.
    expect(mean).toBeLessThan(radius * 0.6);
  });

  it('produces a different cloud for a different seedOffset (tiers do not overlap identically)', () => {
    const a = generateCorePoints(30, 1.6, 1);
    const b = generateCorePoints(30, 1.6, 2);
    expect(Array.from(a)).not.toEqual(Array.from(b));
  });
});

describe('generateCoreConnections', () => {
  it('is deterministic across repeated calls', () => {
    const points = generateCorePoints(100, 1.6, 1);
    const a = generateCoreConnections(points, 20, 0.8);
    const b = generateCoreConnections(points, 20, 0.8);
    expect(a).toEqual(b);
  });

  it('never links a point to itself', () => {
    const points = generateCorePoints(100, 1.6, 1);
    const connections = generateCoreConnections(points, 30, 0.8);
    for (const c of connections) {
      expect(c.from).not.toEqual(c.to);
    }
  });

  it('only links points within the given max distance (short local arcs, not a full mesh)', () => {
    const points = generateCorePoints(150, 1.6, 1);
    const maxDist = 0.5;
    const connections = generateCoreConnections(points, 60, maxDist);
    for (const c of connections) {
      const d = Math.hypot(c.from.x - c.to.x, c.from.y - c.to.y, c.from.z - c.to.z);
      expect(d).toBeLessThanOrEqual(maxDist + 1e-9);
    }
  });

  it('produces far fewer connections than a fully-connected mesh would (STEP 4)', () => {
    const points = generateCorePoints(150, 1.6, 1);
    const connections = generateCoreConnections(points, 40, 0.8);
    const fullMeshEdges = (150 * 149) / 2;
    expect(connections.length).toBeLessThan(fullMeshEdges * 0.05);
  });

  it('gives each connection a pulse phase in [0, 1)', () => {
    const points = generateCorePoints(100, 1.6, 1);
    const connections = generateCoreConnections(points, 20, 0.8);
    for (const c of connections) {
      expect(c.pulsePhase).toBeGreaterThanOrEqual(0);
      expect(c.pulsePhase).toBeLessThan(1);
    }
  });

  it('returns no connections for an empty point set', () => {
    expect(generateCoreConnections(new Float32Array(0), 10, 0.8)).toEqual([]);
  });
});
