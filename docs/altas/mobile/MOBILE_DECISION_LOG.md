# Atlas Mobile Decision Log

This log separates accepted M02 architecture choices from owner decisions that remain open. An unresolved item must not be converted into a production default by implementation convenience.

## Accepted decisions

### D-001 — Native SwiftUI client

- **Decision:** Build a native Swift/SwiftUI iPhone app, not a web wrapper or cross-platform runtime.
- **Why:** native lifecycle, Keychain/LocalAuthentication/APNs/accessibility, low-latency streaming UI, and premium platform behavior are core product requirements.
- **Reversibility:** Relay and JSON contracts are platform-neutral; another client can be added without moving authority to iOS.

### D-002 — Thin client; existing Atlas remains authoritative

- **Decision:** Phone is presentation/input only. Control plane, outbound worker, gateway/engine, policy, transcript, and approval ledger own truth.
- **Why:** avoids a second agent/policy/transcript authority and never exposes the local gateway publicly.
- **Reversibility:** None desired; changing this would be a new product/security architecture.

### D-003 — Four-tab information architecture

- **Decision:** Today, Threads, Activity, Settings. Voice is a contextual mode; approvals are sheets.
- **Why:** supports check/continue/decide without an empty text-first Voice tab or desktop configuration rail.
- **Reversibility:** Router supports a future Voice tab after evidence shows it is a persistent top-level job.

### D-004 — iOS 18 floor with conditional iOS 26 materials

- **Decision:** Minimum iOS 18.0, built with scoped Xcode 26.6; iOS 26 visual enhancements behind availability.
- **Why:** broad modern platform baseline while delivering the desired current-device glass quality with an accessible fallback.
- **Reversibility:** Deployment floor can rise after supported-device/product data; no contract dependency.

### D-005 — HTTP commands and replay; foreground WebSocket events

- **Decision:** Durable commands/snapshots/replay use HTTP; one foreground WebSocket supplies low-latency events. Replay is correctness.
- **Why:** makes acceptance/idempotency/recovery explicit and avoids background-socket assumptions.
- **Reversibility:** Transport may evolve behind `RelayStreaming`; event/command semantics stay versioned.

### D-006 — Push is an opaque hint

- **Decision:** APNs contains resource category/opaque ID only; app authenticates and refetches.
- **Why:** push delivery/order/content is not trustworthy enough for transcript or approval truth and lock-screen privacy matters.
- **Reversibility:** Owner-approved notification copy can become more specific without changing authority.

### D-007 — Ed25519 Keychain for M03, versioned P-256 migration seam

- **Decision:** Match current Ed25519 server proof in M03, protect private bytes with `WhenUnlockedThisDeviceOnly`, and advertise device-key algorithm.
- **Why:** Secure Enclave does not supply an Ed25519 signing key; claiming hardware backing would be false. P-256 server support is the recommended pre-production migration.
- **Reversibility:** Versioned algorithm field and rotation flow support migration.

### D-008 — Proof-bound rotating account renewal, not a reusable refresh bearer

- **Decision:** Keep ten-minute-current account and five-minute device access sessions in memory. Renew the device session with fresh proof; renew account access through a single-use rotating handle that also requires fresh phone proof and lives in `ThisDeviceOnly` Keychain.
- **Why:** the implemented phone APIs require human account assertion plus exact device session. A proof-bound rotating family avoids browser login every ten minutes without creating a reusable bearer/client secret; reuse revokes the family.
- **Reversibility:** TTL and renewal-family lifetime remain policy/config. A provider-native sender-constrained token can replace the handle behind `SessionBroker`.

### D-009 — Text-first vertical slice; push-to-talk second

- **Decision:** M03 proves text + tool progress + synthetic approval. Push-to-talk reuses the composer only after the path is reliable.
- **Why:** isolates relay/identity/replay truth from audio permission/provider/lifecycle complexity.
- **Reversibility:** Voice seams are reserved in routing/composer and do not change command execution.

### D-010 — Managed approval only; no general remote writes

- **Decision:** Unknown/consequential actions fail closed and require exact managed approval. M03 executes only the synthetic no-artifact/no-write export fixture.
- **Why:** current guarded lifecycle is evidence-backed; general connectors and write policies are not.
- **Reversibility:** Add one typed action kind/tool integration at a time through policy/security review.

### D-011 — Typed mobile-safe event projection

- **Decision:** Worker/control plane project explicit mobile payloads; raw gateway/provider/tool frames never reach the phone.
- **Why:** current broad payload forwarding after type/size checks is a material privacy/security gap.
- **Reversibility:** New event types/fields are versioned additions; raw fallback remains prohibited.

### D-012 — Uncertain execution is visible and never auto-retried

- **Decision:** If the worker dies after execution starts and the gateway outcome is unknown, emit `command.uncertain` and block blind retry.
- **Why:** the current gateway lacks persisted command idempotency; replay could duplicate a side effect.
- **Reversibility:** Gateway idempotency/outcome resolution can later convert more cases into proven terminal states.

