# WS-09 Handoff — Managed action policy and approvals

## Status

- Branch: `codex/ws-09-managed-approvals`
- Draft PR: [#9](https://github.com/Seomzo/atlas-platform-experiments/pull/9)
- Baseline: M02 tip `4fe762d71f8a2dff66d1156dfcb80bb01d4a221d`
- Last validated: 2026-08-12 America/Los_Angeles
- Overall: exact, expiring, single-use approval contract and one synthetic
  approval-gated workflow implemented; final mobile/React UI and live actions
  intentionally deferred

## Goal and scope

WS-09 adds a deterministic managed-action vocabulary and a durable approval
service on the WS-05 identity and M02 relay boundary. It proves one
`fixed_ops.synthetic_export` workflow from worker request through exact phone
decision and one-time server consume without creating a file or external side
effect.

This branch does not build the iOS app, final React approval UI, APNs, live
Tekion/file/message writes, a credential viewer, billing rules, or production
operator administration.

## Decisions made

- Classify managed work as read, navigate, analyze, draft, download/export,
  send/message, submit, mutate, credential, or administrative.
- Require approval for every export, send, submit, mutation, credential, and
  administrative action. Unknown managed tools fail closed until they have an
  explicit classifier and target projection.
- Bind one decision to server-owned tenant/store/user/phone/worker/agent,
  relay session, exact job attempt and claim, workflow/capability, lease nonce
  and expiry, action digest, target, policy version, expiry, and correlation.
- Cap approval expiry at the original lease expiry and use versioned immediate
  transactions for phone decision races.
- Treat an exact repeated phone response as idempotent but make successful
  worker consumption strictly single use.
- Recheck every live policy and identity edge at consumption and cancel on
  device/job/agent/store/entitlement/subscription changes.
- Encrypt human display objects before Control Plane and relay persistence;
  audit only classifications, IDs, digests, fixed reasons, and outcomes.
- Preserve the mandatory managed tool guard. Consequential tool dispatch must
  consume the exact approval after the ordinary policy allow.

## Deferred decisions

- Final phone and Desktop approval UI, notification channel, accessibility,
  reauthentication, and support/recovery flows.
- Production data store, KMS key lifecycle, distributed presence/concurrency,
  expiry scheduler, retention, rate limits, and abuse monitoring.
- Per-connector projections for real messages, exports, forms, browser
  mutations, and credential operations.
- Production OIDC and operator RBAC.
- General managed supervisor orchestration for injecting exact approval
  references into every future consequential Hermes request. The enforcement
  hook exists; this workstream orchestrates only the synthetic export.

## Files changed

- `altas/managed/actions.py`: canonical action schema, digest, risk policy, and
  allowlisted tool classifier.
- `altas/control_plane/approval_repository.py`: durable encrypted approval
  lifecycle, exact scope validation, expiry, races, consume, and cancellation.
- `altas/control_plane/approval_api.py`: worker and enrolled-phone contracts.
- `altas/control_plane/database.py` and `config.py`: approval table, indexes,
  and bounded TTL.
- `altas/control_plane/app.py`, `relay_repository.py`, and `repository.py`:
  service wiring, durable control events, policy-state cancellation, and the
  synthetic entitlement.
- `altas/managed/client.py`, `context.py`, `policy_guard.py`, and `worker.py`:
  approval client/receipt, mandatory consequence guard, and synthetic worker
  orchestration.
- `altas/fixed_ops/approved_export.py` and `__init__.py`: deterministic
  no-side-effect approved export fixture.
- `tests/altas/test_managed_approvals.py`, `test_managed_policy_guard.py`, and
  `test_managed_worker.py`: API/database, concurrency, scope, alternate guard,
  and end-to-end worker coverage.
- `docs/altas/MANAGED_APPROVALS.md`, `ARCHITECTURE.md`, `SECURITY.md`,
  `MVP_ACCEPTANCE.md`, `.env.atlas.example`, and this handoff.

## Contracts and migrations

Implemented endpoints:

```text
POST /api/v1/worker/approvals
GET  /api/v1/worker/approvals/{approval_id}
POST /api/v1/worker/approvals/{approval_id}/consume
GET  /api/v1/mobile/relay/approvals
GET  /api/v1/mobile/relay/approvals/{approval_id}
POST /api/v1/mobile/relay/approvals/{approval_id}/responses
```

Fresh databases add `managed_approvals` plus phone/status, job/attempt/status,
and expiry indexes. Human display payloads use the existing relay AES-GCM
cipher. Request/decision idempotency keys remain hash-only. The default
`ATLAS_MANAGED_APPROVAL_TTL_SECONDS=120` must be at least 30 seconds and no
longer than the ordinary lease TTL.

The M02 stream adds `approval.requested`, `approval.resolved`,
`approval.expired`, `approval.canceled`, and `approval.consumed` encrypted
events. The exact schemas and lifecycle are in
[`MANAGED_APPROVALS.md`](../MANAGED_APPROVALS.md).

## Validation

Latest validation:

```text
scripts/run_tests.sh tests/altas/ -q
python -m ruff check <all changed Python files>
python -m ruff format --check <all changed Python files>
git diff --check
```

Result: 25 files, 415 tests passed, 0 failed. The focused approval, managed
guard, and worker subset passed 33 tests. Ruff and diff checks passed.

Coverage includes exact success, request/decision idempotency, modified action,
cross-store and cross-job denial, phone isolation, concurrent approve/deny,
persisted expiry, stale lease, superseded attempt, revocation, policy-resource
cancellation, single-use consume, encrypted display persistence, ordered relay
events, unavailable policy, mandatory alternate dispatch paths, and the
synthetic worker flow.

## Security and privacy checks

- Approval augments policy; it cannot grant a capability, store, job, or lease.
- Phone authority comes from live account membership, role, store grant,
  enrolled-device session, and exact relay pairing.
- Every worker read/request/consume requires the live lease and job claim.
- Human approval sees a canonical bounded action, not raw model arguments.
- Digest/version/idempotency comparisons and immediate transactions close
  replay and phone-decision races.
- Consumption rechecks original lease identity and all live scope before the
  deterministic action runs.
- Raw credentials and arbitrary customer/job payloads do not enter approval or
  audit metadata.
- The synthetic workflow performs no external write and creates no artifact.

## Visual evidence

None. WS-09 is a backend/client contract. The final approval surface belongs to
the user's next iOS prompt.

## Known risks and limitations

- SQLite and the current cipher key source remain prototype-only.
- Expiry is enforced on reads/transitions rather than a background scheduler;
  it is still fail closed at decision and consume time.
- M02 worker presence is single-process and no APNs wake path exists.
- The existing Desktop compatibility worker still uses its legacy bearer;
  enrolled Ed25519 worker-session wiring is a separate Desktop integration.
- Only the synthetic export is fully orchestrated. Real consequential actions
  require reviewed target projections, safe connector contracts, and their own
  invariant tests.

## Integration order and conflicts

1. Review/merge WS-05 draft PR #7.
2. Rebase/review M02 draft PR #8 on the accepted WS-05 result.
3. Rebase/review WS-09 on the accepted M02 result.
4. Build the native iOS client against the versioned enrollment, relay, and
   approval contracts; do not connect it to Desktop `/api/ws`.

Likely shared conflicts are `altas/control_plane/app.py`, `database.py`,
`repository.py`, managed client/worker/guard files, and architecture/security
docs. Do not resolve them by dropping live-state checks, exact claims,
encrypted relay events, or the mandatory managed guard.

## Exact next actions

1. Review draft PR #9 only after its WS-05 and M02 bases are accepted; do not
   merge the stack out of order.
2. Review the action vocabulary and phone response contract before freezing
   the first iOS API models.
3. In the next iOS workstream, implement account/device bootstrap, relay text,
   durable event replay, and exact approval presentation/response in that
   order; keep APNs and voice behind explicit follow-up acceptance criteria.
