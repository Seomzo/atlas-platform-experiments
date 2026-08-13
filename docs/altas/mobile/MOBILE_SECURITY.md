# Atlas Mobile Security Plan

**Security posture:** The iPhone is an untrusted presentation/input endpoint that can prove an enrolled device key. Every authorization decision and consequential-action consumption remains server-side. M03 is an isolated engineering slice, not a production security certification.

## 1. Assets and trust boundaries

Protected assets include account membership and store grants, device private keys, short-lived sessions, transcripts, tool/progress summaries, approvals and action digests, worker/gateway credentials, connector/provider secrets, reports, audit correlation, and notification metadata.

Trust boundaries:

1. iOS app sandbox and Keychain;
2. Apple browser/authentication and APNs services;
3. public TLS edge/control plane;
4. control-plane identity, relay, approval, storage, keys, and operations;
5. outbound worker and its encrypted local relay store;
6. loopback gateway/engine/policy/tool execution;
7. external identity/model/connector systems.

Crossing a boundary requires an authenticated, versioned, size-bounded, allowlisted contract. A network location, device label, tenant ID, or cached role is not authority.

## 2. Security invariants

- No model/provider/connector/worker/gateway secret is stored on or transmitted to the phone.
- No local gateway accepts internet ingress for mobile.
- Human identity, phone device identity, and worker device identity are distinct and non-interchangeable.
- Server reloads live membership, role, grants, subscription, entitlement, pairing, agent, job/lease, credential version, and approval context.
- Mobile commands and decisions are idempotent and durably accepted before success is shown.
- Unknown tools/events/contract versions fail closed; raw payload fallback is prohibited.
- Approval is exact-context, version/digest-bound, expiring, and single-use.
- Push is a content-minimal hint; app fetches truth after authentication.
- Revocation is an authorization state, not a recoverable network error.
- Logs, analytics, diagnostics, screenshots, and crash reports are separate exfiltration surfaces and use explicit allowlists.

## 3. Threat model

