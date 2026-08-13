# Atlas Security and Trust Model

## Purpose

This document defines what Atlas protects, which components are trusted, and
what the prototype does not yet claim. It is a design contract, not a
compliance certification.

## Security statement

Atlas assumes that:

- Model output may be incorrect or adversarial.
- Content read from browsers, messages, files, and APIs may contain prompt
  injection.
- A customer with administrator access to a local machine can inspect or
  modify the software running there.
- Plugins and skills loaded in-process share the engine's privileges.
- Network and provider dependencies will fail.

Therefore prompts, local configuration, tool descriptions, memory rules,
redaction, and approval heuristics are not authorization boundaries.

## Assets

Highest-value assets are:

- Atlas model-provider credentials
- Tekion app-level credentials
- Dealer/store credentials and browser sessions
- Dealership operational data
- Cross-tenant isolation
- Entitlement and billing state
- Account identities, organization memberships, roles, and store grants
- Workflow and skill intellectual property
- Device identities and worker leases
- Audit integrity
- Update signing keys

## Enforcement boundaries

### Server-side policy

Subscription, device, store, capability, and budget checks occur in the
Control Plane. A running job grants only its named capability and explicitly
declared infrastructure dependencies. The engine cannot grant itself an
entitlement or borrow an unrelated capability from the lease.

### Short-lived leases

A valid device credential receives a scoped, expiring lease. Lease validation
is not sufficient by itself: protected endpoints also recheck the live device
and subscription state so a remote disable takes effect immediately.

### Account and device enrollment

The account API accepts a short-lived assertion from an injected identity
verifier and maps its verified issuer/subject to server-owned users,
memberships, roles, and store grants. Enrollment requests contain a store, not
a tenant; the server derives tenant scope from the live grant. `owner` and
`operator` roles may enroll workers and revoke devices. A live member may
enroll a phone only for an explicitly granted store.

Each enrollment token is random, stored only as a hash, expires after ten
minutes by default, and is consumed in the same immediate transaction that
creates the device. The device supplies a raw Ed25519 public key; the private
key never crosses the device boundary. A signed timestamp and nonce are recorded
once before the server issues a five-minute device session. Live device status,
credential expiry, and credential version are checked again when the session is
used. Rotation requires both the existing device session and proof of the new
private key. Revocation increments the credential version and invalidates old
sessions immediately.

### Mobile text relay

Mobile relay HTTP calls require both the verified account assertion and the
short-lived session for the exact phone enrolled by that user. The Control
Plane derives tenant/store scope and accepts no tenant field. A pairing is one
phone, one active Ed25519 worker, and the worker's exact active agent binding.

The worker connects outbound with a short-lived device session. Browser
origins and query credentials are rejected. Its local adapter is syntactically
restricted to loopback and hard-codes only session creation, text submission,
and interruption; mobile input cannot select an arbitrary gateway method,
workspace, profile, model, or tool.

Relay payloads are AES-GCM encrypted before Control Plane and worker SQLite
writes. Durable command/event identities and signed cursors provide replay and
deduplication. Audit metadata excludes content. Queue and event sizes are
bounded. Device revocation closes active relay scope and cancels nonterminal
commands. See [`mobile/M02_TEXT_RELAY.md`](mobile/M02_TEXT_RELAY.md).

### Process isolation target

Production uses one Hermes engine process/profile per store or credential
boundary. When untrusted external content is accepted, the whole process must
run in an OS/container sandbox with restricted filesystem and network access.
Internal Hermes profiles are organization aids, not multi-tenant isolation.

### Narrow tools

Managed production mode exposes named dealership workflows. Generic terminal,
filesystem, browser, code execution, delegation, arbitrary plugin loading, and
self-modifying skills are disabled unless a separately reviewed deployment
explicitly requires them.

### Credential vault

Dealer secrets are retrieved through an opaque vault interface by trusted
connector code. The language model receives neither the value nor a
model-callable secret-read tool.

## Stable denial reasons

The prototype uses stable, lower-case machine-readable outcomes such as:

- `allowed`
- `device_identity_invalid`
- `device_inactive`
- `device_context_mismatch`
- `account_authentication_failed`
- `store_access_denied`
- `role_not_allowed`
- `enrollment_not_redeemable`
- `device_proof_invalid`
- `device_session_required`
- `subscription_inactive`
- `store_inactive`
- `lease_signature_invalid`
- `lease_expired`
- `lease_context_mismatch`
- `lease_capability_missing`
- `entitlement_missing`
- `entitlement_inactive`
- `job_context_mismatch`
- `job_claim_invalid`
- `job_capability_mismatch`
- `model_request_limit_exceeded`
- `model_requested_token_limit_exceeded`

The local managed guard uses `POLICY_UNAVAILABLE` when it cannot obtain a
server decision at all; that fail-closed transport outcome is distinct from a
server policy code.

All allows and denials are auditable without recording secrets or unnecessary
dealership payloads.

## Threats and controls

