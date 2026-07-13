# Atlas Agent Workstreams

## Purpose

This document contains paste-ready prompts for fresh Codex sessions that have
no access to the conversation that created Atlas. Use one prompt per session.
Do not paste several prompts into one session.

The prompts intentionally require each session to read the repository's
canonical context and decision files. Chat history is not a project database;
the repository is.

## Coordination rules

1. Give each workstream its own Codex task, branch, and preferably its own git
   worktree. Concurrent tasks must never switch branches in the same checkout.
2. Ask Omar for the latest integration branch. If none is specified, the
   current baseline is `origin/codex/desktop-onboarding`.
3. Never run two workstreams that own the same integration files at the same
   time. The dependency waves below are designed to avoid that.
4. Every workstream creates a unique
   `docs/altas/workstreams/WS-XX-HANDOFF.md`. Do not use one shared status file;
   separate files merge cleanly.
5. Every handoff records scope, decisions, files, tests, screenshots or other
   evidence, known risks, deferred decisions, and exact follow-up work.
6. Commit only the workstream's files, push the branch to the private Atlas
   repository, and open a draft pull request. Never merge it automatically.
7. Proprietary Jay skills, dealership data, credentials, and live Tekion
   sessions must not be committed.

## Dependency waves

### Wave A — start now

- **WS-00:** Desktop setup parity contract. This is the first setup task.
- **WS-05:** Production account and device-enrollment contract.
- **WS-06:** Control Center product/API specification.
- **WS-08:** Jay skill-pack and skill-index framework.
- **WS-11:** Messaging and delivery decision package.
- **WS-12:** Desktop packaging and release-readiness audit.

WS-07 may also start if it remains inside a new browser-profile module and does
not edit Desktop setup integration files.

### Wave B — after WS-00 is reviewed and merged

- **WS-01:** Shared setup backend service.
- **WS-02:** Full Desktop setup UI.
- **WS-03:** Desktop setup secret boundary.

These may run in parallel only after they agree on the WS-00 contract and keep
to their file ownership boundaries.

### Wave C — after WS-01, WS-02, and WS-03 integrate

- **WS-04:** End-to-end terminal/Desktop parity verification.

### Product-platform sequence

- **WS-09** follows the WS-05 identity/device contract if it needs to modify
  control-plane policy schemas.
- **WS-10** follows WS-05 and WS-06 because billing entitlements depend on the
  organization, role, store, and console contracts.
- A later Control Center implementation session follows WS-05, WS-06, and
  WS-10; the current WS-06 prompt prevents premature framework/vendor choices.

---

## WS-00 — Desktop setup parity contract

Paste this into a fresh session first:

```text
Goal: Produce the authoritative terminal-to-desktop setup parity contract for Atlas. This is a careful discovery and specification task; do not implement the complete React wizard or backend service yet.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-00
Suggested branch: codex/ws-00-setup-parity-contract
Baseline: ask me for the latest integration branch; if I do not specify one, use origin/codex/desktop-onboarding. Work in an isolated git worktree/branch and do not switch the shared checkout's branch.

This is a contextless session. Before acting, read these files completely: AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, docs/altas/SETUP_DEFAULTS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, and docs/altas/UPSTREAM.md. Inspect the real current implementation instead of trusting documentation alone.

Confirmed product decision: the current Atlas Desktop milestone must preserve the exact functional choices and behavior of terminal `atlas setup`, including Quick Setup, Full Setup, Blank Slate, section-specific setup, reconfigure/current-value behavior, missing-items-only setup, reset, cancellation, validation, config backup, and final summary. All current provider, model, TTS, terminal, messaging, tool, skill, plugin, MCP, memory, and agent choices remain available. Future managed defaults are proposals only. The Desktop should use the established dark-blue Atlas workstation aesthetic.

Trace the actual setup graph through hermes_cli/setup.py, hermes_cli/subcommands/setup.py, hermes_cli/model_setup_flows.py, hermes_cli/tools_config.py, gateway setup code, config/auth helpers, tui_gateway/server.py, and the current Desktop onboarding files. Inventory dynamic registries and conditional branches rather than freezing current provider/model/tool counts.

Deliver:
1. docs/altas/DESKTOP_SETUP_PARITY.md containing every mode, section, nested decision, source function/module, config fields, secret fields, validation, side effects, cancel/back behavior, platform gates, existing-install behavior, and recovery path.
2. A proposed typed setup-session contract for catalog/snapshot/validate/apply/cancel/summary operations. Clearly separate ordinary config from secrets and long-running OAuth/device-code flows.
3. A terminal-to-desktop coverage matrix and dependency diagram.
4. A list of behavior that must remain dynamic so future upstream additions automatically reach Desktop.
5. A phased implementation plan with non-overlapping ownership for WS-01, WS-02, WS-03, and WS-04.
6. docs/altas/workstreams/WS-00-HANDOFF.md.

Do not select commercial defaults, remove choices, store secrets, or implement a parallel setup engine. Do not use live credentials or external accounts. Verify every claim against current code and call out any documentation drift you find. Documentation links and paths must be valid. Commit only this workstream, push it to the private origin, and open a draft PR; do not merge.
```

