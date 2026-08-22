import { Info } from 'lucide-react';

// Honest by construction (STEP 12): only rendered when the backend's
// own `truncated` flag is true, and it never claims a specific missing
// count -- the Graph API doesn't report one, so this doesn't invent one.
export function TruncationBanner() {
  return (
    <div
      className="flex items-center gap-2 px-4 py-1.5 text-xs shrink-0"
      style={{
        background: 'color-mix(in srgb, var(--color-accent-amber) 10%, transparent)',
        borderBottom: '1px solid color-mix(in srgb, var(--color-accent-amber) 20%, transparent)',
        color: 'var(--color-accent-amber)',
        fontFamily: 'var(--font-hud)',
      }}
    >
      <Info size={13} />
      Showing a bounded portion of the graph. Refine filters or select a node to explore further.
    </div>
  );
}
