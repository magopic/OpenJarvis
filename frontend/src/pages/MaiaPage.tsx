import { MaiaCore } from '../components/MaiaCore/MaiaCore';
import { CORE_BG } from '../components/MaiaCore/coreTheme';

// FASE 4O.4B: dedicated route for the MAIA Neural Core. Added as a new
// page rather than replacing the existing `/` (Chat) landing route --
// see the phase report's STEP 8 analysis for why a blind global
// routing change was deliberately avoided in V1.
export function MaiaPage() {
  return (
    <div className="flex flex-col h-full w-full overflow-y-auto p-6" style={{ background: CORE_BG }}>
      <MaiaCore height="45vh" />
    </div>
  );
}