---

## WS-01 — Shared setup backend service

Run only after WS-00 is merged:

```text
Goal: Implement the shared typed backend service that lets Atlas Desktop execute the complete terminal setup workflow without duplicating setup defaults or secret-writing logic in React.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-01
Suggested branch: codex/ws-01-setup-backend
Dependency: the reviewed WS-00 DESKTOP_SETUP_PARITY.md and its typed contract must already be on the baseline branch.

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, docs/altas/SETUP_DEFAULTS.md, docs/altas/DESKTOP_SETUP_PARITY.md, docs/altas/SECURITY.md, and docs/altas/UPSTREAM.md completely. Inspect hermes_cli/setup.py, config/auth helpers, tui_gateway/server.py, and existing JSON-RPC conventions before designing anything.

Implement a narrow setup service and JSON-RPC surface that exposes the WS-00 catalog/session contract. Reuse or extract existing terminal setup functions and registries; do not create a second catalog of providers, models, terminals, gateways, tools, plugins, skills, MCP servers, memory providers, or defaults. Keep terminal `atlas setup` behavior working.

Requirements:
- Support fresh setup, existing-install reconfigure, section-only setup, missing-items-only setup, reset, validation, cancellation, config backup, and summary.
- Model long-running provider authentication as cancellable setup operations with explicit states.
- Return secret metadata such as configured/missing/replacement-required, never raw secret values.
- Ensure config writes are validated, scoped to the active Atlas home/profile, and recoverable after partial failure.
- Keep dynamic/provider/platform gates dynamic.
- Preserve managed-mode restrictions.
- Make errors structured and useful to the Desktop without leaking credentials.

Primary ownership: Python setup/config service modules, tui_gateway setup RPC handlers, and focused Python tests. Do not build the React wizard and do not redesign Electron secret storage; consume the WS-03 seam if it already exists.

Use scripts/run_tests.sh for Python tests as required by AGENTS.md. Add behavior/invariant tests with temporary HOME and HERMES_HOME, including real config round trips and cancellation; do not add brittle snapshots of changing catalogs. Update docs/altas/workstreams/WS-01-HANDOFF.md with the RPC contract, migrations, tests, and integration instructions. Commit only this scope, push it, and open a draft PR; do not merge.
```

---

## WS-02 — Full Atlas Desktop setup UI

Run after WS-00; it may run in parallel with WS-01 using a mock adapter:

```text
Goal: Build the complete Atlas Desktop setup and reconfiguration experience described by the approved WS-00 parity contract, preserving every current terminal setup choice while making the experience polished, coherent, and accessible.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-02
Suggested branch: codex/ws-02-desktop-setup-ui
Dependency: reviewed docs/altas/DESKTOP_SETUP_PARITY.md on the baseline branch.

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, docs/altas/SETUP_DEFAULTS.md, docs/altas/DESKTOP_SETUP_PARITY.md, and apps/desktop/README.md completely. Inspect the current Desktop architecture, nanostores, UI primitives, i18n, onboarding tests, setup-blueprint.tsx, and backend request conventions before editing.

Confirmed UX direction: retain the established dark navy/blue Atlas workstation aesthetic and the quality of the current setup blueprint. Do not expose a terminal. Do not hide choices because a future managed default has been proposed. Recommendations and advanced labels are allowed, but Quick Setup, Full Setup, Blank Slate, every setup section, and all corresponding choices must remain reachable.

Build a typed, testable wizard state machine and React surfaces for mode selection, section navigation, forms, OAuth/device-code progress, validation, back/cancel, reconfigure/current values, reset confirmation, backups/recovery messaging, and final review/summary. Support narrow windows, keyboard navigation, focus management, reduced motion, loading/error/retry states, and safe password-field behavior.

Use an adapter matching the WS-00 contract so UI work can use deterministic fixtures until WS-01 lands. The renderer must never persist or re-display raw secrets. Do not create a second provider/tool/default catalog in TypeScript; render backend descriptors dynamically and keep only presentation metadata that is truly UI-owned.

Primary ownership: apps/desktop/src setup/onboarding components, stores, types, i18n, and component tests. Avoid Python setup modules and Electron secret internals. Include visual evidence of all major screens. Run Desktop typecheck, lint, focused tests, and a production build. Create docs/altas/workstreams/WS-02-HANDOFF.md with screenshots, state-machine notes, test commands, and WS-01 integration seams. Commit only this scope, push it, and open a draft PR; do not merge.
```

---

## WS-03 — Desktop setup secret boundary

Run after WS-00; coordinate its typed seam with WS-01 and WS-02:

```text
Goal: Implement and verify the secure secret boundary used by Atlas Desktop setup while preserving current terminal setup compatibility.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-03
Suggested branch: codex/ws-03-setup-secret-boundary

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, docs/altas/SETUP_DEFAULTS.md, docs/altas/DESKTOP_SETUP_PARITY.md, docs/altas/SECURITY.md, PRODUCT.md, and the current credential-vault and Electron IPC code completely. Inspect existing save_env_value/auth.json/provider OAuth behavior before proposing changes.

Design and implement the narrowest safe flow for a Desktop form to submit, replace, validate, and remove a setup credential without exposing existing values to the renderer. Ordinary current development credentials may still need to land through the existing Atlas `.env`/auth compatibility paths so terminal and Desktop remain interoperable; do not silently migrate or break them. Establish an OS-vault-ready interface for production device and connector credentials without pretending all existing upstream providers already support it.

Requirements:
- No API, RPC, IPC response, renderer store, localStorage, URL, log, analytics event, error, support bundle, or test snapshot contains a raw secret.
- The UI can learn only configured/missing/invalid/replacement-required state.
- Secret inputs are cleared after submission and cannot be recovered through back navigation.
- Validation and writes are scoped to the active Atlas home/profile.
- Cancellation and failed validation leave the previous valid secret intact.
- macOS behavior is tested without using the developer's real Keychain; Windows/Linux contracts are explicit and honestly marked where unvalidated.
- Backward compatibility and migration behavior are documented.

Primary ownership: credential boundary modules, Electron main/preload IPC if required, redaction, and focused tests. Do not build the full wizard or change product defaults. Use temporary homes and fake keychain adapters. Run the required security and Desktop checks. Create docs/altas/workstreams/WS-03-HANDOFF.md, commit only this scope, push it, and open a draft PR; do not merge.
```

---

## WS-04 — End-to-end setup parity verification

Run only after WS-01, WS-02, and WS-03 are integrated:

```text
Goal: Prove end to end that Atlas Desktop setup has functional parity with terminal `atlas setup` and is safe on fresh and existing installations.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-04
Suggested branch: codex/ws-04-setup-e2e
Dependencies: integrated WS-01 backend, WS-02 UI, and WS-03 secret boundary.

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, docs/altas/SETUP_DEFAULTS.md, docs/altas/DESKTOP_SETUP_PARITY.md, docs/altas/TESTING.md, docs/altas/SECURITY.md, and every prior WS-01/02/03 handoff. Inspect the final merged code before selecting test seams.

Build hermetic behavior tests and a manual verification runbook covering Quick Setup, Full Setup, Blank Slate/minimal and opt-in paths, each section-only flow, reconfigure/current values, missing-items-only setup, reset, cancellation during ordinary and OAuth flows, invalid input, partial failure, backups, summary, profile isolation, and recovery. Compare meaningful resulting config/auth state between terminal and Desktop for representative paths; do not freeze changing provider/model/tool counts.

Use temporary HOME, HERMES_HOME, Electron userData, fake providers, fake browsers, and fake external auth. Never touch ~/.atlas, a real Keychain, live accounts, or network services. Assert that no sentinel secret appears in renderer state, logs, RPC/IPC results, screenshots, support output, or committed fixtures. Include macOS first-run testing and record honest gaps for other platforms.

Fix only issues directly required to make the parity suite pass; route architectural changes back to the owning workstream. Run scripts/run_tests.sh for Python paths plus Desktop typecheck, lint, unit/integration tests, production build, and the fresh-launch harness. Create docs/altas/workstreams/WS-04-HANDOFF.md with a signed-off coverage matrix and remaining release blockers. Commit, push, and open a draft PR; do not merge.
```

