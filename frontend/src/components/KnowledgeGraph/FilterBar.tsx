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
  background: 'var(--color-input-bg)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-text)',
  borderRadius: 6,
  padding: '4px 8px',
  fontSize: 12,
  fontFamily: 'var(--font-hud)',
};

const labelStyle: React.CSSProperties = {
  fontSize: 11,
  color: 'var(--color-text-tertiary)',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
};

export function FilterBar({ filters, availableDomains, onChange }: FilterBarProps) {
  const update = (partial: Partial<GraphFilterParams>) => onChange({ ...filters, ...partial });

  return (
    <div
      className="flex flex-wrap items-center gap-3 px-4 py-2"
      style={{ borderBottom: '1px solid var(--color-border-subtle)', background: 'color-mix(in srgb, var(--color-bg-secondary) 70%, transparent)' }}
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
        <option value="">Confirmed + proposed relationships</option>
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
    </div>
  );
}
