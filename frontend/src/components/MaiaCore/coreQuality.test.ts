import { describe, expect, it } from 'vitest';
import { CORE_QUALITY, DEFAULT_QUALITY, type CoreQualityLevel } from './coreQuality';

describe('CORE_QUALITY (STEP 11)', () => {
  const levels: CoreQualityLevel[] = ['HIGH', 'MEDIUM', 'LOW'];

  it('defines all three required levels', () => {
    for (const level of levels) {
      expect(CORE_QUALITY[level]).toBeDefined();
    }
  });

  it('strictly decreases particle counts from HIGH to MEDIUM to LOW', () => {
    const total = (level: CoreQualityLevel) => {
      const { small, medium, large } = CORE_QUALITY[level].particleCounts;
      return small + medium + large;
    };
    expect(total('HIGH')).toBeGreaterThan(total('MEDIUM'));
    expect(total('MEDIUM')).toBeGreaterThan(total('LOW'));
  });

  it('strictly decreases connection count from HIGH to MEDIUM to LOW', () => {
    expect(CORE_QUALITY.HIGH.connectionCount).toBeGreaterThan(CORE_QUALITY.MEDIUM.connectionCount);
    expect(CORE_QUALITY.MEDIUM.connectionCount).toBeGreaterThan(CORE_QUALITY.LOW.connectionCount);
  });

  it('caps device pixel ratio at or below HIGH for every level', () => {
    for (const level of levels) {
      expect(CORE_QUALITY[level].dpr[1]).toBeLessThanOrEqual(CORE_QUALITY.HIGH.dpr[1]);
    }
  });

  it('defaults to MEDIUM in V1 (no automatic benchmark yet)', () => {
    expect(DEFAULT_QUALITY).toBe('MEDIUM');
  });

  it('every level has a positive particle count in every tier (no accidental zero)', () => {
    for (const level of levels) {
      const { small, medium, large } = CORE_QUALITY[level].particleCounts;
      expect(small).toBeGreaterThan(0);
      expect(medium).toBeGreaterThan(0);
      expect(large).toBeGreaterThan(0);
    }
  });
});
