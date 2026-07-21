# WS-20 Handoff — Dense Nebula Starmap

## Status

- Branch: `codex/ws-20-dense-nebula`
- PR: None (this workstream must not open one)
- Baseline: `3a271d5ce`
- Approved reference: `sketches/001-dense-nebula/index.html` from `06df7e401`
- Last validated: 2026-07-21

## Goal and scope

Replaced the rejected WS-16 constellation overview with the approved Dense Nebula default view:

- Atlas and every worker brain now share one viewport-wide starfield with deterministic organic gravity wells;
- owner identity is preserved on every composed node and edge, but it no longer determines a spatial region;
- worker chips filter the shared sky by dimming other owners to 7% opacity;
- isolation remains a separate action that opens the complete WS-15 single-brain workspace;
- the default scene contains a bounded real graph page and honest native edges for every reachable profile;
- exact residual aggregate stars preserve WS-17 performance and count truth for nodes beyond that page;
- node hover includes date, type, owner brain, and the inspect affordance; clicking a real node opens the existing Cortex detail panel with the owning profile;
- slow deterministic drift and twinkle run in the existing canvas loop, pause while hidden/unfocused, and stop under reduced motion;
- English, Simplified Chinese, Japanese, and Traditional Chinese cover every new or changed control and state.

No channel, profile-dialog, Electron, Python engine, gateway, Cortex storage, or API surface changed.

## What was removed vs. kept

Removed from the default overview:

- per-brain region circles and region-radius growth footprints;
- main-brain and worker avatar anchors in the sky;
- anchor-to-region ownership lines;
- worker rings and partition-specific force targets;
- the aggregate-only default page that could reduce a large brain to one badge-like star;
- the `constellation` navigation mode and its constellation-only locale copy.

Kept unchanged for the isolated brain view:

- the complete WS-15 workspace, search, domain filters, six node-type toggles, legend vocabulary, timeline, node list, detail panel, health/maintenance UI, and node-focused camera;
- WS-17 aggregate-first isolated loading, progressive community/type resolution, per-brain region cache, and off-viewport force culling;
- the six Cortex shapes and colors: entity, memory, evidence, session, document, and community;
- private Cortex share restrictions and legacy non-Cortex map loadout codes.

The shared default view also keeps the six-type vocabulary as an interactive bottom-left legend/filter.

## Filter-chip semantics

- **Atlas / whole sky:** clears `$starmapBrainFilter`; every brain returns to normal opacity.
- **Worker primary chip click:** sets `$starmapBrainFilter` only. It performs no graph request and does not change `$starmapMode`.
- **Non-selected owner:** nodes and native edges render at `0.07` of their normal alpha and are removed from hover hit-testing.
- **Isolate icon:** navigates to `view=brain&brain=<profile>` and opens the existing single-brain workspace.
- **Double-click primary chip:** provides the same isolate action without changing the single-click filter contract.
- **Empty brain:** remains in the chip row with “No memory yet,” `totalNodeCount=0`, and no placeholder node.
- **Disabled/unavailable brain:** remains discoverable with a muted status and no invented graph content.

Changing owner or node-type filters clears an inspected node when it is no longer active.

## Data composition and honest edges

The default nebula makes one existing profile-scoped graph request per brain:

```text
limit=500&projection=growth&profile=<profile>
```

Each response is adapted independently, then its node IDs, edge IDs, and edge endpoints are namespaced with the owning profile. `brainProfile` remains attached after composition. An edge survives only when both returned endpoints exist in that same namespaced brain; there are no ownership edges, aggregate inference edges, or cross-brain knowledge edges.

The one-page maximum is a render/fetch bound, not a claim that a large brain has only 500 nodes. For every Cortex type, the adapter compares exact backend counts with the real rows in the bounded page and adds at most one residual aggregate star for the difference. Real community rows use the existing larger community treatment when the response supplies their attested member count. Type totals in the header/legend remain exact.

## LOD integration

- Default nebula: up to 500 real rows per brain plus at most six exact residual type stars; native edges among returned rows remain visible.
- Isolated brain: unchanged WS-17 initial 24-community aggregate page, two-page progressive resolution, and cache behavior.
- Aggregate radii remain logarithmic and count-backed.
- Residual aggregates are placed in the same gravity wells as real nodes; they do not form a tier ring or separate UI region.
- `updateSimulationViewport()` remains active, so offscreen detail is frozen and removed from active D3 nodes/links.
- The ambient phase field changes only rendered position/alpha. It does not mutate graph data, simulation ownership, or persisted coordinates.

This restores a dense first impression without making force/render work proportional to an unbounded Cortex database.

## Navigation and share migration

New navigation state emits:

```text
view=nebula
view=brain&brain=<encoded-profile-name>
```

The decoder maps the old WS-16 `view=constellation` state to `{ mode: "nebula", brainProfile: "default" }`. Existing brain deep links remain unchanged and profile validation still occurs before requests. URLs contain profile identity only—never graph content or memory text.

## Header and motion

- The header reports exact total nodes and worker count.
- Seven-day growth is intentionally omitted because no existing cross-profile contract exposes an honest value cheaply.
- The shared `Panel` chrome remains the only top-right close affordance, preserving WS-19’s one-✕ intent.
- Ambient drift/twinkle is capped by the existing approximately 30 fps canvas loop, pauses on `document.hidden`, window blur, and component unmount, and becomes static for `prefers-reduced-motion`.

## Files

Added/replaced Dense Nebula composition, UI, and tests:

- `apps/desktop/src/app/starmap/nebula.ts`
- `apps/desktop/src/app/starmap/nebula.test.ts`
- `apps/desktop/src/app/starmap/nebula-overview.tsx`
- `apps/desktop/src/app/starmap/nebula-overview.test.tsx`
- `apps/desktop/src/app/starmap/constellation.ts` (removed)
- `apps/desktop/src/app/starmap/constellation.test.ts` (removed)
- `apps/desktop/src/app/starmap/constellation-overview.tsx` (removed)

Renderer, LOD, route, and isolated-workspace integration:

- `apps/desktop/src/app/starmap/index.tsx`
- `apps/desktop/src/app/starmap/star-map.tsx`
- `apps/desktop/src/app/starmap/simulation.ts`
- `apps/desktop/src/app/starmap/render.ts`
- `apps/desktop/src/app/starmap/geometry.ts`
- `apps/desktop/src/app/starmap/types.ts`
- `apps/desktop/src/app/starmap/lod.ts`
- `apps/desktop/src/app/starmap/lod.test.ts`
- `apps/desktop/src/app/starmap/cortex-workspace.tsx`
- `apps/desktop/src/app/starmap/share-code.ts`
- `apps/desktop/src/app/starmap/share-code.test.ts`

State and locales:

- `apps/desktop/src/store/starmap.ts`
- `apps/desktop/src/store/starmap.test.ts`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`

Handoff:

- `docs/altas/workstreams/WS-20-HANDOFF.md`

## Tests and results

Passed:

- `npx vitest run --environment jsdom --config apps/desktop/vite.config.ts apps/desktop/src/app/starmap apps/desktop/src/store/starmap.test.ts apps/desktop/src/i18n` — 11 files, 73 tests passed.
- `npx tsc --noEmit -p apps/desktop` — passed with no output.
- focused Desktop ESLint over every touched TypeScript/TSX file — passed with no output.
- `git diff --check` — passed with no output.

New behavior coverage includes:

- exact 7% non-owner dimming;
- primary-chip filter behavior versus isolate-icon/double-click navigation;
- old `view=constellation` migration and new `view=nebula` emission;
- honest empty-brain chip state without placeholder nodes;
- per-brain node and edge identity through independent adaptation and namespacing;
- same-brain-only native knowledge edges;
- real-node/edge retention plus exact residual LOD type counts.

## Manual visual QA checklist

Open `/starmap` with Atlas, at least two workers, and one empty worker:

1. The initial view should be one full-screen field of many typed stars in several organic wells. There should be no large empty main-brain circle, no corner blob, no avatar anchor in the sky, no brain-region boundary, and no ownership connector.
2. Cyan entities, amber memories, violet evidence, blue sessions, seafoam documents, and coral communities/aggregates should all use the WS-15 shapes. Residual aggregates should look like larger glowing stars inside the same field, with truthful count labels.
3. Native edges should be visible among related real nodes. No edge should connect different brain owners.
4. The top row should show the exact total node count, worker count, and one close ✕ at the far right supplied by the panel chrome.
5. The bottom-left type controls should hide/show their node type and remove/restore incident edges without changing other types.
6. The bottom-right Atlas chip should start active and read “whole sky.” Every worker should have an avatar chip; the empty worker should read “No memory yet” and contribute no star.
7. Click a populated worker chip. Its stars/edges should remain bright while all other owners fade to a barely visible 7% field; the geometry must not move. Click Atlas to restore the entire sky.
8. Hover a bright real node. The tooltip should show date, owner brain, Cortex type, title, and “click to inspect.” Dimmed owners should not capture hover.
9. Click that real node. The existing Cortex detail panel should open using the correct worker profile and native node ID. Closing it should leave the nebula/filter intact.
10. Click a worker chip’s isolate icon (or double-click its primary chip). The existing WS-15 single-brain workspace should open with search, domains, six type filters, legend, LOD resolution, node list, detail, timeline, and telemetry unchanged. “Whole sky” should navigate back.
11. Leave the sky idle and observe slow independent drift/twinkle without layout migration. Switch apps or hide the window and confirm activity pauses; enable reduced motion and confirm the sky remains static.
12. Open an old `?view=constellation` URL and confirm it lands on the nebula. Open a `?view=brain&brain=...` URL and confirm it still isolates the validated brain.

## Security and privacy

- All graph/detail calls reuse the authenticated, profile-scoped Cortex API.
- The default page remains body-free; evidence/document bodies enter only the existing selected-node detail response.
- No graph content is written to a URL, another profile, local persistence, telemetry, or an external service.
- Empty/disabled/unavailable brains produce zero placeholder knowledge.
- No model tool, environment variable, API endpoint, storage migration, or cross-profile mutation was added.

## Known limitations

- The default sky intentionally renders only the first bounded real page for each brain. Exact residual stars represent the remainder; isolation is the path to progressive WS-17 resolution.
- A profile with many returned edges can still look visually busy. Edge alpha stays low and owner/type filtering reduces the field without deleting data.
- The ambient check verifies CPU lifecycle and deterministic math, not a browser GPU trace.
- Seven-day growth remains absent until an existing authenticated contract can provide exact per-profile growth without extra unbounded reads.

## Integration notes

WS-20 builds directly on WS-17 and supersedes WS-16’s default presentation. During conflict resolution, preserve:

- `$starmapMode: "nebula" | "brain"` and `$starmapBrainFilter` as separate state;
- one `Panel` close control (WS-19 may touch nearby chrome);
- exact residual aggregate counts and the unchanged isolated progressive resolver;
- native node IDs recovered only for profile-scoped detail requests;
- all four locale objects and the `nebula` translation contract.

No `channels/`, profile dialogs, `electron/`, or Python file belongs in this workstream.