| Threat | Prototype control | Production follow-up |
|---|---|---|
| Prompt asks worker to add a store | Server entitlement check | Stripe-backed upgrade workflow |
| Account request claims another tenant/store | Tenant derived from verified membership and store grant; request-body tenant rejected | Production OIDC issuer/audience/PKCE review |
| Local config claims another tenant/store | Tenant derived from device; relationship check | Platform attestation where practical |
| Customer copies install directory | No provider keys; Ed25519 private key remains device-side; revocable versioned credential | Hardware-backed non-exportable key + attestation where practical |
| Enrollment token is stolen or replayed | Ten-minute expiry, hash-only storage, atomic single use, live membership recheck | Risk-based browser reauthentication and out-of-band confirmation |
| Device proof is replayed | Signed timestamp, bounded skew, persisted per-device nonce | Distributed nonce store when the API is multi-instance |
| Model calls provider directly | Atlas gateway profile; production network policy | Egress allowlist |
| Policy service times out | Managed guard denies | Multi-region policy service and cached lease policy with bounded TTL |
| Device is remotely disabled | Live device recheck on protected calls | Push invalidation and fleet alerting |
| Phone calls broad Desktop control methods | Phone can call only narrow Control Plane relay endpoints; worker adapter hard-allowlists three loopback RPCs | Separate supported Task Thread service process |
| Relay reconnect duplicates a prompt | Server and worker durable idempotency; ambiguous non-idempotent local RPC fails closed instead of replaying | Promote durable Task Thread turns end to end |
| Relay database exposes chat text | AES-GCM payload encryption on both persistence planes; content-free audit | KMS envelope keys and retention/deletion policy |
| Job is abandoned or completed by an old attempt | One active claim per device, visibility timeout, one-time hashed claim token | Durable queue with lease renewal and dead-letter policy |
| Model loops or races exceed expected spend | Atomic per-job request and requested-token reservations | Tenant billing ledger, provider hard caps, and anomaly alerts |
| Model request multiplies spend through payload extensions | Strict field allowlist, one completion, and bounded messages/request size | Model-specific tokenization plus tenant/provider hard caps |
| Secret reaches model/log | Opaque vault contract; safe metadata | Automated data-loss-prevention tests |
| Store browser session leaks | Per-store profile requirement | OS user/container isolation and encrypted storage |
| Malicious skill/plugin | Curated signed packs only | Signature verification and release provenance |
| Upstream update changes behavior | Pinned commit and patch ledger | Staged signed Atlas update channel |

## Prototype limitations

The current prototype:

- Implements provider-neutral user/membership/store authorization and
  Ed25519 enrollment, proof, short-lived device sessions, rotation, and
  revocation. Demo browser identity is a deterministic localhost-only adapter;
  no production OIDC issuer, authorization-code/PKCE flow, recovery policy, or
  platform attestation has been selected or claimed.
- Retains the hashed bearer only for compatibility with the existing seeded
  worker. The current Hermes provider adapter still receives that scoped bearer
  in its isolated process environment. Desktop must adopt the new OS-vault key
  and short-lived session flow before the compatibility path can be removed.
- Uses a shared localhost development-admin bearer instead of operator identity
  and RBAC.
- Uses a deterministic model provider.
- Uses fixture data, not a live Tekion connector.
- Runs the fixture workflow directly; it does not yet launch or supervise a
  Hermes engine process.
- Enables the Hermes dispatch guard only inside an authenticated, context-local
  managed request scope. The Atlas provider does not toggle managed mode
  process-wide. The upstream developer CLI remains intentionally unmanaged and
  is not the commercial worker. A production supervisor must create the scoped
  authorization, isolate the process, and restrict network egress before
  launching the conversational engine.
- Binds official managed Cortex execution end to end with a strict persisted
  local-job provenance envelope. The shipped worker compares it to the exact
  next admitted Cortex job and leases only that job; claim, model, and atomic
  usage-reservation paths require the same dedicated admission and
  constant-time header match. Generic job queue/requeue routes reject Cortex,
  and invalid legacy/tampered active jobs are terminally quarantined.
- This Cortex provenance contract is official-runtime path integrity, not
  remote attestation. The control plane still trusts an authenticated device's
  claim about its owner-local database. A compromised device can fabricate
  local state until production adds hardware-backed identity/attestation and
  proof-bound inference credentials.
- Does not provide production Windows/Linux credential-vault adapters.
- Does not ship an OS sandbox, egress firewall, signed updater, SBOM, SSO,
  Stripe integration, or formal data-retention system.

Do not place real dealership data, live Tekion credentials, or production
provider keys into the prototype.

## Incident-ready logging

Every security-relevant event includes a timestamp, event type, outcome,
and available tenant/store/user/device/agent/job identifiers. Enrollment create
and redemption events share a server-generated correlation ID; the job ID
remains the workflow correlation key. Metadata must be allowlisted. Request bodies,
Authorization headers, lease and claim tokens, provider credentials, dealer
credentials, cookies, and raw browser storage are never audit fields.

## Required production reviews

Before a dealership pilot with real data:

1. External architecture and application security review.
2. Tekion partner agreement and API/browser authorization review.
3. Commercial name and trademark review for Atlas.
4. OS-specific secure-storage and installer review.
5. Dependency, asset-license, and SBOM review.
6. Data-processing agreement, retention, deletion, and backup design.
7. Operator/customer RBAC and support-consent design.
8. Threat-model update against the exact deployment topology.
