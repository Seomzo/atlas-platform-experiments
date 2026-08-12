# M01 Local Discovery and Mobile Readiness Audit

## Audit record

| Field | Value |
|---|---|
| Audit date | 2026-08-12 |
| Repository | `Seomzo/atlas-platform-experiments` |
| Authoritative branch | `codex/mobile-m01-discovery` |
| Audited commit | `4934f94eae2d02e039a8bb430f0cfb27075cc40e` |
| Baseline relationship | Audit branch was created directly from `origin/main`; a live `ls-remote` and the local `origin/main` both resolved to the audited commit when discovery began |
| Worktree label | `<M01_WORKTREE>` |
| Scope | Read-only discovery, isolated prototype execution, and mobile-readiness documentation |
| Explicitly out of scope | Building an iPhone app, changing product runtime code, using live provider/dealership credentials, exposing a local worker to inbound internet, or claiming production readiness |

## Executive result

The exact Atlas repository and a clean current baseline were found and audited.
The local control-plane walking skeleton works with deterministic fixtures, and
the desktop conversation stack can carry a real streamed agent turn and a
guarded tool approval through its local JSON-RPC/WebSocket gateway. Those are
useful foundations, but they are not yet a mobile product contract.

**Mobile implementation readiness: NO-GO.** Do not start an iOS client against
the current server APIs. The provider-neutral account/device-enrollment
contract (WS-05), production Control Center contract (WS-06), managed approval
contract (WS-09), and Atlas-owned phone-to-worker conversation relay are absent
from this baseline. The current admin bearer, worker bearer, lease, and upstream
messaging relay must not be repurposed as phone authentication.

**Contract-design readiness: CONDITIONAL GO.** Land the provider-neutral WS-05
identity and enrollment vertical slice first. A successor relay workstream can
then define a versioned mobile contract by reusing the verified session/event
vocabulary from the desktop gateway and the deterministic tenancy/policy ideas
from the Control Plane. It must treat user roles, approvals, cursor/replay,
push, and transcript retention as missing contracts, not inferred behavior.

## Evidence labels

This audit uses the following labels to prevent prototype evidence from being
mistaken for production evidence:

- **REAL / CURRENT BASELINE** — source or runtime behavior at the audited commit.
- **REAL / DIRTY PROTOTYPE** — behavior exercised through
  `<VOICE_PROTOTYPE_WORKTREE>` at
  `e91602ae4b1a8fec3b813af2f3ad785b37262a09` plus uncommitted UI/runtime work.
  This proves a path is technically possible; it is not an integration target.
- **FIXTURE / DETERMINISTIC** — real Atlas orchestration using synthetic store
  records, a fixture connector, or a local no-key model.
- **INSTALLED / UNMAPPED** — an installed binary or bundle whose build cannot
  be mapped to the audited Git history.
- **DOCUMENTED TARGET** — architecture described in documentation but not
  implemented in the audited baseline.
- **NOT VERIFIED** — unavailable or intentionally not exercised during M01.

## 1. Checkout identity and source-of-truth result

The user-selected workspace directory was an unrelated empty Git repository:
it had no commits, no remote, and two untracked context files. It was not used
as product source. A bounded local search located several checkouts with the
correct remote. The authoritative M01 worktree was created from the newest
verified `origin/main` without changing the default checkout or the dirty voice
prototype.

| Candidate | Result |
|---|---|
| Empty workspace repository | Rejected: no remote and no commits |
| Default Atlas checkout | Correct remote, but its checked-out `main` was 16 commits behind the already-fetched `origin/main` |
| Native voice/task demo worktree | Correct remote, but intentionally preserved dirty; used only as a separately labelled visual/runtime prototype |
| `<M01_WORKTREE>` | Accepted: clean worktree, correct remote, branch `codex/mobile-m01-discovery`, exact live baseline SHA |

The verified remote was
`https://github.com/Seomzo/atlas-platform-experiments.git`. The historical
`altas/` spelling remains the repository namespace and documentation directory;
the public product name and CLI are Atlas. `README.md`, `PRODUCT.md`,
`docs/altas/PROJECT_CONTEXT.md`, and `docs/altas/DECISIONS.md` establish that
boundary.

