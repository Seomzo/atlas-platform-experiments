# M01 Mobile Readiness Gap Matrix

Status: discovery audit, 2026-08-12. This is not an iPhone implementation or a
production-readiness certification.

## Verdict

The intended path remains sound:

```text
Atlas iPhone app
        |
        v
Authenticated Atlas Control Plane relay
        |
        v
Outbound-connected enrolled Atlas worker
        |
        v
Existing Atlas gateway and engine
```

There is no end-to-end `Ready` capability in this matrix. The repository has a
useful local Control Plane prototype and a broad local conversation gateway,
but it does not yet have the production identity contract, authenticated relay,
or hosted operating environment that a phone surface would require. A row
marked `Reusable with adaptation` identifies a real seam worth preserving; it
does not mean that seam is safe to expose to the internet or ship on iOS.

The six classifications mean:

- `Ready`: implemented, production-shaped, and usable by the proposed mobile
  topology. No row met this bar.
- `Reusable with adaptation`: working code or a durable contract exists, but
  trust, transport, or client adaptation is required.
- `Prototype only`: exercised locally, but tied to fixture data, local trust,
  or a non-production implementation.
- `Specified but missing`: the repository describes the behavior but contains
  no usable implementation on the audited baseline.
- `Blocked by another decision`: implementation should wait for a named product,
  platform, or policy decision.
- `Unsafe for mobile`: the current mechanism must not be extended to a phone or
  public network.

## WS-05 disposition

WS-05 is **specified but unstarted on the audited baseline**. Its provider-neutral
account and device-enrollment target exists only as a prompt in
[`AGENT_WORKSTREAMS.md`](../AGENT_WORKSTREAMS.md). There is no
`docs/altas/workstreams/WS-05-HANDOFF.md`, no merged account/user/organization/
role/enrollment implementation, and no current-ref partial WS-05 implementation
found during M01. The older worker-identity work is device-bearer prototype
plumbing, not the browser-authenticated membership and one-time enrollment flow
that WS-05 specifies. No newer design was found that supersedes WS-05.

