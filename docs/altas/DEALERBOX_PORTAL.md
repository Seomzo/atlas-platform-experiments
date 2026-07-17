# DealerBox Portal — Customer-Facing Web Plan

Status: planning document, created 2026-07-16 from Omar's direction (see
`docs/altas/brand/BRAND.md`). Captures the decided direction plus open
decisions. Product name ("Atlas") is interim pending Omar + Joe (+ Ashley).

## 1. What this is

The DealerBox Portal is the **customer-facing web presence**: marketing site,
account login/authentication, subscription management, and the onboarding
walkthrough that connects a purchased Atlas worker to a customer account. It
fills the product slot vacated by stripping the Nous Portal from Atlas builds.

It is **not** the same thing as two adjacent surfaces:

| Surface | Audience | Owner workstream |
| --- | --- | --- |
| **DealerBox Portal** (this doc) | Dealership buyers/admins, pre- and post-sale | new (WS-13 proposed below) |
| **Atlas Control Center** (internal ops) | DealerBox operators | WS-06 spec |
| **Dealer Admin Portal** (tenant admin console) | Dealership admins managing devices/agents/usage | WS-06 spec |

The likely production shape: the DealerBox Portal is the front door
(marketing + auth + onboarding + billing), and the Dealer Admin Portal from
WS-06 becomes the logged-in surface it hands off to. Whether they share a
shell is a WS-06 decision — this doc only requires that login on the
DealerBox Portal is the single customer identity entry point.

## 2. Pieces

### 2.1 Marketing site

- Positioning: **"AI workers, not agents"** — workers do real dealership
  work: Tekion operations, employee stat tracking, emails, recurring reports.
  First product: Atlas for Fixed Ops.
- Explains the DealerBox model: each worker ships on its own box (likely
  Mac Mini), runs locally in the store, controlled from the cloud
  ("Local execution. Cloud control.").
- Brand: interim mark = concept 003 "Worker Badge"; dark-navy workstation
  palette (`docs/altas/brand/BRAND.md`). Titan Ink (001) is candidate hero
  artwork for the site.
- Content sections (minimum viable): what an Atlas worker is / why not "an
  AI agent", Fixed Ops workflows it performs, security model
  (read-only-first Tekion access, deterministic authorization, per-store
  isolation), pricing/plans, demo request, login.

### 2.2 Authentication / accounts

- Single customer identity for everything: portal login, desktop-app device
  enrollment, admin portal.
- Backed by the Atlas Control Plane account model. **WS-05 owns the
  provider-neutral account + device-enrollment contract** — the portal is a
  consumer of that contract, not a second identity system.
- Identity vendor (Auth0/Clerk/Cognito/etc.) is an **open decision**
  (Omar + Joe, DECISIONS.md open item #4). Portal work must stay
  provider-neutral behind the WS-05 contract until it's made.
- Native clients (desktop app) never embed reusable secrets; the portal is
  where the browser-side of the OIDC/device-enrollment dance happens.

### 2.3 Onboarding walkthrough (the Nous-portal-style one-shot)

Model: the upstream `hermes portal` one-shot flow
(`hermes_cli/setup.py::_run_portal_one_shot`), which takes a brand-new user
from zero to a fully working session in one command: sign-up URL → OAuth
device-code login → model selection → provider configured → hosted tool
routing opt-in → "you're done, start chatting."

DealerBox equivalent — one continuous walkthrough from purchase to working
worker:

1. **Account** — create/sign in to the DealerBox account on the portal
   (dealership org, role derived from membership — WS-05).
2. **Store** — pick/confirm the store (tenant/store scoping from the
   control plane).
3. **Connect the box** — enroll the shipped device: portal displays a
   short-lived one-time enrollment code/link; the Atlas desktop app on the
   box redeems it (WS-05's enrollment transaction). This replaces "OAuth
   device-code login" from the Nous flow.
4. **Intelligence** — model routing comes from the Control Plane
   entitlements (Atlas-managed aliases like `atlas-balanced`, per
   SETUP_DEFAULTS.md future direction) — customers never pick raw
   providers/API keys in the managed flow.
5. **Tekion access** — guided read-only Tekion connection with per-store
   browser profile isolation (ADR-006; WS-07).
6. **Permissions & delivery** — approval policy defaults + where reports/
   messages get delivered.

Note the intentional rhyme with the desktop onboarding blueprint's stages
01–06 (Account, Store, Intelligence, Tekion access, Permissions, Delivery —
`apps/desktop/.../setup-blueprint.tsx`). The portal walkthrough and desktop
setup should present the **same conceptual steps**, with the portal owning
account/store/billing and the device owning local execution details.
Desktop parity work (WS-00..WS-04) remains the prerequisite for the device
side of this flow.

### 2.4 Subscription / billing surface

- Plan management lives on the portal (the Nous "manage-subscription" slot).
- Billing platform is an open decision; the **billing-to-entitlement
  boundary is WS-10** — the portal displays and initiates, the control
  plane owns entitlement truth (ADR-003: the local worker is never the
  source of commercial truth).

## 3. Relationship to the Nous Portal strip

Stripping and replacing are one product motion, two engineering motions:

1. **Strip (now, in-repo):** remove/gate every customer-visible Nous Portal
   path in Atlas builds — the `portal` subcommand, Quick Setup option #1,
   `atlas status` portal lines, `atlas login` defaults, `atlas debug share`
   upload target. The complete file:line hit list is
   `docs/altas/rebrand/INVENTORY_CLI.md` §2 and `INVENTORY_DESKTOP.md`.
2. **Replace (new build):** the DealerBox Portal fills the slot. Until it
   exists, Atlas builds simply have **no** portal path — setup is
   provider-credential-based (current desktop overlay / `atlas setup`).
   Do not point stripped surfaces at a placeholder URL.

## 4. Proposed workstream: WS-13 — DealerBox Portal

Not yet in AGENT_WORKSTREAMS.md; proposed scope split (needs Omar sign-off):

- **WS-13a — Marketing site**: static/SSG site, brand-correct, no auth
  dependency; can start as soon as name/logo are settled enough (interim:
  Atlas + concept 003).
- **WS-13b — Portal auth + onboarding walkthrough**: depends on WS-05
  (account/enrollment contract) and the identity-vendor decision;
  UI can be prototyped against WS-05's development adapter.
- **WS-13c — Billing/plan pages**: depends on WS-10 boundary + billing
  vendor decision.

Sequencing reality: WS-00 (desktop setup parity) is still first in the
implementation queue; WS-13a is the only portal piece with zero upstream
dependencies and can run in parallel as a design/content effort.

## 5. Open decisions this plan waits on

1. Product name (Omar + Joe + possibly Ashley) — blocks final domain,
   wordmark, site copy.
2. Identity provider (DECISIONS.md open #4) — blocks WS-13b production auth.
3. Billing platform (DECISIONS.md open #4/#6) — blocks WS-13c.
4. Portal ↔ Dealer Admin Portal shell relationship — WS-06 spec output.
5. Domain/hosting for the portal (not yet discussed).
