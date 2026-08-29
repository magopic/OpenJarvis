import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { Network } from 'lucide-react';

import { NeuralCoreScene } from './NeuralCoreScene';
import { CoreHUD } from './CoreHUD';
import { useMaiaVisualState, MAIA_STATE_CONFIG } from './coreState';
import { CORE_QUALITY, DEFAULT_QUALITY, type CoreQualityLevel } from './coreQuality';
import { CORE_BG, HUD_BORDER, HUD_PANEL_BG, HUD_TEXT_SECONDARY } from './coreTheme';

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return reduced;
}

export interface MaiaCoreProps {
  /** STEP 11: architecture only in V1 -- no automatic benchmark selects this yet. */
  quality?: CoreQualityLevel;
  /** Fixed pixel height for the core's own box; the host page controls layout. */
  height?: number | string;
  /** STEP 9: interaction point opening the existing Knowledge Graph -- normal navigation in V1, not yet a cinematic transition. */
  showExploreAction?: boolean;
}

// MAIA Neural Core (FASE 4O.4B) -- a presentation/runtime layer that
// visualizes MAIA's live assistant state. It is not the Knowledge
// Graph, not the Second Brain, not a new memory or data source: every
// particle/connection/ring here is decorative, and the only "real"
// data it reads (model name, tool count, assistant state) comes from
// already-existing frontend state/endpoints, never invented.
export function MaiaCore({ quality = DEFAULT_QUALITY, height = '42vh', showExploreAction = true }: MaiaCoreProps) {
  const visualState = useMaiaVisualState();
  const reducedMotion = usePrefersReducedMotion();
  const stateConfig = MAIA_STATE_CONFIG[visualState];
  const qualityConfig = CORE_QUALITY[quality];
  const navigate = useNavigate();

  return (
    <div
      className="relative w-full overflow-hidden rounded-xl"
      style={{ height, background: CORE_BG, border: `1px solid ${HUD_BORDER}` }}
    >
      <NeuralCoreScene quality={qualityConfig} stateConfig={stateConfig} reducedMotion={reducedMotion} />
      <CoreHUD visualState={visualState} />

      {showExploreAction && (
        <button
          onClick={() => navigate('/knowledge-graph')}
          className="absolute top-4 right-4 flex items-center gap-2 rounded-full px-3 py-1.5 text-xs pointer-events-auto"
          style={{
            background: HUD_PANEL_BG,
            border: `1px solid ${HUD_BORDER}`,
            color: HUD_TEXT_SECONDARY,
            backdropFilter: 'blur(8px)',
            fontFamily: "'IBM Plex Mono', 'SF Mono', ui-monospace, monospace",
          }}
        >
          <Network size={13} />
          Explore Knowledge
        </button>
      )}
    </div>
  );
}