This is the primary mobile blocker described by
[`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md). WS-09 is similarly a prompt,
not a merged production approval contract.

## Identity

| Required item | Classification | Evidence | Gap | Next action |
| --- | --- | --- | --- | --- |
| User authentication | Specified but missing | WS-05 specifies system-browser authentication through an eventual OIDC provider in [`AGENT_WORKSTREAMS.md`](../AGENT_WORKSTREAMS.md). | The Control Plane authenticates worker devices and a shared development admin bearer, not people. | Implement the WS-05 provider-neutral authorization-code/PKCE contract and deterministic development adapter before any phone sign-in UI. |
| Organization membership | Specified but missing | WS-05 names organization membership; the prototype persists tenants, stores, subscriptions, devices, and agents in [`repository.py`](../../../altas/control_plane/repository.py). | No user-to-organization membership model or API exists. | Add server-derived membership with tenant isolation and round-trip tests for wrong-tenant and revoked membership. |
| Role | Specified but missing | WS-05 and WS-06 require roles; current policy in [`policy.py`](../../../altas/control_plane/policy.py) evaluates device, store, subscription, entitlement, job, and capability. | No human role or role-to-store authorization is evaluated. | Define minimal operator/member roles and bind every mobile scope to authenticated membership plus store access. |
| Phone enrollment | Specified but missing | [`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md) requires an enrolled phone paired with an enrolled worker. | There is no enrollment transaction, redemption endpoint, QR/universal-link handoff, or phone device class. | Extend the reviewed WS-05 contract with short-lived, one-time, replay-resistant phone enrollment. |
| Device-bound key material | Specified but missing | WS-05 asks for device-bound material; [`SECURITY.md`](../SECURITY.md) says the current hashed bearer should become a device-bound key. | The prototype uses a reusable bearer and has no proof-of-possession or iOS key generation contract. | Register a Secure Enclave/Keychain-backed public key where available and bind token exchange to signed challenges. |
| Token rotation | Unsafe for mobile | Short-lived signed worker leases exist in [`security.py`](../../../altas/control_plane/security.py), but the underlying prototype device bearer remains reusable. | There is no mobile refresh-token family, rotation, reuse detection, or proof binding. | Design short-lived access tokens plus rotated, device-bound refresh credentials; reject bearer reuse before exposing a relay. |
| Revocation | Prototype only | Protected worker endpoints recheck live device/subscription state in [`app.py`](../../../altas/control_plane/app.py), and the Control Center exposes device disable. | Revocation covers prototype worker devices, not users, memberships, phones, refresh-token families, or active relay sessions. | Generalize revocation to every identity layer and force-close affected relay streams with an auditable reason. |
| Worker pairing | Reusable with adaptation | Device/agent/store assignments and heartbeat state exist in [`repository.py`](../../../altas/control_plane/repository.py); the mobile plan specifies phone-to-worker pairing. | Assignment is seeded/admin prototype state, not an authorized user-selected phone pairing. | Reuse immutable device/agent IDs but add a scoped pairing resource derived from membership and enrollment. |
| Store access | Prototype only | The Control Plane derives tenant context from the worker credential and verifies store/agent/subscription relationships in [`app.py`](../../../altas/control_plane/app.py). | There is no person-to-store grant or safe multi-store chooser. | Add explicit membership-to-store grants and test cross-store denial at sign-in, pairing, relay, and every approval. |
| Biometric reauthentication | Blocked by another decision | No iOS client or reauthentication policy exists; mobile scope and approval sensitivity remain open in [`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md). | It is undecided which actions require Face ID/Touch ID and how recent authentication is proven to the server. | After WS-05/WS-09, define risk tiers and a signed reauthentication challenge before designing LocalAuthentication UX. |
| Keychain storage | Specified but missing | [`SECURITY.md`](../SECURITY.md) requires an OS-specific secure-storage review; the repository has a Python credential-vault abstraction in [`altas/credentials`](../../../altas/credentials). | No iOS Keychain access group, data-protection class, migration, backup, or deletion behavior exists. | Specify non-exportable key/refresh-token storage and hermetic Keychain tests in the eventual native client. |

## Relay

| Required item | Classification | Evidence | Gap | Next action |
| --- | --- | --- | --- | --- |
| Outbound worker connection | Prototype only | [`worker.py`](../../../altas/managed/worker.py) sends heartbeats, obtains leases, and polls for jobs over outbound HTTP. | It is periodic request/response polling, not a persistent authenticated reverse conversation channel. | Add a worker-originated relay connection with lease renewal, explicit connection identity, and fail-closed policy checks. |
| Mobile-to-worker routing | Specified but missing | The app -> Control Plane -> worker route is described in [`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md). | No relay endpoint, route registry, phone/worker authorization check, or worker adapter exists. | Implement one text-only routing vertical slice after WS-05, with one phone, one worker, and one store. |
| Session addressing | Reusable with adaptation | Gateway events carry `session_id`; session create/list/resume/history methods exist in [`server.py`](../../../tui_gateway/server.py). | IDs are accepted inside the local gateway trust boundary and are not relay capabilities. | Preserve opaque session IDs but authorize each lookup through pairing, membership, and store scope. |
| Stream multiplexing | Reusable with adaptation | [`json-rpc-gateway.ts`](../../../apps/shared/src/json-rpc-gateway.ts) carries session-scoped message, tool, approval, and background events over one WebSocket. | There is no Control Plane multiplexer, per-stream quota, or isolation between phones/workers. | Define a versioned envelope with connection, session, event type, and authorization context; add cross-session isolation tests. |
| Ordered event delivery | Prototype only | WebSocket delivery preserves live connection order; the committed Task Thread prototype at `e91602ae` adds sequenced task events but is absent from the audited baseline. | Baseline events have no durable sequence across disconnects, and the committed prototype is not a merged relay contract. | Assign monotonic per-session sequence numbers at the authoritative worker/relay boundary and persist bounded event history. |
| Cursor-based replay | Prototype only | The committed Task Thread prototype at `e91602ae` implements local cursor/replay; baseline session history can be reloaded through [`server.py`](../../../tui_gateway/server.py). | No baseline or Control Plane cursor, retention window, authorization, or gap response exists. | Standardize an opaque cursor and `after` replay contract with explicit `cursor_expired` recovery. |
| Idempotency | Reusable with adaptation | The job path has idempotent Cortex dispatch and one-time claim-token patterns in [`app.py`](../../../altas/control_plane/app.py). | Conversation submission, approval response, and relay events have no shared idempotency-key contract. | Require client-generated idempotency keys for mutations and persist results within a bounded replay window. |
| Reconnect | Reusable with adaptation | [`json-rpc-gateway.ts`](../../../apps/shared/src/json-rpc-gateway.ts) exposes connection states/timeouts; [`websocket-url.ts`](../../../apps/shared/src/websocket-url.ts) can mint fresh OAuth WebSocket URLs. | Reconnect does not resume a Control Plane relay cursor or prove mobile session continuity. | Combine fresh authentication, pairing recheck, last acknowledged cursor, and explicit resync state. |
| Backpressure | Specified but missing | The mobile plan mentions session queuing; the current gateway has request timeouts but no relay flow-control contract. | A slow phone or worker can cause unbounded buffering or silent loss. | Define bounded queues, byte/event limits, acknowledgement windows, and a stable `client_too_slow` outcome. |
| Offline queueing | Specified but missing | [`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md) expects queuing to behave like existing adapters. | Desktop prompt queues are process-local; there is no authorized durable phone-to-worker queue. | Queue only explicitly allowed message types with idempotency, TTL, user-visible state, and cancel-before-dispatch. |
| Expiration | Reusable with adaptation | Worker leases expire in [`security.py`](../../../altas/control_plane/security.py), and gateway requests have bounded timeouts. | Relay sessions, cursors, queued prompts, pairings, and approval requests have no unified expiry semantics. | Put server timestamps and `expires_at` on every ephemeral resource and define terminal expired outcomes. |
| Worker unavailable state | Reusable with adaptation | Heartbeat recency/device status feed the Control Center fleet state; screenshot `02-control-center-fleet.png` shows the local result. | No mobile-facing availability state distinguishes offline, revoked, busy, updating, sleeping, or unreachable. | Derive a privacy-safe state machine from heartbeat, active relay, lease, and maintenance state; surface retry guidance. |

## Conversation

| Required item | Classification | Evidence | Gap | Next action |
| --- | --- | --- | --- | --- |
| Session list | Reusable with adaptation | `session.list` and rich persisted session metadata exist in [`server.py`](../../../tui_gateway/server.py) and [`hermes_state.py`](../../../hermes_state.py). | The contract is local-gateway shaped and lacks relay pagination, membership filtering, and stable mobile DTOs. | Publish a minimal versioned session summary and authorize/filter it server-side. |
| Transcript | Reusable with adaptation | `session.history`, persisted conversation replay, and Desktop transcript rendering are implemented in [`server.py`](../../../tui_gateway/server.py) and [`apps/desktop/src/components/chat`](../../../apps/desktop/src/components/chat). | Tool/media payloads may be local-path oriented; relay redaction and pagination are undefined. | Define typed text/tool/status parts, bounded pages, redaction, attachment references, and retention behavior. |
| Create | Reusable with adaptation | `session.create` exists in [`server.py`](../../../tui_gateway/server.py). | It accepts local profile/runtime choices that a phone must not control directly. | Add a relay wrapper that selects only the paired worker/profile and allowlisted options. |
| Resume | Reusable with adaptation | `session.resume` restores durable history and live child state in [`server.py`](../../../tui_gateway/server.py). | Remote authorization, cursor catch-up, and stale/archived outcomes are not defined. | Resume through a pairing-scoped capability and return transcript cursor plus worker availability. |
| Submit | Reusable with adaptation | `prompt.submit` drives real gateway execution and the M01 deterministic Desktop run exercised it. | No relay idempotency, offline semantics, or server-side mobile authorization exists. | Wrap submit in an idempotent, expiring command that binds phone, user, store, worker, and session. |
| Stream | Reusable with adaptation | Message/thinking/reasoning/tool delta events are typed in [`json-rpc-gateway.ts`](../../../apps/shared/src/json-rpc-gateway.ts); screenshot `07-desktop-tool-streaming.png` proves the renderer path with a deterministic local provider. | Live gateway events have no durable relay envelope, sequence, replay, or privacy filter. | Preserve event parts while adding ordered envelopes, safe payload limits, acknowledgements, and replay. |
| Steer | Reusable with adaptation | `session.steer` injects text into a live agent turn in [`server.py`](../../../tui_gateway/server.py). | A remote steer needs explicit actor/audit context and a race contract when a turn completes. | Require an expected turn ID, idempotency key, and auditable accepted/too-late outcome. |
| Interrupt | Reusable with adaptation | `session.interrupt` cooperatively stops a turn and denies pending local approvals in [`server.py`](../../../tui_gateway/server.py). | It is local-session scoped and does not prove caller authority or worker acknowledgement through a relay. | Bind interrupt to user/session/turn, make it idempotent, and emit requested/acknowledged/completed events. |
| Fork | Reusable with adaptation | `session.branch` copies conversation history in [`server.py`](../../../tui_gateway/server.py). | Mobile-visible lineage, permissions, titles, and concurrent fork races are undefined. | Expose fork only after session DTO/lineage and idempotent mutation rules are fixed. |
| Archive | Reusable with adaptation | Session archival/lineage behavior exists in [`hermes_state.py`](../../../hermes_state.py) and Desktop Archived Chats UI. | No mobile relay mutation contract, undo window, or cross-device list invalidation exists. | Add an idempotent archive mutation with lineage semantics, event confirmation, and authorized undo. |
| Tool rendering | Reusable with adaptation | The shared event vocabulary includes tool start/progress/complete, and Desktop renders structured tool cards in [`apps/desktop/src/components/chat`](../../../apps/desktop/src/components/chat). | Many tool details assume desktop width or local files; arbitrary output is not safe for push/relay. | Define a compact safe tool-summary DTO, expandable detail fetch, redaction, and unavailable-local-artifact states. |
| Background task completion | Reusable with adaptation | `background.complete` is a gateway event and Desktop has native completion notifications in [`native-notifications.ts`](../../../apps/desktop/src/store/native-notifications.ts). | Completion is not persisted/delivered through APNs or linked to a relay cursor. | Persist a privacy-safe completion event, deliver it in-band first, then add APNs as a wake-up hint. |

## Voice

| Required item | Classification | Evidence | Gap | Next action |
| --- | --- | --- | --- | --- |
| Push-to-talk | Reusable with adaptation | `voice.record` and the Desktop microphone/voice-conversation hooks implement push-to-talk/VAD behavior in [`server.py`](../../../tui_gateway/server.py) and [`apps/desktop/src/app/chat/composer`](../../../apps/desktop/src/app/chat/composer). | Capture currently assumes the Desktop/local gateway topology, not an iPhone-to-relay media path. | Start mobile with explicit press/hold or tap-to-record and a bounded recorded clip after text relay is stable. |
| Speech recognition location | Blocked by another decision | Desktop supports local or provider-backed STT; [`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md) leaves on-device versus engine-side STT open. | Privacy, latency, language, offline, cost, and model-quality requirements are undecided. | Decide pilot policy with measured clips; keep the relay contract able to accept either text or bounded audio without exposing provider keys. |
| Audio upload | Reusable with adaptation | Desktop records a `Blob` and calls the local `/api/audio/transcribe` path through [`hermes.ts`](../../../apps/desktop/src/hermes.ts). | The endpoint is not a mobile upload service and lacks phone identity, store scope, streaming limits, malware/container checks, and retention policy. | Define an authenticated, size/time-limited media object with content hash, expiry, and immediate deletion after transcription. |
| Partial transcript | Specified but missing | The current Desktop flow emits a transcript after capture/transcription; no partial transcript relay contract exists. | There are no revision IDs, stabilization markers, ordering, or UI semantics for interim text. | Defer for v1 push-to-talk or add typed `interim`/`final` transcript events with replace-by-segment semantics. |
| TTS | Reusable with adaptation | `voice.tts`, Desktop playback, and auto-speak are implemented in [`server.py`](../../../tui_gateway/server.py) and [`voice-playback.ts`](../../../apps/desktop/src/lib/voice-playback.ts). | Current playback returns local/provider audio through the Desktop gateway, with no mobile media authorization or cache policy. | Prefer device TTS for the first pilot or define short-lived authenticated audio objects with interruption controls. |
| Audio streaming | Specified but missing | Current Desktop playback consumes a completed data URL/audio response, not a Control Plane audio stream. | No codec negotiation, chunk ordering, buffering, cancellation, or relay bandwidth policy exists. | Keep v1 clip-based; design streaming only after text event replay/backpressure is proven. |
| Barge-in | Reusable with adaptation | Desktop voice-conversation code can stop playback and transition back to listening in [`use-voice-conversation.ts`](../../../apps/desktop/src/app/chat/composer/hooks/use-voice-conversation.ts). | The behavior is renderer-local and not synchronized with remote generation or relay audio state. | Couple local playback stop with an explicit turn interrupt/steer decision and visible acknowledgement. |
| Explicit worker interruption | Reusable with adaptation | `session.interrupt` already expresses cooperative turn cancellation in [`server.py`](../../../tui_gateway/server.py). | Mobile voice needs a deterministic distinction between stopping audio, stopping capture, and stopping the worker turn. | Give each action a separate accessible control and event; never infer worker interruption from local audio stop alone. |
| Background audio behavior | Blocked by another decision | No iOS lifecycle exists, and continuous versus push-to-talk remains open in [`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md). | Background recording/playback, lock-screen controls, calls, Bluetooth routing, and battery policy are undecided. | For the pilot, prohibit background recording and stop safely on interruption; revisit only with a documented user benefit. |
| Accessibility | Specified but missing | Desktop voice controls use labels/live regions, but no iOS VoiceOver, captions, haptics, or non-audio fallback has been built. | A voice-first mobile surface cannot rely on sound, waveform, color, or gestures alone. | Require editable transcript, captions, VoiceOver labels, Dynamic Type, haptic state cues, and full text parity before voice launch. |

## Approvals

| Required item | Classification | Evidence | Gap | Next action |
| --- | --- | --- | --- | --- |
| Bounded human-readable action | Prototype only | Desktop displays a redacted command and Run/Reject controls; screenshot `12-desktop-approval-request.png` proves the later harmless probe reached the local surface and was rejected. An earlier synthetic `git push` attempt ran in an empty isolated workspace, failed harmlessly, and did not trigger the gate. WS-09 specifies action and bounded target. | The current prompt is engine safety UI, not an authoritative tenant/store/job action object, and command coverage is incomplete. | Implement the WS-09 server object with action type, human summary, bounded target, actor, immutable digest, and alternate-dispatch coverage. |
| Expiry | Specified but missing | WS-09 requires approval expiry in [`AGENT_WORKSTREAMS.md`](../AGENT_WORKSTREAMS.md). | Local approval waits do not provide a Control Plane `expires_at` contract or terminal expired event. | Enforce server time, show a countdown/absolute time, and reject late responses atomically. |
| Single use | Prototype only | Local approval resolution pops pending requests and supports a one-time choice in [`tools/approval.py`](../../../tools/approval.py). | It is in-process queue behavior, not a durable one-time authorization token under race. | Persist a single state transition and test concurrent approve/deny/retry attempts. |
| Target binding | Specified but missing | WS-09 explicitly requires target binding. | Displayed command text is not a canonical target/action digest checked at execution. | Canonicalize the action, hash it, and require the worker to present the exact approved digest. |
| Job binding | Specified but missing | WS-09 requires job/workflow/attempt context; prototype job claims already bind device/job/capability in [`app.py`](../../../altas/control_plane/app.py). | Approval decisions are not joined to the authoritative job claim and attempt. | Bind approval to tenant, store, user, device, agent, job, workflow, capability, attempt, and lease. |
| Replay resistance | Specified but missing | WS-09 requires replay/race resistance. | A mobile response has no nonce, version, compare-and-swap state, or proof-bound actor. | Use one-time IDs plus atomic pending -> approved/denied/expired transitions and reject stale action versions. |
| Audit | Specified but missing | The Control Plane has safe policy/job audit records in [`repository.py`](../../../altas/control_plane/repository.py). | Local Desktop approvals are not a Control Plane audit lifecycle with requester, responder, action digest, and outcome. | Record requested/viewed/responded/expired/cancelled/executed events without raw payloads or credentials. |
| Denial and cancellation | Prototype only | Local `approval.respond` supports deny, and `session.interrupt` denies pending requests in [`server.py`](../../../tui_gateway/server.py). | Denial reason, cancellation authority, cross-device races, and terminal worker acknowledgement are not production contracts. | Define explicit deny/cancel reasons, actor rules, idempotent terminal outcomes, and fail-closed worker behavior. |

## Notifications

| Required item | Classification | Evidence | Gap | Next action |
| --- | --- | --- | --- | --- |
| APNs requirements | Specified but missing | [`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md) includes approval and job/report notifications. | No APNs environment, device token lifecycle, topic, entitlement, provider key, collapse ID, retry, or revocation contract exists. | After WS-05, model APNs tokens as revocable phone endpoints and use push only as a wake-up hint. |
| Report-ready notification | Reusable with adaptation | Desktop supports `background.complete` and completion notifications in [`native-notifications.ts`](../../../apps/desktop/src/store/native-notifications.ts). | No durable report event, mobile subscription preference, APNs delivery, or authenticated report fetch exists. | Persist a minimal completion event and send a generic push that deep-links to an authenticated fetch. |
| Approval-required notification | Reusable with adaptation | Desktop has approval native notifications and routes actions back to `approval.respond`. | Direct approval from a push would be unsafe until WS-09 identity, action binding, expiry, and reauthentication exist. | Push generic attention text; require app open plus fresh state fetch and risk-appropriate reauthentication before response. |
| Worker-offline notification | Specified but missing | Heartbeats and fleet status exist, but no notification rule or preference exists. | Offline thresholds, maintenance suppression, flapping, recipients, and recovery notifications are undefined. | Define a debounced availability incident and notify only authorized users with store-scoped preferences. |
| Privacy-safe payloads | Specified but missing | [`SECURITY.md`](../SECURITY.md) defines allowlisted audit metadata and forbids secrets/raw payloads. | There is no APNs-specific payload policy, lock-screen redaction level, or notification content preference. | Put no dealer/customer/report/command data in push; send an opaque event ID and generic localized copy. |
| Notification-to-session deep links | Reusable with adaptation | Desktop native notifications carry a `sessionId` and can focus/respond to the relevant local session in [`native-notifications.ts`](../../../apps/desktop/src/store/native-notifications.ts). | No universal-link/custom-scheme contract, pending-login continuation, or server authorization recheck exists. | Use an opaque event deep link, authenticate, fetch current state, then route to the authorized session or a safe unavailable screen. |

