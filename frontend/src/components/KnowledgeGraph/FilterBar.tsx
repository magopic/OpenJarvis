import { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { SlidersHorizontal } from 'lucide-react';

import type { GraphFilterParams, GraphEntryType } from '../../types/graph';

const ENTRY_TYPES: GraphEntryType[] = [
  'PROBLEM', 'HYPOTHESIS', 'DECISION', 'ACTION', 'OUTCOME', 'LESSON',
  'EVENT', 'OBSERVATION', 'PROCEDURE', 'MEETING_NOTE',
];

const TRUST_STATUSES = ['OBSERVED', 'HYPOTHESIS', 'VERIFIED', 'DECISION', 'OUTCOME', 'LEARNED'];
const RELATIONSHIP_STATUSES = ['CONFIRMED', 'PROPOSED', 'REJECTED'] as const;

interface FilterBarProps {
  filters: GraphFilterParams;
  availableDomains: string[];
  onChange: (filters: GraphFilterParams) => void;
}

const selectStyle: React.CSSProperties = {
  background: 'rgba(10, 18, 28, 0.6)',
  border: '1px solid rgba(120, 190, 220, 0.16)',
  color: '#cfe8f5',
  borderRadius: 6,
  padding: '4px 8px',
  fontSize: 11.5,
  fontFamily: "'IBM Plex Mono', 'SF Mono', ui-monospace, monospace",
};

const labelStyle: React.CSSProperties = {
  fontSize: 11,
  color: '#8fb4c7',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
};

function activeFilterCount(filters: GraphFilterParams): number {
  let n = 0;
  if (filters.domains?.length) n++;
  if (filters.entryTypes?.length) n++;
  if (filters.trustStatuses?.length) n++;
  if (filters.relationshipStatuses?.length) n++;
  if (filters.includeArchived) n++;
  if (filters.includeSuperseded) n++;
  return n;
}

// FASE 4O.4A: was a full-width, always-visible bar of six native
// selects ("feels like developer tooling" per user visual review) --
// now a compact floating toggle that expands into a small translucent
// control card, keeping the graph itself the dominant visual element
// (STEP 11). All the same filters remain, forwarded to the Graph API
// exactly as before -- nothing here changed what a filter *does*.
export function FilterBar({ filters, availableDomains, onChange }: FilterBarProps) {
  const [open, setOpen] = useState(false);
  const update = (partial: Partial<GraphFilterParams>) => onChange({ ...filters, ...partial });
  const activeCount = activeFilterCount(filters);

  return (
    <div className="absolute top-3 left-3 z-10">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-full px-3 py-1.5 text-xs"
        style={{
          background: 'rgba(10, 18, 28, 0.6)',
          border: '1px solid rgba(120, 190, 220, 0.18)',
          color: '#a9c7d9',
          backdropFilter: 'blur(8px)',
          fontFamily: "'IBM Plex Mono', 'SF Mono', ui-monospace, monospace",
        }}
      >
        <SlidersHorizontal size={13} />
        Filters
        {activeCount > 0 && (
          <span
            className="rounded-full flex items-center justify-center"
            style={{ width: 16, height: 16, fontSize: 9.5, background: 'rgba(34,211,238,0.22)', color: '#a5f3fc' }}
          >
            {activeCount}
          </span>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.15 }}
            className="mt-2 flex flex-wrap gap-2 rounded-lg p-3"
            style={{
              background: 'rgba(8, 14, 22, 0.75)',
              border: '1px solid rgba(120, 190, 220, 0.16)',
              backdropFilter: 'blur(12px)',
              maxWidth: 360,
            }}
          >
            <select
              style={selectStyle}
              value={filters.domains?.[0] ?? ''}
              onChange={(e) => update({ domains: e.target.value ? [e.target.value] : undefined })}
            >
              <option value="">All domains</option>
              {availableDomains.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>

            <select
              style={selectStyle}
              value={filters.entryTypes?.[0] ?? ''}
              onChange={(e) => update({ entryTypes: e.target.value ? [e.target.value] : undefined })}
            >
              <option value="">All entry types</option>
              {ENTRY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>

            <select
              style={selectStyle}
              value={filters.trustStatuses?.[0] ?? ''}
              onChange={(e) => update({ trustStatuses: e.target.value ? [e.target.value] : undefined })}
            >
              <option value="">All trust statuses</option>
              {TRUST_STATUSES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>

            <select
              style={selectStyle}
              value={filters.relationshipStatuses?.[0] ?? ''}
              onChange={(e) =>
                update({
                  relationshipStatuses: e.target.value ? [e.target.value as (typeof RELATIONSHIP_STATUSES)[number]] : undefined,
                })
              }
            >
              <option value="">Confirmed + proposed</option>
              {RELATIONSHIP_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.toLowerCase()} only
                </option>
              ))}
            </select>

            <label style={labelStyle}>
              <input
                type="checkbox"
                checked={!!filters.includeArchived}
                onChange={(e) => update({ includeArchived: e.target.checked })}
              />
              Include archived
            </label>

            <label style={labelStyle}>
              <input
                type="checkbox"
                checked={!!filters.includeSuperseded}
                onChange={(e) => update({ includeSuperseded: e.target.checked })}
              />
              Include superseded
            </label>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