## 2. Source review and workstream state

The required product, architecture, security, roadmap, test, upstream, mobile,
and desktop sources were read in full. Runtime claims were then checked against
the implementation rather than copied from roadmap prose.

| Area | Authoritative sources | Current finding |
|---|---|---|
| Product and trust boundary | `README.md`, `PRODUCT.md`, `docs/altas/PROJECT_CONTEXT.md`, `docs/altas/DECISIONS.md`, `docs/altas/SECURITY.md` | Atlas is explicitly “local execution, cloud control”; a customer-controlled worker is not a trust anchor |
| Prototype architecture | `docs/altas/ARCHITECTURE.md`, `docs/altas/MVP_ACCEPTANCE.md`, `docs/altas/TESTING.md`, `altas/control_plane/`, `altas/managed/` | SQLite control plane, deterministic policy, device bearer, signed lease, claim token, fixture workflow, model gateway, audit, and local admin UI are implemented |
| Desktop | `apps/desktop/README.md`, `apps/desktop/DESIGN.md`, `apps/desktop/package.json`, `apps/shared/src/json-rpc-gateway.ts`, `tui_gateway/server.py` | Electron renderer uses a local headless Atlas engine through JSON-RPC over WebSocket; it is a separate runtime plane from `altas/control_plane` |
| Mobile direction | `docs/altas/MOBILE_COMPANION.md` | Thin phone surface and Control Plane relay are a documented target only; the document explicitly blocks app work on WS-05 and Phase 1 |
| Workstream definitions | `docs/altas/AGENT_WORKSTREAMS.md` | WS-05, WS-06, and WS-09 are prompts/specifications, not completed handoffs in this baseline |
| Completed handoffs | `docs/altas/workstreams/README.md`, `docs/altas/workstreams/WS-13-HANDOFF.md` through `docs/altas/workstreams/WS-22-HANDOFF.md` | Later numbered development/collaboration handoffs exist, but none supplies the missing customer identity, production admin, approval, or mobile relay contract |
| Experimental upstream relay | `gateway/relay/`, `tests/gateway/relay/` | Reusable outbound WebSocket, capability, reconnect, HMAC, and delivery mechanics exist for an external messaging connector; this is not an Atlas Control Plane mobile relay |

Negative path checks at the audited commit confirmed that the following
expected deliverables do **not** exist:

- `docs/altas/workstreams/WS-05-HANDOFF.md`
- `docs/altas/CONTROL_CENTER_SPEC.md`
- `docs/altas/workstreams/WS-06-HANDOFF.md`
- `docs/altas/workstreams/WS-09-HANDOFF.md`

No branch or repository reference named as a WS-05 implementation was found.
The existing `codex/workers-p0-identity` history concerns desktop worker
profiles, not customer accounts, organization membership, laptop enrollment,
phone enrollment, or device-bound proof.

## 3. Local environment inventory

The inventory below records capabilities, not secrets. No real `.env`, auth
file, Keychain item, session transcript, or customer record was copied into the
audit.

