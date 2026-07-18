# Atlas Roadmap

## Active track — Desktop setup parity

This track runs before Atlas narrows the experience into commercial managed
defaults. It is the current product directive.

- Preserve Quick Setup, Full Setup, and Blank Slate.
- Reproduce every current `atlas setup` section and choice in Atlas Desktop.
- Reuse the terminal setup/configuration logic as the source of truth.
- Add safe desktop secret persistence without exposing values to the renderer,
  prompts, logs, or analytics.
- Add first-run, reconfigure, section-only, reset, cancel, validation, backup,
  and recovery coverage.
- Keep the existing Atlas dark-blue workstation setup aesthetic.
- Treat any hiding or removal of terminal options as a later explicit product
  decision, not part of parity work.

## Phase 0 — Preserve the engine baseline

- Pin and document Hermes upstream.
- Preserve MIT attribution.
- Keep Atlas changes isolated and establish a patch ledger.
- Record current fixed-ops workflow assumptions and sample outputs.

## Phase 1 — Managed worker walking skeleton

- Control Plane, device authentication, heartbeat, leases, policy, jobs.
- Deterministic model gateway and fixture-backed fixed-ops report.
- Worker supervisor, credential-vault interface, managed engine guard.
- Local operator Control Center.
- Automated security and end-to-end smoke tests.

## Phase 2 — Real connector lab

- Formalize Tekion app-level and dealer-level authorization flow.
- Implement read-only API connector behind the trusted connector boundary.
- Add per-store browser profile manager for workflows not covered by API.
- Add reauthorization, rate-limit, and connector-health UX.
- Validate with internal non-production store data.

## Phase 3 — Design-partner appliance

- Rebrand and harden the existing Electron wrapper as Atlas Desktop.
- One-click enrollment and managed service installation.
- macOS and Windows credential-vault adapters.
- Whole-process isolation and outbound network policy.
- Signed worker/workflow updates with staged rollout and rollback.
- Consent-based diagnostics and support bundle.

## Phase 4 — Commercial control plane

- PostgreSQL, migrations, backup/restore, queueing, and production observability.
- Operator/customer authentication, RBAC, SSO-ready organization model.
- Stripe subscription webhooks mapped to entitlements.
- Slack and email delivery.
- Data retention, deletion, export, and customer audit views.
- Multi-environment deployment and disaster recovery.

## Phase 5 — Hosted workers

- Isolated per-store browser/container runtime.
- Managed MFA and session-renewal workflow.
- Resource quotas, autoscaling, canaries, and provider fallback.
- Multi-store and enterprise deployment controls.
- Mobile companion app: talk to your worker from a phone via the Control
  Plane relay (see `MOBILE_COMPANION.md`; earliest Phase 3+, placed here as
  the default slot).

## Phase 6 — Workflow marketplace

- Signed, versioned workflow packs.
- Approval and permission manifests.
- Test fixtures and compatibility contracts.
- Custom dealership workflows without modifying engine core.

## Launch gates

Commercial launch requires:

- Name/trademark clearance
- Tekion agreement and integration approval
- External security review
- Production authentication/RBAC
- Signed releases and rollback
- Credential-vault support on every advertised platform
- Support and incident-response runbooks
- Billing/reconciliation tests
- Data-processing and retention terms
- Pilot evidence for reliability, report value, support burden, and unit economics
