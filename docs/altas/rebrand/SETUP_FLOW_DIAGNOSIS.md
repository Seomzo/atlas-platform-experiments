# Atlas Desktop Setup Flow Diagnosis

**Date:** 2026-07-16
**Symptom (Omar):** Launching the Atlas desktop app "took me straight to step 2 or 3 of the setup, which is just the API keys" instead of starting a full guided setup.
**Verdict:** This is not a routing bug. The desktop app **has no full setup wizard**. Its entire first-run experience is a single provider-credential overlay inherited from Hermes, recently wrapped in a decorative "blueprint" screen whose sidebar *displays* steps 01–06 with steps 01–02 pre-checked and step 03 highlighted. The app therefore *looks like* it skipped to step 3, and one click later the only real content is the provider/API-key form. Parity with the terminal `atlas setup` flow (ADR-004/ADR-005) has not been implemented.

---

## 1. What setup/onboarding UI exists in the desktop app, and what triggers it

The desktop's only onboarding surface is `DesktopOnboardingOverlay`, mounted unconditionally in the app shell:

- **Mount point:** `apps/desktop/src/app/desktop-controller.tsx:1295–1303` — rendered with `enabled={gatewayState === 'open'}`, i.e. it activates as soon as the JSON-RPC gateway connection to the Python backend is open.
- **Component:** `apps/desktop/src/components/onboarding/index.tsx:163` (`DesktopOnboardingOverlay`), split from a former god-file into `apps/desktop/src/components/onboarding/` (`index.tsx`, `flow.tsx`, `providers.tsx`, `memory-model-picker.tsx`, `setup-blueprint.tsx`, `glyph.tsx`).
- **State store:** `apps/desktop/src/store/onboarding.ts` (`$desktopOnboarding` atom).

**First-run trigger chain:**

1. On mount / whenever `enabled` or `requested` flips, the overlay calls `refreshOnboarding(ctx)` (`index.tsx:234–238`).
2. `refreshOnboarding` (`store/onboarding.ts:1038–1148`) calls `checkRuntime` (`store/onboarding.ts:267–273`) → `evaluateRuntimeReadiness` (`apps/desktop/src/lib/runtime-readiness.ts:145–152`), which issues **two gateway RPCs in parallel** (`runtime-readiness.ts:74–77`):
   - `setup.status` → `tui_gateway/server.py:12375` → `_has_any_provider_configured()` (is *any* provider credential discoverable?)
   - `setup.runtime_check` → `tui_gateway/server.py:12385` → `resolve_runtime_provider()` (does the configured/default model *actually* resolve to a usable runtime + credential?)
3. If the runtime is **not ready** (no usable provider credential), `refreshOnboarding` sets `configured: false` (`store/onboarding.ts:1138–1139`) and loads the OAuth provider list. The overlay then shows.
4. If the runtime **is ready**, it additionally validates the Cortex memory-model route (`store/onboarding.ts:1062–1115`) and either dismisses the overlay or drops into a memory-model recovery flow.
5. A localStorage cache (`atlas-desktop-onboarded-v1`, plus legacy `hermes-desktop-onboarded-v1`; `store/onboarding.ts:126–129`) seeds `configured` so returning users don't flash the overlay. A separate `atlas-onboarding-skipped-v1` flag records "I'll choose a provider later" (`dismissFirstRunOnboarding`, `store/onboarding.ts:1026–1032`).

**What the overlay actually contains** (the complete inventory of desktop "setup"):

1. **`SetupBlueprint`** (`setup-blueprint.tsx`) — a static dark-blue "Configuration preview" screen added in commit `0f130e3c6` ("feat: define Atlas desktop onboarding defaults"). One button: **"Connect intelligence"**.
2. **Provider picker** (`index.tsx:447–…`, `Picker`) — Nous Portal featured row, other OAuth providers, or an **API-key form** (`ApiKeyForm`, OpenRouter/OpenAI/Gemini/xAI/local endpoint + long tail derived from `model.options`; `index.tsx:56–156`).
3. **OAuth/key flow panel** (`flow.tsx`) — sign-in, device code, key validation.
4. **Model confirmation** → **Cortex memory-model confirmation** (`store/onboarding.ts` flow states `confirming_model`, `confirming_memory_model`) → done.

That's it. There are no account, store, Tekion, permissions, delivery, TTS, terminal-backend, messaging, tools, or agent-settings steps anywhere in the desktop.

## 2. Exact decision logic for which step is shown (file:line)

All in `apps/desktop/src/components/onboarding/index.tsx`:

| Line(s) | Logic |
| --- | --- |
| `index.tsx:275–277` | `if (onboarding.configured === true && !onboarding.manual) return null` — overlay hidden entirely when a provider is already configured. |
| `index.tsx:282–284` | Hidden if the user previously clicked "I'll choose a provider later" (`firstRunSkipped`) and flow is idle. |
| `index.tsx:303` | `ready = onboarding.manual \|\| (enabled && onboarding.configured === false)` — waits for the runtime check before showing real content; otherwise shows the `Preparing` boot progress panel (`index.tsx:363–365, 384–411`). |
| `index.tsx:304` | `showPicker = flow.status === 'idle' \|\| flow.status === 'success'`. |
| **`index.tsx:305`** | `showBlueprint = ready && showPicker && blueprintOpen && !onboarding.manual && !onboarding.localEndpoint` — the blueprint screen is the first thing a not-configured first-run user sees. `blueprintOpen` is plain `useState(true)` (`index.tsx:183`), reset on every mount; "Connect intelligence" sets it false (`index.tsx:352–353`). |
| `index.tsx:352–367` | Render switch: blueprint → else picker / flow panel / preparing. |
| **`index.tsx:459–476`** | Inside `Picker`: `if (localEndpoint \|\| mode === 'apikey' \|\| !hasOauth)` → render **`ApiKeyForm` directly**. `hasOauth` is `ordered.length > 0` from the OAuth provider list (`index.tsx:451–452`). So if the OAuth provider fetch returns empty or hasn't been committed, the user lands **straight on the API-keys form** with no picker at all. |

The "step" numbers the user perceives come from `setup-blueprint.tsx:4–11`:

```ts
const SETUP_STAGES = [
  { index: '01', label: 'Account',       state: 'managed' },  // pre-checked ✓
  { index: '02', label: 'Store',         state: 'managed' },  // pre-checked ✓
  { index: '03', label: 'Intelligence',  state: 'current' },  // highlighted
  { index: '04', label: 'Tekion access', state: 'next' },
  { index: '05', label: 'Permissions',   state: 'queued' },
  { index: '06', label: 'Delivery',      state: 'queued' },
]
```

This array is **hard-coded**. Stages 01/02 render a checkmark ("Managed default", `setup-blueprint.tsx:81–83`) and stage 03 is highlighted as current. Nothing computes these states; there is no navigation between stages; stages 01, 02, 04, 05, 06 have no implementation. The screen's own footer admits it: *"Account and store enrollment are coming next. For now, connect the intelligence provider that powers this development workstation."* (`setup-blueprint.tsx:134–137`).

## 3. Does the desktop reuse the terminal setup wizard? No.

- The terminal wizard is `run_setup_wizard` in `hermes_cli/setup.py:2809`, with the Quick Setup / Full Setup / Blank Slate mode choice at `setup.py:2950–2965`, the six sections in `SETUP_SECTIONS` at `setup.py:2717–2724` (`model`, `tts`, `terminal`, `gateway`, `tools`, `agent`), section-specific invocation at `setup.py:2869–2897`, quick/reconfigure handling at `setup.py:2914–2934`, and config backup at `setup.py:2838–2851`. The CLI parser is `hermes_cli/subcommands/setup.py:12–58`.
- **None of that is reachable from the desktop.** The gateway (`tui_gateway/server.py`) exposes ~119 JSON-RPC methods; the only setup-related ones are `setup.status` (line 12375) and `setup.runtime_check` (line 12385) — both read-only probes. There is no `setup.wizard`, no section runner, no mode selection RPC.
- The desktop instead writes configuration through ad-hoc REST endpoints on the web server (`apps/desktop/src/hermes.ts`): `/api/env` for keys (`setEnvVar`, hermes.ts:~505), `/api/providers/oauth/*` for OAuth (`hermes.ts:523–561`), plus `model.options`/`model.save_key`/`model.disconnect` gateway methods and Cortex memory-model endpoints. This is exactly the "second, divergent interpretation of configuration" that ADR-005 prohibits — the desktop onboarding is its **own React implementation** covering only the provider slice of section 1 of 6.
- Interestingly, `cli.exec` exists as a gateway method, but nothing routes the desktop into the interactive wizard through it, and the wizard is TTY-interactive (`setup.py:2853–2862` bails to guidance text without a TTY), so it couldn't be embedded as-is.

## 4. Why Omar landed directly on an "API keys" step

Compounding causes, in order of impact:

1. **The desktop's whole setup *is* the provider step.** By design (inherited from Hermes), first-run onboarding is gated on one question — "can the backend resolve a usable model provider?" (`setup.runtime_check`). Fresh install ⇒ `configured: false` ⇒ overlay opens. There are no earlier steps to show.
2. **The new blueprint screen visually claims steps 01–02 are already done.** Commit `0f130e3c6` added the `SetupBlueprint` with hard-coded `state: 'managed'` checkmarks on Account and Store and `'current'` on 03 Intelligence (`setup-blueprint.tsx:4–11`). To a user, that reads as "setup jumped to step 3." It's a static mock of the *future* customer sequence from `docs/altas/SETUP_DEFAULTS.md` ("Proposed future customer setup sequence", stages 1–7), not a functioning stepper.
3. **One click later, the only content is provider/API keys.** "Connect intelligence" (`setup-blueprint.tsx:139–146`) closes the blueprint and reveals `Picker`. And per `index.tsx:459`, if the OAuth provider list is empty or hasn't loaded (`!hasOauth`) — plausible in an isolated first-run environment where `/api/providers/oauth` returns nothing usable — the picker is bypassed entirely and the **raw API-key form** renders immediately. Even when OAuth rows do load, the visible content is "connect a provider or paste an API key," i.e. exactly what Omar described.
4. **Possible step-skipping via stale caches** (secondary): `configured` is seeded from localStorage (`store/onboarding.ts:139–167`, including legacy `hermes-desktop-onboarded-v1` migration) and `firstRunSkipped` from `atlas-onboarding-skipped-v1`. A previous dev session on the same Electron user-data dir can suppress or shorten the flow. The README recipe added in `ccaedef3c` ("fix Atlas desktop first-run launch", `apps/desktop/README.md`) exists precisely because credentials/state auto-discovered from the real `$HOME`/`HERMES_HOME`/user-data dir contaminate first-run previews — a true blank-slate preview requires isolating all three.
5. **If flow status isn't idle** (e.g. a recovery/memory-model error state), `showPicker` is false ⇒ `showBlueprint` is false (`index.tsx:304–305`), so the app can drop the user mid-flow past the blueprint entirely.

Bottom line: the app didn't "skip" anything — steps 1–2 (and 4–6) don't exist, and the sidebar is a rendering of a product aspiration, not of progress.

## 5. What full desktop setup parity requires

Per the accepted directives — `docs/altas/PROJECT_CONTEXT.md` §"Current setup decision", `docs/altas/SETUP_DEFAULTS.md` §"Confirmed current directive: desktop parity first", and `docs/altas/DECISIONS.md` **ADR-004** ("Desktop setup parity before managed defaults", accepted 2026-07-13) and **ADR-005** ("One underlying setup implementation") — the desktop must offer the *same* modes, sections, options, validation, cancellation, backups, and summaries as terminal `atlas setup`, via **shared backend services, not a React reimplementation**.

Concretely that means:

1. **A typed setup backend contract in the gateway** (ADR-005's "typed backend contract rather than copying terminal prompt logic into React"). The wizard logic in `hermes_cli/setup.py` is currently welded to TTY prompts (`prompt`, `prompt_choice`, `prompt_checklist`, `prompt_yes_no`, setup.py:293–462). Parity requires factoring each section's *decision model* (choices, current values, defaults, validation, apply/commit) out of the prompt loop into an engine both surfaces drive — e.g. gateway methods like `setup.begin(mode)`, `setup.sections`, `setup.section_state(key)`, `setup.answer/apply`, `setup.summary`, `setup.cancel`. A step-descriptor/server-driven-form approach keeps the desktop from re-encoding defaults.
2. **All three first-run modes:** Quick Setup (Nous Portal one-shot ≈ `_run_portal_one_shot`/`_run_first_time_quick_setup`), Full Setup, Blank Slate (`setup.py:2950–2965`), plus existing-install behaviors: full reconfigure showing current values as keep-on-Enter defaults (`setup.py:2914–2934`), `--quick` missing-items-only (`_run_quick_setup`), reset (`setup.py:2827–2830`), and section-specific re-entry (`setup.py:2869–2897`).
3. **All six sections** from `SETUP_SECTIONS` (`setup.py:2717–2724`): Model & Provider, Text-to-Speech, Terminal Backend, Messaging Platforms (Gateway), Tools, Agent Settings — with every provider/model/terminal/gateway/tool/skill/plugin/MCP/memory/agent choice the terminal exposes. Managed-default recommendations may be *labeled* but not hidden (ADR-004 consequence).
4. **Shared side-effect rules:** config backup before mutation (`setup.py:2838–2851`), secret writing through the same `.env`/config paths the CLI uses (no independent secret-writing behavior — ADR-005), the same validation and end-of-run summary (`_print_setup_summary`, setup.py:492).
5. **UI:** a real multi-step Atlas-branded wizard (the dark-blue workstation aesthetic the blueprint already establishes) with genuine progress state, back/cancel, current-value display, and accessibility — replacing the static `SETUP_STAGES` array with state derived from the backend contract.
6. **Keep the terminal wizard as reference and recovery path** (SETUP_DEFAULTS.md directive) — the engine refactor must not regress `atlas setup`.
7. The proposed 7-stage customer sequence (Account/Store/Intelligence/Tekion/Permissions/Delivery/Review) is the *future* managed product, explicitly **not** a license to ship fewer options now ("These are preserved… They do not override ADR-004", DECISIONS.md:100–101). The blueprint screen can remain as a preview/landing, but should stop implying stages are complete.

## 6. Recommended next steps

1. **Immediate copy fix (low risk):** change `SETUP_STAGES` presentation in `setup-blueprint.tsx` so stages 01/02 read as "coming soon"/"not yet available" rather than checked-off "managed" — eliminating the "it skipped steps" impression without touching flow logic.
2. **Design the shared setup contract (the ADR-005 deliverable):** write a short spec for gateway `setup.*` methods (session-based wizard: begin/mode, list sections, get section state incl. current values, submit answers, validate, summary, apply-with-backup, cancel/reset). Decide between (a) fully typed per-section schemas or (b) a generic server-driven form descriptor; (b) tracks terminal changes with far less desktop churn.
3. **Refactor `hermes_cli/setup.py` incrementally:** extract one section first (Model & Provider is mostly duplicated in desktop already — OAuth, key save, model assignment, memory model) into a prompt-free engine consumed by both the TTY wizard and new gateway methods; verify `atlas setup model` is behavior-identical; then proceed section by section (TTS → Terminal → Gateway → Tools → Agent).
4. **Build the desktop wizard shell:** mode chooser (Quick/Full/Blank Slate) as the true first screen after the blueprint, real stepper bound to backend section list, per-section forms rendered from the contract, summary + backup confirmation at the end. Reuse the existing provider flow components (`flow.tsx`, `providers.tsx`, memory-model picker) as the Model & Provider section body.
5. **Fix the `!hasOauth` fall-through** (`index.tsx:459`): distinguish "OAuth list still loading/failed" from "no OAuth providers exist" so a slow or failed fetch doesn't silently dump users on the raw API-key form.
6. **Harden first-run detection:** consider a backend-authoritative first-run signal (e.g. `setup.status` returning "never completed setup" vs "provider missing") instead of localStorage caches + provider probing, so a wiped `HERMES_HOME` reliably restarts full setup and stale Electron user-data can't skip it. Use the `ccaedef3c` README isolation recipe (`HOME`/`HERMES_HOME`/`HERMES_DESKTOP_USER_DATA_DIR` to temp dirs) as the standard QA procedure for verifying first-run behavior.
7. **Add a parity checklist test/doc:** enumerate every terminal prompt (modes, sections, flags: `--quick`, `--reset`, `--reconfigure`, `--portal`, per-section) and track desktop coverage, so ADR-004 compliance is measurable before the managed-defaults decision session with Omar and Joe.

---

### Key file:line index

| Concern | Location |
| --- | --- |
| Overlay mount / trigger | `apps/desktop/src/app/desktop-controller.tsx:1295–1303` |
| Show/hide + step decision | `apps/desktop/src/components/onboarding/index.tsx:275–284, 303–305, 352–367` |
| API-key form fall-through | `apps/desktop/src/components/onboarding/index.tsx:459–476` |
| Fake 6-step sidebar | `apps/desktop/src/components/onboarding/setup-blueprint.tsx:4–11, 81–83, 134–137` |
| Runtime readiness / refresh | `apps/desktop/src/store/onboarding.ts:267–273, 1038–1148`; `apps/desktop/src/lib/runtime-readiness.ts:68–152` |
| localStorage gates | `apps/desktop/src/store/onboarding.ts:126–129, 139–230` |
| Gateway setup RPCs (read-only) | `tui_gateway/server.py:12375 (setup.status), 12385 (setup.runtime_check)` |
| Terminal wizard | `hermes_cli/setup.py:2809 (run_setup_wizard), 2950–2965 (mode choice), 2717–2724 (SETUP_SECTIONS), 2869–2897 (sections), 2914–2934 (reconfigure/quick)` |
| CLI parser | `hermes_cli/subcommands/setup.py:12–58` |
| Product decisions | `docs/altas/DECISIONS.md` ADR-004 (:38–47), ADR-005 (:49–56); `docs/altas/SETUP_DEFAULTS.md:23–48`; `docs/altas/PROJECT_CONTEXT.md:94–99` |
| Relevant commits | `0f130e3c6` (blueprint + defaults doc), `ccaedef3c` (first-run isolation recipe + session-header fix) |