| Component | Observed state | Mobile consequence |
|---|---|---|
| Host | macOS 26.6.1 (build 25G76), Apple Silicon | Plausible iOS development host |
| Disk | 460 GiB volume, approximately 54 GiB free at audit | Adequate for discovery; Xcode/simulator growth should be watched |
| Active Apple developer directory | `/Library/Developer/CommandLineTools` | Default `xcodebuild`/Simulator commands fail until full Xcode is selected or a scoped `DEVELOPER_DIR` is supplied |
| Full Xcode / Simulator | Xcode 26.6 build 17F113; scoped `DEVELOPER_DIR` access enumerated iOS 18.6, 26.4, and 26.5 runtimes | Apple SDK inspection works without changing the system selection; no simulator was booted and no iOS project was built |
| Swift | 6.3.3 | Compiler and SDK tooling are present; this does not prove an Atlas iOS target or build |
| Signing | One Apple Development identity was discoverable; the identity string is intentionally omitted | Existence only; team, provisioning, bundle ID, and device eligibility were not verified |
| Connected Apple device | One available iPhone running iOS 26.6 was enumerated; its identifier is omitted | Physical connectivity is confirmed; no install, pairing change, or device test was attempted |
| Computer Use / Xcode | Xcode's accessibility tree and current window were inspected without accepting a new permission prompt | GUI access is available; no Apple Account or signing action was taken |
| Python | Shell 3.14.3, system 3.9.6, installed Atlas runtime 3.11.14; other existing venvs use 3.12.12/3.13.11 | Repository requires `>=3.11,<3.14`; smoke used the installed Atlas runtime to avoid dependency mutation |
| Node / npm | Node 22.23.2; npm 10.9.8 | Sufficient for the existing desktop dev workspace |
| Git / GitHub CLI | Git 2.53.0; GitHub CLI 2.86.0 authenticated | Repository delivery is possible, subject to final diff validation |
| Docker / ffmpeg | Docker CLI 29.4.1 with daemon stopped; ffmpeg 8.0.1 | Available but not needed for the M01 runtime proof |
| Firewall | Host application firewall reported disabled | Audit fact only; not an authorization to expose services. Every M01 listener remained loopback-only |
| XcodeBuildMCP | Version 2.6.2 found in an existing npx cache/plugin configuration; not on `PATH` and not callable in this task | Apple inspection used scoped command-line tools and Computer Use instead |

### Installed Atlas inventory

| Installed component | Version / identity | Evidence classification | Finding |
|---|---|---|---|
| Public `atlas` CLI | 0.18.2 (`2026.7.7.2` friend-bundle), Python 3.11.14, OpenAI SDK 2.24.0 | INSTALLED / PARTIALLY MAPPED | Wrapper launches `<ATLAS_HOME>/hermes-agent/venv/bin/atlas`; package version matches `pyproject.toml`, but the installed root has no Git metadata |
| `atlas-control` | 0.1.0 | INSTALLED / CURRENT SOURCE VERSION | Executable exists inside the Atlas venv but is not on the ordinary shell `PATH`; version matches `altas/__init__.py` |
| Atlas Desktop app | bundle `com.dealerbox.atlas`, version 0.17.0 | INSTALLED / UNMAPPED | Installed build stamp `90be699` does not resolve in the current repository history; do not treat this bundle as the audited source UI |
| Atlas home | `<ATLAS_HOME>` | LOCAL STATE / NOT READ | Expected config, auth, state database, sessions, logs, collaboration data, and installed runtime were inventoried by filename only; contents were not used |
| Launch agents | Atlas gateway/collaboration/update labels were present | INSTALLED / NOT ATTRIBUTED | Presence does not prove a healthy or current Atlas service; no persistent mobile relay listener was found |

The three version surfaces already disagree: Desktop 0.17.0, CLI 0.18.2, and
Atlas-owned control-plane package 0.1.0. A mobile client cannot use a single
unversioned “Atlas version” for compatibility decisions.

## 4. Isolated runtime evidence

### 4.1 Deterministic control-plane smoke

The following command ran from `<M01_WORKTREE>` using the already-installed
Atlas Python runtime:

```bash
make PYTHON="<ATLAS_HOME>/hermes-agent/venv/bin/python" atlas-smoke
```

`scripts/altas-smoke.py` creates its own temporary directory and SQLite
database, then uses FastAPI `TestClient`; it does not depend on a live network
service. Result:

```text
status: passed
job_id: job_demo_daily_report
report_sales: 8084.0
unentitled_store_denied: true
device_revocation_verified: true
```

Classification: **FIXTURE / DETERMINISTIC**. The control-plane, policy,
claim-token, model-gateway, completion, overview, and revocation code paths were
real. The Tekion snapshot and model completion were synthetic.

### 4.2 Loopback Control Plane and worker

A separate runtime used a temporary SQLite database and the demo seed/model,
then launched:

```bash
<ATLAS_PYTHON> -m altas serve --host 127.0.0.1 --port 8787
```

