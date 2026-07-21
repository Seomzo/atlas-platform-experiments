# WS-21 Handoff — Worker Chat Models

## Status

- Branch: `codex/ws-21-worker-models`
- PR: None (this workstream must not open one)
- Baseline: `3d8923a0b`
- Last validated: 2026-07-21

## Goal and scope

Atlas Desktop now lets Omar choose the provider and main conversation model for each worker from the Workers tab. The worker detail page:

- reads the selected worker's config and resolved model metadata;
- shows friendly provider/model identity and the exact model slug;
- opens the existing searchable, provider-grouped model picker in that worker's scope;
- saves optimistically, confirms the persisted result by re-fetching, and rolls back with friendly copy on failure;
- warns when the worker's configured provider is not authenticated;
- refreshes the worker roster so its summary reflects the saved model immediately.

The create-worker dialog also has a collapsed optional Model row. It defaults to **Same as Atlas**, writes Atlas's current provider/model explicitly at creation, and can assign another connected model before the worker is created.

This affects future worker sessions, including new channel and DM sessions backed by that worker profile. Existing live sessions and explicit per-session model overrides are intentionally not mutated, preserving conversation prompt caching and session isolation.

No channel routing, session binding, gateway platform, Starmap, or Electron code changed.

## Exact persisted shape

Worker detail changes use the existing profile-scoped config API:

```json
PUT /api/config
{
  "profile": "codex",
  "config": {
    "model": {
      "provider": "openai-api",
      "default": "gpt-4o-mini"
    }
  }
}
```

The Electron request also carries `profile: "codex"` as routing metadata, so local and remote per-profile backends receive the request in the same way as other worker-scoped settings.

Only `model.provider` and `model.default` are submitted. The config endpoint recognizes that exact minimal assignment and routes it through the existing main-model normalization/assignment helpers. This preserves same-provider endpoint settings while clearing a previous provider's stale `base_url`, endpoint key/mode, and `context_length` on a provider switch. The outer deep merge cannot resurrect removed model subkeys.

Create-time selection uses the existing `POST /api/profiles` `provider` and `model` fields. The backend writes those fields into the new worker's `model.provider` and `model.default` through the same assignment helper.

## Picker reuse

There is no second picker implementation. `WorkerModelSection` and the create dialog render the shared `ModelPickerDialog`, retaining its existing search, friendly model labels, current-selection indicator, and provider grouping.

The shared `requestModelOptions` path now accepts an optional worker name. Explicit worker requests intentionally use the already-existing profile-scoped REST model-options endpoint, even when the foreground chat gateway is connected, because that gateway represents the active chat profile. The normal active-session picker keeps its gateway behavior unchanged.

The existing Add Provider action is hidden inside named-worker pickers because its onboarding flow is currently coupled to the foreground profile. Worker pickers show only providers already connected for that worker; provider setup remains available through the existing worker setup/configuration path.

## Provider readiness

The Model section requests the worker's full provider catalog with `include_unconfigured=1`. It uses the existing catalog signals:

- `authenticated` determines ready versus needs-setup state;
- `auth_type` distinguishes the API-key warning copy;
- the provider catalog supplies the friendly provider name.

An already-configured but unauthenticated provider produces a **Needs setup** badge and an inline warning. A selection that resolves to an unauthenticated catalog row is rejected before persistence. No key value is fetched, exposed, copied, or logged.

## Files

Worker UI and coverage:

- `apps/desktop/src/app/profiles/worker-model-section.tsx`
- `apps/desktop/src/app/profiles/worker-model-section.test.tsx`
- `apps/desktop/src/app/profiles/index.tsx`
- `apps/desktop/src/app/profiles/index.test.tsx`
- `apps/desktop/src/app/profiles/create-profile-dialog.tsx`
- `apps/desktop/src/app/profiles/create-profile-dialog.test.tsx`

Shared picker/config plumbing:

- `apps/desktop/src/components/model-picker.tsx`
- `apps/desktop/src/lib/model-options.ts`
- `apps/desktop/src/lib/model-options.test.ts`
- `apps/desktop/src/hermes.ts`
- `apps/desktop/src/types/hermes.ts`

Locales:

- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`

Profile-scoped persistence and proof:

- `hermes_cli/web_server.py`
- `tests/hermes_cli/test_web_server_profile_unification.py`

Handoff:

- `docs/altas/workstreams/WS-21-HANDOFF.md`

## Tests and results

Passed:

- `npx vitest run --environment jsdom --config apps/desktop/vite.config.ts apps/desktop/src/app/profiles apps/desktop/src/i18n apps/desktop/src/lib/model-options.test.ts apps/desktop/src/hermes.test.ts` — 8 files, 51 tests passed.
- `npx tsc --noEmit -p apps/desktop` — passed with no output.
- `scripts/run_tests.sh tests/hermes_cli/test_web_server_profile_unification.py` — 32 tests passed, 0 failed.
- `scripts/run_tests.sh tests/hermes_cli/test_web_server.py -k 'ModelContextLength or DenormalizeProviderSwitch'` — 17 selected tests passed, 0 failed.
- focused Desktop ESLint over every touched TypeScript/TSX/i18n file — passed with no output.
- `git diff --check` — passed with no output.

New behavior coverage includes:

- current worker model/provider rendering from profile-scoped endpoints;
- the exact worker-scoped PUT body and Electron routing scope;
- optimistic success and roster refresh;
- failed-save rollback and friendly error copy;
- unauthenticated-provider warning state;
- create-time Same as Atlas default and explicit alternate selection;
- named-worker REST option resolution while a chat gateway exists;
- API-level cross-profile isolation plus stale endpoint/key/context cleanup.

## Manual QA for Omar

1. Open **Workers**, select the Codex worker, and find the new **Model** section. Confirm it shows a friendly model name, provider name, and exact model slug.
2. Click **Change model**. Search for a cheap chat model already connected for Codex, such as `gpt-4o-mini`, and select it. Confirm the provider grouping and current-model check behave like the main model picker.
3. Confirm the section updates immediately, then remains on the selected model after the save completes. The Codex roster row should refresh to the new friendly model name.
4. Close and reopen Atlas Desktop, return to Codex, and confirm the choice persisted. For a direct disk check, inspect the Codex worker's `config.yaml`: `model.provider` and `model.default` should match, while the default Atlas config should be unchanged.
5. To prove runtime behavior, start a **fresh** Codex DM session after the save (use `/new` in an existing Codex DM before sending the next message, or create a new DM binding). Send a message, then open Sessions and inspect that worker session's model stamp; it should show the cheap model/provider. `/status` in the DM should report the same model where that platform exposes model status.
6. Repeat through a channel bound to Codex if desired: create/reset the session after the save, send a message, and confirm the session row/status stamp uses Codex's selected model.
7. Existing live conversations may continue their established model, especially when they have an explicit session override. This is expected; WS-21 changes the worker default for future sessions rather than mutating cached conversation state.
8. Select a worker whose configured provider is missing its credential. Confirm the Model section shows **Needs setup** and an API-key/connection warning instead of implying the worker is ready.
9. Open **Create worker**. The Model row should start collapsed and read **Same as Atlas**. Expand it, choose another connected model, create the worker, and confirm its detail page shows that choice.

## Security and integration notes

- Provider readiness uses existing boolean/catalog metadata only; secrets never enter renderer state.
- Worker writes are scoped both in transport routing and in the request body, then re-read from the same worker before success is shown.
- The API regression test proves the worker config changes while the default profile remains unchanged.
- No model tool, config environment variable, credential endpoint, storage migration, or new API route was added.
- Preserve the explicit-profile REST branch in `requestModelOptions`; sending named-worker requests through the foreground chat gateway would show the wrong profile's provider inventory.
