# WS-14 Handoff — Atlas Teams channels scaffold

## Status

- Branch: `codex/ws-14-channels-scaffold`
- Draft PR: None (this workstream must not open one)
- Baseline: `57ad52ce5ad863d75b8bbf917510a4a12bd7d978` (`origin/main` when work began)
- Last validated: 2026-07-20

## Goal and scope

Built the Atlas Desktop visual and renderer-data scaffold for named channels and direct messages:

- typed channel records with archive and turn-policy settings;
- renderer persistence across app restarts;
- Channels and Direct messages groups in the chat sidebar;
- a worker-backed creation dialog using the existing profiles API/store;
- a dedicated channel route with member identity, transcript attribution contract, settings, and an `@` picker;
- an honest no-routing state and disabled channel composer;
- one real DM handoff into the existing isolated worker chat path;
- English, Simplified Chinese, Japanese, and Traditional Chinese strings.

No multi-agent routing, shared-context engine, gateway platform adapter, Electron update, Python, or profile-management code changed.

## Decisions made

### Persistence

Channels use a nanostore backed by renderer `localStorage` under `atlas.desktop.channels.v1`. This is the smallest persistence mechanism that survives a Desktop restart without adding a backend contract during a visual-only workstream. Records are validated on hydration and contain only channel metadata:

```text
id, name, kind, memberWorkerIds, createdAt, archived, settings.turnPolicy
```

This storage is workstation-local. It is not synced across devices, shared with other clients, or included in Hermes profile/session backup behavior. A backend store should replace it only when cross-device or server-owned channel state becomes a product requirement.

### Prompt and session isolation

The scaffold does not create a shared mutable agent context. `channelTurnTargets()` is deliberately pure: it computes eligible member IDs but performs no sends. A future channel turn must fan out over separate per-worker sessions so each worker retains a stable prompt-cache prefix and valid role alternation.

The default stored turn policy is `mention-only`. `all-members` is visible and persistable for product exploration, but the UI explicitly says routing is inactive and never simulates a worker response.

### DM realness investigation

A cheap, real one-worker path exists, with an important limitation.

The traced path is:

1. `newSessionInProfile(workerId)` in `src/store/profile.ts` records the target worker, requests a fresh session, and starts `ensureGatewayProfile(workerId)`.
2. `ensureGatewayProfile()` delegates to `ensureGatewayForProfile()` in `src/store/gateway.ts`. The renderer retains a primary gateway plus lazily opened secondary profile sockets and moves the active pointer without collapsing the prior connections.
3. The Electron preload calls `getConnection(profile)`; the main process handles `hermes:connection` through `ensureBackend(profile)`, which lazily starts or reuses that profile's `hermes serve` backend.
4. On the first real message, `createBackendSessionForSend()` in `src/app/session/hooks/use-session-actions/index.ts` awaits the selected profile gateway and sends `session.create` with the target `profile`.

The DM empty state therefore offers **Open live chat with worker**, which uses that existing path and navigates to the standard chat view. Messages then use a real, isolated session owned by the selected worker.

The limitation is that a DM channel does not yet own or resume one stable session ID. Opening the action requests a fresh standard chat, and its messages remain in the normal chat transcript rather than the channel transcript. Treat this as a working handoff, not channel-engine completion.

### Worker identity source

The picker calls `refreshProfiles()` from `src/store/profile.ts`, which reads `GET /api/profiles`. The server response already includes identity summary fields loaded by `hermes_cli/web_server.py` (`display_name`, `role`, and `has_avatar`); UI avatars reuse the existing exported `WorkerAvatar` component. No profile implementation file was modified.

## Deferred decisions

The engine workstream must define:

- a durable mapping from `(channelId, workerId)` to that worker's profile and session ID, including resume, missing-worker, archive, and failed-session behavior;
- fan-out concurrency, cancellation, retries, partial-failure display, deterministic response ordering, and response-to-user-turn correlation;
- authoritative mention parsing by stable worker ID rather than display name, and whether an unmentioned channel turn is stored only or rejected;
- the final scope and naming of `all-members` versus `mention-only` policies;
- loop prevention: worker output must not recursively become another worker's user turn without an explicit bounded policy, hop metadata, deduplication, and budget;
- an append-only channel event/transcript contract that attributes every sender while leaving each worker's private session history intact;
- unread cursors, notification semantics, and whether channel metadata migrates from local renderer storage to a backend/sync service;
- authorization boundaries for channel membership and cross-profile gateway access, especially for remote backends.

## Files changed

