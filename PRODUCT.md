# Atlas Managed AI Worker Platform

## Product definition

Atlas is a managed AI worker platform for dealership fixed-operations teams.
It gives a dealership a worker that can analyze operational data, run approved
browser and API workflows, generate recurring reports, and communicate with
managers—while Atlas retains operational control over licensing, store access,
model usage, updates, support, and revocation.

Atlas is not a generic chatbot, a prompt bundle, or a raw copy of an agent
repository. Hermes Agent supplies a capable reasoning and tool-execution
engine. Atlas is the commercial system around that engine:

- A managed worker runtime on an isolated local or hosted machine.
- A cloud control plane that owns identity, policy, entitlements, jobs, model
  routing, usage, billing state, audit, and support actions.
- A setup and status application that hides developer tooling from customers.
- Versioned dealership workflow packs, beginning with fixed operations.
- A support console for operating a fleet of workers across customers.

The first product is **Atlas for Fixed Ops**.

## Why it exists

Dealership teams do valuable work across systems that were not designed for
autonomous software: browser sessions, store-specific credentials, reports,
messages, and dealership-management APIs. A useful worker must operate close
to those systems and remember the dealership's workflow. A safe commercial
product must also assume that:

- The customer controls the local computer.
- A language model can be prompted incorrectly or adversarially.
- Browser sessions and provider APIs will fail.
- One dealership group may contain multiple stores with different access.
- A technically capable customer could inspect or modify local files.
- Support, updates, auditability, and billing enforcement are part of the
  product—not back-office afterthoughts.

Atlas addresses those realities by separating execution from authority.

## Core product principle

> **Local execution. Cloud control.**

The local worker may execute an approved browser or connector workflow. It
does not decide whether the customer paid for the workflow, whether a store is
entitled, whether a tool is permitted, which model provider key to use, or
whether the device has been revoked.

The language model proposes actions. Deterministic application code authorizes
and executes them.

## Product promise

For a dealership, Atlas should feel like:

> “Our fixed-ops worker runs the right reports and follow-up every day, and we
> can see what it did.”

For the Atlas operator, the platform should provide:

> “We can identify, authorize, meter, update, support, and disable every worker,
> store, capability, and model request.”

## Initial users

### Fixed-ops manager

Needs a concise daily view of advisor, repair-order, labor, parts, and exception
performance without manually assembling data. Interacts through a business
channel such as Slack, email, or the Atlas web experience.

### Dealer-group administrator

Authorizes stores, sees which workers and integrations are active, manages
users, and understands the subscription and usage.

### Atlas fleet operator

Monitors health, failures, versions, model cost, policy denials, and connector
status across all tenants. Can perform safe support actions with a complete
audit trail.

### Atlas workflow engineer

Builds and tests named, versioned workflows. Does not grant those workflows
authority; the control plane and entitlement system do.

## First high-value workflows

The first workflow pack is intentionally narrow:

- Daily fixed-ops performance report
- Advisor performance summary
- Repair-order exception report
- Parts and labor summary
- Manager update draft
- Store comparison for entitled rooftops
- Scheduled report delivery

The prototype uses sanitized fixture data. Live Tekion API and browser
connectors remain an explicit integration boundary until legal, technical, and
security requirements are verified with the real partner environment.

## System components

### Atlas Control Plane

The Control Plane is the source of truth for:

- Tenants and dealer groups
- Stores and store assignments
- Users and roles
- Devices and workers
- Agents and workflow packs
- Subscription state
- Store and capability entitlements
- Short-lived worker leases
- Job dispatch and state
- Policy decisions
- Model routing and usage
- Audit events
- Support and disable actions
- Release eligibility

No prompt or editable local configuration can create a paid entitlement.

### Atlas Worker

The worker is an outbound-connected supervisor that:

- Holds a device identity in OS-protected credential storage.
- Sends health heartbeats.
- Receives a short-lived lease and scoped configuration.
- Claims jobs for its assigned store.
- Requests authorization for every meaningful action.
- Runs only named workflows in managed mode.
- Reports safe results, usage, diagnostics, and audit context.
- Stops starting paid work when its lease or authorization is unavailable.
- Supervises one isolated engine profile per credential boundary.

