# Altas Security and Trust Model

## Purpose

This document defines what Altas protects, which components are trusted, and
what the prototype does not yet claim. It is a design contract, not a
compliance certification.

## Security statement

Altas assumes that:

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

- Altas model-provider credentials
- Tekion app-level credentials
- Dealer/store credentials and browser sessions
- Dealership operational data
- Cross-tenant isolation
- Entitlement and billing state
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
| Local config claims another tenant/store | Tenant derived from device; relationship check | Signed asymmetric requests + nonce replay protection |
| Customer copies install directory | No provider keys; revocable device credential | Device-bound key + attestation where practical |
| Model calls provider directly | Altas gateway profile; production network policy | Egress allowlist |
| Policy service times out | Managed guard denies | Multi-region policy service and cached lease policy with bounded TTL |
| Device is remotely disabled | Live device recheck on protected calls | Push invalidation and fleet alerting |
| Job is abandoned or completed by an old attempt | One active claim per device, visibility timeout, one-time hashed claim token | Durable queue with lease renewal and dead-letter policy |
| Model loops or races exceed expected spend | Atomic per-job request and requested-token reservations | Tenant billing ledger, provider hard caps, and anomaly alerts |
| Model request multiplies spend through payload extensions | Strict field allowlist, one completion, and bounded messages/request size | Model-specific tokenization plus tenant/provider hard caps |
| Secret reaches model/log | Opaque vault contract; safe metadata | Automated data-loss-prevention tests |
| Store browser session leaks | Per-store profile requirement | OS user/container isolation and encrypted storage |
| Malicious skill/plugin | Curated signed packs only | Signature verification and release provenance |
| Upstream update changes behavior | Pinned commit and patch ledger | Staged signed Altas update channel |

## Prototype limitations

The current prototype:

- Uses a hashed bearer device secret instead of an asymmetric device key.
- The prototype Hermes provider adapter receives that scoped device bearer in
  its isolated process environment. Production should replace this with a
  supervisor-local gateway or a short-lived proof-bound inference credential.
- Uses a shared localhost development-admin bearer instead of operator identity
  and RBAC.
- Uses a deterministic model provider.
- Uses fixture data, not a live Tekion connector.
- Runs the fixture workflow directly; it does not yet launch or supervise a
  Hermes engine process.
- Enables the Hermes dispatch guard only when the Altas provider selects
  managed mode. The upstream developer CLI remains intentionally unmanaged and
  is not the commercial worker. A production supervisor must set managed mode,
  isolate the process, and restrict network egress before launching the engine.
- Does not provide production Windows/Linux credential-vault adapters.
- Does not ship an OS sandbox, egress firewall, signed updater, SBOM, SSO,
  Stripe integration, or formal data-retention system.

Do not place real dealership data, live Tekion credentials, or production
provider keys into the prototype.

## Incident-ready logging

Every security-relevant event includes a timestamp, event type, outcome,
and available tenant/store/device/agent/job identifiers. The job ID is the
prototype correlation key. Metadata must be allowlisted. Request bodies,
Authorization headers, lease and claim tokens, provider credentials, dealer
credentials, cookies, and raw browser storage are never audit fields.

## Required production reviews

Before a dealership pilot with real data:

1. External architecture and application security review.
2. Tekion partner agreement and API/browser authorization review.
3. Commercial name and trademark review for Altas.
4. OS-specific secure-storage and installer review.
5. Dependency, asset-license, and SBOM review.
6. Data-processing agreement, retention, deletion, and backup design.
7. Operator/customer RBAC and support-consent design.
8. Threat-model update against the exact deployment topology.
