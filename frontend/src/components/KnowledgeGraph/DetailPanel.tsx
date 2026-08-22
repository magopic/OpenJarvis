import { useMemo } from 'react';
import { X, GitBranch } from 'lucide-react';

import type { GraphResponse } from '../../types/graph';
import { Button } from '../ui/button';

interface DetailPanelProps {
  graph: GraphResponse;
  selectedNodeId: string;
  onClose: () => void;
  onShowExperience: (anchorEntryId: string) => void;
}

const EXPERIENCE_TYPES = new Set(['PROBLEM', 'HYPOTHESIS', 'DECISION', 'ACTION', 'OUTCOME', 'LESSON']);

function formatDate(ts: number | null): string | null {
  if (ts == null) return null;
  return new Date(ts * 1000).toLocaleString();
}

export function DetailPanel({ graph, selectedNodeId, onClose, onShowExperience }: DetailPanelProps) {
  const node = graph.nodes.find((n) => n.id === selectedNodeId);
  const connectedEdges = useMemo(
    () => graph.edges.filter((e) => e.source === selectedNodeId || e.target === selectedNodeId),
    [graph.edges, selectedNodeId],
  );

  if (!node) return null;

  const relationshipEdges = connectedEdges.filter((e) => e.kind !== 'NAVIGATION');
  const navigationEdges = connectedEdges.filter((e) => e.kind === 'NAVIGATION');
  const connectedEntryCount = new Set(
    navigationEdges.map((e) => (e.target === selectedNodeId ? e.source : e.target)),
  ).size;

  return (
    <aside
      className="flex flex-col shrink-0 w-[320px] h-full overflow-y-auto"
      style={{
        borderLeft: '1px solid var(--color-border)',
        background: 'color-mix(in srgb, var(--color-bg-secondary) 88%, transparent)',
        backdropFilter: 'blur(8px)',
        color: 'var(--color-text)',
        fontFamily: 'var(--font-hud)',
      }}
    >
      <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
        <span className="text-xs uppercase tracking-wider" style={{ color: 'var(--color-text-tertiary)' }}>
          {node.kind}
        </span>
        <button onClick={onClose} aria-label="Close detail panel" style={{ color: 'var(--color-text-secondary)' }}>
          <X size={16} />
        </button>
      </div>

      <div className="px-4 py-4 flex flex-col gap-4">
        <div>
          <h2 className="text-base leading-snug" style={{ fontFamily: 'var(--font-display)', color: 'var(--color-text)' }}>
            {node.label}
          </h2>
          {node.derived && (
            <p className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)' }}>
              Derived navigation node — not a stored memory itself.
            </p>
          )}
        </div>

        {node.kind === 'ENTRY' && (
          <dl className="text-xs flex flex-col gap-2">
            <Field label="Type" value={node.entry_type} />
            <Field label="Trust status" value={node.trust_status} />
            <Field label="Lifecycle" value={node.lifecycle} />
            <Field label="Visibility" value={node.visibility} />
            <Field label="Domains" value={node.domains.length ? node.domains.join(', ') : null} />
            <Field label="Entities" value={node.entities.length ? node.entities.join(', ') : null} />
            <Field label="Created" value={formatDate(node.created_at)} />
          </dl>
        )}

        {node.kind !== 'ENTRY' && (
          <dl className="text-xs flex flex-col gap-2">
            <Field label="Label" value={node.label} />
            <Field label="Derived" value={node.derived ? 'yes' : 'no'} />
            <Field label="Connected entries in view" value={String(connectedEntryCount)} />
          </dl>
        )}

        {relationshipEdges.length > 0 && (
          <div>
            <h3 className="text-xs uppercase tracking-wider mb-2" style={{ color: 'var(--color-text-tertiary)' }}>
              Relationships
            </h3>
            <ul className="flex flex-col gap-1.5 text-xs">
              {relationshipEdges.map((edge) => {
                const otherId = edge.source === selectedNodeId ? edge.target : edge.source;
                const other = graph.nodes.find((n) => n.id === otherId);
                const direction = edge.source === selectedNodeId ? '→' : '←';
                return (
                  <li key={edge.id} style={{ color: 'var(--color-text-secondary)' }}>
                    <span style={{ color: edge.status === 'CONFIRMED' ? 'var(--color-accent)' : 'var(--color-accent-amber)' }}>
                      {edge.status === 'CONFIRMED' ? '● ' : '○ '}
                    </span>
                    {direction} {edge.basis} — {other?.label ?? otherId}
                    {edge.status && (
                      <span style={{ color: 'var(--color-text-tertiary)' }}> ({edge.status.toLowerCase()})</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {node.kind === 'ENTRY' && node.entry_type && EXPERIENCE_TYPES.has(node.entry_type) && (
          <Button
            size="sm"
            variant="secondary"
            className="gap-2 mt-1"
            onClick={() => onShowExperience(node.id)}
          >
            <GitBranch size={14} />
            Show Experience
          </Button>
        )}
      </div>
    </aside>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt style={{ color: 'var(--color-text-tertiary)' }}>{label}</dt>
      {/* Never invent filler prose for a missing field -- show it plainly as absent. */}
      <dd style={{ color: value ? 'var(--color-text)' : 'var(--color-text-tertiary)' }}>
        {value ?? 'not recorded'}
      </dd>
    </div>
  );
}
