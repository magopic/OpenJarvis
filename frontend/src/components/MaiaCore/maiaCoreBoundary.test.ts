import { describe, expect, it } from 'vitest';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

// FASE 4O.4B STEP 14: "no accidental Knowledge Graph mutation" +
// route/navigation wiring. This codebase has no @testing-library/react
// dependency (every existing test exercises store/logic functions or,
// here, source structure directly), so these are static-source checks
// rather than a rendered-DOM assertion -- deliberately simple and
// exact for what they need to prove.
const here = dirname(fileURLToPath(import.meta.url));
const src = (relativeToSrc: string) => join(here, '..', '..', relativeToSrc);

function read(path: string): string {
  return readFileSync(path, 'utf-8');
}

describe('MAIA Neural Core / Knowledge Graph boundary', () => {
  const maiaCoreFiles = [
    'MaiaCore.tsx',
    'NeuralCoreScene.tsx',
    'CoreParticles.tsx',
    'CoreConnections.tsx',
    'CoreRings.tsx',
    'CoreHUD.tsx',
    'coreState.ts',
    'coreTheme.ts',
    'coreAnimation.ts',
    'coreQuality.ts',
  ].map((f) => join(here, f));

  it('never imports the Knowledge Graph store, API client, or graph truth types (no coupling to Graph truth)', () => {
    // Deliberately checked as import-statement patterns, not bare
    // substrings -- a comment may legitimately *mention* graph-related
    // filenames for attribution/precedent without that being a real
    // coupling; an actual `from '...'` reference would be.
    const forbiddenImportSources = ['useGraphStore', '/graphApi', '/graphLayout', 'mergeGraphResponses', "from '../../types/graph'"];
    for (const file of maiaCoreFiles) {
      const contents = read(file);
      const importLines = contents.split('\n').filter((line) => line.trim().startsWith('import'));
      for (const term of forbiddenImportSources) {
        const hit = importLines.some((line) => line.includes(term));
        expect(hit, `${file} unexpectedly imports something matching ${term}`).toBe(false);
      }
    }
  });

  it('only reuses the two explicitly-generic KnowledgeGraph utilities (CanvasSizeSync, AmbientField), nothing graph-specific', () => {
    const sceneSource = read(join(here, 'NeuralCoreScene.tsx'));
    const kgImports = [...sceneSource.matchAll(/from '\.\.\/KnowledgeGraph\/(\w+)'/g)].map((m) => m[1]);
    expect(new Set(kgImports)).toEqual(new Set(['CanvasSizeSync', 'AmbientField']));
  });
});

describe('routing / navigation wiring (STEP 8)', () => {
  it('registers a /maia route without removing any existing route', () => {
    const appSource = read(src('App.tsx'));
    const requiredPaths = ['index element', 'dashboard', 'settings', 'get-started', 'data-sources', 'knowledge-graph', 'agents', 'logs', 'maia'];
    for (const p of requiredPaths) {
      expect(appSource.includes(p), `App.tsx is missing route fragment: ${p}`).toBe(true);
    }
  });

  it('adds a MAIA sidebar entry without removing any existing nav item', () => {
    const sidebarSource = read(src('components/Sidebar/Sidebar.tsx'));
    const requiredLabels = ['MAIA', 'Chat', 'Dashboard', 'Data Sources', 'Knowledge Graph', 'Agents', 'Logs', 'Settings', 'Get Started'];
    for (const label of requiredLabels) {
      expect(sidebarSource.includes(`label: '${label}'`), `Sidebar.tsx is missing nav item: ${label}`).toBe(true);
    }
  });
});
