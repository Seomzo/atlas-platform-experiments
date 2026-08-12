# M01 Gateway Contract Inventory

**Audit date:** 2026-08-12

**Baseline:** `4934f94eae2d02e039a8bb430f0cfb27075cc40e` (`origin/main`)

**Prototype reference:** `e91602ae4b1a` on `feat/native-voice-task-demo`

**Scope:** contract discovery only; no mobile implementation or production certification

## Decision

No current gateway is safe to expose directly to an iPhone.

The strongest mobile foundation is a **new authenticated mobile relay/control-plane contract** that combines four existing ideas without publishing any current local endpoint:

1. durable conversations from `SessionDB` and `session.resume`;
2. the explicit run/session event vocabulary from the API-server SSE surfaces;
3. cursor replay, idempotent commands, stable event IDs, and exact approval IDs from the committed Task Thread prototype;
4. the Team Gateway's outbound-dial, reconnect, and buffered-delivery mechanics, after its receipt boundary is corrected.

The existing Desktop WebSocket is a trusted workstation control channel, not a public client protocol. The API server has a more legible HTTP/SSE shape, but one global bearer can authorize server-host tool execution. The Team Gateway relay is experimental and its required connector is outside this repository. The Task Thread contract is not on the audited baseline and lacks product identity/ownership fields.

## Evidence labels

| Label | Meaning |
| --- | --- |
| **Baseline** | File and behavior present at `4934f94`; eligible for reuse analysis, not automatically production-ready. |
| **Committed prototype** | File and behavior committed at `e91602ae`; absent from baseline. Paths are written as `prototype:<path>`. |
| **Dirty local-only prototype** | Uncommitted or untracked material observed beside `e91602ae`; evidence of exploration only, not a shippable contract. |

The prototype worktree was dirty during this audit. Its committed backend and its local UI experiments are therefore separated throughout this document.

## Contract surface summary

| Surface | Transport and entrypoint | Authentication | Durable truth | Mobile assessment |
| --- | --- | --- | --- | --- |
| Desktop gateway | JSON-RPC 2.0 over `/api/ws` | Loopback session token, or gated single-use WebSocket ticket; host/origin guards | Conversation transcript in `state.db`; live events are transient | **Do not expose.** Reuse selected session semantics only. |
| API session service | Bearer-authenticated REST plus named SSE at `/api/sessions/.../chat/stream` | One global `API_SERVER_KEY`; explicit CORS allowlist | Session transcript in `state.db`; per-turn live stream is transient | **Shape is reusable.** Auth, authorization, replay, and approval are insufficient. |
| API runs service | REST submission/status/control plus SSE at `/v1/runs/{run_id}/events` | Same global bearer | Run task/status/event queues are process memory; terminal status retained about one hour | **Useful lifecycle vocabulary.** Not a durable mobile run service. |
| OpenAI-compatible API | `/v1/chat/completions`, `/v1/responses` | Same global bearer | Session transcript and Responses store; only selected non-streaming requests have in-memory idempotency | **Compatibility surface, not mobile authority.** |
| Team Gateway relay | Gateway dials connector `/relay` WebSocket; newline-delimited frames | Per-gateway expiring HMAC bearer; connector owns user binding and platform credentials | Connector-side buffering is specified; connector implementation is external | **Promising transport pattern, experimental.** Receipt boundary must be fixed. |
| Task Threads | `threads.*` and `approvals.*` JSON-RPC methods | Inherits Desktop gateway auth; no thread owner authorization | Kanban `tasks`/`task_events` plus adapter tables in SQLite | **Best contract model, prototype only.** Add tenancy, device/user identity, retention, and server-side policy. |
| Desktop voice | Browser media + REST STT/TTS around normal Desktop prompt flow | Inherits Desktop REST/WS connection | Transcript persists as ordinary chat; audio/live state does not | **UX reference only.** It is half-duplex and workstation-coupled. |

## 1. Desktop JSON-RPC WebSocket

### Wire and lifecycle