| Threat | Attack / failure | Required controls | Residual / gate |
|---|---|---|---|
| Stolen unlocked phone | Attacker reads threads or decides an approval | short device session, server live checks, biometric reauth for managed actions, app-switcher shield, remote revoke, bounded protected cache | Cached content may be visible while unlocked; production privacy review required |
| Stolen locked phone | Offline extraction or background access | device passcode, `ThisDeviceOnly` Keychain, `NSFileProtectionComplete`, app-layer cache encryption, no content push | Compromised passcode weakens local controls |
| Copied app container | Attacker obtains database/preferences from backup or filesystem | random cache key in nonmigrating Keychain, app-layer encryption, file protection, exclude cache from backup, no tokens in preferences | Metadata/file sizes may remain; minimize them |
| Backup restore / phone migration | Old cache appears on another/new phone without its key | `ThisDeviceOnly` keys, backup exclusion, enrollment cannot migrate, delete unreadable cache, require new enrollment | Orphan server device requires owner cleanup/revoke |
| Key extraction | Malware/jailbreak reads Ed25519 bytes | Keychain access class, no sync/export/logging, jailbreak risk signals, short sessions, App Attest evaluation, rotation/revoke | Ed25519 is not Secure-Enclave-backed; production decision required |
| Enrollment interception | QR/link token stolen or replayed | 128-bit random single-use token, digest at rest, 10-minute current expiry, atomic consume, account/environment/device-kind binding, optional verification code | User can still scan malicious code; clear issuer UI and recovery needed |
| Auth callback injection | Malicious app/browser replays OAuth callback | authorization code + PKCE, exact redirect, state/nonce/issuer/audience, universal links where possible, one-time transaction | Identity vendor configuration unresolved |
| Cross-tenant/store access | Client swaps IDs or cursor | server-derived grants, exact pairing/session checks, cursor scope/signature, subscription reauthorization, adversarial tests | Any cache/log mixing is release blocking |
| Replay/duplicate command | Network repeats submission/delivery | idempotency key + request digest, durable relay/inbox constraints, ordered ACK, no blind retry | Gateway lacks persisted idempotency; executing crash remains uncertain |
| Forged/reordered event | Attacker or bug changes progress/outcome | TLS, authenticated worker, event IDs/sequences, signed opaque cursors, strict schema/scope, atomic cursor commit, gap repair | End-to-end content signature is optional; server is trusted boundary |
| Raw payload exfiltration | Gateway event contains credential/path/private data | deny-by-default projection, per-event fields, `secret` prohibited, string/nesting caps, capture tests | Current relay projection is too broad; must close before M03 GO execution |
| WebSocket token leakage | Token in URL/proxy/log/subprotocol | Authorization header only, no query/cookie, log redaction, short session, TLS | Platform/network logging review required |
| MITM/TLS compromise | Traffic interception | ATS, TLS 1.2+ with modern suites, OS trust/hostname, HSTS at service edge, certificate transparency monitoring | No app pinning in v1; see section 9 |
| Malicious/compromised worker | Sends cross-scope or hostile payload; asks unsafe action | exact worker/pairing/agent session, typed projection, policy/approval guard, rate/size limits, revoke/contain | Worker still sees authorized local data; customer-host security required |
| Compromised control plane | Reads/changes relay content | least privilege, managed KMS envelope keys, audit, segmented services, encryption, operational access controls | Prototype SQLite/process-secret keys are production blockers |
| Malicious customer administrator | Admin enrolls/revokes/routes beyond legitimate business intent | server role/store grants, no phone tenant input, exact device metadata, immutable audit, alerts for enrollment/revoke, least privilege and owner-approved admin policy | An authorized owner can cause authorized damage; governance/recovery required |
| Approval spoofing/race | Approve stale/different action | live refetch, exact digest/version/context, atomic response, expiry, role/grant recheck, biometric user presence, single-use consume | Human can still misunderstand; UX and policy review required |
| Prompt injection | User/source/tool text tries to grant capability or bypass policy | data/instruction separation, capability/policy enforcement outside model, typed tool allowlist, managed guard, no secret requests, approval cannot alter capability | Models may still generate bad text; execution guard is mandatory |
| Malicious tool output | HTML/Markdown/payload attacks UI or smuggles secrets/instructions | typed redacted projection, no raw HTML/scripts/data URLs, safe link policy, size/nesting limits, output treated as data, fuzz/canary tests | Safe summaries can still mislead; show provenance and verification |
| Stale authorization | Cached membership/grant or open socket outlives revocation | short sessions, credential version live check, per-subscription reauth, heartbeat/request checks, revoke event/close, privileged refetch | Revocation latency bounded by specified checks, not instantaneous radio delivery |
| Notification disclosure | Lock-screen reveals customer/action data | opaque resource IDs, generic copy default, user notification privacy controls, fetch on open | Product owners must choose notification detail policy |
| Push-provider compromise | APNs/token channel sends forged/delayed or observes metadata | opaque non-authoritative payload, app fetch/auth, environment-scoped tokens/topics, APNs key custody/rotation, dedupe/expiry | Provider sees delivery metadata; document in privacy model |
| Screenshot/recording | Sensitive screen captured/shared | privacy shield on inactive scene, `UIScreen.isCaptured` warning/degrade high-risk views, content-minimal notifications, user education | iOS cannot guarantee screenshot prevention; no false claim |
| Pasteboard/share leakage | Content copied to other apps | no automatic pasteboard, typed safe diagnostic share, optional copy only for permitted content, sensitive-field exclusions | User-authorized sharing remains a product policy question |
| Logging/analytics leak | Tokens/content sent to logs/SDKs | structured allowlist logger, privacy annotations, production log levels, analytics content ban, test scanner, no third-party SDK in M03 | Crash SDK choice/privacy terms unresolved |
| Jailbroken/compromised OS | Hooking, key/content extraction, forged UI | risk signals, App Attest evaluation, block/step-up managed approvals, short sessions, revoke | Detection is bypassable; never claim jailbreak prevention |
| Denial of service | Flood prompts/subscriptions/events | per-user/device/worker limits, bounded queues, Retry-After, stream windows, size caps, circuit breakers | Capacity and SLOs require production load design |
| Dependency/supply chain | Malicious Swift/package/build artifact | minimal dependencies, pinned checksums, SBOM, code review, CI provenance, Apple signing separation | Release process not yet implemented |
| Environment mix-up | Internal app reaches production or wrong APNs | immutable per-build endpoints, distinct bundle/keychain/push IDs, visible internal badge, issuer/env binding | Exact cloud/account identities unresolved |
| Developer build escape | Deterministic identity, fixtures, debug logging, or relaxed ATS reaches customers | compile-time target separation, Release assertions, no fixture modules in production link, signing/bundle isolation, CI binary/config scan | Apple/team release control still requires operational review |

