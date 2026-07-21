# WS-16 Handoff — Brain constellation

## Status

- Branch: `codex/ws-16-brain-constellation`
- Draft PR: None (this workstream must not open one)
- Baseline: `f5998aa550bf8b6f224ae97abab400a4c1fa2f9d` (WS-15)
- Last validated: 2026-07-20

## Goal and scope

Replaced the WS-15 brain dropdown with a constellation-first Starmap:

- the main Atlas brain is the central region;
- every worker is an avatar identity anchor with its own independently fetched Cortex graph clustered beside it;
- all regions share one canvas and one bounded simulation without merging their knowledge;
- clicking any identity anchor opens that brain in the complete WS-15 isolated workspace;
- a breadcrumb returns to the constellation;
- `/starmap` defaults to the constellation, while a validated query encodes isolated mode and brain selection;
- enabled-empty, Cortex-disabled, and temporarily unavailable brains remain visible without placeholder nodes.

No Cortex engine, storage, Python API, profile-management UI, channel, Electron, or gateway-platform code changed. No shared-space marker was added because the current public graph/health contracts do not expose an attested shared-space field.

## Decisions made

### Presentation composition, not knowledge federation

Each `/api/cognitive/graph?profile=...` response is adapted independently. Node and edge identifiers are namespaced by the owning profile before the responses share a render scene. Native edges are retained only when both endpoints exist in the same namespaced brain. The composed knowledge-edge array therefore contains no cross-brain relation, similarity, or inferred identity edge.

Ownership is a separate structural contract: every identity anchor has exactly one `ownership` record pointing to its own region. The UI draws only that anchor-to-region connector. Ownership records never enter the Cortex knowledge-edge array, so they cannot be mistaken for stored relations.

### One partitioned canvas

The constellation uses one `StarMap` instance and one D3 simulation rather than one canvas per brain. This preserves the existing six Cortex node shapes/colors while avoiding several simultaneously animated render loops.

Layout is deterministic:

- main Atlas region: world origin, radius 118;
- first worker ring: up to 8 evenly spaced anchors at radius 205;
- subsequent worker rings: capacities grow by 8 and move outward by 165 world units;
- each worker graph center is 62 world units outward from its avatar anchor, with region radius 72;
- domain neighborhoods remain grouped inside each brain partition;
- the overview camera is locked so DOM avatar anchors and ownership lines cannot drift away from canvas regions.

The overview intentionally hides the isolated view's global time rings, scrubber, and core scramble. A single global time disk would falsely imply one federated temporal graph. Opening a brain restores the complete WS-15 radial time view, filters, legend, node details, and maintenance panel.

Once the constellation simulation and fades settle, its render loop sleeps until a simulation tick or interaction invalidates the scene.

### Per-brain budget policy

Constellation has a fixed 420-node total envelope:

```text
per_brain_limit = max(1, floor(420 / brain_count))
```

Every brain, including empty or disabled workers, participates in the divisor so request cost is known before any store is opened. The isolated brain path remains unchanged at `limit=500`; overview budgeting cannot degrade the single-brain experience.

### Empty, disabled, and unavailable visuals

- **Ready:** full-color avatar, cool-blue region boundary, real node count.
- **Enabled but empty:** dim avatar, cool dashed empty region, “No memory yet”; zero graph nodes.
- **Cortex disabled:** grayscale avatar, muted red dashed region and slash badge, “Cortex off”; zero graph nodes.
- **Unavailable/error:** dim amber/dotted region, “Unavailable”; zero graph nodes.

Clicking an empty, disabled, or unavailable anchor still enters an isolated state with the same back affordance. A disabled default brain is no longer hidden behind the legacy learning-graph fallback; the 404 fallback remains only for an un-upgraded default backend.

### Navigation and deep links

The default `/starmap` route always selects constellation mode. Isolated state serializes through `share-code.ts` as:

```text
?view=brain&brain=<encoded-profile-name>
```

The selected name is validated against the real `/api/profiles` response before any per-profile Cortex request. Unknown names return to `/starmap`. The navigation serializer contains only mode/profile identity, never graph or memory content.

## Deferred decisions

The federation engine ADR still needs to decide:

- whether identities in multiple brains are aliases, duplicates, or independent claims;
- ownership and authorization for genuinely shared knowledge spaces;
- copy versus reference semantics for future cross-brain edges;
- provenance, deletion, temporal validity, conflict, and GraphRAG-version propagation;
- whether shared-space membership belongs in graph, health, or a separate federation contract;
- whether maintenance in an isolated worker brain should remain writable from the main Desktop process;
- how a future remote/offline worker differs from a local Cortex store that is temporarily unavailable.

WS-16 does not pre-decide any of these.

## Files changed

Constellation composition, layout, UI, and tests:

- `apps/desktop/src/app/starmap/constellation.ts`
- `apps/desktop/src/app/starmap/constellation.test.ts`
- `apps/desktop/src/app/starmap/constellation-overview.tsx`
- `apps/desktop/src/app/starmap/index.tsx`
- `apps/desktop/src/app/starmap/cortex-workspace.tsx`
- `apps/desktop/src/app/starmap/simulation.ts`
- `apps/desktop/src/app/starmap/render.ts`
- `apps/desktop/src/app/starmap/star-map.tsx`
- `apps/desktop/src/app/starmap/share-code.ts`
- `apps/desktop/src/app/starmap/share-code.test.ts`

