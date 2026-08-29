// MAIA Neural Core — visual state contract (FASE 4O.4B).
//
// This is a PRESENTATION-ONLY adapter. It invents no business state: it
// only reads state that already exists elsewhere in the app (the
// shared Zustand store's `streamState`/`voiceLoopState`, and a health
// check the app already performs in `Layout.tsx`) and maps it to one
// of six visual states. It never writes to that state, never creates a
// second voice loop, never talks to the Second Brain or Graph API, and
// is not itself a new source of truth for anything.
import { useEffect, useState } from 'react';
import { useAppStore } from '../../lib/store';
import { checkHealth } from '../../lib/api';
import type { StreamState } from '../../types';
import type { VoiceLoopState } from '../../hooks/useVoiceLoop';
import {
  COLOR_ERROR,
  COLOR_IDLE,
  COLOR_LISTENING,
  COLOR_SPEAKING,
  COLOR_THINKING,
  COLOR_TOOL_ACTIVE,
} from './coreTheme';

export type MaiaVisualState = 'IDLE' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'TOOL_ACTIVE' | 'ERROR';

export interface MaiaStateConfig {
  color: string;
  /** Idle-motion multiplier for rings/breathing -- 1 is calm baseline. */
  motionIntensity: number;
  /** Core glow/emissive multiplier. */
  glowIntensity: number;
  /** How much the core "expands" relative to its resting scale. */
  scalePulse: number;
}

// STEP 6's per-state behavior, expressed as data rather than scattered
// conditionals -- every visual component reads this same table so the
// six states can never drift out of sync with each other.
export const MAIA_STATE_CONFIG: Record<MaiaVisualState, MaiaStateConfig> = {
  IDLE: { color: COLOR_IDLE, motionIntensity: 0.5, glowIntensity: 0.5, scalePulse: 1.0 },
  LISTENING: { color: COLOR_LISTENING, motionIntensity: 1.1, glowIntensity: 1.0, scalePulse: 1.06 },
  THINKING: { color: COLOR_THINKING, motionIntensity: 1.6, glowIntensity: 1.15, scalePulse: 1.0 },
  SPEAKING: { color: COLOR_SPEAKING, motionIntensity: 1.3, glowIntensity: 1.2, scalePulse: 1.1 },
  TOOL_ACTIVE: { color: COLOR_TOOL_ACTIVE, motionIntensity: 1.4, glowIntensity: 1.1, scalePulse: 1.03 },
  ERROR: { color: COLOR_ERROR, motionIntensity: 0.35, glowIntensity: 0.7, scalePulse: 0.97 },
};

const HEALTH_POLL_MS = 30000;

/**
 * Pure state-derivation logic, extracted from the hook below so it's
 * directly unit-testable without rendering React (this codebase has no
 * @testing-library/react dependency -- tests exercise store/logic
 * functions directly, matching every other `*.test.ts` in this repo).
 *
 * Priority order matters: an active tool call during a stream is more
 * informative than "thinking" in general, so it's checked first. An
 * unreachable backend overrides everything else -- nothing else is
 * meaningful to show while MAIA itself can't be reached.
 */
export function deriveMaiaVisualState(
  streamState: Pick<StreamState, 'isStreaming' | 'activeToolCalls'>,
  voiceLoopState: VoiceLoopState,
  apiReachable: boolean | null,
): MaiaVisualState {
  if (apiReachable === false) return 'ERROR';
  if (voiceLoopState === 'listening') return 'LISTENING';
  if (voiceLoopState === 'speaking') return 'SPEAKING';
  if (streamState.activeToolCalls.some((tc) => tc.status === 'running')) return 'TOOL_ACTIVE';
  if (streamState.isStreaming || voiceLoopState === 'thinking') return 'THINKING';
  return 'IDLE';
}

/**
 * Derives one `MaiaVisualState` from real, already-existing app state:
 * - ERROR: the backend health check (same one `Layout.tsx` already
 *   polls) is failing.
 * - LISTENING / SPEAKING: `useVoiceLoop`'s state, broadcast into the
 *   shared store (see `store.ts`'s `voiceLoopState` field) -- this
 *   component never starts its own microphone/VAD instance.
 * - TOOL_ACTIVE: `streamState.activeToolCalls` has a `running` entry.
 * - THINKING: `streamState.isStreaming`, or the voice loop's own
 *   'thinking' phase.
 * - IDLE: none of the above -- the default resting state.
 */
export function useMaiaVisualState(): MaiaVisualState {
  const streamState = useAppStore((s) => s.streamState);
  const voiceLoopState = useAppStore((s) => s.voiceLoopState);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      checkHealth()
        .then((ok) => {
          if (!cancelled) setApiReachable(ok);
        })
        .catch(() => {
          if (!cancelled) setApiReachable(false);
        });
    };
    check();
    const interval = setInterval(check, HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return deriveMaiaVisualState(streamState, voiceLoopState, apiReachable);
}
