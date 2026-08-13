# M02 Handoff — Native iPhone Product and Architecture Plan

**Status:** Complete specification; gated GO for M03 engineering, NO-GO for production/pilot
**Scope:** Documentation and proposed versioned contracts only; no native app or backend implementation
**Branch:** `codex/mobile-m02-product-architecture`
**Worktree:** `/Users/ethansandhu/.codex/worktrees/mobile-m02-product-architecture/atlas-platform-experiments`
**Base:** M01 head `ed85a5cc810bf1f5ed51d1ab473b28d011eb30b8`
**Repository:** `Seomzo/atlas-platform-experiments`

## Outcome

Atlas should ship a native SwiftUI iPhone interface whose first real capability is one secure text conversation and one bounded synthetic approval through this authority chain:

```text
iPhone -> hosted Atlas control plane -> outbound enrolled worker -> loopback Atlas gateway/engine
```

The phone is not an agent, gateway, shell, policy engine, credential store for workers/providers/connectors, second transcript authority, or direct network path to a Mac.

Recommended v1 navigation is **Today / Threads / Activity / Settings**. Voice is an immediate second-slice push-to-talk mode and later multi-thread Voice Workspace, not an empty text-first tab. Premium glass is limited to native chrome/materials; operational content and approvals stay calm, flat, legible, and accessible.

## Evidence inspected

