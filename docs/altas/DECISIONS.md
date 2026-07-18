# Atlas Decision Log

## How to use this file

This log distinguishes confirmed direction from proposals and open questions.
An agent may implement a confirmed decision. It may research or prototype an
open decision only when its work remains reversible and does not silently pick
the answer for Omar and Joe.

## Confirmed decisions

### ADR-001 — Preserve the upstream engine

- Status: accepted
- Decision: Atlas uses the Hermes Agent engine as its maintained execution
  baseline and keeps unavoidable internal compatibility names where a broad
  rename would add regression or update risk.
- Consequence: customer-visible branding must be Atlas, while internal names
  are changed only through deliberate migrations.

### ADR-002 — Direct Atlas entry point

- Status: accepted
- Decision: `atlas` enters the agent directly. A forwarding bridge that launches
  a separate Hermes process is not part of the architecture.
- Consequence: CLI and Desktop should share the same engine, state, skills,
  sessions, and configuration contracts.

### ADR-003 — Local execution, cloud control

- Status: accepted
- Decision: store-scoped workers and persistent Tekion browser profiles run on
  dedicated local machines for the initial product. The online Atlas Control
  Plane owns identity, subscriptions, entitlements, policy, jobs, usage, audit,
  model routing, and revocation.
- Consequence: the local worker never becomes the source of commercial truth.

### ADR-004 — Desktop setup parity before managed defaults

- Status: accepted on 2026-07-13
- Decision: the next Atlas Desktop setup must expose the same modes, sections,
  options, and behavior as the complete terminal `atlas setup` flow. Keep Quick
  Setup, Full Setup, Blank Slate, and every section-specific configuration
  choice. Present them with polished Atlas UI and the established dark-blue
  workstation aesthetic.
- Consequence: proposed managed defaults may be shown as recommendations, but
  options cannot be hidden or removed until a later explicit decision.

### ADR-005 — One underlying setup implementation

- Status: accepted
- Decision: terminal and desktop setup are separate presentation surfaces over
  shared setup/configuration services and validation. Desktop must not create a
  second source of defaults or independent secret-writing behavior.
- Consequence: setup parity requires a typed backend contract rather than
  copying terminal prompt logic into React.

### ADR-006 — Per-store browser and credential isolation

- Status: accepted
- Decision: each store or credential boundary receives its own persistent
  browser profile and worker context. It never shares cookies, local storage,
  downloads, or credentials with another store or the employee's daily browser.
- Consequence: multi-store convenience cannot weaken the isolation boundary.

### ADR-007 — Skills are governed product assets

- Status: accepted
- Decision: Atlas Core and Jay Premium skills are versioned, indexed, signed,
  entitlement-controlled, staged, observable, and reversible. Skill usage is
  retrieval/product evidence, never authorization.
- Consequence: proprietary skill contents remain private and separate from
  store-authored knowledge.

### ADR-008 — Deterministic authorization outside the model

- Status: accepted
- Decision: prompts, memories, and skills guide behavior but cannot authorize an
  action. Managed capabilities require live deterministic policy, store scope,
  device identity, entitlement, workflow context, and approval when applicable.
- Consequence: policy failure denies sensitive work.

### ADR-009 — Honest prototype boundary

- Status: accepted
- Decision: the current repository is for development and design-partner
  validation. It must not claim production readiness for live Tekion admin
  credentials, production dealership data, billing, hosted identity, or signed
  deployment until the corresponding launch gates are met.

## Proposed future direction, not current setup behavior

- A simplified dealership setup that hides infrastructure choices and speaks in
  Atlas capabilities.
- Atlas-owned model routing through stable aliases such as `atlas-balanced`.
- Desktop, Slack, and email as the first managed delivery surfaces.
- Read/navigation/draft actions allowed first; submits, exports, messages, and
  mutations approval-gated.
- A mobile companion app (phone → Control Plane relay → worker) for talking to
  Atlas remotely, text and voice. Captured July 18, 2026; blocked by WS-05 and
  the Phase 1 Control Plane. See [`MOBILE_COMPANION.md`](MOBILE_COMPANION.md).

These are preserved in [`SETUP_DEFAULTS.md`](SETUP_DEFAULTS.md) for the future
decision session. They do not override ADR-004.

## Open decisions requiring Omar and Joe

1. Which setup choices become Atlas-managed defaults after desktop parity is
   complete?
2. Which communications channels ship at pilot, launch, and later stages?
3. Does Atlas supply dedicated hardware or certify customer-owned machines?
4. Which identity provider, billing platform, transactional email provider,
   and observability stack are selected?
5. Which model providers and fallback/data-handling classes back Atlas plans?
6. What exact plan, rooftop, workflow-pack, and usage entitlement model is sold?
7. Which Tekion workflows may eventually progress from read-only or per-action
   approval to narrowly scoped automatic writes?
8. What data retention, customer export, support-access, and incident-response
   promises are contractual?
9. How are Atlas Core, Jay Premium, and dealer-authored knowledge packaged and
   priced?

## Decision protocol

When a workstream reaches an open decision, its handoff must record:

- the user/business decision in plain language;
- viable options and meaningful tradeoffs;
- security, privacy, operational, and cost implications;
- the recommendation, clearly labeled as a recommendation;
- the latest reversible point in the implementation;
- what can safely continue before the decision is made.
