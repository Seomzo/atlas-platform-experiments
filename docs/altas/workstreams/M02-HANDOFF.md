# M02 Handoff — Authenticated Durable Mobile Text Relay

## Status

- Branch: `codex/mobile-m02-text-relay`
- Draft PR: [#8](https://github.com/Seomzo/atlas-platform-experiments/pull/8)
- Baseline: WS-05 tip `20997113cf87657b607a306bc38b0cfb23f2db29`
- Last validated: 2026-08-12

## Goal and scope

Implement the backend workstream between account/device enrollment and the
future iOS app: one enrolled phone, one explicitly paired worker, one store,
durable text commands, ordered replay, and observable worker availability.

No iOS target, APNs, voice, production identity provider, or merge is in this
workstream.

## Decisions made

- Mobile never connects to the broad Desktop `/api/ws` registry.
- Phone calls require both a verified account assertion and a short-lived
  session for the exact enrolled phone.
- Workers authenticate an outbound WebSocket with a short-lived Ed25519-proven
  device session; query auth and browser origins are rejected.
- Only `session.create`, `prompt.submit`, and `session.interrupt` can cross the
  relay. The worker-local adapter owns all gateway/profile/workspace choices.
- Acknowledgements occur only after durable receipt at each side.
- Payloads are encrypted before both Control Plane and worker SQLite writes.
- The current non-idempotent local gateway crash window fails closed as
  `local_gateway_outcome_unknown`; it is never silently replayed.

## Deferred decisions

- Promotion of the committed Task Thread backend as the supported Desktop
  runtime for seamless post-crash turn retry.
- Production KMS/key rotation and ciphertext retention policy.
- Multi-instance WebSocket routing and distributed presence.
- APNs, voice, and iOS implementation.

## Files changed

- `altas/control_plane/database.py`
- `altas/control_plane/config.py`
- `altas/control_plane/app.py`
- `altas/control_plane/relay_api.py`
- `altas/control_plane/relay_repository.py`
- `altas/control_plane/relay_security.py`
- `altas/mobile_relay/__init__.py`
- `altas/mobile_relay/store.py`
- `altas/mobile_relay/gateway_adapter.py`
- `altas/mobile_relay/worker.py`
- `tests/altas/test_mobile_text_relay.py`
- `tests/altas/test_mobile_relay_worker.py`
- `docs/altas/mobile/M02_TEXT_RELAY.md`
- this handoff

## Contracts and migrations

Additive SQLite tables:

- `relay_pairings`
- `relay_sessions`
- `relay_commands`
- `relay_events`
- `relay_worker_connections`

The worker owns separate encrypted `relay_inbox`, `relay_outbox`, and
`relay_session_bindings` tables. See
[`M02_TEXT_RELAY.md`](../mobile/M02_TEXT_RELAY.md) for HTTP, WebSocket,
idempotency, replay, and crash contracts.

## Validation

- `scripts/run_tests.sh tests/altas/ -q`: 24 files, 403 tests passed.
- Ruff check and format passed for every changed Python file.
- `git diff --check` passed.

The canonical test entry point is `scripts/run_tests.sh`; direct `pytest` was
not used.

## Security and privacy checks

- No client tenant field.
- No reusable secret embedded in a phone or worker request contract.
- No query auth on the public worker relay.
- No browser origin on the worker relay.
- No arbitrary gateway method/profile/workspace/model selection.
- No raw message content in Control Plane audit rows or either SQLite plane.
- Live device status is checked at authentication and worker event commit.
- Revocation terminates pairings, sessions, pending commands, and live worker
  sockets.

## Visual evidence

None. This workstream is backend-only; the iOS surface is intentionally the
next prompt.

## Known risks and limitations

- Control Plane presence is process-local and therefore single-instance.
- Payload encryption derives from the prototype process secret rather than a
  KMS-managed data-encryption key.
- The local Desktop gateway itself remains a broad admin surface. Safety
  depends on loopback-only access and the hard-allowlisted adapter.
- Executing local gateway calls cannot be automatically replayed after an
  ambiguous worker crash until the durable idempotent Task Thread runtime is
  promoted.

## Integration order and conflicts

1. Review/merge WS-05.
2. Rebase and review M02.
3. Add WS-09 managed approval decisions on the relay event/command identities.
4. Begin the iOS app against the versioned M02/WS-09 contract.

M02 is intentionally stacked on WS-05 and must not merge before it.

## Exact next actions

1. Run all Atlas tests and formatting checks.
2. Commit and push this branch.
3. Open a draft PR with base `codex/ws-05-device-enrollment`.
4. Build WS-09 on the M02 tip without merging either branch.
