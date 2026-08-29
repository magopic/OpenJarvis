import { useMemo } from 'react';
import { motion, AnimatePresence } from 'motion/react';
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

// FASE 4O.4A: redesigned as a lightweight, translucent floating card
// that reads as part of the 3D environment rather than a docked
// application sidebar -- it appears/disappears with a short fade+slide
// and never shows anything beyond real, already-loaded graph data.
export function DetailPanel({ graph, selectedNodeId, onClose, onShowExperience }: DetailPanelProps) {
  const node = graph.nodes.find((n) => n.id === selectedNodeId);
  const connectedEdges = useMemo(
    () => graph.edges.filter((e) => e.source === selectedNodeId || e.target === selectedNodeId),
    [graph.edges, selectedNodeId],
  );

  return (
    <AnimatePresence>
      {node && (
        <motion.aside
          key={node.id}
          initial={{ opacity: 0, x: 16 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 16 }}
          transition={{ duration: 0.18, ease: 'easeOut' }}
          className="absolute top-3 right-3 bottom-3 w-[300px] overflow-y-auto rounded-xl"
          style={{
            background: 'rgba(8, 14, 22, 0.68)',
            border: '1px solid rgba(120, 190, 220, 0.16)',
            boxShadow: '0 8px 40px -12px rgba(0,0,0,0.7), 0 0 0 1px rgba(34,211,238,0.04)',
            backdropFilter: 'blur(14px)',
            color: '#dfeef7',
            fontFamily: "'IBM Plex Mono', 'SF Mono', ui-monospace, monospace",
          }}
        >
          <div
            className="flex items-center justify-between px-4 py-3"
            style={{ borderBottom: '1px solid rgba(120, 190, 220, 0.1)' }}
          >
            <span className="text-[10px] uppercase tracking-widest" style={{ color: '#7fa3ba' }}>
              {node.kind}
            </span>
            <button onClick={onClose} aria-label="Close detail panel" style={{ color: '#7fa3ba' }}>
              <X size={15} />
            </button>
          </div>

          <div className="px-4 py-4 flex flex-col gap-4">
            <div>
              <h2 className="text-base leading-snug" style={{ fontFamily: "'Chakra Petch', 'Space Grotesk', system-ui, sans-serif", color: '#f2fbff' }}>
                {node.label}
              </h2>
              {node.derived && (
                <p className="text-xs mt-1" style={{ color: '#6c8ea3' }}>
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
                <Field
                  label="Connected entries in view"
                  value={String(
                    new Set(
                      connectedEdges
                        .filter((e) => e.kind === 'NAVIGATION')
                        .map((e) => (e.target === selectedNodeId ? e.source : e.target)),
                    ).size,
                  )}
                />
              </dl>
            )}

            {connectedEdges.filter((e) => e.kind !== 'NAVIGATION').length > 0 && (
              <div>
                <h3 className="text-[10px] uppercase tracking-widest mb-2" style={{ color: '#7fa3ba' }}>
                  Relationships
                </h3>
                <ul className="flex flex-col gap-1.5 text-xs">
                  {connectedEdges
                    .filter((e) => e.kind !== 'NAVIGATION')
                    .map((edge) => {
                      const otherId = edge.source === selectedNodeId ? edge.target : edge.source;
                      const other = graph.nodes.find((n) => n.id === otherId);
                      const direction = edge.source === selectedNodeId ? '→' : '←';
                      return (
                        <li key={edge.id} style={{ color: '#b9d4e3' }}>
                          <span style={{ color: edge.status === 'CONFIRMED' ? '#a5f3fc' : '#5fb8cc', opacity: edge.status === 'CONFIRMED' ? 1 : 0.7 }}>
                            {edge.status === 'CONFIRMED' ? '● ' : '○ '}
                          </span>
                          {direction} {edge.basis} — {other?.label ?? otherId}
                          {edge.status && <span style={{ color: '#5c7c8f' }}> ({edge.status.toLowerCase()})</span>}
                        </li>
                      );
                    })}
                </ul>
              </div>
            )}

            {node.kind === 'ENTRY' && node.entry_type && EXPERIENCE_TYPES.has(node.entry_type) && (
              <Button size="sm" variant="secondary" className="gap-2 mt-1" onClick={() => onShowExperience(node.id)}>
                <GitBranch size={14} />
                Show Experience
              </Button>
            )}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt style={{ color: '#5c7c8f' }}>{label}</dt>
      {/* Never invent filler prose for a missing field -- show it plainly as absent. */}
      <dd style={{ color: value ? '#dfeef7' : '#4a6172' }}>{value ?? 'not recorded'}</dd>
    </div>
  );
}
