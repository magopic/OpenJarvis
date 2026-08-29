import { Info } from 'lucide-react';

// Honest by construction (STEP 12): only rendered when the backend's
// own `truncated` flag is true, and it never claims a specific missing
// count -- the Graph API doesn't report one, so this doesn't invent one.
// FASE 4O.4A: hardcoded to the MAIA graph palette (not `--color-*`) and
// kept deliberately slim -- a minimal disclosure, not a HUD element.
export function TruncationBanner() {
  return (
    <div
      className="flex items-center gap-2 px-4 py-1 text-[11px] shrink-0"
      style={{
        background: 'rgba(217, 178, 106, 0.06)',
        borderBottom: '1px solid rgba(217, 178, 106, 0.14)',
        color: '#d9b26a',
        fontFamily: "'IBM Plex Mono', 'SF Mono', ui-monospace, monospace",
      }}
    >
      <Info size={12} />
      Showing a bounded portion of the graph. Refine filters or select a node to explore further.
    </div>
  );
}