The listener was verified on loopback only. With isolated
`HOME`/`HERMES_HOME`/`ATLAS_HOME`, `atlas-control doctor` reported:

```text
control_plane: configured
database_parent: ready
fixture: ready
worker: configured
```

One `atlas-control worker --once` cycle completed
`fixed_ops.daily_report` for `job_demo_daily_report`. The Control Center then
showed the synthetic tenant/store/device/agent, the succeeded job, usage, and
audit events. After server shutdown, the same UI showed an explicit
“CONTROL PLANE UNAVAILABLE” state. The 8787 listener was absent at cleanup.

Classification: **REAL TRANSPORT + FIXTURE DATA**. HTTP, browser rendering,
worker polling, lease/policy/job calls, and disconnect handling were real; the
identities, connector data, and model were demo fixtures.

Current-run, local-only visual evidence:

- `artifacts/mobile-readiness/current-surfaces/01-control-center-overview.png`
- `artifacts/mobile-readiness/current-surfaces/02-control-center-fleet.png`
- `artifacts/mobile-readiness/current-surfaces/03-control-center-jobs.png`
- `artifacts/mobile-readiness/current-surfaces/04-control-center-audit.png`
- `artifacts/mobile-readiness/current-surfaces/13-control-center-disconnected.png`

### 4.3 Desktop conversation and approval path

The audited baseline's desktop dependencies were not mutated. To exercise the
most complete available interface, M01 launched the preserved dirty
`<VOICE_PROTOTYPE_WORKTREE>` through:

```bash
HOME=<ISOLATED_MACOS_HOME> \
HERMES_HOME=<ISOLATED_ATLAS_HOME> \
HERMES_DESKTOP_USER_DATA_DIR=<ISOLATED_ELECTRON_USER_DATA> \
HERMES_DESKTOP_HERMES_ROOT=<VOICE_PROTOTYPE_WORKTREE> \
GIT_TERMINAL_PROMPT=0 \
npm --workspace apps/desktop run dev
```

Vite bound to `127.0.0.1:5174`; the Electron-managed headless gateway used an
ephemeral loopback port. A temporary no-key OpenAI-compatible deterministic
provider bound to `127.0.0.1:19081`. The current run verified this live path:

```text
Electron renderer
  -> JSON-RPC over WebSocket
  -> headless gateway
  -> Atlas agent loop
  -> terminal tool
  -> local deterministic provider
  -> streamed gateway events
  -> rendered transcript/tool row
```

The harmless command emitted `M01_DETERMINISTIC_TOOL_OK`; the final streamed
text stated that the real gateway, tool execution, and renderer path completed.
An earlier synthetic `git push` did **not** trigger the command approval gate;
it ran inside the empty isolated temporary workspace and exited 128 because it
was not a Git repository, so no repository or remote changed. A later harmless
`python3 -c print(...)` command did reach the real approval surface and M01
explicitly rejected it (blocked exit `-1`). This is evidence that the current
desktop gate is pattern-sensitive, not a general Atlas managed-action
authorization policy. No consequential command or user-data mutation was
performed.

The first-launch run also exposed an important setup discrepancy: the desktop
requires a distinct memory-model route. M01 used only isolated dummy/local
configuration to pass that gate, and public egress was blocked through an
invalid proxy. No provider key or live model was used.

Classification: **REAL / DIRTY PROTOTYPE + DETERMINISTIC PROVIDER**. The UI,
JSON-RPC socket, agent loop, tool execution, approval wait/response, and stream
renderer were real. The model was local and deterministic; the source checkout
was not clean/current main. The dev command reports exit code 1 when its child
processes receive the deliberate Ctrl-C, but all Electron, Vite, gateway, and
19081 listeners terminated and cleanup was verified.

Current-run, local-only visual evidence:

- `artifacts/mobile-readiness/current-surfaces/05-desktop-first-launch-memory-gate.png`
- `artifacts/mobile-readiness/current-surfaces/06-desktop-setup-failure.png`
- `artifacts/mobile-readiness/current-surfaces/07-desktop-tool-streaming.png`
- `artifacts/mobile-readiness/current-surfaces/08-desktop-model-settings.png`
- `artifacts/mobile-readiness/current-surfaces/09-desktop-voice-settings.png`
- `artifacts/mobile-readiness/current-surfaces/10-desktop-provider-account.png`
- `artifacts/mobile-readiness/current-surfaces/11-desktop-gateway-settings.png`
- `artifacts/mobile-readiness/current-surfaces/12-desktop-approval-request.png`

