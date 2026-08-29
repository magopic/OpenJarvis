// MAIA Neural Core — adaptive quality levels (FASE 4O.4B STEP 11).
//
// Architecture only for V1: no automatic hardware benchmarking is
// implemented (explicitly out of scope this phase) -- `DEFAULT_QUALITY`
// is used until a future phase adds real detection or a user setting.
// Every visual component reads its particle/connection/ring counts and
// pixel ratio from this table so quality tuning never requires
// touching component internals.

export type CoreQualityLevel = 'HIGH' | 'MEDIUM' | 'LOW';

export interface CoreQualityConfig {
  particleCounts: { small: number; medium: number; large: number };
  connectionCount: number;
  ringSegments: number;
  glowScale: number;
  dpr: [number, number];
  animate: boolean;
}

export const CORE_QUALITY: Record<CoreQualityLevel, CoreQualityConfig> = {
  HIGH: {
    particleCounts: { small: 260, medium: 140, large: 60 },
    connectionCount: 46,
    ringSegments: 96,
    glowScale: 1.15,
    dpr: [1, 2],
    animate: true,
  },
  MEDIUM: {
    particleCounts: { small: 160, medium: 90, large: 36 },
    connectionCount: 28,
    ringSegments: 72,
    glowScale: 1.0,
    dpr: [1, 1.5],
    animate: true,
  },
  LOW: {
    particleCounts: { small: 80, medium: 40, large: 16 },
    connectionCount: 14,
    ringSegments: 48,
    glowScale: 0.85,
    dpr: [1, 1],
    animate: true,
  },
};

// V1 default -- see module docstring; not derived from any benchmark.
export const DEFAULT_QUALITY: CoreQualityLevel = 'MEDIUM';
