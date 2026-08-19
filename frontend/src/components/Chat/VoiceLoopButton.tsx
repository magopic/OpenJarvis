import { useState } from 'react';
import { Headphones } from 'lucide-react';
import type { VoiceLoopState } from '../../hooks/useVoiceLoop';

interface VoiceLoopButtonProps {
  state: VoiceLoopState;
  onClick: () => void;
  disabled?: boolean;
  reason?: 'not-enabled' | 'no-backend' | 'streaming';
}

const STATE_COLOR: Record<VoiceLoopState, string> = {
  idle: 'var(--color-text-secondary)',
  listening: 'var(--color-accent)',
  thinking: 'var(--color-warning, #d97706)',
  speaking: 'var(--color-success, #16a34a)',
};

const STATE_LABEL: Record<VoiceLoopState, string> = {
  idle: 'Start continuous voice conversation',
  listening: 'Listening… click to stop',
  thinking: 'Thinking…',
  speaking: 'Speaking… click to stop',
};

export function VoiceLoopButton({ state, onClick, disabled, reason }: VoiceLoopButtonProps) {
  const [showTooltip, setShowTooltip] = useState(false);

  const tooltipText =
    reason === 'not-enabled'
      ? 'Enable voice in Settings'
      : reason === 'no-backend'
        ? 'Speech backend not configured'
        : reason === 'streaming'
          ? 'Wait for response'
          : STATE_LABEL[state];

  const active = state !== 'idle';

  return (
    <div
      className="relative"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <button
        onClick={onClick}
        disabled={disabled}
        className="p-2 rounded-xl transition-all shrink-0"
        style={{
          background: active ? 'color-mix(in srgb, ' + STATE_COLOR[state] + ' 15%, transparent)' : 'transparent',
          color: disabled ? 'var(--color-text-tertiary)' : STATE_COLOR[state],
          cursor: disabled ? 'default' : 'pointer',
          opacity: disabled ? 0.35 : 1,
          animation: state === 'listening' || state === 'speaking' ? 'pulse 1.5s ease-in-out infinite' : 'none',
        }}
      >
        <Headphones size={16} />
      </button>
      {showTooltip && (
        <div
          className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2.5 py-1.5 rounded-lg text-xs whitespace-nowrap pointer-events-none"
          style={{
            background: 'var(--color-text)',
            color: 'var(--color-bg)',
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
          }}
        >
          {tooltipText}
        </div>
      )}
    </div>
  );
}