## 5. What exists today

### 5.1 Control Plane: useful but not phone-ready

The current Atlas-owned control plane implements valuable enforcement seams:

- tenant, store, subscription, device, agent, entitlement, job, usage, and
  audit persistence;
- hashed device bearer authentication;
- signed, short-lived leases;
- one-time job claim tokens and live policy rechecks;
- remote device disable and job cancellation;
- an OpenAI-compatible, non-streaming, job-scoped model surface; and
- a loopback-only demo admin UI protected by a shared development bearer.

The implementation is concentrated in `altas/control_plane/app.py`,
`altas/control_plane/repository.py`, `altas/control_plane/security.py`,
`altas/control_plane/policy.py`, `altas/control_plane/model_gateway.py`, and
`altas/control_plane/schemas.py`. Worker behavior lives in
`altas/managed/client.py`, `altas/managed/context.py`,
`altas/managed/policy_guard.py`, and `altas/managed/worker.py`.

It does **not** implement users, organization membership, roles, OIDC, browser
login, one-time enrollment, phone devices, asymmetric device proof, refresh
rotation, conversation relay sessions, push tokens, approvals, or mobile
transcript/event cursors. The worker uses a configured bearer and periodic
heartbeat/job polling (three-second default), not a persistent mobile
conversation channel.

### 5.2 Desktop gateway: the strongest reusable conversation seam

`apps/shared/src/json-rpc-gateway.ts` defines a compact reusable client and
event vocabulary including `session.info`, `message.start`, `message.delta`,
`message.complete`, thinking/reasoning/status updates, tool lifecycle,
`clarify.request`, `approval.request`, `secret.request`, background completion,
and errors. `tui_gateway/server.py` exposes concrete methods including
`session.create`, `session.resume`, `prompt.submit`, and `approval.respond`.
The desktop's connection, submit, replay/recovery, stream projection, and
approval UI live in `apps/desktop/src/store/gateway.ts`,
`apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`,
`apps/desktop/src/app/session/hooks/use-message-stream/gateway-event.ts`, and
`apps/desktop/src/components/assistant-ui/tool/approval.tsx`.

This is the best current input to a future mobile relay contract, but it is a
local engine protocol. It does not itself supply cloud tenancy, phone
enrollment, mobile reconnect cursors, store authorization, APNs delivery, or
server-retention rules. Its remote-gateway OAuth/token support in
`apps/desktop/electron/main.ts` is an upstream desktop feature and must not be
declared the Atlas customer identity architecture by inference.

### 5.3 Voice exists at the engine/UI edge, not as an Atlas mobile service

The baseline desktop includes recorder/conversation hooks under
`apps/desktop/src/app/chat/composer/hooks/` and voice settings/playback state
under `apps/desktop/src/store/`. The dirty prototype adds task-thread and voice
coordination beyond the baseline. M01 visually verified desktop voice settings,
but did not use a microphone, STT provider, TTS provider, phone audio session,
or background audio. Voice should therefore be described as an existing engine
capability seam, not a verified mobile voice channel.

### 5.4 Experimental relay mechanics are not the Atlas mobile relay

`gateway/relay/adapter.py`, `gateway/relay/auth.py`,
`gateway/relay/descriptor.py`, and `gateway/relay/ws_transport.py` contain useful
generic mechanics: outbound WebSocket transport, capability negotiation,
reconnect, HMAC-authenticated channel setup, channel-authenticated inbound
delivery, buffering/replay expectations, and interrupt/passthrough frames.
Inbound WebSocket frames are not separately HMAC-signed. Their source comments
mark the contract experimental and describe a separate connector boundary.
They do not bind an enrolled Atlas phone user to an enrolled Atlas worker, a
tenant/store, an approval, or a Control Plane audit correlation. Reuse ideas;
do not reuse trust conclusions.

