# Altas

**Managed AI workers for dealership fixed operations.**

Altas turns the open-source Hermes Agent engine into a controlled, observable,
subscription-ready worker platform. The prototype in this repository proves
the business-critical path: a local worker enrolls with the Altas Control
Plane, receives a short-lived lease, is authorized against store and tool
entitlements, runs a fixed-ops workflow, routes model use through an Altas
gateway, and reports results and audit events back to an operator console.

> Status: local prototype for development and design-partner testing. It is
> not yet approved for production dealership data or live Tekion credentials.

## The product boundary

Altas follows one rule:

> **Local execution. Cloud control.**

The local engine can reason and execute approved workflows. The Altas Control
Plane remains the source of truth for device identity, subscriptions, store
access, tool permissions, model usage, jobs, audit history, support actions,
and revocation.

```text
Operator / Slack / Web
          │
          ▼
   Altas Control Plane
   identity · leases · policy · jobs · model gateway · audit
          │
          ▼
     Altas Worker
   managed context · named workflows · connector boundary
          │
          ▼
  Tekion connector boundary
```

Hermes Agent is the replaceable engine underneath Altas—not the commercial
product or the security boundary. Product code is isolated under the
`altas/` namespace, and unavoidable upstream integration patches are kept
small and documented.

## Prototype capabilities

- Real SQLite-backed tenants, stores, subscriptions, devices, agents,
  entitlements, jobs, usage records, and audit events.
- Hashed device credentials and short-lived signed worker leases.
- Deterministic, fail-closed policy checks outside the language model.
- Remote device disable and subscription/store/tool enforcement.
- Job-bound capabilities, one active claim per device, stale-claim recovery,
  and atomic per-job model-request limits.
- Claim-attempt-bound policy/model calls plus strict, size-bounded model
  request schemas that reject cost-amplifying provider extensions.
- An OpenAI-compatible model-gateway surface with a no-key deterministic demo
  provider.
- A fixture-backed fixed-ops report workflow that proves orchestration without
  claiming a live Tekion integration.
- A polished local Altas Control Center for fleet, job, usage, and audit
  visibility.
- A managed Hermes dispatch guard that denies tool execution when policy
  cannot be verified.
- A public `altas` CLI while upstream internal module names remain intact for
  maintainability.

## Quick start

Prerequisites:

- Python 3.11–3.13
- macOS or Linux for the tested quick start
- Windows users should use WSL2 for this prototype; native Windows packaging
  and credential storage are not yet validated
- No model-provider, Tekion, Slack, or Stripe account is required for the demo

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-altas-dev.txt
make altas-dev
```

Then open [http://127.0.0.1:8787](http://127.0.0.1:8787).

In another terminal, run the seeded worker:

```bash
source .venv/bin/activate
make altas-worker
```

Queue a fixed-ops report from Control Center and watch the worker claim,
authorize, execute, and report it. The exact demo identities and expected
results are documented in [docs/altas/TESTING.md](docs/altas/TESTING.md).

## Repository map

```text
altas-platform/
├── altas/                         # Altas-owned Python product code
│   ├── control_plane/             # API, persistence, policy, gateway
│   ├── credentials/               # opaque local credential-vault boundary
│   ├── fixed_ops/                 # named workflows + Tekion mock adapter
│   └── managed/                   # worker, client, runtime context, guard
├── plugins/model-providers/altas/ # Hermes → Altas gateway adapter
├── tests/altas/                   # product, security, and integration tests
├── docs/altas/                    # product and engineering source of truth
├── apps/desktop/                  # upstream desktop foundation; rebrand later
├── agent/, gateway/, tools/       # upstream Hermes engine internals
└── THIRD_PARTY_NOTICES.md         # required attribution and provenance
```

## Engineering principles

1. Prompts are behavior guidance, never authorization.
2. A customer-controlled device is not trusted.
3. Tenant and store context is mandatory for every meaningful action.
4. Policy failures deny work; they do not silently fall back.
5. Provider and app-level secrets stay server-side whenever technically
   possible.
6. Dealer credentials are exposed only to trusted connector code, never to the
   model, audit metadata, or tool results.
7. Production workers receive named business capabilities, not unrestricted
   shell, filesystem, or browser tools.
8. Production deployments use one isolated engine process/profile per
   credential boundary.
9. Upstream engine updates are pinned, reviewed, tested, and rolled out by
   Altas—not fetched autonomously on customer devices.
10. The demo must be honest about every mocked boundary.

## Documentation

- [Product definition](PRODUCT.md)
- [System architecture](docs/altas/ARCHITECTURE.md)
- [Security and trust model](docs/altas/SECURITY.md)
- [Prototype acceptance criteria](docs/altas/MVP_ACCEPTANCE.md)
- [Local testing guide](docs/altas/TESTING.md)
- [Roadmap](docs/altas/ROADMAP.md)
- [Upstream baseline and patch policy](docs/altas/UPSTREAM.md)

## Upstream and license

This repository began from
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
Hermes Agent is MIT licensed. Its original license remains in
[LICENSE](LICENSE), and Altas-specific provenance is recorded in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Altas is an independent product and does not imply endorsement by Nous
Research. Tekion is not bundled with or endorsed by this prototype.
