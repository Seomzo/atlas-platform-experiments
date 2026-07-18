# WS-00 Handoff — Desktop setup parity contract

## Status

- Branch: `codex/ws-00-setup-parity-contract`
- Draft PR: not created; this worktree brief explicitly requires no push or PR
- Baseline: `origin/main`
- Last validated: 2026-07-18

## Goal and scope

WS-00 audited the real terminal setup graph, its dynamic registries and
persistence helpers, the existing TUI gateway/REST seams, and current Atlas
Desktop onboarding. It produced the authoritative behavior and typed-session
contract for complete terminal-to-Desktop setup parity.

This workstream is documentation-only. It does not change setup behavior,
provider defaults, credentials, source registries, or Desktop UI.

## Decisions made

- Terminal and Desktop must become adapters over one backend-owned setup graph;
  React must not own provider/tool/default catalogs.
- The setup session is profile-pinned and catalog-revisioned.
- Ordinary config, secrets, and OAuth/device operations have separate typed
  methods and response rules.
- Current terminal cancellation is truthfully modeled as incremental: default
  cancel keeps already-applied sections/effects, while unapplied drafts are
  discarded. Config restore is explicit and cannot imply secret/OAuth rollback.
- Every terminal state receives a redacted summary and exact recovery scope.
- Dynamic registries and behavior/invariant tests replace catalog snapshots and
  fixed counts.
- Existing Desktop provider/model, OAuth, toolset, messaging, MCP, skills, and
  memory APIs are reusable dependencies, but the current provider+Cortex
  onboarding flow is not a setup-session implementation.

## Deferred decisions

1. **Atlas first-run Quick Setup is blocked on product policy.** The confirmed
   milestone requires Quick, but Atlas-branded terminal code hides the only
   implemented Quick path because it is Nous-specific. WS-00 does not select a
   replacement commercial provider/default. Product owners must either permit
   the existing Nous flow or approve a provider-neutral Quick graph.
2. Decide whether the currently unreachable Docker resource helper should
   become a supported setup choice; it is not part of current parity.
3. Decide whether to unify the differing standalone-TTS and Tools-TTS catalogs
   or preserve both indefinitely. Until then, both are contractual.
4. Gateway ownership should resolve the built-in/plugin Mattermost duplicate;
   the contract records current built-in precedence rather than changing it.

## Files changed

- [`docs/altas/DESKTOP_SETUP_PARITY.md`](../DESKTOP_SETUP_PARITY.md)
- [`docs/altas/workstreams/WS-00-HANDOFF.md`](WS-00-HANDOFF.md)

## Contracts and migrations

The main document defines:

- `setup.catalog`
- `setup.session.create`
- `setup.session.snapshot`
- `setup.session.validate`
- `setup.session.apply`
- `setup.secret.apply`
- `setup.operation.start/status/input/cancel`
- `setup.session.cancel`
- `setup.session.summary`

It also defines catalog, scope, snapshot, validation, apply, operation, and
summary types plus cancel/back and recovery semantics.

No migration is performed in WS-00. WS-03 must design compatibility for legacy
inline custom/auxiliary API keys and the Bedrock secret alias without breaking
existing terminal reads. WS-01/WS-03 must also route memory-provider secret
writes through the shared credential boundary.

## Validation

- Read every file required by the worktree brief and repository instructions.
- Traced setup parser flags and all six section entry points.
- Traced fresh, existing, missing-only, Blank Slate, reset, migration,
  cancellation, backup, and summary branches.
- Traced provider/model, auxiliary, TTS, terminal, gateway, tools, skills,
  plugins, MCP, memory, config, auth, TUI gateway, REST, and Desktop onboarding
  sources.
- Cross-checked every repository-relative Markdown link after authoring.
- Confirmed the diff is documentation-only under `docs/altas/`.

No runtime tests are required for this specification-only workstream. The final
documentation checks were:

```sh
git diff --no-index --check /dev/null docs/altas/DESKTOP_SETUP_PARITY.md
git diff --no-index --check /dev/null docs/altas/workstreams/WS-00-HANDOFF.md
# Extract each relative Markdown link and assert its target exists.
git diff --check
```

## Security and privacy checks

- No credentials, live external accounts, customer data, proprietary skills,
  cookies, or unredacted logs were used.
- The contract forbids raw secrets in catalog/snapshot/validation/apply/error/
  summary/renderer/support data.
- Secret replacement, OAuth cancellation, profile isolation, managed-scope
  failure, MCP validation, and honest partial-recovery behavior are explicit.
- Current risky compatibility behavior is documented rather than copied into a
  proposed Desktop contract.

## Visual evidence

Not applicable. WS-00 changes Markdown only. The existing dark-blue Desktop
blueprint was inspected as the presentation baseline; WS-02 owns screenshots
and visual verification.

## Known risks and limitations

- Quick Setup cannot be considered implementation-ready until the deferred
  product decision is made.
- Current terminal behavior contains several parity blockers: reset-before-
  backup, narrow existing-install detection, omitted Full TTS/agent sections,
  hard-coded missing-only messaging, incomplete Blank plugin/MCP actions, and
  inconsistent secret storage.
- Gateway and provider registries are intentionally moving targets. Any
  implementation that copies the current inventory into TypeScript will drift.
- External installs, service operations, and completed OAuth grants cannot all
  be rolled back. Summaries must remain honest about this.

## Integration order and conflicts

1. Review and merge WS-00 documentation.
2. Resolve the Atlas Quick decision before calling Quick implementation
   complete.
3. WS-01, WS-02, and WS-03 may proceed in parallel against the approved typed
   contract and their non-overlapping ownership boundaries.
4. Integrate WS-01 backend, WS-03 secret seam, and WS-02 UI.
5. Run WS-04 only after all three are integrated.

Likely conflict boundaries:

- WS-01 owns Python setup/config/RPC service and terminal adaptation.
- WS-02 owns Desktop setup components, store/types/i18n, and presentation.
- WS-03 owns secret/credential IPC, compatibility writers, and redaction.
- WS-04 owns parity fixtures and sign-off, not broad architecture.

## Exact next actions

1. Omar and Joe choose the permitted Atlas first-run Quick behavior.
2. WS-01 implements the live catalog/session service and moves terminal setup
   onto it, starting with backup/reset, installation-state, and summary
   invariants.
3. WS-03 provides the status-only secret adapter and legacy read/write
   compatibility interface consumed by WS-01.
4. WS-02 builds the accessible dark-blue wizard against a typed fixture
   adapter, then swaps to WS-01 without adding a frontend catalog.
5. WS-04 proves representative terminal/Desktop output-state equivalence and
   sentinel-secret absence using temporary Atlas homes and fake external
   services.
