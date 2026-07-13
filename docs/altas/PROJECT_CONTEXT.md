# Atlas Project Context

## Purpose

This is the minimum durable context for a person or agent entering the Atlas
repository without access to prior conversations. Read this file before making
product or architecture assumptions, then follow the more specific documents
linked below.

## Product

Atlas is a DealerBox product for dealership operations. It starts from the
MIT-licensed Hermes Agent engine, keeps the proven local agent runtime, and adds
Atlas-owned product boundaries for dealership identity, store isolation,
permissions, skills, browser access, billing, support, and fleet management.

The product rule is:

> Local execution. Cloud control.

The Atlas Desktop application and worker run on a store-scoped machine. The
future online Atlas Control Plane remains authoritative for identity,
subscriptions, entitlements, policy, jobs, usage, audit, revocation, and model
routing. A local worker is never the commercial or security authority.

## Repository identity

- Product and customer-facing name: **Atlas**.
- Repository directory: `altas-platform` (historical spelling; do not rename it
  casually).
- Atlas-owned Python package: `altas/` (historical namespace; do not perform a
  global rename without a dedicated migration plan).
- Upstream compatibility names such as `hermes_cli`, `HERMES_HOME`, and internal
  Electron IPC names may remain when changing them would create update or
  regression risk.
- Customer-facing commands, text, paths, and branding use Atlas and `~/.atlas`.
- There is no bridge process forwarding `atlas` to `hermes`; Atlas enters the
  engine directly.

## Confirmed direction

1. Preserve the Hermes engine behavior that already works.
2. Rebrand all customer-visible product surfaces as Atlas.
3. Use the existing Electron application as the Atlas Desktop foundation.
4. The current desktop milestone is exact functional parity with the complete
   terminal `atlas setup` workflow, presented as polished Atlas UI.
5. Do not remove setup options or enforce proposed commercial defaults until
   Omar and Joe review and approve those decisions.
6. Keep persistent Tekion browser state local and isolated per store.
7. Keep raw credentials out of prompts, model-visible arguments, ordinary logs,
   audit metadata, and browser-visible status pages.
8. Treat the Atlas Core and future Jay Premium skills as signed, versioned,
   indexed product assets with explicit entitlements.
9. Production permissions are enforced by deterministic policy outside the
   model. Prompts and skills cannot grant authorization.
10. GitHub is a private engineering backup and collaboration system, not a
    runtime customer dependency.

## Working today

- The public `atlas` CLI enters the complete agent directly.
- Atlas config, secrets, sessions, skills, memory, and logs live under
  `~/.atlas` by default.
- The terminal setup wizard is Atlas-branded and supports Quick Setup, Full
  Setup, Blank Slate, section-specific setup, and reconfiguration.
- Atlas Desktop launches from source, starts the Python backend, and provides
  the existing chat, settings, tools, providers, sessions, profiles, artifacts,
  and gateway surfaces.
- A first-run Atlas workstation blueprint and provider onboarding surface exist.
- The local control-plane prototype includes SQLite-backed tenants, stores,
  subscriptions, devices, agents, entitlements, jobs, leases, policy, usage,
  audit, a deterministic model gateway, and a localhost Control Center.
- A fixture-backed fixed-operations workflow proves the managed worker contract
  without claiming a live Tekion integration.

## Not complete yet

- The desktop does not yet reproduce the entire terminal setup workflow.
- Atlas account login, organization/store enrollment, and production device
  identity are not connected to Desktop.
- The Control Center is a localhost development console, not the hosted
  production operator/customer platform.
- The production Control Plane still needs PostgreSQL, production identity and
  RBAC, deployment, backups, observability, billing integration, and disaster
  recovery.
- The per-store persistent browser profile manager and live Tekion connector
  are not production-ready.
- The proprietary Jay skill pack has not been imported into this repository.
- The final skill-index contract, signing, distribution, entitlement, staged
  rollout, and rollback system remain.
- Managed communications defaults are intentionally undecided.
- Signed/notarized installers and production update infrastructure remain.

## Current setup decision

The complete terminal setup is the behavioral source of truth. Desktop should
offer the same modes, sections, options, validation, cancellation, backups, and
summaries. It should reuse the underlying setup/configuration implementation,
not maintain a divergent React-only interpretation of configuration.

The proposed dealership-friendly managed setup remains a future product layer.
It may label or recommend options during development, but it must not silently
remove them. See [SETUP_DEFAULTS.md](SETUP_DEFAULTS.md).

## Security boundary

- Do not use production dealership data or live Tekion administrator
  credentials in the current prototype.
- One worker/browser profile per store or credential boundary.
- Device and control-plane tokens are scoped, revocable, and short-lived in the
  production design.
- Provider master keys remain server-side whenever technically possible.
- Local secrets go through an OS credential-vault boundary; `.env` remains a
  development compatibility surface.
- Managed actions fail closed if policy or the Control Plane cannot be verified.
- Read-only Tekion behavior comes before write workflows. Submits, exports,
  messages, and mutations require explicit workflow and approval design.

## Canonical documents

Read these before implementing a workstream:

1. [`AGENTS.md`](../../AGENTS.md) — repository engineering rules.
2. [`PRODUCT.md`](../../PRODUCT.md) — product and commercial boundary.
3. [`DECISIONS.md`](DECISIONS.md) — active decisions and open questions.
4. [`SETUP_DEFAULTS.md`](SETUP_DEFAULTS.md) — current setup parity directive
   and future managed-default proposal.
5. [`ARCHITECTURE.md`](ARCHITECTURE.md) — system boundaries and contracts.
6. [`SECURITY.md`](SECURITY.md) — threat model and security requirements.
7. [`ROADMAP.md`](ROADMAP.md) — phase ordering and launch gates.
8. [`MVP_ACCEPTANCE.md`](MVP_ACCEPTANCE.md) — prototype definition of done.
9. [`TESTING.md`](TESTING.md) — safe local validation.
10. [`UPSTREAM.md`](UPSTREAM.md) — upstream synchronization and patch policy.
11. [`AGENT_WORKSTREAMS.md`](AGENT_WORKSTREAMS.md) — parallelization plan and
    paste-ready prompts.

## Local entry points

```bash
cd /Users/omaralsadoon/Desktop/altas-platform
source .venv/bin/activate

atlas setup       # terminal setup source of truth
atlas             # conversational CLI
atlas desktop     # build and launch the desktop app
make atlas-dev    # local Control Plane and Control Center
make atlas-worker # seeded local worker
make atlas-smoke  # managed walking-skeleton smoke test
```

Desktop source development:

```bash
cd /Users/omaralsadoon/Desktop/altas-platform/apps/desktop
HERMES_DESKTOP_HERMES_ROOT=/Users/omaralsadoon/Desktop/altas-platform npm run dev
```

## How parallel work is coordinated

- Every workstream uses a separate branch and preferably a separate git
  worktree.
- Workstreams own distinct files or merge in dependency order; two sessions do
  not concurrently edit the same integration seam.
- Each session creates its own
  `docs/altas/workstreams/WS-XX-HANDOFF.md` describing decisions, files,
  validation, open risks, and follow-up work. Distinct handoff files minimize
  merge conflicts.
- Handoffs follow the template in
  [`docs/altas/workstreams/README.md`](workstreams/README.md).
- Agents do not make deferred business decisions. They document the decision
  needed, alternatives, evidence, and the narrow seam that can be built without
  pre-deciding it.
- Changes are committed intentionally, pushed to the private repository, and
  opened as draft pull requests for integration review.