The worker is replaceable and revocable. It is not the database of record for
subscriptions or permissions.

### Hermes engine adapter

Hermes Agent supplies the reasoning loop, memory primitives, skill system,
gateway adapters, browser tooling, and desktop foundation. Atlas integrates at
the smallest stable seams:

- OpenAI-compatible model-provider adapter to the Atlas gateway
- Mandatory managed-mode guard at central tool dispatch
- Isolated engine home/profile
- Curated toolsets and workflow packs
- Structured lifecycle and usage events

Internal upstream namespaces remain intact in the first product slice. A
global source rename would add regression and update risk without improving
customer-facing branding or security.

### Atlas Model Gateway

Production workers never receive Atlas-owned model-provider master keys.
Workers call an Atlas OpenAI-compatible endpoint with a scoped worker
credential. The gateway:

- Revalidates the device and lease.
- Enforces plan and usage policy.
- Selects provider and model.
- Applies request limits.
- Records usage and estimated cost.
- Supports provider fallback without a customer-device update.
- Redacts provider credentials from every response and log.

The prototype ships with a deterministic mock provider so the entire system
can be tested without an external account.

### Credential vault

Credentials are represented by opaque handles. Trusted connector code resolves
a handle immediately before use. Raw values are never placed in:

- Prompts
- Model-visible tool arguments
- Workflow results
- Audit metadata
- Browser-visible status pages
- Standard logs

The prototype defines the vault contract and a Python `keyring` adapter that
uses macOS Keychain in the validated development environment. The walking-
skeleton worker still receives its demo device bearer through an environment
variable; wiring device identity and future dealer credentials into the vault
is a production milestone. Windows and Linux backends must be validated before
those platforms can handle real dealer credentials.

### Tekion connector boundary

The intended authorization model contains two separate concepts:

1. Atlas/Tekion app-level identity controlled by the vendor.
2. Dealer/store-level authorization controlled by the dealership.

Tekion authorization does not create an Atlas commercial entitlement. A dealer
may authorize multiple stores, while the Atlas subscription authorizes only a
subset.

Each store receives an isolated connector and browser profile. Production
starts read-only. Write-back workflows require separate scopes, approval,
idempotency, and rollback design.

### Atlas Control Center

The customer-facing status view and internal fleet console eventually become
separate permissioned applications. The prototype uses one localhost-only
operator console with a permanent development-authentication warning.

The first console covers:

- Fleet overview
- Device health and remote enable/disable
- Store and entitlement status
- Job queue and results
- Model usage
- Policy and audit history

### Desktop wrapper

The upstream Electron application is the foundation for Atlas Desktop. Its
product role is setup and supervision, not security policy. The commercial
wrapper will eventually handle:

- Sign-in and device enrollment
- Dealership and store selection
- OS credential-vault access
- Worker installation and boot registration
- Browser dependency and profile setup
- Connector authorization
- Channel connection
- Health and support status
- Signed Atlas updates

The prototype prioritizes the control-plane/worker contract before packaging
the desktop installer.

## Identity and scoping

Every meaningful operation carries:

- `tenant_id`
- `store_id`
- `device_id`
- `agent_id`
- `job_id`
- requesting user or system actor when applicable

The server derives tenant identity from verified authentication. It never
trusts a caller-supplied tenant identifier. Store and agent relationships are
checked against that tenant before work begins. In the prototype, `job_id` is
also the correlation key shared by policy, usage, completion, and audit events;
a separate cross-service trace identifier is a production milestone.

## Policy model

Policy is deterministic code. A managed action is allowed only when all
required checks pass:

1. Device credential is valid.
2. Device is active.
3. Worker lease is valid and unexpired.
4. Subscription is usable.
5. Store belongs to the authenticated tenant.
6. Device and agent are assigned to the store.
7. Store entitlement is active.
8. Capability or tool is included.
9. Usage budget is available.
10. Human approval exists when required.

