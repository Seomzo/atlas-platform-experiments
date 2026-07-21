# WS-17 Handoff — Unbounded Starmap LOD

## Status

- Branch: `codex/ws-17-starmap-lod`
- PR: None (this workstream must not open one)
- Baseline: `bcaa8aa859afc1bbd3af58d060b790437df44c40`
- Builds on: WS-15 stratified Cortex projection and WS-16 brain constellation
- Last validated: 2026-07-21

## Goal and scope

Removed the Starmap's fixed 420-node constellation budget and 500-node isolated-brain ceiling. The page-size clamp remains 500, but it is now only a per-request safety bound: detail resolution walks the existing cursor contract and the backend no longer rejects cursor offsets above 10,000.

The Starmap now has two data-backed levels of detail:

- aggregate tier: a bounded sky of the largest real communities plus one aggregate ring-star for each non-community node type with uncommunitied nodes;
- detail tier: selected or zoomed aggregates progressively resolve into their real nodes through cursor pagination.

No new renderer or visualization dependency was introduced. The existing canvas renderer and D3 simulation were extended because they already own Cortex shapes, time layout, selection, filters, and constellation partitions.

No channel, profile-management, Electron, gateway-platform, model-tool, configuration, or storage-migration surface changed.

## LOD design

### Aggregate tier

The first paint requests one `projection=communities` graph page with a limit of 24. Community rows are ordered by exact `member_count`, largest first. Each visible community becomes one aggregate star whose label, domain, ID, and count all come from the public graph response.

The graph response also carries exact active counts for the six Cortex graph node types and exact active relation count. For each non-community type, its `uncommunitied_count` becomes one type aggregate when nonzero. Community and type aggregate radii are monotonic and unbounded:

```text
aggregate_radius = kind_base + log10(count + 1) * 2.1
```

No sampled detail node or invented edge enters this tier. Aggregate composition produces no knowledge edges. The WS-16 ownership records remain separate from the knowledge graph.

### Detail tier

Detail begins at 1.8 times the fitted Cortex camera zoom. Zooming further while pointing at an aggregate, selecting an aggregate on canvas, or selecting it in the side list requests up to two pages of 160 nodes. Repeating the action continues from the returned cursor until the region is complete.

- Community resolution filters the graph to the selected community's stored member IDs.
- Type resolution filters to one node type and excludes nodes that belong to any active community.
- Resolved real nodes replace the corresponding portion of the aggregate count.
- A residual aggregate remains with the exact unresolved count until every member has been fetched.
- Native edges are kept only when both real endpoints are present, preserving the honest-edges rule.
- Aggregate nodes never open a fabricated Cortex detail panel; only resolved nodes do.

The existing query, type, and domain filters operate over resolved real nodes. Exact aggregate type totals continue to drive the legend and header while detail is only partially loaded.

### Growth signal

Constellation region radius now uses each brain's exact graph total instead of the number of rendered canvas elements:

```text
main_radius   = 118 + sqrt(total_nodes) * 0.62
worker_radius =  72 + sqrt(total_nodes) * 0.50
```

Both are monotonic and have no maximum. Worker-ring spacing expands from the largest real brain radius so growing regions do not retain the old fixed 118/72 footprint. Identity anchors and constellation status badges display exact total-node counts. In the isolated view, aggregate radius/glow and visible count labels provide the same count-backed growth signal.

## Fetch and cache policy

- Initial isolated paint: one 24-community graph page plus the existing health request; no detail cursor is followed.
- Initial constellation paint: one 24-community graph page per real profile; no detail cursor is followed.
- Detail action: at most two 160-node pages for one region.
- Backend request clamp: unchanged at 500 nodes per page.
- Cursor depth: unbounded by the former 10,000-offset guard.
- Cache key: `brain profile + aggregate region key` for the Desktop process session.
- Cache lifetime: preserved while moving between constellation and isolated brains; cleared by Starmap reset.
- Concurrent requests for the same brain/region share one in-flight promise.
- Cached pages are merged by real node/edge ID, so revisiting a region does not refetch completed pages.

Resolved nodes outside the camera plus 80 screen pixels are culled by the renderer, frozen at their last coordinates, and removed from D3's active node and link arrays. Re-entering the viewport reactivates them. This keeps force work proportional to visible resolved detail rather than total cached detail.

## Backend/API changes

`GET /api/cognitive/graph` keeps the existing redacted node projection and adds:

```json
{
  "aggregates": {
    "relation_count": 100000,
    "total_nodes": 50000,
    "types": [
      { "type": "evidence", "count": 25000, "uncommunitied_count": 1842 }
    ]
  }
}
```

The counts are produced by a fixed set of SQL `COUNT` queries over active rows and active community membership. They select no evidence body, document body, memory text, or other source content.

New optional graph query parameters:

- `community=<community-id>` — return nodes whose IDs occur in that active community's membership list;
- `uncommunitied=true` — return nodes absent from every active community.

Those filters are mutually exclusive. Unknown or stale community IDs return the existing not-found error path. The cursor still encodes an offset and each page remains bounded; only the former global offset rejection and internal 10,501 fetch truncation were removed.

No SQLite schema or data migration is required.

## No-fake-data and privacy invariants