| Dependency | Verified live status | What M02 used |
|---|---|---|
| M01 discovery | base `ed85a5cc…` | Runtime/gateway contract, mobile gaps, rendered desktop/control-center evidence, Xcode/device evidence |
| WS-05 identity | draft PR [7](https://github.com/Seomzo/atlas-platform-experiments/pull/7), open, head `20997113…`, base `main` | Provider-neutral account mapping, one-time enrollment, Ed25519 proof, five-minute device sessions, rotation/revocation |
| Durable relay | draft PR [8](https://github.com/Seomzo/atlas-platform-experiments/pull/8), open, head `cf7bec22…`, base WS-05 | Pairing/session/text/interrupt endpoints, outbound worker WS, durable inbox/outbox/ACK/replay, signed cursors, idempotency/uncertain outcome |
| Managed approvals | draft PR [9](https://github.com/Seomzo/atlas-platform-experiments/pull/9), open, head `85443dad…`, base relay | Exact managed-action digest/context, atomic lifecycle, phone decision API, single-use consume, synthetic no-write integration |

M02 read the implementation and handoffs from all three dependency worktrees. It also re-ran the five focused files together on the stacked WS-09 head: **35 tests passed, 0 failed** using `scripts/run_tests.sh` (device enrollment, text relay, relay worker, approvals, and managed policy guard).

Computer Use opened the current M01 desktop tool-streaming reference in Preview; M02 also inspected the approval and disconnected surfaces at rendered resolution. The design system translates their strongest qualities—Atlas identity, quiet hierarchy, typed activity, and truthful failure state—without porting desktop rails, raw command UI, or dense audit tables.

Apple toolchain recheck: the machine-wide developer directory is still Command Line Tools, so plain `xcodebuild` fails. Scoped `/Applications/Xcode.app/Contents/Developer` reports Xcode 26.6, iOS 26.5 SDK, and installed iOS 18.6/26.4/26.5 runtimes. The plan chooses iOS 18.0 as the deployment floor with conditional iOS 26 materials.

## Existing code versus required M03 work

### Present on the reviewed stacked draft branches

- server-derived user/membership/role/store authority and a provider-neutral identity verifier;
- atomic one-time phone/worker enrollment, Ed25519 device proof, short device sessions, key rotation and revocation;
- exact phone-worker-store-agent pairing and outbound worker relay;
- encrypted durable command/event storage, worker inbox/outbox, ACK/replay, signed cursors and bounded queues;
- narrow `session.create`, `prompt.submit`, and `session.interrupt` commands;
- managed action classification, exact expiring approval lifecycle, phone approve/deny and single-use worker consumption;
- synthetic export proving `artifact_created=false` and `external_write=false`.

### Missing or prototype-only

- merged/integrated backend baseline—the dependencies are still stacked draft PRs;
- production OIDC/PKCE/MFA/recovery and the specified proof-bound rotating account renewal family;
- hosted TLS/domain, production Postgres-class storage, managed KMS, distributed relay routing/presence, backups/restore, observability/on-call;
- phone foreground WebSocket and stream controls, APNs registration/delivery, worker/profile/product thread/report projections;
- a fully typed/redacted mobile event adapter (current worker checks type/size but broadly forwards payload shape);
- persisted gateway idempotency/outcome resolution beyond the current honest `uncertain` guard;
- an iOS app, Keychain/LocalAuthentication integration, protected cache, physical-device/cellular evidence, and every rendered native state;
- the multi-store phone-identity migration: current WS-05 phone records are bound to one store, which is acceptable only for the M03 single-store slice;
- speech/TTS, real reports/artifacts, real connector actions, and general managed-policy orchestration.

## M03 decision

### GO — isolated engineering vertical slice, with gates

Start M03 only after:

1. PR 7 → PR 8 → PR 9 are reviewed and integrated into one clean named base;
2. `contracts/mobile/` v1 is frozen for the slice;
3. a hosted isolated environment, synthetic account/store/worker/profile, signing, and physical iPhone are verified;
4. real data, provider/connector credentials, and external writes remain prohibited.

The phone-safe event projection and phone stream are early owned M03 subtracks. Native end-to-end integration cannot claim progress past their seam until they are implemented and contract-tested rather than mocked.

The exact slice signs in/enrolls, selects one real enrolled worker, loads/opens or creates a session, sends text, receives real gateway message/tool events, interrupts, survives app termination/network and worker reconnect through cursor replay, resolves one synthetic approval, returns from one content-minimal completion push, repeats over cellular, and proves remote revocation. `IOS_PRODUCT_SPEC.md` lists the 16 evidence steps.

### NO-GO — production or external pilot

Production identity, hosted persistence/keys/routing, APNs operations, safe projection, privacy/retention, gateway outcome idempotency, support/incident recovery, native security verification, and physical-device evidence are incomplete. M02 is not a launch, compliance, write-safety, exactly-once, end-to-end-encryption, or production-readiness claim.

## Highest-risk boundary

The highest immediate risk is the **worker gateway-event → public mobile-event projection**. The worker currently allowlists event type and size but does not yet prove a deny-by-default field projection/redaction contract. M03 must never fall back to raw gateway/provider/tool frames. The second linked risk is local gateway execution after a crash: until the gateway persists an idempotency/outcome key, the app must show `Outcome uncertain—verify before retrying` and never replay automatically.

## Identity dependency status

WS-05 is a strong executable foundation but not production identity. Its implemented phone APIs require a human account assertion plus the exact enrolled phone device session. Current development account/device TTLs are 600/300 seconds. M02 specifies a one-time rotating, device-proof-bound account renewal handle so the app does not store a reusable bearer or force browser auth every ten minutes; that gap must be implemented and reviewed. The current Ed25519 phone key can be protected in `WhenUnlockedThisDeviceOnly` Keychain but cannot honestly be called Secure-Enclave-backed; versioned P-256 support is the recommended pre-production migration.

## Implementation workstreams

- **B0:** integrate dependency PRs 7 → 8 → 9 and preserve tests/docs.
- **M03:** first physical-iPhone text/replay/interrupt/synthetic-approval/push/cellular slice.
- **M04:** production relay persistence, KMS and distributed routing/presence.
- **M05:** typed/redacted worker adapter and gateway idempotency/outcome contract.
- **M06:** native Core/design system/build/accessibility foundation.
- **M07:** full thread/Today/Activity/report and multi-scope experience.
- **M08:** push-to-talk and later Voice Workspace.
- **M09:** complete managed approvals, APNs, reports and lost-phone recovery.
- **M10:** production identity/deployment/security/privacy/operations and pilot GO/NO-GO.

M04/M05/M06 may run in parallel after M03; M07 integrates them. M08 and most of M09 may then run in parallel. Contracts, shared Xcode composition, environment, and integration evidence remain single-owner choke points.

## Unresolved owner decisions

Product/security/operations owners must approve production identity/recovery, verified hosting account/cloud/region/domain, database/queue/KMS/routing, per-class retention/replay, notification detail/preferences, speech processing/retention, pilot audience/data/actions/support, Apple app/team/signing/APNs identity, real connector action policy, telemetry/support data, P-256 hardware-backed migration, and final supported OS/device range. M02 records options, recommended defaults, reversible seams, and approvers in `MOBILE_DECISION_LOG.md`; it does not choose vendors, retention, pricing, or live writes silently.

## Deliverables

- `docs/altas/mobile/IOS_PRODUCT_SPEC.md`
- `docs/altas/mobile/IOS_INFORMATION_ARCHITECTURE.md`
- `docs/altas/mobile/IOS_DESIGN_SYSTEM.md`
- `docs/altas/mobile/IOS_ARCHITECTURE.md`
- `docs/altas/mobile/MOBILE_RELAY_PROTOCOL.md`
- `docs/altas/mobile/MOBILE_IDENTITY_AND_ENROLLMENT.md`
- `docs/altas/mobile/MOBILE_SECURITY.md`
- `docs/altas/mobile/MOBILE_TEST_STRATEGY.md`
- `docs/altas/mobile/MOBILE_IMPLEMENTATION_ROADMAP.md`
- `docs/altas/mobile/MOBILE_DECISION_LOG.md`
- `docs/altas/workstreams/M02-HANDOFF.md`
- `contracts/mobile/README.md`, seven versioned Draft 2020-12 schemas, and seven synthetic valid examples

Required Mermaid diagrams are included for system context, enrollment, foreground conversation, reconnect/replay, background/push, approval, worker disconnection, component ownership, and workstream dependencies.

## Validation

- All JSON schema and example files parse with `jq`.
- Seven schemas pass `Draft202012Validator.check_schema`; all seven named examples validate with format checking under `jsonschema 4.26.0`. The stream-to-event URN reference also validates, and seven hostile cases are rejected (secret classification, extra raw event field, approval reason mismatch, arbitrary command, invalid Retry-After, client tenant assertion, and client thread-store assertion).
- Focused stacked dependency tests: 35 passed, 0 failed.
- All 11 required documents and contract paths exist. The only Markdown links are the three live dependency PRs, reverified with `gh`.
- All 14 Mermaid blocks parse and render under Mermaid CLI 11.12.0, including every required diagram.
- The 12 authored Markdown files pass `markdownlint` 0.38.0 with only the repository-unconfigured 80-column rule disabled; placeholder and common secret-pattern scans return no matches.
- Final diff review and branch cleanliness are completed at publication; exact commands/results are reported in the draft PR/final handoff.

## Integration note

Draft PR 8 already contains `docs/altas/workstreams/M02-HANDOFF.md` for the backend text-relay slice. When the stacks converge, this architecture handoff should own that exact path; preserve the backend-specific detail in `docs/altas/mobile/M02_TEXT_RELAY.md` rather than dropping either body of evidence.

## Publication

- Artifact commit: `2e45a4a8d` (`docs: specify native iPhone architecture`)
- Draft PR: [10](https://github.com/Seomzo/atlas-platform-experiments/pull/10)
- PR base: `codex/mobile-m01-discovery` (draft PR 6), so the review diff is M02-only
- PR head: `codex/mobile-m02-product-architecture`
- Merge status: not merged; this workstream never merges automatically