Primary sources are `apps/shared/src/json-rpc-gateway.ts`, `tui_gateway/ws.py`, `tui_gateway/server.py`, `hermes_cli/web_server.py`, and `apps/desktop/src/hermes.ts`.

Client requests have the conventional envelope:

```json
{"jsonrpc":"2.0","id":17,"method":"prompt.submit","params":{"session_id":"runtime-id","text":"..."}}
```

Server events are JSON-RPC notifications:

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta","session_id":"runtime-id","payload":{"text":"..."}}}
```

`JsonRpcGatewayClient` waits up to 15 seconds for a socket open and uses a 120-second default RPC timeout. Desktop overrides `prompt.submit` to 1,800 seconds because completion is communicated by events, not the RPC response. An `AbortSignal` removes only the client-side pending resolver; it does not send a protocol cancellation. WebSocket close rejects outstanding RPC promises, but events emitted while disconnected have no cursor, replay, acknowledgement, or recovery handshake. Desktop batches visible deltas on roughly a 33 ms cadence in `apps/desktop/src/app/session/hooks/use-message-stream/utils.ts`; that is a renderer optimization, not transport backpressure.

There is no protocol-version/capabilities negotiation, maximum-frame contract, event ID, turn ID, command idempotency key, delivery acknowledgement, or resumable event cursor. Retrying a timed-out or disconnected `prompt.submit` can duplicate a turn because the server may have accepted the first request before its response was lost.

### Method inventory

| Group | Representative methods and payloads | Persistence / effect | Mobile suitability |
| --- | --- | --- | --- |
| Session identity | `session.create`, `session.list`, `session.resume`, `session.history`, `session.status`, `session.active_list`, `session.title`, `session.branch`, `session.save`, `session.close`, `session.release`, `session.delete` | Creates or reconstructs an in-process runtime around a durable `SessionDB` row and transcript. Stored and runtime IDs are distinct. | Reuse the stored/runtime distinction and resume behavior. Add an owner-scoped opaque conversation ID. |
| Turn control | `prompt.submit {session_id,text,truncate_before_user_ordinal?}`, `session.steer {session_id,text}`, `session.interrupt`, `prompt.background` | One active turn per runtime. Submit acknowledges `status: streaming`; steer feeds a live run; interrupt is cooperative and denies pending approvals. | Add idempotency, exact turn IDs, durable command outcomes, and replay before mobile use. |
| Human input | `clarify.respond {request_id,answer}`, `approval.respond {session_id,choice,all?}`, `sudo.respond`, `secret.respond`, `terminal.read.respond` | Process-memory waiters. Clarify/sudo/secret carry transient request IDs; baseline approval resolves the oldest per-session queue entry or all entries. | Clarify can inform a future contract. Never relay sudo passwords or secrets to a phone. Baseline approval is too ambiguous. |
| Tools and workspace | attachments, `shell.exec`, `cli.exec`, process/browser commands, config/model/tool/skill/plugin/cron/rollback methods | Broad workstation authority, including host file and process operations. | Must remain behind server-side policy. A mobile principal must never inherit this method registry wholesale. |
| Voice | `voice.toggle`, `voice.record`, `voice.tts` | Uses the **server machine's** microphone/audio stack through `hermes_cli/voice.py`. Runtime flags and events are process-local. | Not a phone-media contract. Do not reuse directly. |

The registry is deliberately broad; the complete method declaration list lives in `tui_gateway/server.py`. A mobile API should use an allowlisted, versioned domain contract rather than mirroring that registry.

### Event inventory

| Event family | Representative payload | Durability / correlation | Mobile suitability |
| --- | --- | --- | --- |
| Connection/session | `gateway.ready`, `session.info` | No server event ID; `session.info` is a replace-like runtime snapshot. | Snapshot concept is reusable; add revision and principal scope. |
| Assistant stream | `message.start`, `message.delta {text}`, `message.complete {text,...}` | Correlated only by `session_id`; no explicit turn/message identity or replay. | Vocabulary is familiar, but insufficient for reconnecting mobile clients. |
| Reasoning/status | `thinking.delta`, `reasoning.delta`, `reasoning.available`, `status.update` | Transient. | Optional projection only; not authoritative state. |
| Tools/subagents | `tool.start`, `tool.progress`, `tool.complete`, `tool.generating`, `subagent.*` | Some tool payloads carry a tool ID, but there is no global event sequence or delivery ACK. | Expose sanitized summaries, never raw host arguments/results by default. |
| Blocking input | `clarify.request`, `approval.request`, `sudo.request`, `secret.request` | Clarify/sudo/secret get transient request IDs. Baseline approvals have no stable client-visible approval ID and are FIFO within a session. Default wait is about 300 seconds. | Only a new exact-ID approval contract is acceptable. Sudo/secret are prohibited. |
| Background/voice | `background.complete`, `voice.status`, `voice.transcript`, `error` | Process-local and lossy on disconnect/restart. | Status hints only, not mobile truth. |

`prompt.background` starts a daemon thread with an ephemeral `AIAgent` and later emits `background.complete`. Its task and completion routing are process memory, so it is not a durable background-job primitive.

### Authentication and authorization boundary

`hermes_cli/dashboard_auth/ws_tickets.py` defines 30-second, single-use browser tickets. `hermes_cli/web_server.py` also supports the legacy dashboard session token and a multi-use internal credential for server-spawned clients. In gated mode, the ticket is minted from an authenticated browser session and contains user/provider metadata, but the current WebSocket auth check consumes it for a boolean decision and discards that identity before JSON-RPC dispatch. The dispatch layer therefore has no durable principal, role, organization membership, device binding, per-method capability, or per-resource owner check.

This is adequate only as a trusted Desktop/dashboard boundary. Authentication at upgrade time must not be mistaken for authorization to every JSON-RPC method.

### Desktop code path

```text
Composer submit
  -> Desktop request helper / JsonRpcGatewayClient
  -> JSON-RPC prompt.submit on /api/ws
  -> tui_gateway.ws.handle_ws
  -> tui_gateway.server.dispatch
  -> prompt.submit creates/claims the runtime turn
  -> AIAgent.run_conversation in a worker thread
  -> SessionDB transcript persistence
  -> event notifications (message/tool/approval/...)
  -> Desktop session-keyed reducer and renderer