State and shared Desktop contracts:

- `apps/desktop/src/store/starmap.ts`
- `apps/desktop/src/store/starmap.test.ts`
- `apps/desktop/src/types/hermes.ts`

Locales:

- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`

Handoff:

- `docs/altas/workstreams/WS-16-HANDOFF.md`

## Contracts and migrations

- No SQLite, API, or configuration migration.
- No Python endpoint change.
- `StarmapNode` and `StarmapEdge` gain optional `brainProfile` ownership metadata for composed scenes.
- New Starmap mode atom: `constellation | brain`; reset/default is `constellation`.
- New constellation store contains partitions, separately typed ownership edges, bounded composed graph, and outer world radius.
- Existing explicit Cortex profile query behavior in `hermes.ts` is reused unchanged.
- Existing isolated-brain health, detail, dream-status, filtering, and maintenance calls remain profile-scoped.

## Validation

Passed required commands:

- `npx vitest run --environment jsdom --config apps/desktop/vite.config.ts apps/desktop/src/app/starmap apps/desktop/src/store/starmap.test.ts apps/desktop/src/i18n` — 9 files, 63 tests passed.
- `npx tsc --noEmit -p apps/desktop` — passed with no output.
- focused Desktop ESLint over every touched TypeScript/TSX file — passed with no output.
- `git diff --check` — passed.

New invariant coverage includes:

- profile normalization and proportional budget calculation;
- deterministic main/worker spatial partitions;
- one anchor-to-region membership record per brain;
- no cross-brain knowledge edges after namespacing;
- empty/disabled regions contain no placeholder nodes;
- constellation → isolated brain → constellation store transitions;
- isolated mode still requests 500 nodes;
- navigation-state round-trip with an encoded worker brain name;
- a measured four-brain simulation settlement check.

Python tests were not required because no Python file or backend contract changed.

## Security and privacy checks

- All graph requests use the existing authenticated, profile-scoped Cortex API.
- The client never accepts or constructs a Cortex brain ID or database path.
- Overview payloads remain body-free; no evidence/document body enters composition.
- No graph data is copied into another profile, persisted, uploaded, or sent to a new endpoint.
- URL state contains only a validated profile name and view mode.
- Failed/disabled reads render zero nodes rather than legacy or invented placeholder data.
- No telemetry, model tool, environment variable, external request, or mutation channel was added.

## Visual evidence

The dark-blue observatory presentation uses the existing WS-15 type vocabulary inside every region:

- entity — cyan circle;
- memory — amber diamond;
- evidence — violet triangle;
- session — blue hexagon;
- document — seafoam square;
- community — coral star.

Worker avatars reuse `WorkerAvatar`; the main Atlas anchor is larger and central. Ownership connectors are thin cyan dotted lines from each worker identity to only its own outward brain region. No line crosses from one knowledge region to another.

Renderer sanity fixture: 1 main + 3 worker brains, each capped at 105 nodes, produced exactly **420 canvas nodes**, **416 native within-brain edges**, and **4 separate ownership edges**. After 180 synchronous simulation ticks, every node position remained finite; the test completed inside the required Vitest run with no simulation blowup. No screenshot or generated build artifact was committed.

## Known risks and limitations

- The overview is intentionally sampled. Users must open a brain for the 500-node isolated projection and full time/detail controls.
- More worker rings increase world extent and therefore reduce visual scale; the total node ceiling remains fixed.
- An unavailable region does not expose the raw backend error in the sky; it uses a safe generic state and can be retried by reopening Starmap.
- If `/api/profiles` is unavailable, the safe fallback shows only the main Atlas brain rather than guessing worker names.
- Current backends expose no public shared-space membership marker, so the constellation contains no shared-space node.
- The older-backend legacy graph fallback applies only to an isolated default brain after a Cortex 404; the constellation requires the current per-profile Cortex graph contract.

## Integration order and conflicts

WS-16 depends directly on WS-15 and should land after it. Likely conflict surfaces are:

- `apps/desktop/src/app/starmap/index.tsx`
- `apps/desktop/src/app/starmap/cortex-workspace.tsx`
- `apps/desktop/src/app/starmap/star-map.tsx`
- `apps/desktop/src/store/starmap.ts`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/i18n/{types,en,zh,ja,zh-hant}.ts`

Preserve the presentation-only composition boundary, explicit per-profile fetches, 420/500 budget split, and separate ownership-edge collection during conflict resolution. No `channels/`, profile UI implementation, Electron, gateway-platform, or Cortex-engine file should be pulled into this workstream.

## Exact next actions

1. Review the constellation visually against representative real profile counts, including at least one enabled-empty and one Cortex-disabled worker.
2. Keep knowledge-level federation blocked on its ADR; do not promote ownership lines into Cortex relation rows.
3. If shared-space markers are desired, first add one attested, authorization-aware public API field and contract tests, then render exactly that field.
4. Decide worker-brain maintenance authorization before changing the existing isolated maintenance controls.