- `apps/desktop/src/store/channels.ts`
- `apps/desktop/src/store/channels.test.ts`
- `apps/desktop/src/app/channels/index.tsx`
- `apps/desktop/src/app/channels/create-channel-dialog.tsx`
- `apps/desktop/src/app/channels/transcript.tsx`
- `apps/desktop/src/app/channels/routing.ts`
- `apps/desktop/src/app/channels/routing.test.ts`
- `apps/desktop/src/app/chat/sidebar/channels-section.tsx`
- `apps/desktop/src/app/chat/sidebar/index.tsx`
- `apps/desktop/src/app/routes.ts`
- `apps/desktop/src/app/desktop-controller.tsx`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`
- `docs/altas/workstreams/WS-14-HANDOFF.md`

## Contracts and migrations

- New renderer storage key: `atlas.desktop.channels.v1`.
- Invalid records and duplicate IDs are ignored during hydration; a DM must have exactly one worker and a channel must have at least one.
- New route shape: `/channels/:channelId`, with IDs URL-encoded by `channelRoute()`.
- New pure routing contract: `channelTurnTargets(channel, mentionedWorkerIds)`.
- No backend schema, session database, API, Python, or filesystem migration.

## Validation

Passed:

- `npm run typecheck --workspace apps/desktop`
- focused ESLint over every touched Desktop source file
- `npx vitest run --config apps/desktop/vite.config.ts --environment jsdom apps/desktop/src/store/channels.test.ts apps/desktop/src/app/channels/routing.test.ts` — 2 files, 9 tests passed
- `npm run build --workspace apps/desktop` — renderer, Electron main/preload, staged assets, and post-build assertion passed
- `git diff --check`

Repository-suite caveats observed on the unchanged baseline:

- The requested literal `npx vitest run apps/desktop` does not load `apps/desktop/vite.config.ts`; it runs in Node without the Desktop aliases/jsdom and reported 148 failed files (including `@/…` resolution and missing `window`/`document` errors), 43 passed files, 102 failed tests, 313 passed tests, and 10 skipped tests.
- The official `npm run test:ui --workspace apps/desktop` does load the Desktop config. The channel tests passed, but the existing full suite reported 46 failed and 145 passed files, with 21 failed and 1,267 passed tests (plus 36 Electron `node:test` files reported as having no Vitest suite). Failures were outside WS-14 in existing model-option, pane persistence/sizing, provider mock, attachment, preview, timeline/jsdom, and fallback-title tests.

No Python was changed, so `scripts/run_tests.sh` was not required.

## Security and privacy checks

- Local persistence stores only channel metadata and worker IDs; it stores no messages, prompts, credentials, secrets, or API responses.
- Worker discovery uses the existing authenticated Desktop API bridge and existing profile endpoints.
- The scaffold does not widen gateway access, introduce a tool, invoke a worker from the channel route, or mutate another worker's context.
- All displayed worker output would require an explicitly attributed transcript event; the current empty transcript intentionally has none.

## Visual evidence

The production renderer build passed. No screenshot was committed. The implemented route uses the Atlas navy workstation palette, grid/radar texture, worker avatar stack, attributed transcript shell, and explicit visual-scaffold status. The channel send control is visibly disabled.

## Known risks and limitations

- Channel metadata is local to one renderer storage origin and can be cleared with app/browser storage.
- Renamed or deleted workers leave a member ID fallback until a later membership-management flow repairs the channel.
- Channel and DM transcripts are empty shells; drafts are not persisted and channel sending is disabled.
- The DM live path opens a fresh standard chat and does not bind that chat back to the DM channel.
- Unread badges are fixed at `0`.
- `all-members` can be selected but has no runtime effect.

## Integration order and conflicts

This branch starts from the worker identity/avatar baseline and imports `WorkerAvatar` without changing `src/app/profiles/`. Likely merge-conflict surfaces with parallel Desktop navigation work are intentionally limited to append-style edits in:

- `src/app/chat/sidebar/index.tsx`
- `src/app/routes.ts`
- `src/app/desktop-controller.tsx`
- shared locale/type objects

Keep the new files in `src/app/channels/`, `src/store/channels.ts`, and `src/app/chat/sidebar/channels-section.tsx` intact when resolving those conflicts.

## Exact next actions

1. Merge after the supervisor reconciles the small shared-route/sidebar/locale append points.
2. Have the engine workstream specify the channel-to-per-worker-session binding and append-only transcript event contract before enabling Send.
3. Implement mention resolution, bounded fan-out, loop prevention, response attribution, and partial-failure UX against that contract.
4. Decide whether channel ownership requires backend persistence; if so, migrate `atlas.desktop.channels.v1` once with explicit versioning rather than silently maintaining two authorities.
5. Replace the DM handoff with channel-owned session resume only after the engine exposes stable binding APIs.