The system returns stable reason codes and audits both allows and denials.
Policy service errors produce `POLICY_UNAVAILABLE` and deny the action.

## Deployment modes

### Cloud-hosted Atlas

Runs on Atlas-controlled infrastructure. This should become the preferred
premium deployment because it improves monitoring, update control, isolation,
and support. Browser authentication, MFA, network allowlists, and per-tenant
isolation must be validated before broad availability.

### Managed local Atlas

Runs on a dedicated dealership or Atlas-provided machine and connects outbound
to the Control Plane. This is the primary design-partner deployment because it
matches browser-heavy workflows while retaining central policy and support.

### Customer-owned device

Useful for trials and low-risk workflows. It is the lowest-trust option. The
platform assumes a local administrator can inspect or modify software. Atlas
protects its own cloud resources and business services; it does not claim to
control the customer's machine.

## Subscription model

Marketing plans map directly to runtime entitlements:

- Active stores
- Active workers
- Enabled workflow packs
- Browser and connector capabilities
- Monthly model budget
- Support tier
- Data-retention policy
- Hosted or local deployment
- Custom workflow access

A request to add a store creates an upgrade/onboarding flow. It cannot mutate
the entitlement database from a conversation.

Billing integration is deferred in the prototype. Seeded subscription records
exercise the same enforcement path that Stripe webhooks will drive later.

## Trial experience

The trial should prove value without distributing the full high-risk runtime.
Candidates include:

- A sample-data fixed-ops report
- Daily manager digest
- Email summary worker
- Slack Q&A against approved sample or uploaded data
- Limited read-only connector access after authorization

The trial does not enable unrestricted browser automation, shell access, or
cross-store exports.

## Reliability model

Atlas assumes models, browsers, APIs, networks, and local processes fail.
Reliability comes from:

- Idempotent jobs and explicit state transitions
- Short-lived leases
- Heartbeats and last-known health
- Structured errors and correlation IDs
- Retry policies at known safe boundaries
- Provider abstraction and fallback
- Per-store browser profile isolation
- Safe remote diagnostics
- Version pinning and staged updates
- Observable denials, not silent behavior changes

## Security posture

Prompting is not security. Tool toggles, skill scans, output redaction, and
approval heuristics are useful defense-in-depth, but they are not isolation
boundaries.

Workers handling untrusted input run in a whole-process sandbox or dedicated
machine identity. Generic shell, filesystem, browser, arbitrary plugin, and
self-modifying skill capability is disabled in managed production mode.
Trusted named workflows may use lower-level browser operations internally, but
the model cannot call those operations directly.

See [docs/altas/SECURITY.md](docs/altas/SECURITY.md) for the complete threat
model and non-claims.

## Product non-goals for the prototype

- Production Tekion connectivity
- Automatic write-back into dealership systems
- Stripe billing
- Public multi-tenant admin authentication
- Mobile application
- Slack or email delivery
- Signed desktop updates
- Remote shell
- Silent remote desktop access
- General-purpose customer coding agent
- Strong control over customer-owned dealer credentials outside Atlas
- Production compliance certification

These are roadmap items or explicit exclusions, not hidden mock behavior.

## Success criteria

The prototype is successful when:

1. A seeded worker authenticates and receives a short-lived lease.
2. An entitled Store A report is authorized and completed from fixture data.
3. An unentitled Store B request is denied before connector or model use.
4. The Control Center shows health, job, policy, usage, and audit state.
5. Disabling the device immediately prevents new jobs and model requests.
6. Expired or tampered leases fail closed.
7. Raw sentinel secrets never appear in API responses, logs, reports, prompts,
   or audit metadata.
8. Atlas product surfaces do not expose provider keys or editable production
   policy.

## Internal north star

Atlas should make a complex worker fleet feel simple to the dealership and
legible to the operator. The durable business is not ownership of one agent
loop. It is the managed system of permissions, workflows, integrations,
support, data boundaries, reliability, and continuous improvement around that
loop.
