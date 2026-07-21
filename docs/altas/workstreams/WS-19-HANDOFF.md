# WS-19 Handoff — Desktop worker and Starmap polish

## Status

- Branch: `codex/ws-19-desktop-polish`
- PR: None (per workstream instructions)
- Baseline: `3a271d5cefbcc266eaa1ffff81225563e8a7395f`
- Last validated: 2026-07-21

## Scope

This workstream fixes two desktop UX bugs reported on 2026-07-21:

1. worker creation exposed a technical profile-id collision and rendered the
   raw backend/IPC failure;
2. the Brain Constellation Starmap visually covered its standard close button.

No Starmap visual redesign, channel code, Electron code, gateway platform, or
profile runtime behavior was changed.

## Root causes

### Worker name collision

`CreateProfileDialog` did not derive the profile slug from the display name or
role. Its first step collected a separate `name` field labeled "Worker ID";
the second step collected `displayName` and `role`. Submit posted the first
field verbatim as `createProfile({ name })` before saving identity. Therefore
the rejected slug `test` came from the separately entered Worker ID. The role
also being `test` was coincidental.

Python's `_RESERVED_NAMES` includes `test`, so `validate_profile_name()` raised
a `ValueError` mentioning "the Hermes installation." The profile endpoint
returned that text in an HTTP 400 `detail`. Electron's JSON bridge wrapped the
response in an `Error` containing the internal `hermes:api` method and raw JSON,
and the dialog rendered `err.message` without translation or sanitization.

### Missing Starmap close affordance

`StarmapView` already used the standard `Panel`/`OverlayView`, so click and
Escape close behavior existed. The standard titlebar chrome was at `z-10`,
while the new Brain Constellation header was at `z-30`. The immersive header
painted over the X even though its layout reserved right-side space for it.

## Fixes

### Worker creation

- The identity step now comes first. A safe lowercase worker id is derived from
  the display name, normalized to ASCII letters/digits and hyphens, capped at
  64 characters, and shown as a localized live preview. Non-Latin-only names
  safely fall back to `worker`.
- Added `GET /api/profiles/name-suggestion?name=...`. The backend remains the
  source of truth for reserved and existing ids and returns the first available
  candidate: the base id, then `-2`, `-3`, and so on. Invalid ids return 400
  immediately.
- The dialog debounces the live name check and repeats it immediately before
  creation, closing the ordinary collision window. If the preview endpoint is
  temporarily unavailable, creation still proceeds with the safe local
  derivation and uses the friendly error fallback.
- Reserved, taken, and invalid 400 errors map to localized friendly copy.
  Unknown errors map to a generic retry message. Raw exception text is never
  rendered, so `hermes:api`, JSON, and upstream product naming cannot leak.
- `validate_profile_name()` now passes its remaining reserved-name detail
  through `brand_text()`, producing "Atlas installation" in the Atlas runtime.

### Starmap close

- Raised the standard `OverlayView` titlebar chrome to `z-40`, above the
  constellation's `z-30` header. The existing standard X is now visible and
  navigates back to chat; the existing Escape behavior remains intact.

## Files changed

Desktop worker flow and tests:

- `apps/desktop/src/app/profiles/create-profile-dialog.tsx`
- `apps/desktop/src/app/profiles/create-profile-dialog.test.tsx`
- `apps/desktop/src/app/profiles/index.test.tsx`
- `apps/desktop/src/hermes.ts`
- `apps/desktop/src/types/hermes.ts`

Starmap overlay chrome and test:

- `apps/desktop/src/app/overlays/overlay-view.tsx`
- `apps/desktop/src/app/starmap/index.test.tsx`

Localized copy:

- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`

Backend validation and tests:

- `hermes_cli/profiles.py`
- `hermes_cli/web_server.py`
- `tests/hermes_cli/test_profile_identity.py`

Handoff:

- `docs/altas/workstreams/WS-19-HANDOFF.md`

## Contracts and migrations

- New read-only endpoint:
  `GET /api/profiles/name-suggestion?name=<derived-id>`.
- Response shape:
  `{ available: boolean, name: string, suggestion: string }`.
- No persistence, configuration, SQLite, or identity-file migration.
- Existing `POST /api/profiles` semantics remain unchanged; callers that need a
  stable exact id can continue posting it directly.

## Validation

Passed required commands:

- `npm --prefix apps/desktop run test:ui -- src/app/profiles/create-profile-dialog.test.tsx src/app/profiles/index.test.tsx src/app/starmap/index.test.tsx src/i18n`
  — 6 files, 28 tests passed.
- `npx tsc --noEmit -p apps/desktop` — passed with no output.
- `scripts/run_tests.sh tests/hermes_cli/test_profile_identity.py` — 1 file,
  33 tests passed.
- Focused ESLint over every touched TypeScript/TSX file — passed with no
  output.
- Focused Prettier check over every touched TypeScript/TSX file — passed.
- `git diff --check` — passed.

Regression coverage verifies reserved/existing suffix selection, invalid-name
rejection, Atlas-branded backend detail, display-name slug preview, submit-time
collision resolution, friendly known/generic error rendering without raw
transport text, and visible Starmap close navigation above the constellation.

## Notes

- The pre-existing untracked root `node_modules` entry was not modified or
  included.
- No build command was run; no generated `.js` or `.js.map` file belongs to
  this workstream.
