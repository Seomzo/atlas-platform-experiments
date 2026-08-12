# M01 Handoff — Local discovery and mobile readiness audit

## Status

- Branch: `codex/mobile-m01-discovery`
- Draft PR: pending
- Baseline: `4934f94eae2d02e039a8bb430f0cfb27075cc40e`
- Baseline relation: audit-start HEAD, local `origin/main`, and live
  `refs/heads/main` were equal
- Last validated: 2026-08-12 America/Los_Angeles
- Overall: discovery complete; Atlas is not ready for a native phone client

## Goal and scope

M01 located the current repository by exact remote identity, preserved every
existing worktree, inventoried the installed Atlas environment, exercised the
Control Plane, worker, Control Center, Desktop, gateway, engine, tool stream,
and approval UI in isolated local environments, inspected current GUI states,
and traced the implementation behind sessions, events, tools, approvals,
voice, relay, policy, and managed-worker jobs.

M01 is an audit and specification workstream. It does not add an iPhone
project, change product source, modify the real Atlas profile, connect to a
dealership, expose a listener beyond loopback, or select an identity vendor.

The authoritative findings are split by concern:

- `docs/altas/mobile/M01_DISCOVERY_AUDIT.md`
- `docs/altas/mobile/M01_RUNTIME_MAP.md`
- `docs/altas/mobile/M01_GATEWAY_CONTRACT_INVENTORY.md`
- `docs/altas/mobile/M01_MOBILE_GAP_MATRIX.md`
- `docs/altas/mobile/M01_VISUAL_SURFACE_AUDIT.md`

Machine-specific paths, tool versions, launch commands, and screenshots are
kept in the ignored `artifacts/mobile-readiness/` tree.

## Decisions made

- Treat commit `4934f94e...` as the audited baseline. The normal `main`
  checkout was 16 commits behind; an active voice prototype was two committed
  changes ahead and materially dirty. Neither was mutated.
- Treat the installed CLI, installed Desktop bundle, baseline repository, and
  voice prototype as separate artifacts. The installed build stamp cannot be
  mapped to current repository history.
- Do not expose Desktop `/api/ws`, the generic global-bearer API server, the
  experimental Team Gateway connector protocol, or the localhost Control
  Center admin API directly to a phone.
- Reuse contracts selectively: SessionDB resume semantics, the API stream
  envelope, Task Thread durable event/idempotency/exact-approval primitives,
  managed-worker policy/claim seams, and outward-dial/reconnect topology.
- Correct the product documentation assumption that a managed worker already
  maintains a persistent authenticated Control Plane connection. Current
  workers heartbeat and poll ordinary HTTP every three seconds.
- Run WS-05 before mobile UI work. Identity and device enrollment are specified
  but unimplemented, and every safe relay/approval decision depends on their
  actor and device binding.

## Deferred decisions

- Identity vendor and hosted login UX. WS-05 can remain provider-neutral.
- Production relay transport, hosted topology, regions, database, queue,
  retention, and disaster-recovery choices.
- Exact migration path from the prototype Task Thread branch to current main.
- Phone voice transport, speech-processing location, raw-audio retention,
  background behavior, and barge-in semantics.
- APNs provider, notification privacy payload, and deep-link policy.
- Operator/customer application separation and production Control Center RBAC.
- App Store team, signing, privacy disclosure, and release ownership.

## Files changed

- `.gitignore` — keeps machine-local mobile-readiness evidence out of Git.
- `docs/altas/mobile/M01_DISCOVERY_AUDIT.md`
- `docs/altas/mobile/M01_RUNTIME_MAP.md`
- `docs/altas/mobile/M01_GATEWAY_CONTRACT_INVENTORY.md`
- `docs/altas/mobile/M01_MOBILE_GAP_MATRIX.md`
- `docs/altas/mobile/M01_VISUAL_SURFACE_AUDIT.md`
- `docs/altas/workstreams/M01-HANDOFF.md`

Ignored local evidence:

- `artifacts/mobile-readiness/LOCAL_ENVIRONMENT.md`
- `artifacts/mobile-readiness/current-surfaces/01-control-center-overview.png`
  through `13-control-center-disconnected.png`

