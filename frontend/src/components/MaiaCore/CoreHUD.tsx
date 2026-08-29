import { useEffect, useState } from 'react';
import { useAppStore } from '../../lib/store';
import { fetchAvailableTools } from '../../lib/api';
import type { MaiaVisualState } from './coreState';
import { HUD_BORDER, HUD_PANEL_BG, HUD_TEXT_PRIMARY, HUD_TEXT_SECONDARY, HUD_TEXT_TERTIARY } from './coreTheme';

const STATE_LABEL: Record<MaiaVisualState, string> = {
  IDLE: 'IDLE',
  LISTENING: 'LISTENING',
  THINKING: 'THINKING',
  SPEAKING: 'SPEAKING',
  TOOL_ACTIVE: 'TOOL ACTIVE',
  ERROR: 'UNREACHABLE',
};

// FASE 4O.4B STEP 7: minimal, real peripheral data only.
//
// "SECOND BRAIN" and "OPS" status lines were deliberately left out --
// there is no dedicated readiness signal for either surfaced to the
// frontend today. `getMemoryStats()` exists but reports the generic
// legacy memory backend, not the frozen Second Brain (a different
// subsystem entirely); labeling that "SECOND BRAIN: READY" would be
// incorrect, not just imprecise. No OPS-connectivity endpoint exists
// in the frontend either. Per this phase's own instruction ("se un
// dato non è disponibile, meglio non mostrarlo"), both are omitted
// rather than shown with invented or mislabeled data. TOOLS and MODEL
// below are genuinely real, fetched from existing endpoints with zero
// backend changes.
function useCoreHudData() {
  const [toolCount, setToolCount] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchAvailableTools()
      .then((tools) => {
        if (!cancelled) setToolCount(tools.length);
      })
      .catch(() => {
        if (!cancelled) setToolCount(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return { toolCount };
}

const labelStyle: React.CSSProperties = {
  fontSize: 9,
  letterSpacing: '0.12em',
  color: HUD_TEXT_TERTIARY,
  textTransform: 'uppercase',
};

const valueStyle: React.CSSProperties = {
  fontSize: 12,
  color: HUD_TEXT_PRIMARY,
};

function HudField({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span style={labelStyle}>{label}</span>
      <span style={valueStyle}>{value}</span>
    </div>
  );
}

interface CoreHUDProps {
  visualState: MaiaVisualState;
}

export function CoreHUD({ visualState }: CoreHUDProps) {
  const { toolCount } = useCoreHudData();
  const selectedModel = useAppStore((s) => s.selectedModel);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const model = selectedModel || serverInfo?.model || null;

  return (
    <div
      className="absolute inset-0 pointer-events-none"
      style={{ fontFamily: "'IBM Plex Mono', 'SF Mono', ui-monospace, monospace" }}
    >
      <div className="absolute top-4 left-4">
        <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: '0.14em', color: HUD_TEXT_PRIMARY }}>MAIA</div>
        <div style={{ ...labelStyle, marginTop: 2 }}>
          {visualState === 'ERROR' ? 'SYSTEM UNREACHABLE' : 'SYSTEM READY'}
        </div>
      </div>

      <div
        className="absolute bottom-4 left-4 flex gap-6 rounded-lg px-4 py-2.5"
        style={{ background: HUD_PANEL_BG, border: `1px solid ${HUD_BORDER}`, backdropFilter: 'blur(10px)' }}
      >
        {model && <HudField label="Model" value={model} />}
        {toolCount != null && <HudField label="Tools" value={String(toolCount)} />}
        <HudField label="State" value={STATE_LABEL[visualState]} />
      </div>

      <div className="absolute bottom-4 right-4" style={{ ...labelStyle, color: HUD_TEXT_SECONDARY }}>
        NEURAL CORE V1
      </div>
    </div>
  );
}
