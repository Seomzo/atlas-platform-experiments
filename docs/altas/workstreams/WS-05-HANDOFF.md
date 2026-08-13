# WS-05 Handoff — Atlas account and device enrollment

## Status

- Branch: `codex/ws-05-device-enrollment`
- Draft PR: <https://github.com/Seomzo/atlas-platform-experiments/pull/7>
- Baseline: `4934f94eae2d02e039a8bb430f0cfb27075cc40e`
- Last validated: 2026-08-12 America/Los_Angeles
- Overall: provider-neutral identity/membership and Ed25519 device-enrollment
  vertical slice implemented; production OIDC and Desktop UI intentionally
  deferred

## Goal and scope

WS-05 adds the missing server-owned account, membership, role, store-grant,
one-time enrollment, device-bound proof, key rotation, and revocation contract
to the existing SQLite Control Plane. It preserves the seeded legacy worker and
its current bearer/lease/job/model paths while providing the production-shaped
path that Desktop and a future phone client can consume.

This branch does not choose an identity vendor, create live users, build the
full Desktop sign-in UI, create an iOS target, expose the localhost admin
surface, or claim platform attestation.

## Decisions made

- Treat an injected `IdentityVerifier` as the provider boundary. It returns a
  cryptographically verified issuer/subject; Atlas reloads all authorization
  state from its database.
- Derive tenant from a live membership plus store grant. Enrollment schemas do
  not contain `tenant_id`; unknown fields fail validation.
- Use `owner`, `operator`, and `member` roles. Members may enroll a phone only;
  owners/operators may also enroll a worker and revoke devices.
- Require worker enrollment to bind an active unassigned agent in the derived
  tenant/store. Phone enrollment never binds an agent.
- Store the random enrollment token only as a hash, return it once, expire it
  after ten minutes, and redeem it with `BEGIN IMMEDIATE` so concurrent callers
  cannot create two devices.
- Register raw Ed25519 public keys and keep private keys device-side. A signed
  timestamp plus persisted nonce mints a five-minute versioned device session.
- Require both the current session and a signature by the replacement key for
  rotation. Incrementing the credential version makes old sessions fail on the
  next request.
- Disable and version-bump a revoked device, unbind its agent, and cancel its
  queued/running work. Unauthorized and missing device IDs share one safe
  response.
- Keep the original bearer only as an explicitly marked compatibility path for
  the current seeded supervisor; enrolled devices cannot use that path.

## Deferred decisions

- Production OIDC issuer/vendor, authorization-code/PKCE callback ownership,
  account recovery, MFA, browser reauthentication, and support impersonation.
- Secure Enclave/TPM/Keychain/Credential Manager integration and whether
  platform attestation is justified for the pilot threat model.
- Multi-instance nonce/replay storage and PostgreSQL migrations.
- Rate limits, suspicious-proof alerting, device recovery/replacement UX, and
  credential-expiry notification.
- Full Desktop browser UI and removal of the seeded bearer after migration.
- Phone-to-worker pairing and relay authorization; those consume WS-05 rather
  than expanding its trust model.

## Files changed

- `altas/control_plane/identity.py`: injected verifier protocol, fail-closed
  unconfigured adapter, and loopback deterministic signed development adapter.
- `altas/control_plane/security.py`: versioned short-lived device sessions,
  Ed25519 key validation, proof verification, and canonical challenges.
- `altas/control_plane/database.py`: users, memberships, store grants,
  enrollments, proof nonces, device credential fields, audit correlation, and
  idempotent existing-database migrations.
- `altas/control_plane/repository.py`: live account authorization, atomic
  enrollment/redemption, device sessions, rotation, listing, and revocation.
- `altas/control_plane/schemas.py` and `app.py`: strict request contracts,
  account/device endpoints, demo adapter route, and worker/phone separation.
- `tests/altas/test_device_enrollment.py`: deterministic API/database and
  migration coverage.
- `docs/altas/DEVICE_ENROLLMENT.md`, `ARCHITECTURE.md`, and `SECURITY.md`:
  client wire contract and implemented trust-boundary updates.

## Contracts and migrations

Implemented endpoints:

```text
GET  /api/v1/account/context
POST /api/v1/account/enrollments
GET  /api/v1/account/devices?store_id=...
POST /api/v1/account/devices/{device_id}/revoke
POST /api/v1/device/enrollments/redeem
POST /api/v1/device/sessions
POST /api/v1/device/credentials/rotate
POST /api/v1/dev/identity/token        # demo mode + localhost admin only
```

Canonical proof bytes and complete client sequencing are in
`docs/altas/DEVICE_ENROLLMENT.md`.