---

## WS-05 — Atlas account and device enrollment

This can run in parallel with WS-00 because its primary ownership is the Atlas
control-plane contract:

```text
Goal: Implement a production-shaped, provider-neutral Atlas account and device-enrollment vertical slice on top of the existing control-plane prototype.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-05
Suggested branch: codex/ws-05-device-enrollment

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, docs/altas/MVP_ACCEPTANCE.md, and existing altas/control_plane code/tests completely. Trace the existing tenant, store, subscription, device, agent, bearer, lease, and audit behavior before adding anything.

Target flow: a user authenticates in the system browser through an eventual OIDC provider; the server derives organization membership and role; a short-lived one-time enrollment transaction assigns a laptop to an allowed tenant/store; the laptop registers device-bound material; subsequent heartbeats exchange it for short-lived, scoped leases; an operator can revoke the device. Native clients must not embed a reusable client secret. Do not choose an identity vendor in this workstream.

Implement the provider-neutral domain/API contract and deterministic development adapter needed to exercise that flow locally. Prefer asymmetric/device-bound identity where the prototype can support it honestly; if a temporary secret remains, scope, hash, rotate, expire, and revoke it. Derive tenant from authenticated identity, never request-body trust. Enforce role/store relationships, enrollment expiry, single use, replay resistance, audit correlation, and safe errors. Preserve existing prototype commands and tests or provide explicit migrations.

Scope is control-plane models, schemas, repository, security, endpoints, migrations, client contract, and focused tests. A minimal CLI/test client is acceptable. Do not build the full Desktop login UI or select Auth0/Clerk/Cognito/etc. No live accounts or real dealership data.

Use scripts/run_tests.sh and add real API/database round-trip tests for success, expiry, replay, wrong tenant/store, revoked user/device, malformed proof, and concurrent redemption. Update architecture/security docs only where the implemented contract changes them. Create docs/altas/workstreams/WS-05-HANDOFF.md with the exact API and Desktop integration steps. Commit, push, and open a draft PR; do not merge.
```

---

## WS-06 — Production Control Center specification

This is a decision-ready product and technical specification, not a premature
framework rewrite:

```text
Goal: Define the production Atlas Control Center and Dealer Admin Portal precisely enough that separate frontend/backend agents can implement them without inventing permissions, tenancy, billing, or support behavior.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-06
Suggested branch: codex/ws-06-control-center-spec

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, docs/altas/ROADMAP.md, altas/control_plane, and the current localhost Control Center completely. Treat the current UI as a prototype, not automatically as the production frontend architecture.

Produce docs/altas/CONTROL_CENTER_SPEC.md covering:
- separation and possible shared shell for internal Atlas operators versus dealership admins;
- roles and permissions down to tenant, dealer group, store, device, agent, workflow/skill entitlement, job, approval, usage, billing, audit, support, and revocation actions;
- page/route information architecture and critical empty/loading/error/permission states;
- API inventory mapped to existing endpoints, missing endpoints, event/audit requirements, and pagination/filter/export needs;
- support impersonation/access rules that never bypass audit or tenant isolation;
- device enrollment, health, remote disable, skill rollout, browser/connector health, model usage, plan, invoice, retention, and incident workflows;
- production migration from localhost/SQLite/dev token to hosted/RBAC/PostgreSQL without coupling the frontend to a chosen identity or billing vendor;
- accessibility, responsive behavior, observability, and operational acceptance criteria;
- threat model and highest-risk authorization mistakes.

Create wireframes or route diagrams only when they clarify structure; do not implement a broad new app, pick vendors, or change production code. Mark every user/business decision required from Omar and Joe, present viable options and tradeoffs, and identify the reversible implementation seam. Cross-check against WS-05 if available but do not edit its owned code.

Deliver docs/altas/CONTROL_CENTER_SPEC.md and docs/altas/workstreams/WS-06-HANDOFF.md. Validate all code/document claims against the repository and use current screenshots of the prototype when helpful. Commit, push, and open a draft PR; do not merge.
```