## 6. Surface audit summary

Detailed visual findings live in `docs/altas/mobile/M01_VISUAL_SURFACE_AUDIT.md`.
The runtime-level findings are:

- The Control Center is visually coherent and explicit about fixture/dev state,
  but its dense fixed sidebar, large editorial headers, tables, and admin queue
  form are desktop/operator patterns, not a mobile shell.
- The Control Center exposes a clear disconnected state rather than silently
  showing stale data. A mobile contract needs the same explicit freshness
  semantics, backed by event cursor and delivery state rather than a browser
  refresh timestamp alone.
- Desktop proves streaming text, tool progress, settings, and a blocking
  approval interaction. The approval UI is useful design evidence, but current
  upstream choices (`once`/`deny`) are not the missing WS-09 Atlas approval
  artifact bound to actor, tenant, store, target, expiry, and attempt. The
  synthetic `git push` observation also proves that the current command-pattern
  gate must not be treated as complete action classification.
- First-launch memory-model coupling is a release blocker for a thin companion:
  phone setup must not require configuring a second agent runtime or provider.

## 7. Discrepancies and readiness blockers

| Priority | Discrepancy / blocker | Evidence | Required resolution before iOS implementation |
|---|---|---|---|
| P0 | WS-05 is specified but not implemented | Missing WS-05 handoff and no account/enrollment API or models | Provider-neutral user/org/role/device enrollment with one-time redemption, replay resistance, device-bound proof, expiry, rotation, revocation, and audit |
| P0 | No Atlas mobile conversation relay | `docs/altas/MOBILE_COMPANION.md` is target prose; current worker only heartbeats/polls | Versioned phone↔Control Plane↔worker session transport, explicit addressing, backpressure, reconnect cursor, idempotency, and revocation |
| P0 | No WS-09 managed approval contract | Missing handoff/server approval entity | Human-readable bounded action, actor/tenant/store/device/agent/job/target binding, expiry, single use, mutation detection, race/replay defense, fail closed |
| P0 | Local admin token is not operator/customer auth | Shared bearer and loopback checks in `altas/control_plane/app.py` | Authenticated customer/operator applications with tenant-aware RBAC, CSRF/rate controls, and separated support authority |
| P0 | No streaming conversation endpoint in Atlas Control Plane | Model endpoint is job-scoped and non-streaming; desktop socket is local engine protocol | Choose relay ownership and define event envelope; do not expose model gateway directly to phones |
| P1 | Desktop/control-plane version planes are independent and currently skewed | Installed 0.17.0 / 0.18.2 / 0.1.0; unmapped app stamp | Versioned capability negotiation and supported-version policy |
| P1 | No push/background delivery contract | No APNs token/device/session records | Push token lifecycle, notification minimization, cursor fetch after wake, logout/revoke behavior |
| P1 | Transcript persistence/retention boundary undefined for relay | Local engine owns sessions; mobile doc forbids unbounded plane persistence | Decide ciphertext/plaintext ownership, retention, deletion/export, audit-vs-transcript separation, and multi-device consistency |
| P1 | WS-06 production Control Center specification absent | Current local UI is fixture/admin-only | Resolve customer vs operator roles and shared API conventions before sharing endpoints with mobile |
| P1 | Atlas iOS build/signing path is undefined | Full Xcode, iOS runtimes, and a connected device were enumerated, but no iOS target, bundle ID, provisioning, simulator boot, or device build exists | Keep using a scoped full-Xcode developer directory until selection changes; define the target and verify simulator, signing team, bundle ID, and test-device installation in the eventual app workstream |
| P2 | Desktop first launch couples chat to a distinct memory provider | Current-run setup gate | Make companion authentication independent of local provider setup; define worker-unavailable/configuration error states |
| P2 | Baseline voice and dirty prototype evidence are mixed | Voice run came from dirty prototype | Land and review any intended voice/task contract before using it as mobile API truth |

## 8. Reusable contract candidates

The following may be carried into a successor relay workstream as inputs, not
copied wholesale:

1. **Session lifecycle nouns and event names** from
   `apps/shared/src/json-rpc-gateway.ts` and the method behavior in
   `tui_gateway/server.py`.
2. **Transport-independent rendering model** from desktop stream and approval
   reducers.
3. **Deterministic server-side authorization posture** from
   `altas/control_plane/policy.py` and immutable scope from
   `altas/managed/context.py`.
4. **Short-lived revocable authorization and attempt binding** from control
   plane leases/claim tokens.
5. **Outbound persistent-connection and reconnect patterns** from
   `gateway/relay/`, after replacing its connector trust model with Atlas
   enrolled identity and tenant/store policy.
6. **Explicit dev/fixture/freshness states** demonstrated by the Control
   Center, simplified for a phone.

The mobile client should never receive a dealership credential, provider
master key, worker bearer, admin bearer, or raw connector session. It should
receive only a short-lived user/device session scoped to permitted worker
conversations and approvals.

## 9. What M01 did not verify

- No iOS project, Swift package, simulator, archive, signing, TestFlight, APNs,
  Keychain, biometric, deep-link, background-task, microphone, or accessibility
  test was created or run.
- No live model provider, Tekion credential, dealership payload, customer
  account, OIDC provider, billing provider, Slack account, or messaging relay
  was contacted.
- No production database, queue, hosted Control Plane, TLS termination, WAF,
  rate limiter, observability stack, backup/restore, or multi-region behavior
  was exercised.
- No current-main Desktop end-to-end run was claimed; the visual conversation
  proof is explicitly the preserved dirty prototype.
- No installed Atlas Desktop build provenance beyond its local bundle metadata
  and unresolved build stamp was established.
- No remote attestation or hardware-bound device identity exists or was tested.

## 10. Evidence and source index

All repository-relative paths in this report were checked in
`<M01_WORKTREE>`. Local screenshots exist under the ignored
`artifacts/mobile-readiness/current-surfaces/` directory for this audit; the
four missing workstream/spec paths listed earlier were checked and confirmed
absent.

Primary implementation evidence:

- `Makefile`
- `pyproject.toml`
- `scripts/altas-smoke.py`
- `altas/__init__.py`
- `altas/cli.py`
- `altas/control_plane/app.py`
- `altas/control_plane/config.py`
- `altas/control_plane/database.py`
- `altas/control_plane/model_gateway.py`
- `altas/control_plane/policy.py`
- `altas/control_plane/repository.py`
- `altas/control_plane/schemas.py`
- `altas/control_plane/security.py`
- `altas/control_plane/static/app.js`
- `altas/control_plane/static/index.html`
- `altas/control_plane/static/styles.css`
- `altas/fixed_ops/fixtures/tekion_service_snapshot.json`
- `altas/fixed_ops/workflow.py`
- `altas/managed/client.py`
- `altas/managed/context.py`
- `altas/managed/policy_guard.py`
- `altas/managed/worker.py`
- `plugins/model-providers/altas/plugin.yaml`
- `apps/desktop/electron/backend-command.ts`
- `apps/desktop/electron/backend-env.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/src/hermes.ts`
- `apps/desktop/src/store/gateway.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`
- `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event.ts`
- `apps/desktop/src/components/assistant-ui/tool/approval.tsx`
- `apps/shared/src/json-rpc-gateway.ts`
- `apps/shared/src/websocket-url.ts`
- `tui_gateway/server.py`
- `gateway/relay/adapter.py`
- `gateway/relay/auth.py`
- `gateway/relay/descriptor.py`
- `gateway/relay/ws_transport.py`
- `tests/altas/test_control_center_delivery.py`
- `tests/altas/test_control_plane_api.py`
- `tests/altas/test_managed_worker.py`
- `tests/gateway/relay/test_relay_roundtrip.py`

## Decision

M01 is complete as an audit. The next safe workstream is WS-05's
provider-neutral identity and device-enrollment vertical slice, not an iOS
implementation. A reviewed text-only mobile relay contract should follow and
must make the remaining P0 rows above testable before any phone client is
coupled to Atlas.
