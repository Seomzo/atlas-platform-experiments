# Atlas Account and Device Enrollment Contract

Status: WS-05 provider-neutral vertical slice. This contract is implemented by
the Control Plane prototype. It does not select or configure a production
identity vendor, hosted deployment, or device-attestation service.

## Authority model

An injected identity verifier validates the browser's short-lived assertion and
returns only stable `issuer` and `subject` coordinates. The Control Plane maps
those coordinates to its own user, membership, role, tenant, and store-grant
records. Clients never assert a tenant.

Roles are intentionally small:

| Role | Enroll phone | Enroll worker | Revoke device |
| --- | --- | --- | --- |
| `owner` | yes, granted stores | yes, granted stores | yes, granted stores |
| `operator` | yes, granted stores | yes, granted stores | yes, granted stores |
| `member` | yes, granted stores | no | no |

A worker enrollment must name an active, currently unbound agent in the same
derived tenant/store. A phone enrollment must not name an agent.

## Browser/account API

Every response under `/api/` has `Cache-Control: no-store`.

### Read server-owned context

```http
GET /api/v1/account/context
Authorization: Bearer <verified-browser-identity-assertion>
```

The response contains the current user plus live memberships and only their
explicitly granted active stores. Revoking the user or membership affects the
next request; identity-token claims are not treated as role or tenant authority.

### Create a one-time enrollment

```http
POST /api/v1/account/enrollments
Authorization: Bearer <verified-browser-identity-assertion>
Content-Type: application/json

{
  "store_id": "store-sunrise-vw",
  "device_class": "phone",
  "device_name": "Operator's iPhone"
}
```

For a worker, `device_class` is `worker` and `agent_id` is required. There is no
`tenant_id` field. Unknown fields are rejected.

The `201` response includes enrollment metadata and one plaintext redemption
token under `redemption.token`. That value is returned once, stored only as a
SHA-256 digest, and expires after 600 seconds by default. The create and redeem
audit events share the response's server-generated `correlation_id`.

## Device API

### Redeem and register a key

Before redemption, the device generates an Ed25519 key pair. It stores the
private key in the platform credential vault and sends only the raw 32-byte
public key encoded as unpadded base64url.

```http
POST /api/v1/device/enrollments/redeem
Content-Type: application/json

{
  "enrollment_token": "<one-time-token>",
  "public_key": "<43-character-base64url-public-key>",
  "platform": "ios",
  "platform_version": "26.6",
  "app_version": "0.1"
}
```

Redemption is one immediate database transaction: it rechecks the user,
membership, role, tenant, store grant, optional agent assignment, expiry, and
pending state; creates one device; binds the worker agent when applicable; and
marks the token redeemed. Concurrent or replayed redemption returns the same
safe `enrollment_not_redeemable` outcome and creates no second device.

### Prove the device and mint a session

Generate a fresh cryptographically random unpadded-base64url nonce (at least 16
bytes), take the current Unix timestamp in seconds, and sign these exact UTF-8
bytes with the enrolled private key:

```text
atlas-device-session-proof-v1\n{device_id}\n{timestamp}\n{nonce}
```

Then send:

```http
POST /api/v1/device/sessions
Content-Type: application/json

{
  "device_id": "device_...",
  "timestamp": 1786579200,
  "nonce": "<unpadded-base64url-nonce>",
  "signature": "<86-character-base64url-Ed25519-signature>"
}
```

The default accepted clock skew is 60 seconds. A nonce is persisted and can be
used only once. A valid proof returns a five-minute bearer under
`access_token`. The session includes the credential version, and every use
reloads the device's live status/version, so rotation or revocation invalidates
an already issued session immediately.

An enrolled worker uses this short-lived session as the `Authorization` bearer
on the existing heartbeat. The heartbeat derives tenant from the session-bound
device. Its legacy `tenant_id` body field remains a compatibility cross-check,
not authority. The returned worker lease remains short-lived, store/agent/
capability scoped, and subject to live policy rechecks.

### Rotate the key

Rotation requires a still-valid device session and proof of the replacement
private key. Sign:

```text
atlas-device-key-rotation-v1\n{device_id}\n{new_public_key}\n{timestamp}\n{nonce}
```

Send the new public key, timestamp, nonce, and signature to:

```http
POST /api/v1/device/credentials/rotate
Authorization: Bearer <current-device-session>
```

The atomic update consumes the nonce, changes the key thumbprint, increments
the credential version, and returns a session for the new version. The old key
and all sessions for its version fail immediately.

### Revoke

```http
POST /api/v1/account/devices/{device_id}/revoke
Authorization: Bearer <verified-browser-identity-assertion>
Content-Type: application/json

{"reason":"lost device"}
```

Only an active owner/operator with a grant to the device's live store may
revoke it. Revocation disables the device, increments its credential version,
unassigns its worker agent, and cancels associated queued/running jobs. Missing
and unauthorized device IDs share `device_not_found`.

## Stable safe outcomes

- `account_authentication_failed`
- `store_access_denied`
- `role_not_allowed`
- `agent_not_enrollable`
- `enrollment_not_redeemable`
- `device_key_invalid`
- `device_key_already_registered`
- `device_proof_invalid`
- `device_authentication_failed`
- `device_session_required`
- `device_credential_stale`
- `device_credential_conflict`
- `worker_device_required`

Validation errors use HTTP `422`. Authentication failures use `401`; store/
role denials use `403`; one-time state and rotation races use `409`. Enrollment
tokens, identity assertions, device sessions, signatures, and private keys are
never written to audit metadata.

## Deterministic development adapter

When and only when `seed_demo_data` is enabled, the loopback/admin boundary
registers:

```http
POST /api/v1/dev/identity/token
Authorization: Bearer <localhost-development-admin-token>
Content-Type: application/json

{"subject":"atlas-demo-owner"}
```

It returns a short-lived signed assertion for an already seeded user. This
route is absent outside demo mode and is not a production sign-in design. A
production deployment injects an `IdentityVerifier` that validates the chosen
OIDC issuer, audience, signature, expiry, and browser authorization flow while
leaving the account/enrollment API unchanged.

## Desktop integration sequence

1. Open the system browser for the eventual authorization-code/PKCE flow and
   return its verified short-lived assertion to Desktop without embedding a
   client secret.
2. Read `/api/v1/account/context`; let the user select only a returned store and
   an eligible unbound agent.
3. Create the enrollment in the browser/account session.
4. Generate the Ed25519 private key in Keychain/Credential Manager with the
   strongest non-exportable protection available; never place it in config or
   process environment.
5. Redeem once with the public key, then erase the enrollment token.
6. On launch/reconnect, sign a fresh device-session challenge. Keep the
   returned short-lived session in memory and renew it before expiry.
7. Use that session for heartbeat and receive the existing scoped worker lease.
8. On rotation, persist the new private key only after the server accepts it;
   retain a recoverable rollback strategy until the response arrives.
9. On `401`, do not fall back to the legacy bearer. Re-prove the current key or
   require authorized re-enrollment.

The current Desktop supervisor still uses the seeded compatibility bearer. The
steps above are the exact follow-up integration; WS-05 does not build the full
Desktop login UI.