---

## WS-07 — Per-store persistent browser profile manager

This may start independently if it stays in a new module and avoids Desktop
setup integration files:

```text
Goal: Build and verify the local per-store persistent browser-profile manager that Atlas will use for Tekion computer-use workflows.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-07
Suggested branch: codex/ws-07-browser-profile-manager

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, and the browser sections of docs/altas/SETUP_DEFAULTS.md. Inspect all existing browser, CDP, Camofox, Browserbase, browser tool, and Electron process-management code before adding a manager.

Implement a narrow Atlas-owned browser-profile lifecycle abstraction for create, inspect, launch visible Chromium, attach through a loopback-only CDP endpoint, health check, stop, reauthorize-required state, repair, and safe delete. Profiles are keyed by verified tenant/store/device context and must never share cookies, local storage, downloads, ports, or process state. The employee's normal browser profile is never reused.

Use a testable process/browser adapter so tests do not require live Tekion or the developer's real Chrome profile. Handle stale locks, crashed processes, port collisions, concurrent launch attempts, upgrades, corrupted metadata, and deletion while running. Do not capture or log cookies/passwords. Ensure filesystem permissions and path validation prevent cross-store access and traversal. Define human takeover/MFA behavior without automating MFA.

Primary ownership: a new Atlas browser-profile module, focused local API/CLI seam if needed, and tests. Do not build the final Desktop setup pages, use live Tekion credentials, claim API partnership, or implement generic ungoverned browser access. Integrate only through existing `browser.cdp_url` or another minimal verified seam.

Use scripts/run_tests.sh for Python tests and real temporary-directory/process-boundary tests where safe. Create docs/altas/BROWSER_PROFILE_MANAGER.md and docs/altas/workstreams/WS-07-HANDOFF.md with lifecycle/state diagrams and Desktop integration instructions. Commit, push, and open a draft PR; do not merge.
```

---

## WS-08 — Jay skill pack and skill index framework

This can start before the proprietary skills are supplied by using synthetic
fixtures only:

```text
Goal: Build the private Atlas skill-pack packaging and indexing framework needed for Atlas Core and Jay Premium, without importing or fabricating the proprietary work-agent skills themselves.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-08
Suggested branch: codex/ws-08-skill-pack-index

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, docs/altas/SETUP_DEFAULTS.md, and all existing skill loader, skill usage, curator, Skills Hub, optional-skill, and plugin code relevant to indexing and lifecycle. Preserve prompt caching and existing skill activation semantics.

Design and implement an Atlas-owned pack manifest and local index that can represent pack identity, skill identity, immutable version, description, source/provenance, compatibility, permissions/capabilities requested, tenant/store scope, entitlement, signature/checksum state, rollout channel, enabled state, use/view/edit counts, last-used time, outcome/health metadata, and rollback predecessor. Usage metadata may influence retrieval and product decisions but never permission.

Requirements:
- Import from a supplied directory through explicit validation; never scrape or copy proprietary contents from another machine automatically.
- Keep executable skills separate from dealer-authored knowledge.
- Reject traversal, symlink escape, duplicate identity/version, malformed manifests, unexpected executables, and checksum/signature mismatch.
- Never expose private skill contents to the cloud index unless a future explicit policy allows it.
- Support deterministic install, index rebuild, enable/disable, staged update, rollback, and uninstall semantics with synthetic fixtures.
- Extend existing skill infrastructure rather than creating a competing loader.

Do not commit Jay skill content, dealership data, or secrets. Do not build a marketplace or final billing rules. Use scripts/run_tests.sh and behavior/invariant tests rather than freezing catalog counts. Create docs/altas/SKILL_PACKS.md and docs/altas/workstreams/WS-08-HANDOFF.md with the exact future import procedure for Omar's supplied pack. Commit, push, and open a draft PR; do not merge.
```

---

## WS-09 — Managed action policy and approvals

Run after WS-05 if control-plane identity/policy schemas will be edited:

```text
Goal: Harden Atlas managed-action authorization and define the approval contract for dealership browser, connector, file, messaging, export, and write workflows.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-09
Suggested branch: codex/ws-09-policy-approvals

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, docs/altas/MVP_ACCEPTANCE.md, and all current altas/control_plane policy plus altas/managed guard code/tests. Read the WS-05 handoff if merged. Trace every managed tool-dispatch path before changing policy.

Define a deterministic capability and approval model that distinguishes read, navigate, analyze, draft, download/export, send/message, submit, mutate, credential, and administrative actions. Authorization must bind tenant, store, device, agent, user/system actor, job/workflow, capability, target, expiry, attempt, and audit correlation. Prompts, skills, prior approvals, or model arguments cannot widen permission.

Implement the narrow server/worker contracts and tests needed for one approval-gated synthetic workflow end to end. Approval requests must show a human understandable action and bounded target, expire, be single use, resist replay/race, and fail closed if state changes. Deny cross-store, cross-job, stale-lease, superseded-attempt, disabled-device, expired-approval, modified-action, and policy-unreachable cases. Never put raw credentials or customer payloads in approval/audit metadata.

Keep the work outside generic upstream core where possible and preserve the mandatory managed dispatch guard. Do not build the final React approval UI or enable live Tekion writes. Use deterministic fixtures and scripts/run_tests.sh, including concurrency and alternate-dispatch-path coverage. Update security/architecture contracts as needed and create docs/altas/workstreams/WS-09-HANDOFF.md. Commit, push, and open a draft PR; do not merge.
```

---

## WS-10 — Billing-to-entitlement boundary

Run after WS-05 and WS-06 contracts are reviewed:

```text
Goal: Implement a vendor-neutral billing-event-to-Atlas-entitlement boundary that can later accept Stripe or another billing provider without making the Desktop or model authoritative for subscriptions.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-10
Suggested branch: codex/ws-10-billing-entitlements
Dependencies: reviewed account/device/organization contract from WS-05 and Control Center specification from WS-06.

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, docs/altas/ROADMAP.md, existing subscription/entitlement schemas, and WS-05/WS-06 handoffs. Inspect the current control-plane policy checks before adding fields.

Define and implement an internal billing event contract and entitlement projection for customer/account, dealer group/tenant, store/rooftop, plan, workflow/skill pack, included usage, limits, effective period, grace/past-due/canceled state, and audit provenance. Use a deterministic signed fake-provider adapter for tests. If a Stripe adapter is included, isolate it behind the internal contract and verify webhook signatures/idempotency; do not make Stripe the domain model or choose it as the final vendor.

Requirements include idempotent/out-of-order/duplicate event handling, monotonic audit history, replay resistance, mapping review for unknown customers/products, no client-supplied entitlement mutation, safe plan changes, cancellation/grace behavior, and immediate policy visibility. Desktop only reads server-issued effective entitlements; it never decides plan access. Model prompts and chat tools cannot mutate billing.

Do not collect real payment data, call live billing APIs, build checkout, or set final prices/plans. Use scripts/run_tests.sh with database/API round trips for duplicates, reordered events, signature failure, mapping failure, upgrade/downgrade, past due, cancellation, and concurrent delivery. Create docs/altas/BILLING_ENTITLEMENTS.md and docs/altas/workstreams/WS-10-HANDOFF.md. Commit, push, and open a draft PR; do not merge.
```

---

## WS-11 — Messaging and delivery decision package

This is deliberately decision support; Omar and Joe have not selected launch
channels yet:

```text
Goal: Produce a decision-ready messaging and delivery package for Atlas without choosing launch channels or changing runtime defaults on behalf of Omar and Joe.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-11
Suggested branch: codex/ws-11-messaging-decisions

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, docs/altas/SETUP_DEFAULTS.md, and all existing gateway platform/setup/service code and documentation. Verify supported behavior in code; do not rely on an old channel list.

Inventory every current setup-visible communication/delivery option and map: dealer use case, inbound/outbound direction, identity model, secrets, approval requirements, recipient/channel allowlisting, message threading, attachments, rate limits, service supervision, platform policy risk, data retention, audit needs, support burden, pricing/vendor dependency, and macOS/Windows deployment behavior.

Produce docs/altas/MESSAGING_DECISIONS.md with:
- a complete terminal-to-Desktop setup inventory so current parity work keeps all options;
- a scored pilot/launch/later recommendation matrix clearly labeled as recommendations, not decisions;
- architecture common to all channels: destination identity, store scope, ingress authentication, egress approval, idempotency, audit, retry, dead-letter behavior, and token locks;
- questions Omar and Joe must answer about primary chat, reports, alerts, customer consent, hours, recipients, and support ownership;
- the narrow reversible service interface that can be implemented before choosing channels.

Do not enable/disable channels, select vendors, change defaults, send messages, use live tokens, or write production integrations. Create docs/altas/workstreams/WS-11-HANDOFF.md. Documentation must cite real repository paths and distinguish upstream capability from Atlas product support. Commit, push, and open a draft PR; do not merge.
```

---

## WS-12 — Desktop packaging and release readiness

This can run as an audit now; avoid editing the active setup UI files:

```text
Goal: Audit Atlas Desktop packaging, signing, updates, first-install behavior, and release infrastructure, then implement only low-risk correctness improvements that do not require production certificates or vendor accounts.

Repository: /Users/omaralsadoon/Desktop/altas-platform
Workstream: WS-12
Suggested branch: codex/ws-12-desktop-release-readiness

This is a contextless session. Read AGENTS.md, docs/altas/PROJECT_CONTEXT.md, docs/altas/DECISIONS.md, PRODUCT.md, docs/altas/ARCHITECTURE.md, docs/altas/SECURITY.md, docs/altas/ROADMAP.md, docs/altas/UPSTREAM.md, apps/desktop/README.md, package manifests, Electron builder config, bootstrap/update code, and GitHub workflows completely.

Trace a clean dealership laptop from download through install, first launch, runtime/bootstrap, setup, local service registration, update, rollback, repair, uninstall, and support-bundle collection. Audit macOS app identity, icons/name, DMG/zip, signing/notarization seams, hardened runtime/entitlements, update source and trust, private-repository assumptions, install stamp, Python/runtime provenance, offline/error handling, userData/HERMES_HOME paths, auto-start, permissions, and removal of generic upstream self-updates in managed deployments. Record Windows parity gaps separately.

Deliver docs/altas/DESKTOP_RELEASE_READINESS.md with a release checklist, threat model, artifact/provenance chain, environment/certificate inventory without values, manual pilot process, rollback/recovery runbook, and prioritized blockers. Implement only verified low-risk fixes such as branding metadata, deterministic build configuration, missing validation, or safe tests. Do not create certificates, publish releases, upload artifacts, alter production update channels, or claim signing works when it has not been validated.

Run Desktop typecheck, lint, platform tests, production build, pack where safe, and inspect generated metadata without committing artifacts. Do not modify the active setup wizard unless a packaging integration fix is unavoidable and coordinated. Create docs/altas/workstreams/WS-12-HANDOFF.md. Commit, push, and open a draft PR; do not merge.
```

---

## Prompt for a later integration session

Use this only after several workstream draft PRs exist:

```text
Goal: Act as the Atlas integration lead for the current batch of completed workstream draft PRs. Do not implement new product scope.

Repository: /Users/omaralsadoon/Desktop/altas-platform

This is a contextless session. Read AGENTS.md and every file under docs/altas that is canonical to the PRs being integrated, including PROJECT_CONTEXT.md, DECISIONS.md, AGENT_WORKSTREAMS.md, and each WS-XX-HANDOFF.md. Inspect every PR diff, commit history, test result, branch base, and file overlap. Ask me which PR numbers are in the batch if I did not provide them.

Build a dependency/overlap table, verify that no PR silently makes an open business decision, and identify contract mismatches before merging code. Rebase or refresh branches safely without discarding user changes or contributor authorship. Run the union of relevant validation on an integration branch, including real temp-home paths for setup/security work. Check branding, profile isolation, prompt caching, secret redaction, tenant/store scope, and upstream compatibility. Resolve integration defects narrowly and route architectural disputes back to the owning workstream.

Deliver an integration report containing merge order, conflicts resolved, contracts changed, complete validation, remaining blockers, and rollback points. Push an integration branch and open a draft PR. Do not merge to main or mark it ready without my explicit approval.
```
