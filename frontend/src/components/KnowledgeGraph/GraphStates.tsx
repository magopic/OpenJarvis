import { Loader2, Network, Lock, AlertTriangle } from 'lucide-react';
import { Button } from '../ui/button';

// FASE 4O.4A: hardcoded to the MAIA graph palette rather than the
// app's `--color-*` variables -- these states render inside the
// always-dark graph workspace (`graphTheme.ts`), which is independent
// of the surrounding app's light/dark theme setting.
const wrapStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 12,
  height: '100%',
  width: '100%',
  color: '#8fb4c7',
  fontFamily: "'IBM Plex Mono', 'SF Mono', ui-monospace, monospace",
  textAlign: 'center',
  padding: 24,
};

export function GraphLoadingState() {
  return (
    <div style={wrapStyle}>
      <Loader2 size={26} className="animate-spin" style={{ color: '#22d3ee' }} />
      <span className="text-sm">Loading the knowledge graph…</span>
    </div>
  );
}

export function GraphEmptyState({ onRetry }: { onRetry?: () => void }) {
  return (
    <div style={wrapStyle}>
      <Network size={28} style={{ color: '#4a6172', opacity: 0.7 }} />
      <div>
        <p className="text-sm" style={{ color: '#dfeef7' }}>
          Nothing to show yet.
        </p>
        <p className="text-xs mt-1" style={{ color: '#5c7c8f', maxWidth: 320 }}>
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
      <Lock size={26} style={{ color: '#4a6172' }} />
      <div>
        <p className="text-sm" style={{ color: '#dfeef7' }}>
          Nothing available for your current identity.
        </p>
        <p className="text-xs mt-1" style={{ color: '#5c7c8f', maxWidth: 320 }}>
          This graph only ever shows memories you're authorized to see.
        </p>
      </div>
    </div>
  );
}

export function GraphErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={wrapStyle}>
      <AlertTriangle size={26} style={{ color: '#e8917f' }} />
      <div>
        <p className="text-sm" style={{ color: '#dfeef7' }}>
          The knowledge graph couldn't be loaded.
        </p>
        <p className="text-xs mt-1" style={{ color: '#5c7c8f', maxWidth: 320 }}>
          {message}
        </p>
      </div>
      <Button size="sm" variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}