```

Tests that exercise important seams include `tests/tui_gateway/test_protocol.py`, `tests/tui_gateway/test_inline_rpc_gil_starvation.py`, `tests/hermes_cli/test_dashboard_auth_ws_auth.py`, `apps/desktop/src/app/gateway/hooks/use-gateway-boot.test.tsx`, `apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx`, and `apps/desktop/src/components/assistant-ui/tool/approval.test.tsx`.

## 2. API server: session SSE, OpenAI compatibility, and runs

The implementation is concentrated in `gateway/platforms/api_server.py`. The server refuses normal startup without `API_SERVER_KEY`, rejects placeholder/short keys under 16 characters, compares the bearer in constant time, and supports an explicit browser-origin allowlist. This is stronger than an unauthenticated local service, but it is still one bearer with no user, device, tenant, role, or resource ownership model. The server warns rather than refuses when a network-accessible bind can execute unsandboxed local terminal/file tools.

### Session resource and stream

| Endpoint | Contract | State and failure semantics | Mobile assessment |
| --- | --- | --- | --- |
| `GET/POST /api/sessions` | List or create client-visible sessions. | Backed by `SessionDB`; returns client-safe fields. | Useful resource shape after owner scoping. |
| `GET/PATCH/DELETE /api/sessions/{session_id}` | Read metadata; rename/end; privacy-aware delete. | Exact-session active mutation guards; Cortex boundary work can fail closed. | Reuse semantics, but require per-conversation authorization and tombstones/revisions. |
| `GET .../messages` | Durable transcript read. | Reads `SessionDB`; no incremental transcript cursor. | Useful baseline for resync. |
| `POST .../fork` | Durable lineage branch. | Prepares Cortex/SessionDB lineage with conflict guards. | Potential future feature, not MVP mobile relay. |
| `POST .../chat` | One synchronous agent turn. | Durable transcript; request remains coupled to run duration. | Poor mobile network behavior. |
| `POST .../chat/stream` | Named SSE stream. | Disconnect cancels its wrapper task. No event replay or reconnect cursor. No explicit approval response endpoint for this stream. | Best envelope to study, not safe to ship as-is. |

The session stream emits named SSE events including `run.started`, `message.started`, `assistant.delta`, tool progress/start/completion/failure, `assistant.completed`, `run.completed`, `error`, and `done`. Each event receives `session_id`, `run_id`, per-turn `seq`, and `ts`. This is materially clearer than Desktop's unsequenced stream, but `seq` starts within that one HTTP response and is not a durable replay cursor.

### `/v1/runs`

| Endpoint | Payload / result | Durability and mobile risk |
| --- | --- | --- |
| `POST /v1/runs` | Accepts `input`, optional instructions/history/session/model; returns `202 {run_id,status}`. | Active agent, task, approval routing, stream queue, and status map are process memory. Submission has no durable idempotency key. |
| `GET /v1/runs/{run_id}` | Pollable `hermes.run` status. | Terminal statuses are swept after about 3,600 seconds. |
| `GET /v1/runs/{run_id}/events` | SSE objects containing an `event` field: `message.delta`, tool lifecycle, reasoning, approval, completion/failure/cancellation. | One queue is consumed by readers, with no persisted log, fan-out, Last-Event-ID, or replay. Unconsumed streams are swept after about 300 seconds. |
| `POST .../approval` | `{choice: once|session|always|deny, all?}`; run ID maps to an isolated approval session key. | Safer isolation than shared conversation scopes, but resolution still targets the approval queue rather than a stable approval ID. |
| `POST .../stop` | Interrupts the agent and cancels the wrapper task with a bounded wait. | Cooperative; executor work may still unwind after the HTTP response. |

The API server also exposes OpenAI-compatible chat completions and Responses APIs. `X-Hermes-Session-Id` controls conversation continuity and `X-Hermes-Session-Key` scopes long-term memory. Because guessed session identifiers can reveal history, continuity is rejected when no API key is configured. `Idempotency-Key` exists only for selected **non-streaming** chat-completion and Responses calls, backed by an in-memory TTL/LRU cache; it is not a durable command ledger.

Tests include `tests/gateway/test_api_server.py`, `tests/gateway/test_api_server_runs.py`, `tests/gateway/test_api_server_jobs.py`, and `tests/gateway/test_api_server_bind_guard.py`.

## 3. Experimental Team Gateway relay

The formal baseline document is `docs/relay-connector-contract.md`; implementation sources are `gateway/relay/auth.py`, `gateway/relay/descriptor.py`, `gateway/relay/ws_transport.py`, `gateway/relay/adapter.py`, `gateway/relay/transport.py`, `gateway/platforms/base.py`, and `gateway/session.py`.

The Atlas/Hermes gateway dials **out** to the external connector's `/relay` WebSocket. The connector returns a `CapabilityDescriptor` and sends normalized `MessageEvent` frames back down the same socket. Outbound frames use request IDs and matching `outbound_result` frames. The contract also specifies interrupt frames, passthrough forwards, reconnect, going-idle/buffered-only transition, replayed `bufferId` deliveries, inbound acknowledgements, per-user author binding, and connector-held platform credentials.

The WebSocket upgrade bearer is an expiring HMAC-SHA256 token derived from a per-gateway secret. The connector is expected to derive tenant/instance identity from its own secret store, not from `hello`. A post-handshake close code `4401` is treated as terminal credential revocation. This outward-dial shape is compatible with a worker behind NAT and is the best existing transport precedent for a future mobile relay.

It is not a ready Atlas mobile relay:

- the connector implementation and its durable buffer/owner-binding store are external to this repository;
- the document marks the contract experimental until multiple real platform classes validate it;
- the contract is for messaging-platform normalization, not enrolled iOS devices or product users;
- the current buffered receipt boundary is premature. `WebSocketRelayTransport` awaits `RelayAdapter._on_inbound`, but that calls `BasePlatformAdapter.handle_message`, which intentionally returns after creating a background task. The transport then sends `inbound_ack` before the message is durably accepted into `SessionDB`. A crash in that gap can lose the buffered delivery despite the contract's “durable receipt” wording.

Tests cover the protocol in `tests/gateway/relay/test_ws_transport.py`, `tests/gateway/relay/test_relay_adapter.py`, `tests/gateway/relay/test_relay_going_idle.py`, `tests/gateway/relay/test_relay_roundtrip.py`, `tests/gateway/relay/test_contract_doc_conformance.py`, and `tests/gateway/test_relay_upstream_authz.py`.

## 4. Task Thread prototype

### Committed prototype at `e91602ae`

The following files are committed at the prototype revision and absent from baseline:

- `prototype:altas/task_threads/contracts.py`
- `prototype:altas/task_threads/store.py`
- `prototype:apps/shared/src/task-thread-contract.ts`
- Task Thread RPC additions in `prototype:tui_gateway/server.py`
- `prototype:tests/altas/test_task_threads.py`
- `prototype:tests/tui_gateway/test_task_threads.py`

The committed JSON-RPC surface is:

| Method | Key request fields | Result / durable effect |
| --- | --- | --- |
| `threads.create` | `idempotency_key`, title, goal, `worker_profile_id`, optional project/workspace/voice workspace | Idempotent durable task/thread creation and runtime binding. |
| `threads.list`, `threads.get` | archived filter or `thread_id` | Materialized task state plus global cursor. |
| `threads.send`, `threads.steer` | exact `thread_id`, instruction, `idempotency_key` | Creates an idempotent turn and starts/steers its worker runtime. |
| `threads.interrupt` | `thread_id`, `idempotency_key` | Interrupts the exact thread and records the transition. |
| `threads.focus` | `voice_workspace_id`, `thread_id` | Persists which thread a voice workspace targets. |
| `threads.events` | `after_sequence`, optional thread and limit | Ordered cursor replay from durable `task_events`. |
| `approvals.list` | optional `thread_id` | Lists durable approval records. |
| `approvals.respond` | exact `approval_id`, choice | Resolves that exact request rather than the oldest queue entry. |

Events have `event_id`, global monotonic `sequence`, `schema_version`, timestamp, thread/turn/voice-workspace/worker IDs, correlation and causation IDs, name, and payload. Thread creation and turns use idempotency keys. Approval requests get stable IDs. Restart reconciliation clears stale runtime bindings, marks orphaned active work interrupted, and denies approvals whose waiter disappeared. Those are the strongest reusable correctness properties found in this audit.

The prototype is still unsafe as a multi-user mobile authority. It has no tenant, organization, user, enrolled device, owner, role, or audience fields; list/event/approval queries are effectively global within the database; worker profile and workspace choices originate at the client contract; and every delta is persisted without a stated pruning/compaction policy. Its JSON-RPC transport still inherits the broad Desktop authentication and authorization boundary.

### Dirty local-only prototype

The observed worktree also contained untracked Desktop Task Thread UI/reducer files under `prototype:apps/desktop/src/app/task-threads/` and `prototype:apps/desktop/src/store/task-threads.ts`, plus local real-time voice experiments in `prototype:apps/desktop/src/lib/realtime-voice-director.ts` and `prototype:apps/desktop/src/lib/task-thread-voice.ts`. Other integration files were modified locally.

These files are not part of `e91602ae`. They do not establish a committed mobile transport, broker, WebRTC path, APNs background path, or production real-time voice proof. They must not be cited as baseline functionality.

## 5. Voice and remote-profile contracts

### Desktop voice

Desktop voice is an orchestration around existing local surfaces:

```text
Chromium getUserMedia + MediaRecorder + client VAD
  -> POST /api/audio/transcribe (base64 data URL; 25 MiB decoded cap)
  -> ordinary prompt.submit over /api/ws
  -> message.delta / message.complete
  -> POST /api/audio/speak
  -> browser Audio playback
