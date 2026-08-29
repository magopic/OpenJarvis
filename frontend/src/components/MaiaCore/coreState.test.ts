import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { StreamState } from '../../types';
import type { deriveMaiaVisualState as DeriveFn, MAIA_STATE_CONFIG as ConfigType, MaiaVisualState } from './coreState';

// `coreState.ts` imports the shared store (`lib/store.ts`), which calls
// `localStorage` at module-import time -- stub it before the dynamic
// import below, matching the same pattern `store.models.test.ts` uses
// for the same reason (this project's vitest environment is plain
// Node, no jsdom, so `localStorage` doesn't exist by default).
class MemoryStorage {
  private store = new Map<string, string>();
  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

let deriveMaiaVisualState: typeof DeriveFn;
let MAIA_STATE_CONFIG: typeof ConfigType;

beforeEach(async () => {
  vi.resetModules();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage = new MemoryStorage();
  const mod = await import('./coreState');
  deriveMaiaVisualState = mod.deriveMaiaVisualState;
  MAIA_STATE_CONFIG = mod.MAIA_STATE_CONFIG;
});

const stream = (overrides: Partial<Pick<StreamState, 'isStreaming' | 'activeToolCalls'>> = {}) => ({
  isStreaming: false,
  activeToolCalls: [],
  ...overrides,
});

describe('deriveMaiaVisualState', () => {
  it('returns IDLE when nothing else is happening', () => {
    expect(deriveMaiaVisualState(stream(), 'idle', true)).toBe('IDLE');
  });

  it('returns LISTENING when the voice loop is listening', () => {
    expect(deriveMaiaVisualState(stream(), 'listening', true)).toBe('LISTENING');
  });

  it('returns SPEAKING when the voice loop is speaking', () => {
    expect(deriveMaiaVisualState(stream(), 'speaking', true)).toBe('SPEAKING');
  });

  it('returns THINKING while a stream is active with no running tool call', () => {
    expect(deriveMaiaVisualState(stream({ isStreaming: true }), 'idle', true)).toBe('THINKING');
  });

  it('returns THINKING when the voice loop itself is thinking', () => {
    expect(deriveMaiaVisualState(stream(), 'thinking', true)).toBe('THINKING');
  });

  it('returns TOOL_ACTIVE when a tool call is running, even mid-stream', () => {
    const s = stream({
      isStreaming: true,
      activeToolCalls: [{ id: '1', tool: 'web_search', arguments: '{}', status: 'running' }],
    });
    expect(deriveMaiaVisualState(s, 'idle', true)).toBe('TOOL_ACTIVE');
  });

  it('does not treat a finished tool call as TOOL_ACTIVE', () => {
    const s = stream({
      isStreaming: true,
      activeToolCalls: [{ id: '1', tool: 'web_search', arguments: '{}', status: 'success' }],
    });
    expect(deriveMaiaVisualState(s, 'idle', true)).toBe('THINKING');
  });

  it('returns ERROR when the backend is unreachable, overriding every other signal', () => {
    const s = stream({
      isStreaming: true,
      activeToolCalls: [{ id: '1', tool: 'web_search', arguments: '{}', status: 'running' }],
    });
    expect(deriveMaiaVisualState(s, 'speaking', false)).toBe('ERROR');
  });

  it('treats an unresolved health check (null) as reachable, not an error', () => {
    expect(deriveMaiaVisualState(stream(), 'idle', null)).toBe('IDLE');
  });
});

describe('MAIA_STATE_CONFIG', () => {
  const states: MaiaVisualState[] = ['IDLE', 'LISTENING', 'THINKING', 'SPEAKING', 'TOOL_ACTIVE', 'ERROR'];

  it('defines every visual state (success condition 5)', () => {
    for (const s of states) {
      expect(MAIA_STATE_CONFIG[s]).toBeDefined();
    }
  });

  it('gives every state a distinct color', () => {
    const colors = new Set(states.map((s) => MAIA_STATE_CONFIG[s].color));
    expect(colors.size).toBe(states.length);
  });

  it('keeps ERROR visually calm, not the most intense state (no aggressive blinking red)', () => {
    const errorMotion = MAIA_STATE_CONFIG.ERROR.motionIntensity;
    const maxOtherMotion = Math.max(
      ...states.filter((s) => s !== 'ERROR').map((s) => MAIA_STATE_CONFIG[s].motionIntensity),
    );
    expect(errorMotion).toBeLessThan(maxOtherMotion);
  });

  it('gives IDLE the lowest glow intensity (calm resting state)', () => {
    const idleGlow = MAIA_STATE_CONFIG.IDLE.glowIntensity;
    for (const s of states) {
      if (s === 'IDLE') continue;
      expect(MAIA_STATE_CONFIG[s].glowIntensity).toBeGreaterThanOrEqual(idleGlow);
    }
  });
});