### D-013 — Bounded encrypted local projection, not a mobile authority

- **Decision:** Cache bounded transcript/approval pages and cursors with iOS file protection plus app-layer encryption; drafts remain explicitly local.
- **Why:** supports continuity while preserving server truth and purgeability.
- **Reversibility:** Cache engine/limits can change behind repositories; production retention requires owner decision.

### D-014 — No certificate pinning by default

- **Decision:** ATS/OS trust/hostname/HSTS/monitoring, no app certificate pin until operations can safely rotate/recover.
- **Why:** brittle pinning can create a fleet outage and does not fix endpoint compromise.
- **Reversibility:** Add overlapping-key pinning only after a separate threat/operations review.

### D-015 — Sign out revokes this phone and removes its local trust

- **Decision:** First release calls an idempotent exact-phone self-revoke, unregisters push, deletes the device/renewal/cache keys and local data, and requires new enrollment. Other devices are untouched. Offline local purge warns that another surface is needed for immediate server revoke.
- **Why:** avoids an ambiguous signed-out-but-still-trusted phone and orphaned active credential. A narrow self-revoke also lets a member remove their own phone without general device-admin authority.
- **Reversibility:** Product can later distinguish lock/account-switch while retaining an enrolled key after a new security review and account-switching contract.

### D-016 — Separate phone identity from store binding before multi-store

- **Decision:** M03 uses the implemented single-store phone record. Before M07/multi-store, one user/organization phone identity and key gains explicit server-authorized store/worker bindings; workers remain store-bound.
- **Why:** WS-05 currently requires `store_id` on the device. Creating one phone key/device per store would complicate revocation, Keychain, push, unread continuity, and user trust. Store authority must still be live and explicit.
- **Reversibility:** Mobile APIs use opaque device/binding IDs. A migration converts the existing phone store into its first binding without changing the key or v1 relay semantics.

## Open owner decisions

### O-001 — Production identity and recovery

- **Decision needed:** OIDC provider, MFA, enterprise federation, recovery/step-up/admin policies.
- **Why now:** required before any production/pilot account.
- **Options:** managed identity provider; existing company IdP; self-hosted identity (not recommended without dedicated team).
- **Default recommendation:** managed OIDC with authorization-code + PKCE, phishing-resistant MFA option, documented recovery, behind `IdentityVerifier`.
- **Reversible seam:** provider adapter and stable Atlas account/device model.
- **Approver:** Atlas product owners plus security/operations.

### O-002 — Hosting account, cloud, region, and domain

- **Decision needed:** verified production/staging account/project/region/domain and data residency.
- **Why now:** TLS, DB, KMS, APNs, logs, and privacy depend on it.
- **Options:** deploy into an existing verified company cloud account; create a dedicated Atlas account/project; use a managed application platform for the isolated slice and migrate later.
- **Default recommendation:** one isolated non-production environment for M03; no production choice by this workstream.
- **Reversible seam:** environment config and service interfaces; migrations remain portable.
- **Approver:** product owners and infrastructure/security owner.

### O-003 — Production persistence, KMS, and distributed relay

- **Decision needed:** transactional database, queue/routing/presence, managed key service, backup/restore and SLO.
- **Why now:** the current SQLite/process-local/process-secret implementation cannot support multi-instance failover or a production confidentiality/durability claim.
- **Options:** managed Postgres + explicit relay ownership + managed KMS; cloud-native database/queue/KMS; self-managed stack (highest operational burden).
- **Default recommendation:** managed Postgres-class database plus managed KMS and explicit connection ownership; select vendor only after hosting decision.
- **Reversible seam:** repository/crypto/routing interfaces and v1 protocol.
- **Approver:** infrastructure/security owner with product cost approval.

### O-004 — Transcript, event, approval, audio, and local retention

- **Decision needed:** per-class retention/deletion/export/legal hold and replay window.
- **Why now:** server storage, offline product behavior, privacy disclosures, and voice depend on it.
- **Options:** short fixed retention by data class; tenant-configurable bounded retention; minimal event projection with transcript retained only by gateway; legal-hold tier later.
- **Default recommendation:** collect the minimum, configure by class/environment, short local cache, no audio retention after transcription unless explicitly required.
- **Reversible seam:** server advertises replay availability; caches are bounded/purgeable; policy fields are not hardcoded.
- **Approver:** product/privacy/legal/security owners.

### O-005 — Notification policy

- **Decision needed:** opt-in timing, default detail on lock screen, categories, quiet hours/escalation.
- **Why now:** APNs payload/copy and settings can expose customer context or create missed approval expectations.
- **Options:** generic alerts only; user-selected detail after consent; organization-managed policy within an Apple/privacy-compliant boundary.
- **Default recommendation:** generic content, request after value is explained, no lock-screen approval action.
- **Reversible seam:** opaque push resource contract and server templates.
- **Approver:** product/privacy owners.