```

Sources are `apps/desktop/src/app/chat/composer/hooks/use-mic-recorder.ts`, `apps/desktop/src/app/chat/composer/hooks/use-voice-conversation.ts`, `apps/desktop/src/lib/voice-playback.ts`, `apps/desktop/src/hermes.ts`, and `hermes_cli/web_server.py`. It is half-duplex: listen, transcribe, run, synthesize, play, then listen again. Stopping local playback is not itself a durable worker interrupt. The separate `voice.record` JSON-RPC path uses server-machine hardware. Neither is the right abstraction for iOS media/background execution.

### Remote Desktop profiles

`apps/desktop/electron/connection-config.ts`, remote resolution in `apps/desktop/electron/main.ts`, `apps/desktop/src/store/profile.ts`, and `apps/desktop/src/store/gateway.ts` support a global remote backend or per-profile remote override. Static-token mode uses a saved dashboard token; OAuth mode uses session cookies for REST and mints a fresh single-use WebSocket ticket. Secondary profiles can hold concurrent WebSocket connections and reconnect with backoff.

This proves the Desktop can act as a remote client, but it does not create a mobile-safe product boundary. Profiles are runtime/config scopes, not verified Atlas users or roles; an authenticated socket still reaches the full method registry; connection settings and token/cookie handling are Electron-owned; and there is no device enrollment, refresh-token rotation contract for native clients, resource owner authorization, push notification contract, or mobile session revocation model.

## 6. Required mobile relay contract

The direct-mobile decision remains **NO-GO** until all of these exist on one server-owned boundary:

1. **Identity and enrollment:** tenant, user, organization membership, role, enrolled device, scoped access/refresh credentials, rotation, remote revoke, and audit principal on every command.
2. **Narrow authorization:** explicit mobile capabilities and per-thread/resource ownership checks; no passthrough to arbitrary Desktop JSON-RPC, sudo, secret capture, shell, config, or provider-key methods.
3. **Durable commands:** client-generated command ID/idempotency key; exact thread and turn IDs; stored accepted/rejected outcome; safe retry after timeout.
4. **Durable events:** stable event ID, monotonic cursor scoped to an authorized thread/user, bounded retention, `after` replay, snapshot+cursor resync, fan-out to multiple clients, and ACK semantics tied to durable receipt.
5. **Exact approvals:** stable approval ID, sanitized display payload, explicit action scope, expiry, policy version, exact-once resolution, and restart fail-closed behavior. No passwords or secrets through mobile approval.
6. **Background behavior:** server-side run continues independently of a phone socket; APNs carries only a wake/summary hint; foreground reconnect fetches authoritative state and replays from a cursor.
7. **Voice boundary:** phone captures/plays media locally; transport carries scoped audio/text turn data with consent, size/codec limits, interruption/barge-in semantics, and retention policy. Do not invoke workstation microphone RPCs.
8. **Worker relay:** Atlas worker dials out, reconnects, and receives authorized commands. Buffered delivery is acknowledged only after the command/event is in durable worker or control-plane storage.

### Reuse map

| Keep | Adapt | Reject for mobile |
| --- | --- | --- |
| `SessionDB` transcript/resume and lineage concepts | API session SSE names plus durable Task Thread event envelope | Direct `/api/ws` access |
| Task Thread `event_id`, cursor, correlation/causation, idempotency, exact approval ID | Team Gateway outward dial/reconnect/buffering after receipt fix | One global API bearer as end-user authority |
| API run lifecycle and cooperative stop vocabulary | Remote-profile OAuth experience after product identity/device work | FIFO approval, sudo/secret relay, server-microphone voice RPCs |
| Restart fail-close reconciliation | Sanitized tool summaries and bounded retention | Process-memory background tasks/queues as durable state |

The first mobile work after M01 should define and threat-model this relay contract; it should not wrap an existing endpoint in an iOS client.