## Production operations

| Required item | Classification | Evidence | Gap | Next action |
| --- | --- | --- | --- | --- |
| Hosted Control Plane | Specified but missing | [`ARCHITECTURE.md`](../ARCHITECTURE.md) describes cloud control; the audited runtime bound the prototype to loopback. | There is no verified hosted Atlas service, public identity endpoint, relay ingress, or production tenant. | Produce a minimal deployment threat model and staging environment only after WS-05 and the relay contract exist. |
| TLS | Unsafe for mobile | The current Control Center/API run is local HTTP with a development bearer. | Exposing that endpoint or shared bearer would leak authority and provide no production transport identity. | Require TLS 1.2+, managed certificates, HSTS, secure WebSockets, pinning policy decision, and no query-string bearer. |
| Database | Prototype only | [`database.py`](../../../altas/control_plane/database.py) and the architecture use SQLite; PostgreSQL is the production target. | SQLite does not supply the intended multi-instance durability, operations, backup, or migration posture. | Move production-shaped identity/relay state to PostgreSQL with migrations, constraints, tenancy tests, and restore drills. |
| Queue | Prototype only | Job claim/recovery/idempotency logic works against the prototype repository. | There is no durable distributed relay/event queue, dead-letter operation, or multi-instance ordering guarantee. | Select a bounded queue after the relay semantics are fixed; preserve existing claim/idempotency invariants in integration tests. |
| Observability | Prototype only | Safe audit/usage records, structured denial codes, health, and local logs exist in [`app.py`](../../../altas/control_plane/app.py) and [`SECURITY.md`](../SECURITY.md). | No hosted metrics/traces, SLOs, paging, cross-service correlation, or privacy review exists. | Define correlation IDs, redacted metrics/log schemas, relay SLOs, dashboards, and alert ownership before a pilot. |
| Deployment | Specified but missing | Roadmap/architecture describe production targets; no current deployment manifest or verified release pipeline serves mobile. | Environments, secrets, migrations, canaries, rollback, regional topology, and ownership are undefined. | Create staging first with infrastructure-as-code, isolated secrets, migration gates, smoke tests, and rollback. |
| Backups | Specified but missing | [`SECURITY.md`](../SECURITY.md) lists backup design as a required production review. | No backup schedule, encryption, retention, restore test, or RPO/RTO exists. | Define data classes and RPO/RTO, then automate encrypted backups and recurring restore verification. |
| Incident response | Specified but missing | Incident-ready logging fields are specified in [`SECURITY.md`](../SECURITY.md). | There is no on-call owner, severity model, runbook, customer notification process, or revocation drill. | Write and exercise identity compromise, relay outage, data exposure, lost phone, and worker compromise runbooks. |
| App Store signing | Blocked by another decision | The mobile plan is unscheduled and leaves native/cross-platform choice and final app name open. | No app target, bundle ID, team/release account decision, entitlements, provisioning, TestFlight, or review package exists. | Decide pilot audience, platform stack, product name, and release owner before creating signing infrastructure. |
| Privacy disclosures | Blocked by another decision | Mobile transcript persistence, STT location, notification scope, and pilot audience remain undecided. | App Privacy answers and user disclosures cannot be accurate until actual data flows and vendors are fixed. | Complete a data-flow inventory and legal/privacy review against the implemented pilot, then author in-app and store disclosures. |
| Data retention | Specified but missing | [`SECURITY.md`](../SECURITY.md) requires retention/deletion/backup design; [`MOBILE_COMPANION.md`](../MOBILE_COMPANION.md) forbids extra plane transcript persistence. | No enforceable per-artifact retention, deletion propagation, legal hold, export, or backup-erasure policy exists. | Define retention by transcript/event/audio/audit/push-token class and implement deletion plus verification before real data. |

