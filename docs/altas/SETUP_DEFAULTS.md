# Atlas Setup Defaults

## Status

- Current implementation: full terminal-to-desktop setup parity
- Future product policy: proposed design-partner managed defaults
- Applies to: managed local Atlas worker and Atlas Desktop
- Last reviewed: 2026-07-13
- Source baseline: the current `atlas setup` full-configuration flow

This document has two deliberately separate layers:

1. The active development directive: reproduce the complete `atlas setup`
   terminal workflow in Atlas Desktop without removing choices or changing
   behavior.
2. The future managed-product proposal: decide which choices Atlas should own
   for dealerships after the team has reviewed every setup page.

The managed defaults below are not permission to hide or delete setup options
from the current desktop build. The **Implementation** column distinguishes
working behavior from launch work.

## Confirmed current directive: desktop parity first

Until Omar and Joe approve a narrower commercial setup, Atlas Desktop must:

- Offer the same first-run modes as the terminal wizard: Quick Setup, Full
  Setup, and Blank Slate.
- Offer every section available through `atlas setup`: Model & Provider,
  Text-to-Speech, Terminal Backend, Messaging Platforms, Tools, and Agent
  Settings.
- Preserve provider, model, terminal, gateway, tool, skill, plugin, MCP,
  memory, and agent choices exposed by the corresponding terminal flows.
- Show current values when reconfiguring and preserve the terminal behavior of
  keeping a value when the user does not change it.
- Support section-specific reconfiguration, missing-items-only setup, reset,
  cancellation, validation, summaries, and safe config backups.
- Use one setup/configuration implementation underneath both terminal and
  desktop surfaces. The desktop must not fork a second set of defaults or
  reimplement secret-writing rules independently.
- Present the workflow as polished Atlas UI using the established dark-blue
  workstation aesthetic, clear progress, explanations, validation, back/cancel
  behavior, and accessible controls.
- Keep the terminal setup supported as a reference and recovery path.

The desktop may explain which options are recommended, advanced, or intended
for engineering use, but it must not remove an option solely because a future
managed default is proposed below.

## Future commercial product rule

Dealership users should make dealership decisions, not infrastructure
decisions.

The customer-facing setup asks for only:

1. Who are you?
2. Which dealership and store is this workstation assigned to?
3. May Atlas connect to this store's Tekion environment?
4. Which actions may Atlas take without approval?
5. Where should Atlas deliver results?

Atlas owns model routing, terminal backends, browser engines, tool providers,
skill installation, service configuration, updates, and raw credentials. Those
settings remain visible to Atlas operators in diagnostics, but they are not
normal customer choices.

## Proposed future customer setup sequence

| Stage | Customer sees | Atlas default | Implementation |
| --- | --- | --- | --- |
| 1. Account | Sign in to Atlas | Atlas account with short-lived device enrollment | Control-plane contract exists; production identity UI remains |
| 2. Store | Organization and rooftop assignment | One worker and one isolated profile per store/credential boundary | Data model exists; enrollment UI remains |
| 3. Intelligence | “Atlas intelligence” and plan allowance, not a model vendor list | Atlas model gateway with a stable product alias and centrally managed fallback | Model gateway prototype exists; desktop currently exposes upstream providers |
| 4. Tekion | Connect, sign in, test access, and select permitted stores | Dedicated persistent Chromium profile; read-only first | CDP attachment exists; Atlas browser profile manager remains |
| 5. Permissions | Plain-language action policy | Read, navigate, analyze, and draft; ask before submit, export, message, or mutate | Policy engine prototype exists; workflow approval UI remains |
| 6. Delivery | Atlas Desktop, Slack, and email | Desktop on; Slack/email opt-in | Desktop works; managed Slack/email enrollment remains |
| 7. Review | Store, access, permissions, delivery, and health summary | Explicit confirmation before activation | First desktop blueprint implemented; complete setup parity remains |

## Proposed managed translation of the current full setup

### 1. Model and provider

**Current terminal flow**

The user chooses an inference provider, authentication method, model, and
possibly model-specific credentials. Nous Portal is offered as the quick path;
API-key and local OpenAI-compatible endpoints are also supported.

**Atlas managed default**

- Customer label: **Atlas intelligence**.
- Customer choice: plan/usage allowance only; no vendor or raw model picker.
- Runtime: Atlas-owned OpenAI-compatible model gateway.
- Model selection: a stable alias such as `atlas-balanced`, resolved centrally
  by task type, policy, health, cost, and contracted provider availability.
- Provider keys: server-side only. They are never copied to the dealership
  worker.