Fresh databases create `users`, `memberships`, `membership_store_grants`,
`device_enrollments`, and `device_proof_nonces`. Existing databases receive
additive device credential and audit columns plus a partial unique public-key
thumbprint index. The original `devices.secret_hash NOT NULL` constraint cannot
be relaxed additively in SQLite without rebuilding all referencing tables; an
asymmetric device therefore receives a random discarded preimage digest in
that legacy column. `credential_kind='ed25519'` prevents the digest from ever
entering bearer authentication. A future PostgreSQL migration should make the
legacy column nullable and remove this compatibility artifact.

No enrollment token, identity assertion, device session, signature, or private
key is persisted. Public key and SHA-256 thumbprint are non-secret identity
material. Audit rows now accept `user_id` and `correlation_id`; enrollment
create/redeem share the latter.

## Validation

Latest commands:

```text
scripts/run_tests.sh tests/altas -q
scripts/run_tests.sh tests/altas/test_device_enrollment.py -q
scripts/run_tests.sh tests/altas/test_security.py tests/altas/test_control_plane_api.py tests/altas/test_control_center_delivery.py -q
python -m ruff check altas/control_plane tests/altas/test_device_enrollment.py
python -m ruff format --check altas/control_plane tests/altas/test_device_enrollment.py
git diff --check
```

The complete Atlas slice passed: 22 files, 396 tests, 0 failures. The focused
device-enrollment file passed 10 tests, and the legacy control-plane/security/
Control Center subset passed 47 tests without modification.

Focused coverage proves success, server-derived tenant/store scope, member/
operator boundaries, expiry without sleeping, one-time replay, revoked user,
revoked device, stale/wrong/malformed proof, persisted nonce replay, eight-way
concurrent redemption, worker heartbeat/lease exchange, key rotation, identity
assertion expiry, and migration of a pre-WS-05 device table.

## Security and privacy checks

- Browser assertions supply no role, store, or tenant authority.
- Account and device tokens are independent bearer schemes on disjoint routes.
- Every enrolled-device session requires proof of a device-held private key.
- Proof verification is followed by an atomic live key/version/status check and
  nonce insert, closing verification-to-use and replay races.
- Account/user/membership/grant, enrollment, device, agent, credential expiry,
  and revocation state are reloaded at each security boundary.
- Phone sessions cannot call worker heartbeat, job, policy, or model endpoints.
- Safe errors do not reveal whether an enrollment token, device ID, or
  unauthorized store exists.
- Audit contains identifiers, result codes, versions, expiries, correlation,
  and public-key thumbprints only; no raw request or secret material.
- Demo identity issuance is absent unless seeded demo mode is active, still
  requires the separate localhost admin bearer, and remains blocked remotely
  by the existing demo middleware.

## Visual evidence

None. WS-05 is a backend/client-contract workstream; the prompt explicitly
excluded the full Desktop login UI. API/database behavior is the relevant
evidence.

## Known risks and limitations

- The deterministic identity adapter is development-only, not an OIDC or
  production account system.
- Ed25519 proves possession of generated key material but not hardware binding,
  device health, or platform integrity.
- SQLite nonce state is correct for the single Control Plane process only; it
  is not a distributed replay cache.
- The existing worker still reads the seeded legacy bearer. The new flow is
  proven through the same heartbeat API but not yet wired into Desktop's OS
  vault or supervisor lifecycle.
- Identity and device sessions are short-lived server-signed bearers. TLS,
  secure cookie/browser callback handling, issuer key rotation, and hosted
  session termination remain deployment requirements.
- Credential expiry currently fails closed; renewal notification and recovery
  UX are deferred.

## Integration order and conflicts

Land WS-05 before any mobile relay or WS-09 approval work that relies on human,
store, or device authority. A relay branch should base on this branch (or its
reviewed merge) and consume `IdentityVerifier`, account store grants, enrolled
phone/worker device classes, and versioned device sessions without adding a
second identity system.

Likely conflict surfaces are limited to `altas/control_plane/app.py`,
`database.py`, `repository.py`, `schemas.py`, `security.py`, and the two
architecture/security documents. Do not resolve those conflicts by dropping
live-state rechecks, nonce persistence, strict schemas, or compatibility tests.

## Exact next actions

1. Review the API and canonical signing strings in
   `docs/altas/DEVICE_ENROLLMENT.md`.
2. Merge only after draft-PR review and green CI; this workstream does not merge
   itself.
3. Base the text-relay workstream on reviewed WS-05 and add explicit
   phone-to-worker pairing, not request-body routing authority.
4. Wire Desktop to system-browser auth, OS-vault Ed25519 generation, redemption,
   proof, session renewal, and heartbeat; do not fall back to the legacy bearer
   after enrolled-auth failure.
5. Build WS-09 exact managed approvals on the same user/device/store authority.
6. Create the native iOS target only after the user's next prompt, consuming
   this contract rather than embedding any reusable app secret.
