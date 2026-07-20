# WS-13 Handoff — First-Class Workers Tab

## Status

- Branch: `codex/ws-13-workers-tab`
- Draft PR: Not opened (per workstream instructions).
- Baseline: `57ad52ce5ad863d75b8bbf917510a4a12bd7d978` (`origin/main` at start).
- Last validated: 2026-07-20.

## Goal and scope

Promote the existing desktop worker-management UI from the `/profiles` overlay
to a first-class Workers destination. This work adds the sidebar destination,
converts the existing surface to the shared full-page master-detail layout,
shows a worker roster and endpoint-backed detail summary, and keeps create,
rename, delete, identity, portrait, and SOUL.md flows available.

WS-13 does not add channels, change gateway platforms, or extend the Python
engine.

## Decisions made

- Kept `/profiles` as the canonical route so existing deep links, keybinds,
  command-palette actions, and the profile-switcher manage entry keep working.
  The route is no longer listed in `OVERLAY_VIEWS` and now renders inside the
  normal page shell.
- Added the Workers nav item append-only at the end of the existing sidebar nav
  array to minimize the shared-file conflict with WS-14.
- Reused `MasterDetail`, `ListColumn`, `DetailColumn`, `PageSearchShell`, and
  existing panel primitives instead of creating worker-only layout machinery.
- Reused the existing `CreateProfileDialog`, `RenameProfileDialog`, and
  `DeleteProfileDialog`. The duplicate rename and delete implementations in the
  old overlay were removed.
- The roster state precedence is: current active gateway worker, another worker
  with `gateway_running`, then standby. The current state comes from
  `$activeGatewayProfile`; the other status comes from the existing profile-list
  payload.
- Surfaced only fields already returned by `GET /api/profiles`: model/provider,
  skill count, worker state directory, gateway-running state, and identity
  summary. `ProfileInfo.gateway_running` now describes the existing response
  contract on the TypeScript side.
- Customer-facing navigation and new copy use “worker,” with additions in the
  English, Simplified Chinese, Japanese, and Traditional Chinese catalogs.

## Deferred decisions

- `GET /api/profiles` does not return enabled toolsets or `terminal.cwd`. The
  existing `path` field is the worker's Atlas state home, not its terminal
  workdir, so it is shown as “Worker home” and is not mislabeled. A future
  backend contract can add explicit read-only `toolsets` and `workdir` fields.
- The internal route/view/type names remain `profiles` for compatibility. A
  customer-visible `/workers` alias was not added because it would duplicate
  the existing route surface without product value.

## Files changed

- `apps/desktop/src/app/profiles/index.tsx`
- `apps/desktop/src/app/profiles/index.test.tsx`
- `apps/desktop/src/app/chat/sidebar/index.tsx`
- `apps/desktop/src/app/chat/sidebar/profile-switcher.tsx`
- `apps/desktop/src/app/desktop-controller.tsx`
- `apps/desktop/src/app/routes.ts`
- `apps/desktop/src/app/routes.test.ts`
- `apps/desktop/src/app/shell/hooks/use-overlay-routing.ts`
- `apps/desktop/src/app/types.ts`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/store/profile.test.ts`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`
- `docs/altas/workstreams/WS-13-HANDOFF.md`

## Contracts and migrations

- No persistence migration.
- No REST or Python change.
- The desktop `ProfileInfo` type now includes the already-returned
  `gateway_running: boolean` field.
- `/profiles` remains valid but is now a non-overlay `AppView`.

## Validation

- `npm ci` — passed; 1,307 packages installed, 0 vulnerabilities. The local
  Node 23.7.0 runtime emitted engine warnings because several packages support
  Node 20, 22, or 24 rather than odd-numbered Node 23.
- `npm --prefix apps/desktop run typecheck` — passed.
- Focused ESLint from `apps/desktop/` over every changed TypeScript file —
  passed with no warnings or errors.
- `npx vitest run --environment jsdom --config apps/desktop/vite.config.ts
  apps/desktop/src/app/profiles/index.test.tsx
  apps/desktop/src/app/routes.test.ts
  apps/desktop/src/store/profile.test.ts
  apps/desktop/src/i18n` — passed: 6 files, 34 tests.
- `npm --prefix apps/desktop run build` — passed, including TypeScript, Vite,
  Electron main/preload bundles, native-dependency staging, and post-build
  artifact checks. Vite reported the existing CSS-parser and large-single-chunk
  warnings.
- Requested literal `npx vitest run apps/desktop` — could not collect existing
  desktop suites because the root invocation does not load the desktop Vite
  alias (`@/...`) configuration.
- Full corrected renderer attempt after removing build-emitted, ignored `.js`
  duplicates from the TypeScript source tree:
  `npx vitest run --environment jsdom --config apps/desktop/vite.config.ts
  apps/desktop/src` — 145 files passed and 10 files failed; 1,262 tests passed
  and 21 failed. The failures are outside WS-13 and cover pre-existing pane
  persistence, model-option expectations, provider mocks, attachment test
  selectors, preview storage, browser-title copy, and assistant timeline/jsdom
  behavior. All WS-13 files passed in this run.

Python tests were not run because this work makes no Python change.

## Security and privacy checks

- No credentials, customer data, logs, or secrets were added.
- Worker details remain local and read-only on this page except for the
  pre-existing explicit identity/SOUL/CRUD actions.
- No authorization, gateway, control-plane, or prompt-caching boundary changed.

## Visual evidence

- Production renderer build completed successfully.
- No screenshot was captured in this contextless worktree session. Manual
  Electron visual QA remains useful for narrow-window behavior and titlebar
  spacing.

## Known risks and limitations

- `gateway_running` is gateway availability, not a richer job/activity state;
  the UI therefore limits labels to Current, Online, and Standby.
- Toolsets and terminal workdir are intentionally absent until the backend
  returns explicit fields.
- The full repository-wide Vitest command is not currently a clean baseline;
  use the focused green suite above for WS-13 regression verification.

## Integration order and conflicts

- WS-14 may conflict in `apps/desktop/src/app/routes.ts` and
  `apps/desktop/src/app/chat/sidebar/index.tsx`. WS-13 only removes `profiles`
  from `OVERLAY_VIEWS`, adds the existing profile route as a full page, and
  appends one nav item/active predicate. Preserve both workstreams' route and
  nav additions during conflict resolution.
- No files under `src/app/channels/` were created or modified.

## Exact next actions

1. Integrate WS-13 with WS-14 while preserving both append-only sidebar items
   and routes.
2. Launch Atlas Desktop and visually verify Workers at wide and narrow window
   sizes, including create, rename, and delete dialogs.
3. If toolsets/workdir are required in the summary, add explicit read-only
   fields to the profile-list endpoint in a separate backend workstream.