- Failure behavior: bounded provider fallback inside the same capability and
  data-handling class; never silently fall back to an unapproved provider.

**Prototype exception**

The current desktop provider picker remains a development bootstrap until Atlas
account enrollment can issue gateway credentials. Nous Portal and bring-your-
own-key providers are development/engineering options, not the commercial
customer experience.

**Business dependency**

Atlas, not the dealer, owns model-provider contracts and usage reconciliation.
This lets pricing map to rooftops, workflow packs, and included usage rather
than forcing each store to purchase third-party model accounts.

### 2. Terminal backend

**Current terminal flow**

Local, Docker, Modal, SSH, Daytona, and Linux Singularity/Apptainer are
available. Local is the current default.

**Atlas managed default**

- Design-partner deployment: local execution on a dedicated store workstation.
- Working directory: an Atlas-managed store workspace, not the employee's home
  directory.
- The model does not receive unrestricted shell access in managed mode.
- Signed workflow code may use narrowly scoped process execution behind the
  policy boundary.
- Container/cloud backends remain Atlas deployment choices, not dealer setup
  choices.

**Engineering-only alternatives**

- Docker/Singularity: isolation and repeatable support environments.
- Modal/Daytona: hosted-worker experiments.
- SSH: controlled lab and support scenarios.

**Launch gap**

The current upstream local terminal defaults to the user's home directory and
can expose generic terminal/file tools. Atlas must add the store workspace,
process isolation, and managed-tool allowlist before production.

### 3. Agent behavior

| Setting | Upstream default | Atlas design-partner default | Customer editable? |
| --- | --- | --- | --- |
| Maximum turns | 150 | 150 hard ceiling plus lower per-workflow budgets | No |
| Tool progress | `all` | Concise live activity in UI; complete structured audit log | Display preference only |
| Context compression | On at `0.50` | On at `0.50` | No |
| Session reset | Never | 12 hours idle or 4:00 a.m., whichever comes first | Admin policy later |
| Persistent memory | Available | On for approved dealership facts; never raw credentials | Admin can review/delete |
| Personality | Configurable | Atlas dealership operator profile | No in pilot |

Long-lived operational knowledge belongs in approved skills, indexed knowledge,
and structured memory—not in an indefinitely growing chat transcript.

### 4. Browser and Tekion access

**Atlas managed default**

- One dedicated Chromium user-data directory per store.
- Atlas launches that browser with a loopback-only Chrome DevTools endpoint and
  attaches the engine through the existing `browser.cdp_url` seam.
- The operator signs in to Tekion in the visible dedicated browser and handles
  MFA when required.
- Cookies and session state persist in the store profile. Atlas does not ask
  the model to type or remember a raw Tekion password.
- The profile is never the employee's everyday browser profile.
- Store profiles never share cookies, downloads, or local storage.
- The default permission posture is read-only. Submit/write actions require a
  named workflow, entitlement, audit context, and human approval until that
  workflow has separate production authorization.

**Fallbacks**

- Camofox with `browser.camofox.managed_persistence: true` is a supported local
  fallback when its persistent-profile behavior is verified.
- Browserbase, Browser Use, and Firecrawl browser sessions are hosted-worker or
  recovery options, not the local dealer default.

**Launch gap**

Atlas still needs a browser profile manager that creates, launches, health-
checks, repairs, reauthorizes, and deletes a store-scoped profile safely.

### 5. Tool policy

The terminal wizard exposes more than twenty toolsets. Atlas groups them by
product posture instead of asking the dealership to configure each provider.

#### Enabled for managed conversation

- Skills: only signed Atlas packs and store-approved local knowledge.
- Task planning.
- Clarifying questions.
- Session search within the same tenant/store and retention policy.
- Curated persistent memory with review/delete controls.
- Web search and extraction through an Atlas-managed provider when the plan and
  workflow permit it.
- Vision when required by an approved workflow.

#### Available only inside trusted workflows

- Tekion browser automation.
- Background computer use.
- Store-scoped file operations.
- Narrow code/process execution.
- Cron/scheduled work.
- Messaging delivery tools.

These capabilities can exist in the worker without being callable as generic
model tools. The workflow manifest, live entitlement, lease, store assignment,
and approval policy must all allow the action.

#### Off by default

- Generic terminal and unrestricted filesystem access.
- Image and video generation.
- Text-to-speech and automatic voice playback.
- X/Twitter search.
- Spotify, Home Assistant, Discord administration, and consumer messaging
  integrations.
- Arbitrary MCP servers, third-party plugins, and Skills Hub installs.
- Self-editing or unsigned skill creation in managed mode.
- General-purpose delegation to ungoverned agents.