- Every aggregate key/count must match either a returned community `member_count` or a returned type `uncommunitied_count`; this is covered by a direct invariant test.
- Exact totals count active backend rows, not the page sample.
- Empty, disabled, and unavailable brains still render no placeholder knowledge.
- Aggregate tier emits zero inferred knowledge edges.
- Detail tier retains only backend edges whose two real endpoints are both loaded.
- Overview and aggregate queries remain body-free under the existing redaction contract.
- The client sends only a profile name, cursor, node types, and community ID already attested by the profile-scoped response.

## Performance measurement

Measured on an Apple M2 (`arm64`) using the checked-in synthetic test fixture. The fixture represents exactly 50,000 nodes and 100,000 active relations in counts only, exposes the maximum initial 24 community aggregates plus five uncommunitied type aggregates, and therefore renders 29 real aggregate elements and zero sampled edges.

Measurement command:

```text
cd apps/desktop
npx vitest run --environment jsdom --config vite.config.ts src/app/starmap/lod.test.ts --reporter=verbose --silent=false
```

Result: 600 synchronous steady-state D3 simulation ticks averaged **0.063 ms per simulation frame**, below the 16.67 ms 60-fps CPU budget. The six LOD tests completed in 45 ms; the complete file completed in 505 ms.

This is an honest CPU simulation microbenchmark under jsdom, not a browser GPU/compositor trace. It verifies that graph size does not leak into aggregate-tier simulation work and that the maximum initial aggregate scene is far inside the target frame budget. Canvas drawing is additionally bounded by off-screen node/link culling, but was not separately GPU-profiled in this worktree.

## Files changed

LOD math, composition, renderer, simulation, workspace, and tests:

- `apps/desktop/src/app/starmap/lod.ts`
- `apps/desktop/src/app/starmap/lod.test.ts`
- `apps/desktop/src/app/starmap/constellation.ts`
- `apps/desktop/src/app/starmap/constellation.test.ts`
- `apps/desktop/src/app/starmap/constellation-overview.tsx`
- `apps/desktop/src/app/starmap/cortex-workspace.tsx`
- `apps/desktop/src/app/starmap/cortex.test.ts`
- `apps/desktop/src/app/starmap/geometry.ts`
- `apps/desktop/src/app/starmap/render.ts`
- `apps/desktop/src/app/starmap/simulation.ts`
- `apps/desktop/src/app/starmap/star-map.tsx`
- `apps/desktop/src/app/starmap/types.ts`

Desktop state, client contract, and shared types:

- `apps/desktop/src/store/starmap.ts`
- `apps/desktop/src/store/starmap.test.ts`
- `apps/desktop/src/hermes.ts`
- `apps/desktop/src/types/hermes.ts`

Locales:

- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`

Backend contract and tests:

- `altas/cortex/graph.py`
- `hermes_cli/web_server.py`
- `tests/altas/test_cortex_graph.py`

Handoff:

- `docs/altas/workstreams/WS-17-HANDOFF.md`

## Validation

Passed after the final implementation changes:

- `npx vitest run --environment jsdom --config apps/desktop/vite.config.ts apps/desktop/src/app/starmap apps/desktop/src/store/starmap.test.ts apps/desktop/src/i18n` — 10 files, 69 tests passed in 2.64 s.
- `npx tsc --noEmit -p apps/desktop` — passed with no output.
- `scripts/run_tests.sh tests/altas/test_cortex_graph.py` — 1 file, 10 tests passed in 2.7 s.
- focused Desktop ESLint over all touched TypeScript/TSX files — passed with no output.
- `git diff --check` — passed.

New coverage includes:

- exact count-to-radius monotonicity for aggregate stars and constellation regions;
- aggregate/detail tier selection relative to fitted zoom;
- progressive cursor walking, page merging, completion, and per-brain/region cache reuse;
- every rendered aggregate tracing to a backend count;
- maximum initial aggregate-scene performance for a 50k-node/100k-relation fixture;
- off-viewport resolved nodes leaving the active force simulation;
- one bounded initial aggregate page per brain with no shared 420-node budget;
- no cross-brain or invented aggregate-tier knowledge edges;
- exact backend active type/relation counts, community and uncommunitied filters, and cursors beyond the former 10,000 offset.

## Known limitations

- The aggregate tier intentionally shows at most the 24 largest communities; exact total/type counts and brain radius still represent the complete store. Selecting one resolves all of its paginated members.
- Community membership can overlap, so community star counts must not be summed to infer the distinct `total_nodes`; the backend's exact total is authoritative.
- Cross-page edges whose endpoints never appear together in one returned detail page are omitted rather than inferred. This favors the WS-16 honest-edges invariant over visual completeness.
- Detail pages currently recompute exact aggregate counts. This keeps every response independently attested but can add fixed count-query work during long resolution sessions.
- The aggregate benchmark covers CPU simulation under jsdom, not a manual Electron/GPU frame capture.

## Integration notes

WS-17 supersedes WS-16's 420/500 budget policy. During conflict resolution, preserve:

- the aggregate-first one-page initial load;
- the 500 per-page API clamp but not any total cursor/node ceiling;
- exact total counts as the constellation footprint source;
- per-profile/per-region session caching;
- off-screen detail removal from the active simulation;
- separate ownership records and endpoint-attested knowledge edges;
- body-free graph responses and no placeholder data.

Likely conflict surfaces are `constellation.ts`, `cortex-workspace.tsx`, `star-map.tsx`, `simulation.ts`, `store/starmap.ts`, `types/hermes.ts`, `hermes.ts`, and the five Starmap locale contracts.