## 4. On-device data policy

| Data | Storage | Accessibility / lifetime |
|---|---|---|
| Ed25519 device private key | Keychain | `WhenUnlockedThisDeviceOnly`, non-synchronizable; enrollment to sign-out/revoke |
| Local cache encryption key | Keychain | `WhenUnlockedThisDeviceOnly`; delete first during purge |
| Device access session | Memory | current app process; max five minutes today |
| Account access credential | Memory | ten minutes in current development implementation; production TTL owner-approved |
| Proof-bound account renewal handle | Keychain | `WhenUnlockedThisDeviceOnly`, single-use rotation; specified, not yet implemented |
| OIDC code/verifier/state/nonce | Memory plus protected transient transaction record if browser handoff requires | delete on success/cancel/timeout |
| Granted scope selection | protected preference using opaque IDs | refetch/reauthorize; delete on account/env change |
| Transcript/approval projection | app-encrypted SQLite plus `NSFileProtectionComplete` | bounded; product retention unresolved; user can clear |
| Composer drafts | same protected store, marked local-only | until sent/discarded/clear/sign-out |
| Push token | Keychain/protected store and server | environment/device scoped; delete on sign-out/revoke |
| Safe diagnostics | bounded protected ring | no content/secrets/full IDs; explicit share |

Sensitive data is excluded from iCloud Key-Value Store, CloudKit, shared app groups unless explicitly designed, Spotlight, Siri suggestions, widgets, pasteboard automation, Live Activities, lock-screen widgets, and backups. Future extensions require a separate threat review.

## 5. Data minimization and redaction

The mobile relay sends only data required to render an authorized product state. Never send:

- authentication headers/tokens, cookies, passwords, API keys, private key material, enrollment tokens, or MFA/recovery secrets;
- raw environment variables, filesystem paths, shell commands, stack traces, SQL, internal IPs/private URLs, or complete request headers;
- model-provider raw frames, hidden prompts, chain-of-thought, policy internals, or arbitrary tool JSON;
- connector credentials or unrestricted source records;
- other users', tenants', stores', workers', threads', or devices' identifiers.

Safe summaries are constructed at the worker/control-plane projection boundary from typed fields. Redaction failure is terminal for that event; it never triggers raw fallback. Correlation uses opaque IDs/digests.

## 6. Managed-action security

The inspected `atlas.managed-action.v1` action kinds remain the baseline:

- no approval: read, navigate, analyze, draft;
- approval required: `download_export`, `send_message`, `submit`, `mutate`, `credential`, `administrative`;
- unknown tool/action: `ACTION_UNCLASSIFIED`, denied.

An approval binds tenant, store, user, phone, worker, agent, job, attempt, claim, relay/pairing, workflow, capability, action digest, policy version, lease nonce/expiry, approval expiry/version, and correlation. The server atomically enforces pending → approved/denied/expired/canceled → consumed. The phone provides only an explicit response to the exact live object; it does not issue an execution grant itself.

Biometric success is locally fresh user presence. The backend still validates role, scope, context, expiry, digest, and idempotency. Denial requires no biometric and must remain easy.

## 7. iOS privacy behavior

- Microphone permission is requested only after a user initiates Voice; M03 text slice does not request it.
- Notifications are requested after explaining their value, not at first launch. Generic content is the privacy default.
- No Contacts, Photos, Location, Bluetooth, Local Network, Motion, Health, or tracking permission in v1.
- App privacy manifest and App Store privacy labels must match observed APIs/SDKs/data flows before release.
- App background snapshot uses an opaque branded privacy shield; restore content only when active and protected data is available.
- When `UIScreen.isCaptured` is true, warn and optionally hide restricted approval arguments; do not claim to prevent capture.
- No ambient audio, wake word, ad identifier, cross-app tracking, or transcript analytics.

## 8. App Attest and device risk

Evaluate App Attest in the production identity hardening track as an extra signal binding a genuine app instance to enrollment/session mint. It does not prove the human, replace the device key, authorize tenant scope, or stop a compromised authorized worker.

Recommended policy:

- M03 seam and deterministic mock only; do not block isolated development on production Apple attestation infrastructure.
- Before pilot, collect verified App Attest assertions at enrollment and risk-sensitive renewal, with replay-resistant challenges.
- Treat unavailable/unsupported/failed attestation according to an owner-approved risk policy; never silently grant a higher tier.
- Jailbreak/debug/hooking signals are telemetry/risk inputs. They are not reliable proof and must not contain sensitive device fingerprinting.

## 9. Logging, analytics, support, and remote wipe

- Production logging is structured and allowlisted: event name, coarse outcome/error code, app/build/contract/environment, duration bucket, connection-state transition, and opaque correlation suffix. Authentication material, full IDs, content, target labels, tool arguments, URLs, paths, audio, transcripts, and approval previews are prohibited.
- OSLog privacy annotations default dynamic values to private. Debug builds may add local diagnostics only in the isolated environment; they still never log keys/tokens/raw relay payloads.
- M03 ships no third-party analytics. A future allowlist may include app launch, onboarding stage/outcome, feature screen opened, connection/replay state and latency bucket, notification permission outcome, approval sheet outcome category, crash/hang/performance—never message/report/action content or cross-app identifiers.
- Support bundle export is explicit and previewable, contains the same content-free fields, and records user consent/time. There is no hidden remote log upload.
- Remote revocation stops server access even if the phone is offline. iOS does not give Atlas a guaranteed remote filesystem wipe. The app deletes keys/cache when it next receives a denial or opens; until then local protection depends on the device passcode, Keychain, file protection, and cache encryption. Documentation must say “revoke access,” not promise remote wipe.

## 10. Transport and certificate pinning

Use ATS, valid public/private enterprise CA as appropriate, hostname validation, modern TLS, HSTS at the service edge, short-lived credentials, and certificate-transparency/expiry monitoring. **Do not ship certificate/public-key pinning in v1 by default.** Pinning creates outage and recovery risk and does not protect a compromised endpoint/control plane. Revisit only if the threat model demands it and operations can support overlapping pins, signed remote pinsets, emergency bypass, and tested rotation.

Loopback worker → gateway retains explicit loopback enforcement and protected 0600 regular files. Public control-plane → worker is impossible by topology; worker initiates outward.

## 11. Server-side production requirements

The current prototypes use SQLite, process-local presence, and keys derived from a process secret/private key. Before production/pilot:

- transactional multi-tenant database with tested constraints/migrations and point-in-time recovery;
- managed KMS envelope encryption, key separation/rotation, and least-privilege service identities;
- distributed connection routing/presence or sticky ownership with durable handoff;
- hosted TLS/WAF/rate limits and environment/account isolation;
- durable queue/event storage and replay retention approved by owners;
- APNs key custody/rotation and token deletion;
- immutable audit trails, security alerts, dashboards, runbooks, on-call ownership, and incident/revocation procedures;
- backups/restore tests, dependency/vulnerability management, privacy/retention deletion jobs;
- production OIDC/MFA/account recovery and admin access controls.

These are deployment gates, not implementation details the phone can compensate for.

## 12. Security verification gates

M03 must provide:

- dependency and secret scan, generated-contract drift check, and log/fixture scanner;
- unit/property/fuzz tests for canonical proof, nonce, signature, idempotency, cursor, schema limits, redaction, and state transitions;
- integration tests for cross-user/tenant/store/device/worker/agent/pairing denial, revocation, expiry, stale approval, and single-use consumption;
- network tests for TLS failure, duplicate/reordered/oversize frames, backpressure, packet loss, and hostile Markdown/URLs;
- physical-device tests for Keychain accessibility, protected-data lock, app-switcher snapshot, screen capture signal, biometric success/failure/lockout, sign-out purge, and remote revoke;
- a content-free security evidence bundle with exact SHAs, environment, tester, time, correlation IDs, expected/observed result, and residual risk.

Any secret in logs/analytics/push/fixtures, raw gateway payload on the phone boundary, cross-scope response, auto-replayed uncertain command, cached approval execution, or revoked session continuing past the specified heartbeat/request check is a release-blocking failure.

## 13. Security non-claims

M02/M03 must not claim SOC 2, HIPAA, PCI, end-to-end encryption, exactly-once execution, hardware-backed Ed25519, jailbreak prevention, screenshot prevention, zero-knowledge storage, production readiness, or real write-action safety. Each would require a separate defined control set and evidence.