### 6. Skill packs and knowledge

- **Atlas Core** ships with every worker: safe operating rules, Tekion
  navigation primitives, dealership vocabulary, support/diagnostics, and
  workflow-selection guidance.
- **Jay Premium** is a signed, versioned, entitlement-controlled pack built
  from the proven work agent's skills.
- The skill index stores identity, version, description, permissions, source,
  compatibility, use count, last-used time, and outcome telemetry.
- Skill-use counts help retrieval and product decisions; they never grant a
  permission by themselves.
- Updates are staged, signed, reversible, and assigned by tenant/store/channel.
- Dealer-authored knowledge is separate from executable skills and is scoped to
  the store unless explicitly shared within the dealer group.

### 7. Messaging and delivery

| Channel | Default | Product decision |
| --- | --- | --- |
| Atlas Desktop | On | Primary setup, health, approval, and chat surface |
| Slack | Opt-in | First managed collaboration integration |
| Email | Opt-in | Reports/digests; outbound recipients must be allowlisted |
| SMS | Off | Consider only for approved alerts and explicit consent |
| Telegram/Discord/WhatsApp/Signal/iMessage/etc. | Hidden | Upstream engine capability, not an Atlas launch surface |

If a channel is enabled, Atlas installs and supervises the background gateway
service automatically. Customers should see connection and health state, not
launchd/systemd/Scheduled Task choices.

### 8. Secrets

- Device identity and refresh material: macOS Keychain / Windows Credential
  Manager; short-lived access tokens in memory.
- Tekion cookies: dedicated browser profile protected by the OS account and
  store isolation; never copied into prompts or logs.
- Customer-provided connector secrets, if unavoidable: OS vault through a
  typed Atlas credential broker.
- Atlas model/provider master keys: control plane only.
- Local `.env`: development compatibility only, not the production secret
  store.
- Support bundles redact tokens, cookies, headers, customer data, and browser
  storage by default.

### 9. Service, updates, and diagnostics

- Start Atlas automatically after sign-in/reboot.
- Signed automatic updates with staged rollout and rollback.
- Heartbeat, browser health, connector health, skill-pack version, queue state,
  and last successful workflow visible in Desktop.
- Local detailed logs with bounded retention.
- Remote diagnostics require explicit consent and create an audit event.
- A failed policy/control-plane check fails closed for paid or sensitive work.

## Setup ownership matrix

| Decision | Owner | Customer UI |
| --- | --- | --- |
| Atlas identity, device, tenant, store | Atlas control plane | Required setup |
| Tekion dealer/store authorization | Dealership + Atlas connector | Required setup |
| Browser profile and engine | Atlas Desktop | Status and reauthorize only |
| Model vendors and routing | Atlas | Plan/usage only |
| Workflow/skill entitlement | Atlas subscription + dealer admin | Enabled capabilities |
| Action approvals | Dealer admin policy + acting user | Plain-language controls |
| Slack/email destinations | Dealer admin | Optional setup |
| Billing | Atlas billing system | Plan, invoices, usage |
| Raw tool/provider configuration | Atlas engineering/operations | Hidden diagnostics |

## Outside businesses and systems

| System | Role | Launch posture |
| --- | --- | --- |
| Tekion | System of record and browser/API target | Partnership and authorization path are launch-critical |
| Model providers | Inference supply | Atlas contracts and routes; at least one tested fallback |
| Stripe or equivalent | Subscription, invoices, entitlements | Server webhooks are authoritative; never mutate billing from chat |
| Slack | Collaboration/delivery | Optional design-partner channel |
| Transactional email provider | Reports, invites, alerts | Optional but likely required before broad launch |
| Apple/Microsoft OS vaults | Local secret protection | Required on every advertised desktop OS |
| Chromium | Visible persistent Tekion workstation | Bundled/managed version and profile lifecycle required |
| GitHub | Private source backup and engineering workflow | Internal only; not a runtime customer dependency |
| Nous Portal | Development bootstrap and business-model reference | Not an Atlas customer dependency |
| Langfuse/observability vendor | Model tracing if selected | Prefer Atlas-controlled/redacted telemetry; vendor decision remains |

## Decisions intentionally deferred

- Exact model vendors and the `atlas-balanced` routing table.
- Stripe versus another billing platform.
- Transactional email vendor.
- Managed observability vendor versus self-hosted stack.
- Whether Atlas supplies the dedicated Mac/PC or certifies customer hardware.
- When a proven Tekion write workflow can move from per-action approval to a
  narrower policy-based auto-approval.

These choices do not block the current desktop sequence because the UI speaks
in Atlas capabilities, not vendor names.
