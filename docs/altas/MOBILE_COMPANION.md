# Atlas Mobile Companion (future plan)

Status: **future direction, not scheduled**. Captured July 18, 2026 from
Omar's product direction. No workstream, branch, or vendor decisions exist
yet. Nothing here overrides the active milestone (WS-00 desktop setup
parity) or the roadmap phases.

## 1. Product intent (Omar, July 18, 2026)

A phone app companion to Atlas Desktop: the dealership user (initially
Omar; later customers) can talk to their Atlas worker directly from their
phone — text and voice — the way ChatGPT/Codex and Claude connect to a
user's computer remotely. The DealerBox stays the executor; the phone is a
remote conversation surface.

## 2. Why the existing architecture already carries this

- **The engine is multi-surface by design.** The same agent core serves
  CLI, desktop, and every `gateway/platforms/` adapter. A companion app is
  one more conversation surface addressing an existing session store —
  not a second agent implementation.
- **The Control Plane is the natural relay.** ADR core principle: local
  execution, cloud control. The worker already holds (Phase 1) a
  persistent authenticated channel to the Control Plane for heartbeat,
  leases, policy, and jobs. Phone traffic rides that same channel in
  reverse: app → Control Plane → worker. The box is never exposed to
  inbound internet, per-store isolation is preserved, and revocation is
  the Control Plane's existing job.
- **Voice is an existing engine capability.** STT/TTS are already wired
  into the core; mobile voice chat is client UX plus streaming transport,
  not new agent machinery.

## 3. Non-negotiable constraints (inherited, not new)

- Authorization stays deterministic code in the Control Plane. The phone
  surface gets **no** new permission path; approvals and action gating
  behave exactly as they do on desktop (managed action policy, WS-09).
- Phone enrollment is a device-identity problem and therefore **depends on
  WS-05** (account and device-enrollment contract). Which phone may reach
  which box, as which user, with which entitlements — that is WS-05's
  contract, extended with a second device class. Building the app before
  that contract exists means building it twice.
- Customer data in transit phone↔plane↔worker follows SECURITY.md; no
  transcript persistence in the plane beyond what job/audit policy already
  allows.

## 4. Rough shape (to be validated when scheduled)

1. **Relay contract** — Control Plane gains a conversation-relay surface
   (WebSocket or push+poll) that pairs an enrolled phone with an enrolled
   worker. Reuses WS-05 identity; new scope, no new trust model.
2. **Worker side** — a gateway adapter (`gateway/platforms/`-style) that
   speaks to the Control Plane relay instead of a third-party chat vendor.
   Session keys, queuing, and approval interception behave like existing
   adapters.
3. **App** — thin client: chat transcript, voice (push-to-talk first),
   approval prompts, job/report notifications. No local agent, no
   credentials to dealership systems on the phone, ever.
4. **Later** — report cards/push digests (pairs with WS-11 messaging
   decisions), multi-store switcher for group managers.

## 5. Why it matters commercially (not just convenience)

- "Ask your worker from anywhere" is a strong demo for dealership
  higher-ups — a service manager checking shop performance from home is
  the product thesis (workers, not chatbots) made tangible.
- It reinforces the box: the phone is a window to *your* DealerBox, not a
  cloud chatbot. Differentiates from generic AI apps.

## 6. Sequencing

- **Blocked by:** WS-05 (device/identity contract), Control Plane walking
  skeleton (Phase 1), name decision (app-store listing needs the final
  brand).
- **Pairs with:** WS-11 (delivery channels — the app may absorb some
  notification use cases), WS-06 (Control Center spec — shared plane API
  conventions).
- **Earliest sensible slot:** after Phase 1 exists and WS-05 has a
  contract; realistically a Phase 3+ (design-partner era) deliverable.
  Revisit when WS-05 lands.

## 7. Open decisions for Omar (and Joe)

1. iOS-first, Android-first, or cross-platform (React Native/Flutter/
   native)? DealerBox ships Mac Minis — an Apple-leaning fleet suggests
   iOS-first, but customer phones are mixed.
2. Pilot audience: Omar-only internal tool first, or designed for
   customers from day one?
3. Voice: push-to-talk vs. continuous conversation; on-device vs.
   engine-side STT.
4. Does the app also carry approvals/alerts (a pocket Control Center
   lite), or stay purely conversational at v1?