## Shortest credible path

1. **Land WS-05 first.** Implement human authentication, membership, role/store
   grants, one-time phone enrollment, device-bound credentials, rotation, and
   revocation. Do not build a phone login screen against the shared development
   bearer.
2. **Ship a text-only relay vertical slice.** One enrolled phone, one paired
   worker, one store, one session. Use a versioned ordered event envelope,
   idempotent submit, bounded replay, explicit expiry/backpressure, and an
   observable worker-unavailable state.
3. **Adapt the existing gateway rather than reimplementing the agent.** Wrap
   `session.list/create/resume/history`, `prompt.submit`, streaming events,
   steer, and interrupt behind Control Plane authorization. Keep the phone a
   thin surface with no dealership or provider credentials.
4. **Land WS-09 before consequential approvals.** The local Desktop Run/Reject
   UI is useful interaction evidence, but the server must own action, target,
   job, actor, expiry, single use, replay resistance, cancellation, and audit.
5. **Add notifications as hints, then voice.** First deliver in-band completion
   and approval events. Add privacy-safe APNs wake-ups. Add bounded push-to-talk
   only after text replay/reconnect and explicit interruption are proven.
6. **Prove staging operations before real users or data.** Hosted TLS, PostgreSQL,
   durable queues, observability, backup/restore, incident response, privacy,
   retention, and release signing are pilot gates, not post-launch cleanup.

## Source boundaries

The Control Center findings are from the audited baseline and deterministic
fixture runtime. Desktop screenshots `05` through `12` came from a separate
dirty voice/task prototype checkout running with isolated homes and a
deterministic loopback provider. They prove reusable interaction and gateway
seams only; they are not evidence that those changes are merged, clean, or
production safe. The local screenshots live under
`artifacts/mobile-readiness/current-surfaces/` and are intentionally ignored.
