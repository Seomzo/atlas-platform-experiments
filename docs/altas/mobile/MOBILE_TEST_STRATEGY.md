# Atlas Mobile Test Strategy

**Quality decision:** Contract, state-machine, security, and lifecycle tests are the backbone; screenshots alone cannot prove a vertical slice. M03 ends with a correlated physical-device evidence bundle.

## 1. Test layers

| Layer | What it proves | Typical owner / runner |
|---|---|---|
| Schema and codegen | Wire examples validate; Swift/Python models agree; breaking changes detected | Contracts owner; CI |
| Unit/property | Reducers, canonical proof, redaction, idempotency, cursor, cache transactions, copy/status mapping | iOS/backend component owners |
| Component | SwiftUI states/accessibility, actor concurrency, persistence, retry/replay with fakes | iOS feature owners |
| Backend integration | Identity → pairing → relay → approval ledger and authorization/transaction invariants | Backend owners |
| Worker/gateway integration | Outbound connection, inbox/outbox, allowlisted adapter, safe projection, uncertain outcome | Worker/relay owners |
| End-to-end simulator | App navigation and deterministic fixtures against an isolated stack | Integration owner |
| End-to-end physical device | Apple auth/Keychain/biometry/APNs/lifecycle/network behavior and premium rendering | Integration/release owner |
| Security/operational | Adversarial scope, revocation, secret/log scans, load/failure/recovery/runbooks | Security/platform owners |

Shipping code never silently selects a test provider or fabricated stream. Test/internal builds show environment and fixture status visibly.

## 2. Contract testing

- Validate every file in `contracts/mobile/` as JSON Schema Draft 2020-12.
- Maintain golden valid and invalid fixtures for every event, command, approval state, and error code used in M03.
- Generate Swift `Codable`, Sendable types and Python/backend validators from the same schemas or fail CI if checked-in generated output drifts.
- Enforce discriminators, required fields, `additionalProperties: false` for security-sensitive payloads, string/array/nesting/serialized-size caps, and RFC 3339/UUID/opaque-ID patterns.
- Consumer tests prove the app rejects unknown major versions and quarantines unknown event types without raw rendering.
- Producer tests prove the worker projection cannot emit prohibited fields or a `secret` classification.
- Compatibility CI compares the current schema manifest to the last released manifest: additive optional fields may pass within v1; semantic/required/removal changes require v2.

## 3. iOS unit and component matrix

### Identity and enrollment

- PKCE state/nonce/verifier lifecycle, cancellation, callback mismatch, and environment mismatch.
- Key generation, Keychain duplicate/missing/corrupt/locked behavior, non-synchronizable attributes, rotation two-slot recovery, sign-out purge.
- Canonical Ed25519 proof bytes, signature vectors shared with backend, fresh nonce, skew boundaries, session renewal once, revoked/version mismatch fail closed.
- Account/store/worker selections derive only from server grants; scope change clears subscriptions/projections.

### Relay and state

- HTTP accepted vs runtime completed; timeouts resolve by idempotency lookup.
- Duplicate event ID, same-ID/different-digest, out-of-order sequence, gap, replay, cursor expiry, live-buffer merge, snapshot replacement, app kill between event and cursor transaction.
- WebSocket handshake/version/welcome/heartbeat/close reasons; foreground/background transitions and task cancellation.
- Backoff with deterministic clock/randomness, rate-limit Retry-After, queue full, slow-consumer closure, oversize/invalid JSON.
- Worker available/suspect/unavailable/revoked mapping and stale timestamps.
- Uncertain outcome blocks automatic retry.

### Threads and approvals

- Thread pagination, empty/loading/offline/revoked/error states, local draft separation, streaming coalescence, interrupt eligibility.
- Tool event lifecycle, unknown type placeholder, hostile Markdown/link/html, safe label mapping and no raw argument view.
- Approval pending/resolved/expired/canceled/consumed; exact version/digest conflict; scope change; offline control absence; biometric success/cancel/failure/lockout/passcode policy.
- Deep links refetch/reauthorize and do not reveal cached protected content before success.

### Accessibility and design system

- Snapshot/render tests in light/dark, standard/accessibility Dynamic Type, Increase Contrast, Bold Text, Reduce Motion, and Reduce Transparency.
- VoiceOver labels/order/headings/modal focus, coalesced streaming announcements, tool row grouping, approval consequence/button order.
- 44-point touch targets, no clipped/truncated critical text, landscape/small-device keyboard handling, color-independent states and automated contrast checks.
- XCUITest covers enrollment routing, thread send/replay, interrupt, approval deny/approve, notification deep link, revocation lock, and accessibility identifiers without bypassing real network contracts in the isolated end-to-end configuration.

## 4. Backend and authorization matrix

For every identity, relay, stream, thread, and approval endpoint, test:

| Dimension | Negative cases |
|---|---|
| Human | wrong subject, disabled user, inactive membership, wrong role |
| Tenant/store | client-swapped ID, removed grant, cross-tenant pairing/cursor/resource |
| Phone | wrong kind, inactive/revoked, credential-version mismatch, wrong owner, expired session |
| Worker/agent | wrong kind, inactive/revoked, wrong store/tenant, inactive agent, superseded socket |
| Pairing/session | missing/revoked/mismatched pairing, closed session, phone not owner |
| Entitlement | missing subscription/capability/profile |
| Job/approval | wrong job/attempt/claim/lease/workflow/digest/version, expired/canceled/consumed |
| Replay | forged/other-scope cursor, cursor expiry, duplicate/different digest, sequence gap |