### O-006 — Speech processing and language policy

- **Decision needed:** on-device vs server/vendor transcription, locales, audio codec/limits, processing region, deletion and quality target.
- **Why now:** voice data flow, permissions, latency, availability, cost, privacy disclosure, and retention all change with this choice.
- **Options:** on-device Apple speech where supported; Atlas-hosted speech service; contracted external speech provider; hybrid by locale/device with explicit disclosure.
- **Default recommendation:** prefer on-device when quality/language/device support meets requirements; otherwise explicit server seam with immediate deletion and no training.
- **Reversible seam:** `VoiceTranscribing` interface yields reviewable text before common prompt submission.
- **Approver:** product/privacy/security owners.

### O-007 — Pilot audience and distribution

- **Decision needed:** internal/TestFlight cohort, supported device/OS, data class, stores/workers, support/on-call, stop conditions.
- **Why now:** “pilot” determines allowed data/actions, support obligation, signing/distribution, monitoring, and incident authority.
- **Options:** employees with synthetic data; named design partners with approved low-risk data; broader external beta (not recommended before M10 evidence).
- **Default recommendation:** employees/design partners, synthetic or expressly approved low-risk data, no live writes, bounded TestFlight group.
- **Reversible seam:** environment/bundle isolation and feature entitlements.
- **Approver:** product owners and security/operations.

### O-008 — App identity and Apple operations

- **Decision needed:** final name/subtitle, bundle IDs, Apple team/roles, signing, associated domains, APNs keys, App Store privacy metadata.
- **Why now:** bundle identity and Apple team ownership become costly to change after distribution and determine keychain/push/universal-link isolation.
- **Options:** dedicated Atlas internal/production apps under an existing verified team; new Atlas-owned team; enterprise-only distribution where legally/operationally appropriate.
- **Default recommendation:** separate internal and production identifiers; least-privilege Apple roles and managed key custody.
- **Reversible seam:** build configuration; changing production bundle identity after distribution is costly.
- **Approver:** product/brand and Apple account owner.

### O-009 — Real connector and consequential-action policy

- **Decision needed:** which action kinds/tools may perform external writes, per-store policy, approver roles, rollback/verification, audit/incident handling.
- **Why now:** approval UI alone cannot make an unclassified or non-idempotent external action safe.
- **Options:** remain read/draft-only; enable individually reviewed action kinds for named stores; enable broader managed action catalog after connector-specific controls.
- **Default recommendation:** no real writes until each typed action has separate policy, exact preview, approval, idempotency/outcome resolution, and recovery test.
- **Reversible seam:** `atlas.managed-action.v1`, per-tool projections, capability/policy versions.
- **Approver:** product/security owners and the connected-system business owner.

### O-010 — Analytics, crash reporting, and support data

- **Decision needed:** vendors, event allowlist, consent/disclosure, residency/retention, support access.
- **Why now:** telemetry SDKs can become an uncontrolled transcript/identifier exfiltration path and affect App Store privacy disclosures.
- **Options:** content-free first-party diagnostics only; add reviewed crash reporting; add allowlisted product analytics; no telemetry beyond server operational logs.
- **Default recommendation:** no third-party analytics in M03; content-free first-party diagnostics. Add crash reporting only after payload filtering/privacy review.
- **Reversible seam:** `DiagnosticsRecording` interface and explicit event schema.
- **Approver:** product/privacy/security owners.

### O-011 — Hardware-backed phone proof migration

- **Decision needed:** add P-256 Secure Enclave proof or formally accept Keychain-protected Ed25519.
- **Why now:** current Ed25519 interoperability is useful but cannot support a true hardware-backed-key claim on iPhone.
- **Options:** versioned P-256 Secure Enclave migration; retain Keychain Ed25519 with App Attest/risk controls; support both by device/risk tier.
- **Default recommendation:** add versioned P-256 before production and rotate enrolled phones; keep Ed25519 for compatible workers.
- **Reversible seam:** algorithm field, public-key registry, proof-version negotiation, rotation.
- **Approver:** security/identity/backend owners.

### O-012 — Production minimum OS and device support

- **Decision needed:** confirm iOS 18 floor and supported device classes using expected audience data.
- **Why now:** it controls API availability, accessibility/visual parity, QA matrix, support cost, and reachable pilot devices.
- **Options:** iOS 18+ broad modern baseline; iOS 26+ current-design baseline; staged internal iOS 26 build followed by iOS 18-compatible production build.
- **Default recommendation:** retain iOS 18 unless product analytics/support costs justify iOS 26-only.
- **Reversible seam:** conditional materials and platform abstractions.
- **Approver:** product and mobile release owner.

## Decision protocol

An owner decision records date, approver, rationale, chosen option, affected environments/data, rollout/rollback, contract or migration impact, and evidence. Production-affecting open decisions cannot be closed implicitly by a code merge or a local M03 fixture.
