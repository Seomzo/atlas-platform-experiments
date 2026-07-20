# WS-15 Handoff — Cortex whole-brain Starmap

## Status

- Branch: `codex/ws-15-cortex-graph-rehaul`
- Draft PR: None (this workstream must not open one)
- Baseline: `80c2a7f34dbf775b638bba4d7c75217c8a4fd5df` (`origin/main` when work began)
- Last validated: 2026-07-20

## Goal and scope

Made the Atlas Starmap truthfully project and render the complete Cortex graph:

- type-stratified overview selection prevents high-volume evidence from displacing the smaller memory, session, document, and community layers;
- deterministic dream-cycle maintenance derives communities from the stored entity/relation graph;
- all six Cortex node types retain their identity through the Desktop adapter and receive distinct renderer treatments;
- the Starmap has an interactive type legend, visibility filters, and a profile-backed brain selector;
- empty worker brains render as empty Cortex graphs rather than failures;
- English, Simplified Chinese, Japanese, and Traditional Chinese cover the new controls and empty states.

The live `~/.atlas/cortex/cortex.db` was inspected only through the supplied diagnosis. This workstream did not open or mutate it. All implementation verification used fresh SQLite stores under test temporary directories.

## Decisions made

### Recency drowning: confirmed projection bug

`build_graph_overview()` previously merged every requested node type and then sorted primarily by `_sort_at`. The type priority was the second sort key, so it could only break equal timestamps and could not stop newer evidence rows from filling the bounded page.

The replacement builds one deterministic order with:

1. one newest node from every populated requested type;
2. bounded working sets for memory, session, community, document, and entity layers, with memory's first-page allowance explicitly covering the required `N_memory <= 50` invariant;
3. a globally recency-sorted residual, where evidence receives no reserved bulk quota.

The existing opaque offset cursor remains unchanged. The stratified order is deterministic and independent of the requested page size; fixture pagination verifies no duplicate IDs while walking the full result.

### Communities: genuinely missing from the dream pipeline

Before WS-15, the daily `dream_cycle` was intentionally a no-model recovery checkpoint and performed no graph mutation. The only community writer was GraphRAG publication. Personal entities and relations therefore had no community-building path at all; this was not a disabled trigger around an existing personal-community algorithm.

`dream` and `dream_cycle` now rebuild derived communities without invoking a model. The algorithm is deliberately minimal:

- operate per knowledge space;
- treat active directed relations as undirected connectivity for grouping;
- include isolated entities as singleton connected components;
- derive stable IDs from brain, algorithm version, space, and sorted members;
- replace only rows owned by `cortex:connected-components:v1`;
- preserve GraphRAG-authored communities and skip spaces with an active `graphrag:*` community projection.

This is structural maintenance over already-persisted truth. It does not authorize semantic consolidation or create entities, relations, or memories. Only `session_distill` remains model-backed.

### GraphRAG documents: implemented, enabled, but intentionally input-driven

GraphRAG ingestion is not missing and was not broken by the config gate:

- `hermes_cli/config.py` and `CortexConfig` default `cortex.graphrag.enabled` to `true`;
- `GraphRAGIndexManager` already validates, stages, publishes, attests, rolls back, and projects documents, entities, relations, and artifact-supplied communities;
- the CLI already exposes `atlas cortex index import <path> [--publish]`, `publish`, `rollback`, and `list`.

The zero document/index counts mean no approved artifact has been explicitly imported and published. Atlas deliberately does not build Microsoft GraphRAG during a conversation and ships no bundled/live Tekion corpus. Ingestion requires an immutable artifact directory inside the selected profile's configured `cortex.graphrag.index_root` (default `cortex/graphrag`), a bounded/hash-verified `manifest.json`, a supported portable JSON/JSONL or Microsoft GraphRAG format, and at least a documents or text-units role. Managed mode also requires the configured trusted signature. The operator then runs:

```text
atlas cortex index import <artifact-path> --publish
```

No automatic ingestion trigger or new subsystem was added in WS-15.

### Per-worker brains: backend worked; Desktop selection was missing

The authenticated `/api/cognitive/*` routes already accepted `?profile=` and resolved the store inside `_profile_scope`. The Desktop helpers instead always attached the active chat profile as Electron routing metadata, and the Starmap exposed no independent brain choice.

Explicit Cortex brain reads now stay on the primary backend process and append the selected profile as an encoded endpoint query. Calls that omit an explicit brain retain the old active-profile routing behavior. The Starmap store owns a separate `default` brain selection, invalidates stale in-flight writes when it changes, and passes that selection through graph, health, detail, and maintenance-job calls. Named brains never fall back to the unrelated legacy learning graph.

## Deferred decisions

Cross-brain merging and shared-space federation remain deliberately out of scope. A future engine design must decide:

- whether identities that appear in several profile brains are duplicates, aliases, or independent claims;
- which brain owns a shared knowledge space and how authorization is enforced across local and remote profiles;
- whether cross-brain edges are copied, referenced, or computed, and how provenance/deletion propagates;
- how conflicting memories, temporal validity, community IDs, and GraphRAG versions reconcile;
- whether the Starmap brain selection should persist across launches or continue to open on the default brain;
- whether maintenance actions should remain available for a viewed worker brain or become read-only outside that worker's active context;
- how a Cortex-disabled profile should be represented separately from a valid but empty brain.

## Files changed

Backend and tests:

- `altas/cortex/graph.py`
- `altas/cortex/dream.py`
- `tests/altas/test_cortex_graph.py`
- `tests/altas/test_cortex_dream.py`

Desktop Starmap and shared contracts:

- `apps/desktop/src/app/starmap/constants.ts`
- `apps/desktop/src/app/starmap/cortex-detail.tsx`
- `apps/desktop/src/app/starmap/cortex-workspace.tsx`
- `apps/desktop/src/app/starmap/cortex.test.ts`
- `apps/desktop/src/app/starmap/cortex.ts`
- `apps/desktop/src/app/starmap/geometry.ts`
- `apps/desktop/src/app/starmap/index.tsx`
- `apps/desktop/src/app/starmap/render.ts`
- `apps/desktop/src/app/starmap/share-code.ts`
- `apps/desktop/src/app/starmap/star-map.tsx`
- `apps/desktop/src/app/starmap/types.ts`
- `apps/desktop/src/hermes.ts`
- `apps/desktop/src/hermes-profile-scope.test.ts`
- `apps/desktop/src/store/starmap.ts`
- `apps/desktop/src/store/starmap.test.ts`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`

Handoff:

- `docs/altas/workstreams/WS-15-HANDOFF.md`

`altas/cortex/graphrag.py` and `hermes_cli/web_server.py` were investigated but did not require changes: their import/publish and profile-routing contracts were already correct.

## Contracts and migrations

- No SQLite schema or data migration.
- New deterministic community owner/version: `cortex:connected-components:v1`.
- `StarmapNode.kind` now permits the six `CortexNodeType` values in addition to legacy `skill`; legacy share codes still serialize only their existing `memory | skill` vocabulary and Cortex graphs remain non-shareable.
- Explicit Cortex fetches accept a brain profile and encode it as the existing `?profile=` HTTP selector. No new endpoint was added.
- Empty initialized Cortex stores continue returning HTTP 200 with `nodes: []`.
- The graph overview remains body-free; raw evidence and GraphRAG document text are still detail-only.

## Validation

Passed:

- `scripts/run_tests.sh tests/altas/test_cortex_graph.py tests/altas/test_cortex_dream.py` — 2 files, 67 tests passed.
- `npx vitest run --environment jsdom --config apps/desktop/vite.config.ts apps/desktop/src/app/starmap` — 4 files, 26 tests passed.
- `npx vitest run --environment jsdom --config apps/desktop/vite.config.ts apps/desktop/src/store/starmap.test.ts apps/desktop/src/hermes-profile-scope.test.ts apps/desktop/src/i18n` — 5 files, 32 tests passed.
- `npx tsc --noEmit -p apps/desktop`.
- focused Desktop ESLint over every touched TypeScript/TSX file.
- `git diff --check`.

The cross-stack proof is covered by paired real-contract tests: the Python HTTP test constructs a temporary Cortex store containing entity, memory, evidence, session, document, and community rows and asserts `/api/cognitive/graph?profile=...` returns all six types; the Desktop adapter test feeds the same six-type DTO vocabulary through `cortexToStarmap()` and asserts every renderer `kind` equals its Cortex type and none equals `skill`.

## Security and privacy checks

- The live Cortex database was never opened for writes.
- All backend fixtures use isolated temporary profile directories and real `CortexStore` initialization.
- The overview projection still excludes evidence/document bodies and the existing response-model guards remain green.
- Brain selection uses validated existing profile names through the authenticated Desktop API bridge; it does not accept or expose a client-supplied brain ID.
- Community reports contain only already-sanitized canonical entity names and membership IDs. GraphRAG-authored rows are not overwritten.
- No new model tool, environment variable, external request, telemetry, or cross-profile mutation channel was introduced.

## Visual evidence

The renderer contract test verifies six unique colors, six unique shapes, and six unique base radii. The implemented dark-blue workspace uses:

- entity — cyan circle;
- memory — amber diamond;
- evidence — violet triangle;
- session — blue hexagon;
- document — seafoam square;
- community — coral star.

The same glyph vocabulary appears in the canvas and interactive type legend. No screenshot or generated build artifact was committed.

## Known risks and limitations

- Connected components are intentionally a baseline grouping, not semantic community detection. A highly connected space may produce one large community; an unconnected space produces singleton communities.
- Community rebuilds occur on scheduled/manual deterministic dream markers, not on every entity/relation write.
- Type guarantees are bounded working-set guarantees, not a promise that an arbitrarily large durable layer fits in one 500-node response. Pagination remains the path to the remainder.
- GraphRAG stays empty until an operator supplies and publishes an approved artifact.
- Profile discovery is loaded when the Starmap opens; profiles created while it remains open appear after reopening it.
- A profile with Cortex disabled is an explicit error. A profile with Cortex enabled but no rows is the honest empty-brain state.
- The legacy learning-graph fallback exists only for the default brain on older backends; named brain selection requires the native Cortex endpoints.

## Integration order and conflicts

The largest likely conflict surfaces are shared Desktop contracts and locale objects:

- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/hermes.ts`
- `apps/desktop/src/store/starmap.ts`
- `apps/desktop/src/i18n/{types,en,zh,ja,zh-hant}.ts`

Preserve the `CortexNodeType` flow into `StarmapNode.kind`, the explicit-query/implicit-routing distinction in `cortexRequest()`, and `$starmapBrainProfile` request-epoch invalidation when resolving conflicts. No channels, profiles UI implementation, Electron update code, or gateway platform files changed.

## Exact next actions

1. Merge WS-15 after resolving shared Desktop locale/type append points.
2. Run one manual dream marker on a disposable copy of a representative Cortex database and inspect component sizes before choosing any more sophisticated community algorithm.
3. Supply and publish an approved GraphRAG artifact if product owners expect document/index nodes; do not treat the enabled config flag as ingestion.
4. Specify the cross-brain identity, authorization, provenance, deletion, and conflict model before implementing any merged/federated Starmap view.
5. Decide whether selected-brain maintenance should remain writable; make it explicitly read-only if remote worker authorization cannot guarantee safe cross-profile mutation.