Each case asserts status/error code, no state mutation, no sensitive response body, safe audit/correlation record, and closure/cancellation where required.

## 5. Failure-injection scenarios

1. Drop phone connectivity before command response, after 202, mid-stream, and mid-replay.
2. Kill relay before/after command commit and before/after worker ACK.
3. Kill worker before inbox commit, after inbox commit, before gateway call, during gateway execution, and after outcome before outbox ACK.
4. Restart gateway while a thread streams.
5. Expire/revoke phone session, worker session, pairing, membership, store grant, and approval during live use.
6. Fill pending/inflight queues; slow phone ACK; overflow worker local event queue.
7. Corrupt/truncate local app DB, worker inbox/outbox DB, cursor, and cached encrypted column in test copies.
8. Advance/retard device/server clock at proof and approval expiry boundaries.
9. Send duplicate/reordered/oversize/unknown-version/hostile events and unsupported tool kinds.
10. Background/terminate app during stream, biometric prompt, approval response, notification open, and protected-data lock.

Expected outcomes are specified state transitions—not just lack of crash. Every ambiguity is visible and recoverable or fail-closed.

## 6. M03 end-to-end acceptance suite

The suite uses one synthetic organization/store, one enrolled iPhone, one outbound worker, one profile, one thread, one deterministic safe tool, and the synthetic no-write export approval.

### Happy path

- browser sign-in seam → one-time enrollment → fresh device session;
- granted scope selection and paired worker discovery;
- thread list/detail snapshot and foreground subscription;
- text prompt durable acceptance and one message/tool lifecycle;
- approval requested, live fetch, biometric approve or deny, single-use consume;
- completion and Activity projection.

### Continuity path

- repeat prompt response loss and resolve by idempotency key;
- Wi-Fi off/on and Wi-Fi ↔ cellular during stream;
- background 60 seconds, foreground, replay with no gap/duplicate;
- force-kill and relaunch from committed cursor;
- worker disconnect/reconnect with durable command/event replay;
- cursor expiry forces safe snapshot re-anchor.
- background completion emits a generic APNs notification; tapping it authenticates and refetches the exact thread without relying on push content.
- the full submit/stream/replay path repeats on physical iPhone over cellular with Wi-Fi disabled.

### Security path

- attempt all cross-scope matrix cases;
- remote revoke while foreground and while backgrounded;
- approval stale/expired/different digest/second consumption;
- executing-crash produces `OUTCOME_UNCERTAIN` and no automatic replay;
- capture HTTP/WebSocket/push/log output and prove no prohibited fields/raw payloads;
- lock phone and verify Keychain/cache protection, then sign out/purge.

### UX/accessibility path

- VoiceOver completes enrollment, opens a thread, submits text, understands tool progress, and denies an approval;
- largest Dynamic Type, Reduce Motion, Reduce Transparency, light/dark, network/error/revoked states;
- physical-device video shows native navigation/materials, keyboard/composer, background recovery, and biometric sheet.

## 7. Voice test plan (post-text slice)

- microphone permission allow/deny/restricted and Settings recovery;
- hold/release, tap start/stop accessibility alternative, cancel gesture, maximum duration/size;
- call/audio-route/Bluetooth/headphone interruption, Siri/system interruption, background, low storage, upload loss;
- transcript empty/error/partial, review/edit, language/locale, noisy environment, no silent submit;
- audio and transcript retention/deletion according to an owner-approved policy; neither is stored in push/analytics/logs;
- typed and voice prompts converge on the same validated command/idempotency path.

## 8. Performance and resilience budgets for M03

These are engineering targets, not production SLOs:

- cold launch to protected shell under 1.5s on the test iPhone when cache is available;
- input acknowledgment UI immediately, durable 202 state within 2s on healthy isolated network;
- foreground event render p95 under 250ms after control-plane receipt;
- replay 1,000 small events without main-thread stalls and with bounded memory;
- scrolling/thread input maintains 55+ fps on the test device, with Instruments evidence;
- no leaked socket/task after 100 foreground/background cycles;
- local cache remains within configured bounds and purges deterministically.

Load testing must separately establish production capacity and rate limits; M03 targets do not set pricing/SLOs.

## 9. Evidence bundle

Store under an M03-owned artifact directory, not in M02, with:

- exact app/backend/contract SHAs and clean/dirty status;
- Xcode, SDK, simulator/device OS and device class; no device serial in public evidence;
- environment/account/store/worker fixture identifiers safely redacted but correlatable;
- command/event/approval correlation timeline from app, control plane, worker, and gateway;
- test command output and pass/fail counts;
- contract compatibility and prohibited-field scan results;
- screenshots for every named state and physical-device video of the vertical slice;
- Instruments memory/energy/hang summary;
- known failures, residual risks, and reviewer sign-off.

Claims are limited to the exact evidence. Simulator success cannot substitute for Keychain/biometric/APNs/physical lifecycle proof.

## 10. M03 exit criteria

M03 is GO-complete only when all critical suites pass on a clean integrated backend base and a physical iPhone. Any cross-scope data, secret leakage, raw payload, duplicate healthy-path execution, blind uncertain retry, stale approval, failed revocation, accessibility blocker, or uncorrelated end-to-end hop is an automatic NO-GO. Noncritical cosmetic issues may proceed only with explicit owner acceptance and a bounded follow-up.