No application source, dependency lockfile, schema, real configuration, or
secret file changed.

## Contracts and migrations

M01 defines no production API and performs no migration. It inventories the
contracts that a successor workstream may adapt.

The minimum future relay contract must:

- authenticate a user plus enrolled device and derive organization, store,
  role, phone, and worker access from server state;
- expose only list/create/resume/send/observe/interrupt and exact approval
  operations;
- require idempotency keys for mutations;
- emit durable event ID, thread/session ID, turn ID, sequence, correlation,
  causation, timestamp, and typed payload;
- support cursor replay, transcript reconciliation, reconnect, expiration,
  worker-unavailable state, and bounded backpressure;
- bind an approval to the exact actor, phone, worker, job/turn, target digest,
  capability, expiry, and one-use redemption;
- deny shell, arbitrary CLI, configuration, keys, billing, process control,
  sudo, and secret capture to mobile.

The active prototype's Task Thread database and renderer are not a migration
authority. They lack tenant, user, device, and owner fields, trust some
client-selected routing values, and store all deltas without a retention rule.

## Validation

Verified during M01:

```text
git rev-parse HEAD
git rev-parse origin/main
git ls-remote origin refs/heads/main
make PYTHON=<installed-atlas-python> atlas-smoke
<installed-atlas-control> doctor        # isolated Atlas home
<installed-atlas-control> worker --once # fixture job
npm --workspace apps/desktop run dev    # isolated profile, loopback only
```

Observed results:

- Repository, local remote-tracking ref, and live main were equal at
  `4934f94e...` when discovery began.
- Control Plane smoke passed, including the fixed report, cross-store denial,
  usage, claim, and revocation checks against fixtures and `altas-mock`.
- The one-shot worker completed `job_demo_daily_report`.
- Control Center rendered Overview, Fleet, Jobs, Audit, and disconnected states.
- Desktop completed a real composer-to-gateway-to-engine-to-harmless-tool-to-
  stream-renderer turn through a local deterministic provider.
- A harmless command reached the real Desktop approval surface and was
  rejected; it did not execute.
- Audit-started listeners on ports 8787, 5174, and 19081 were closed afterward.

Final document validation passed before handoff:

```text
git diff --cached --check
git status --short
git check-ignore artifacts/mobile-readiness/LOCAL_ENVIRONMENT.md
test -f docs/altas/mobile/M01_DISCOVERY_AUDIT.md
test -f docs/altas/mobile/M01_RUNTIME_MAP.md
test -f docs/altas/mobile/M01_GATEWAY_CONTRACT_INVENTORY.md
test -f docs/altas/mobile/M01_MOBILE_GAP_MATRIX.md
test -f docs/altas/mobile/M01_VISUAL_SURFACE_AUDIT.md
test -f docs/altas/workstreams/M01-HANDOFF.md
```

The final review also resolved every repository-relative Markdown link and
source path, matched all 13 screenshot hashes to the visual inventory, found no
personal home path or credential-shaped assignment in committed documentation,
and reconfirmed that audit-started listeners were absent.

## Security and privacy checks

- No credential, cookie, device identifier, Keychain value, `.env` value,
  customer record, dealership credential, or real conversation is present in
  committed documentation or screenshots.
- Real Atlas configuration contents were not read or modified. Status checks
  recorded configured/missing variable names only.
- Every audit service bound to `127.0.0.1`; no tunnel, port forward, or public
  relay was created.
- Desktop used a temporary home, user-data directory, database, workspace, and
  deterministic loopback provider. The synthetic approval was rejected.
- Control Plane proof used fixture data and the deterministic model gateway.
- Current WebSocket authentication is not authorization: a valid peer can
  reach a broad Desktop/admin registry because the consumed identity is not
  propagated into method/resource authorization.
- Baseline approvals are FIFO and not bound to a stable approval ID or the
  WS-09 target tuple. They are unsafe for phone authorization.
- A synthetic `git push` proposed from the empty isolated workspace did not
  trigger the dangerous-command gate; it failed with `not a git repository`
  before any remote action was possible. A later harmless Python command did
  trigger the approval UI and was rejected. Command gating is therefore
  pattern-sensitive, not a general authorization boundary.
