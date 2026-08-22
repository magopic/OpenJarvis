import { Loader2, Network, Lock, AlertTriangle } from 'lucide-react';
import { Button } from '../ui/button';

const wrapStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 12,
  height: '100%',
  width: '100%',
  color: 'var(--color-text-secondary)',
  fontFamily: 'var(--font-hud)',
  textAlign: 'center',
  padding: 24,
};

export function GraphLoadingState() {
  return (
    <div style={wrapStyle}>
      <Loader2 size={28} className="animate-spin" style={{ color: 'var(--color-accent)' }} />
      <span className="text-sm">Loading the knowledge graph…</span>
    </div>
  );
}

export function GraphEmptyState({ onRetry }: { onRetry?: () => void }) {
  return (
    <div style={wrapStyle}>
      <Network size={30} style={{ color: 'var(--color-text-tertiary)' }} />
      <div>
        <p className="text-sm" style={{ color: 'var(--color-text)' }}>
          Nothing to show yet.
        </p>
        <p className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)', maxWidth: 320 }}>
          No active memories match the current filters. Try including archived or superseded
          entries, or broaden the domain filter.
        </p>
      </div>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Refresh
        </Button>
      )}
    </div>
  );
}

export function GraphNoAccessState() {
  return (
    <div style={wrapStyle}>
      <Lock size={28} style={{ color: 'var(--color-text-tertiary)' }} />
      <div>
        <p className="text-sm" style={{ color: 'var(--color-text)' }}>
          Nothing available for your current identity.
        </p>
        <p className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)', maxWidth: 320 }}>
          This graph only ever shows memories you're authorized to see.
        </p>
      </div>
    </div>
  );
}

export function GraphErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={wrapStyle}>
      <AlertTriangle size={28} style={{ color: 'var(--color-error)' }} />
      <div>
        <p className="text-sm" style={{ color: 'var(--color-text)' }}>
          The knowledge graph couldn't be loaded.
        </p>
        <p className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)', maxWidth: 320 }}>
          {message}
        </p>
      </div>
      <Button size="sm" variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}
