// MAIA Neural Core — visual palette (FASE 4O.4B).
//
// Hardcoded, not read from `--color-*` CSS variables, deliberately
// following the same precedent as `KnowledgeGraph/graphTheme.ts`: this
// scene is always its own immersive dark environment, and resolving
// colors from `document.documentElement`'s theme class inside a 3D
// component previously produced a broken (white) canvas whenever the
// app theme was light. State colors below are the *exact* hues
// `VoiceLoopButton.tsx`/`SystemPulse.tsx` already use for the same
// concepts (accent/purple/warning/success/error), just copied as
// static hex rather than re-reading the variables at render time.

export const CORE_BG = '#05070c';
export const CORE_FOG_COLOR = '#0a1420';

export const COLOR_IDLE = '#5b8ba8'; // dim cold blue -- calm, low presence
export const COLOR_LISTENING = '#22d3ee'; // == --color-accent
export const COLOR_THINKING = '#b794ff'; // == --color-accent-purple
export const COLOR_SPEAKING = '#3ddc97'; // == --color-success
export const COLOR_TOOL_ACTIVE = '#f5a524'; // == --color-warning
export const COLOR_ERROR = '#ff6b6b'; // == --color-error (never blinking/aggressive)

export const HUD_TEXT_PRIMARY = '#eaf7ff';
export const HUD_TEXT_SECONDARY = '#7fa3ba';
export const HUD_TEXT_TERTIARY = '#4a6172';
export const HUD_BORDER = 'rgba(120, 190, 220, 0.16)';
export const HUD_PANEL_BG = 'rgba(8, 14, 22, 0.55)';