- The experimental relay can acknowledge buffered input before the scheduled
  agent task establishes durable receipt. Fix that ordering before treating it
  as delivery evidence.

## Visual evidence

Current-run screenshots cover:

- Control Center overview, fleet, jobs, audit record, and disconnected state;
- Desktop first-launch memory gate, setup failure, main chat/session list,
  real tool plus streamed response, model settings, voice settings,
  provider/account selection, local/remote gateway settings, and approval UI.

The Control Center uses an intentional high-density industrial workstation
language with strong fixture/dev labeling and explicit states. Reuse its
status vocabulary and accountability model, not its persistent rail, small
condensed text, wide tables, or admin job form on iPhone.

Desktop has a calmer, content-first shell with clear session, tool, approval,
provider, voice, and connection patterns. The multi-pane workspace, hidden
hover actions, large settings matrix, local filesystem, arbitrary tools, and
server-machine voice controls are desktop-only concepts.

## Known risks and limitations

- No current end-to-end mobile subsystem is classified **Ready**.
- WS-05, WS-06, and WS-09 have specification prompts but no handoff or merged
  implementation. `CONTROL_CENTER_SPEC.md` is also absent.
- Current Control Plane persistence, admin auth, deterministic model, and
  fixture integration remain prototypes.
- Current managed worker has no persistent outbound relay, durable relay
  cursor, or conversation adapter.
- The generic API server uses one global bearer and process-memory stream/run
  state; Desktop `/api/ws` exposes a much broader local/admin surface.
- Ordinary Desktop events have no replay cursor; submit retry can duplicate a
  turn after a lost acknowledgement.
- Baseline approval requests have no immutable ID. Exact-ID approval exists
  only in the separate prototype.
- Desktop voice is request/response STT and TTS, not a full-duplex mobile media
  path. The realtime voice director is a dirty local-only reducer/types
  skeleton without a transport or live provider proof.
- No hosted environment, TLS, PostgreSQL, queue, APNs, backup, incident drill,
  iOS build, signing, TestFlight, or live external connector was verified.
- Full Xcode exists, but the machine's active developer directory is Command
  Line Tools; a scoped `DEVELOPER_DIR` is required until the user changes it.

## Integration order and conflicts

1. Merge M01 documentation only; it has no runtime dependencies.
2. Implement WS-05 provider-neutral identity and phone/worker enrollment.
3. Integrate or port the Task Thread durability primitives only after adding
   tenant/user/device ownership and server-enforced routing.
4. Build a text-only outbound Control Plane relay for one user, one phone, one
   worker, and one session. Correct relay durable-receipt ordering first.
5. Implement WS-09 exact target-bound approvals on the enrolled identity.
6. Add APNs and offline/background completion.
7. Specify and implement mobile voice only after text relay reliability.
8. Start the native iPhone client against the narrow versioned relay contract.

Potential conflict: the Task Thread/voice prototype has both committed changes
and substantial uncommitted Desktop work. Do not cherry-pick or overwrite it
blindly. Inventory its exact base and split backend durability from UI/voice
experiments before integration.

## Exact next actions

1. Start WS-05 as a provider-neutral vertical slice: users, organization
   memberships, roles/store grants, `worker` and `phone` device classes,
   short-lived one-use enrollment, device-bound proof, scoped rotating access,
   revocation, and concurrent-redemption/replay tests.
2. Add a relay audience/scope to the WS-05 claim contract without building the
   relay or selecting an identity vendor in that workstream.
3. Define the text-only relay resource model and versioned event envelope using
   the M01 gateway inventory and gap matrix.
4. Add server-authoritative ownership to any adopted Task Thread rows and
   enforce it on list/get/send/replay/approval operations.
5. Correct connector ACK semantics so durable receipt precedes ACK.
6. Prove one phone-like client can send, reconnect by cursor, reconcile the
   transcript, interrupt one turn, and see a worker-unavailable state.
7. Implement WS-09 and APNs after that text loop is reliable; defer realtime
   voice and the native iPhone UI until the relay contract is stable.
