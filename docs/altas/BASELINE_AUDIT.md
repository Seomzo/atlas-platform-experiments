# Atlas Dealership Baseline Audit

## Audit identity

- **Audit date:** 2026-07-25
- **Audited commit:** `7a4d7529dc9b5ef0361800d8d94f05e79d716269`
  (`origin/main` when the audit branch was created)
- **Audit branch:** `docs/baseline-audit-plan`
- **Scope:** repository evidence for the Atlas dealership harness, runtime, data
  model, integrations, deployment, tests, setup, observability, security
  boundaries, and failure modes
- **Out of scope:** feature implementation, live dealership data, live Tekion,
  outbound communications, and production deployment
- **Companion proposal:** [Two-Week Web Vertical-Slice Plan](TWO_WEEK_VERTICAL_SLICE_PLAN.md)

This audit intentionally does not depend on the unmerged agent-working-agreement
change. Draft PR #1 was at `c5b896f0eee73f6d1b6ac9e1ab4ea112a4aacb64`
when this branch was created.

## Evidence labels

- **Verified:** observed in source or reproduced locally at the audited commit.
- **Documented:** described as intended behavior, but not proven by an executable
  path at this commit.
- **Inference:** a conclusion drawn from verified evidence; it still needs product
  or production validation.
- **Open question:** requires a human decision or evidence outside this repository.

Primary repository sources:

- product and boundary intent: `PRODUCT.md`, `docs/altas/PROJECT_CONTEXT.md`,
  `docs/altas/ARCHITECTURE.md`, and `docs/altas/SECURITY.md`;
- executable Control Plane and worker: `altas/control_plane/`,
  `altas/managed/`, and `altas/fixed_ops/`;
- schema and persistence: `altas/control_plane/database.py` and
  `altas/control_plane/repository.py`;
- executable evidence: `tests/altas/`, `scripts/altas-smoke.py`, `Makefile`,
  and `.github/workflows/altas-ci.yml`;
- setup, acceptance, and history: `docs/altas/SETUP_DEFAULTS.md`,
  `docs/altas/TESTING.md`, `docs/altas/MVP_ACCEPTANCE.md`, and
  `docs/altas/HISTORY.md`.

## Executive finding

**Verified:** Atlas has a functioning local walking skeleton. A store-scoped device
can authenticate, obtain a capability lease, claim a job, execute the deterministic
`fixed_ops.daily_report` workflow against synthetic Tekion-shaped data, persist a
result, and emit correlated usage and audit records. The local Python suite,
fixed-ops smoke test, upstream dispatch regression set, package build, and packaged
CLI check passed.

**Verified:** this is not yet a production dealership web product. The browser
surface is a loopback operator Control Center protected by one admin bearer token.
The Control Plane has no dealership-user identity or role model, consent ledger,
human-approval workflow, or enforced retention policy for jobs, reports, usage, and
audit records. The Tekion adapter is a fixture reader, outbound delivery is absent,
SQLite is the only Control Plane store, and there is no Atlas-specific production
deployment or alerting configuration.

**Inference:** the safest next proof is a responsive, synthetic-only, read-only
fixed-ops manager report. It can validate the core daily workflow without exposing
customer data or creating a communication/writeback path. Real dealership data or
any outbound action remains blocked by the missing controls above.

## What actually runs

| Area | Status | Evidence and boundary |
| --- | --- | --- |
| Control Plane | **Verified local** | `altas/control_plane/app.py` serves health, operator, device, policy, job, usage, and audit routes. `make atlas-dev` returned the Control Center, rejected an unauthenticated admin request with `401`, and returned authenticated overview data. |
| Managed worker | **Verified local** | `altas/managed/worker.py` authenticated a configured device, claimed a scoped job, ran `fixed_ops.daily_report`, and completed it. |
| Fixed-ops workflow | **Verified synthetic** | `altas/fixed_ops/workflow.py` calculated deterministic KPIs and RO exceptions from `altas/fixed_ops/fixtures/tekion_service_snapshot.json`; the smoke result reported `$8,084` sales and three exceptions. |
| Policy boundary | **Verified local** | `altas/control_plane/policy.py` checked live device, tenant, store, agent, subscription, entitlement, and capability state. A Store B request was denied before connector or model work; disabling the device caused the worker to fail authentication. |
| Audit and usage | **Verified local** | The exercised job produced 11 audit records (8 allowed, 3 succeeded) and one usage record with the job identifier, token totals, requested limit, and zero mock cost. |
| Model gateway | **Verified mock; optional upstream path only** | `altas/control_plane/model_gateway.py` supports a deterministic mock and an OpenAI-compatible upstream with model allowlists and request limits. No production model deployment was exercised. |
| Credential abstraction | **Verified contract/local implementation** | `altas/credentials/` exposes opaque handles, an in-memory implementation, and a keyring adapter. Cross-platform vault behavior was not exercised. |
| Browser UI | **Verified operator prototype** | `altas/control_plane/static/` is a responsive static Control Center. It is not a dealership-manager application and has no user login or role-aware navigation. |
| Packaging | **Verified local** | A wheel and source distribution built; the wheel installed without dependencies into a clean temporary environment and its compatibility `altas --version` entry point started. |
| Production deployment | **Not present for Atlas** | Root Docker and Compose files package the upstream Hermes application. No Atlas Control Plane image, database migration service, production secret wiring, backup policy, or Atlas-specific Render/Vercel deployment was found in the audited paths. |

## Baseline by subsystem

### Harness and runtime

**Verified**

- `Makefile` provides `atlas-dev`, `atlas-worker`, `atlas-test`, `atlas-lint`,
  `atlas-smoke`, and `atlas-reset`.
- `scripts/altas-smoke.py` exercises device revocation, an unentitled store
  denial, a scheduled job, and the fixed-ops result.
- The worker validates tenant, store, agent, device, and capability scope before
  executing work. Claim tokens and short-lived signed leases bind authorization
  to the claimed job.
- Connector and model failures are reduced to bounded error types before
  persistence rather than storing raw exception text.
- The deterministic report workflow aggregates data before model use and does
  not send row-level customer data to the model gateway.

**Documented, not proven here**

- A cloud Control Plane plus dealership-local worker is the target production
  topology in `PRODUCT.md` and `docs/altas/ARCHITECTURE.md`.
- Scheduled daily delivery is a product goal. The exercised Atlas path requires
  job creation; no production scheduler-to-report delivery chain was found.

**Failure modes**

- Control Plane unavailability makes the policy guard deny work
  (`POLICY_UNAVAILABLE`), so managed work fails closed.
- Inactive devices, subscriptions, agents, stores, or entitlements deny work.
- Invalid, expired, or scope-mismatched leases and claim tokens deny or
  quarantine work.
- A worker crash can leave a claim until its visibility timeout; there is no
  production retry dashboard or alert proving recovery.
- SQLite has no demonstrated multi-instance concurrency, migration, backup, or
  restore operating model.

### Data model

**Verified:** `altas/control_plane/database.py` creates tenant, store,
subscription, device, agent, entitlement, job, Cortex dispatch-admission, usage,
and audit tables plus supporting indexes. Operational records carry tenant/store
scope where applicable.

**Verified gaps**

- No dealership users, identities, sessions, store memberships, or role grants.
- No persisted fixed-ops report or report-run resource separate from generic job
  output.
- No report schedule or report-specific idempotency resource beyond the generic
  job idempotency key.
- No consent purpose, subject, status, source, or revocation ledger.
- No human-approval request, decision, approver, expiry, or action binding.
- No Control Plane retention-policy table, `expires_at` contract, purge job, or
  deletion verification for job results, reports, usage, and audit records.

The Cortex subsystem has profile-local memory lifecycle and privacy behavior.
That is separate from, and does not satisfy, Control Plane retention for
dealership reports and operational ledgers.

### Integrations

| Integration | Verified state | Production gap |
| --- | --- | --- |
| Tekion | `altas/fixed_ops/tekion_mock.py` reads a synthetic fixture and requires no credentials. | No API authentication, tenant mapping, pagination, rate limiting, schema-drift handling, incremental sync, or data-processing approval. |
| Model provider | Deterministic mock works; an allowlisted OpenAI-compatible path exists. | No production provider, data-use agreement, residency decision, or live budget/latency evidence. |
| OS credential store | Opaque credential handles and a keyring adapter exist. | Windows/Linux behavior, installer integration, rotation, and recovery are unproven. |
| Customer or staff communication | No email, SMS, Slack, or DMS-writeback path was found in the Atlas slice. | Consent, approval, template, recipient, delivery, suppression, and audit controls must exist before one is added. |
| Billing | Subscription and entitlement state can gate capability use. | No Stripe or other commercial billing integration is implemented for Atlas. |

### Setup and developer experience

**Verified**

- A Python 3.11 virtual environment can install
  `requirements-atlas-dev.txt` plus the editable project.
- `make atlas-dev`, `make atlas-worker`, `make atlas-test`, and
  `make atlas-smoke` are usable local entry points.
- `atlas-control doctor` reported the local setup ready.
- `docs/altas/SETUP_DEFAULTS.md`, `docs/altas/TESTING.md`, and
  `docs/altas/SECURITY.md` describe the local flow and its boundaries.

**Baseline drift**

- `make atlas-lint` stops at Ruff format check because 15 tracked Python files
  need formatting. Ruff lint itself passes.
- The upstream desktop test suite is red in the local Node environment: 58 test
  files failed and 144 passed (151 failed, 1,189 passed). Frequent failures
  included a nonfunctional `window.localStorage.clear` test environment, a
  missing mocked export, and expectation drift. This is broader upstream
  Hermes debt, not evidence that the Atlas static Control Center failed.
- Historical Atlas documents still contain superseded persistent-agent
  assignments and Omar/Joe decision-owner language. Ethan explicitly set the
  durable product-decision owners to Ethan and Omar; draft PR #1 updates the
  root agreement, while older documents remain follow-up reconciliation work.

### Observability

**Verified**

- Health, heartbeat, job state, usage, and audit records expose the local
  operational path.
- Job identifiers correlate the exercised policy, lease, job, model, usage, and
  audit activity.
- Bounded error types reduce the chance of credentials or customer payloads
  leaking into logs and persisted failure detail.

**Gaps**

- No Atlas-specific metrics exporter, distributed traces, error aggregation,
  dashboards, service-level objectives, paging rules, or runbook-linked alerts
  were found.
- No alert proves a scheduled report was late, duplicated, incomplete, or
  unavailable to its intended manager.
- Admin views are operational lists, not a production incident or compliance
  console.

### Deployment

**Verified:** the audited Atlas flow is a local process using SQLite and a static
operator UI. Packaging proves the Python artifact can be built and started.

**Not verified:** production image, cloud environment, TLS termination, managed
database, migration/rollback, secrets service, backups/restores, multi-instance
claim behavior, disaster recovery, dependency provenance, vulnerability
remediation, or deployed runtime health.

The Atlas workflow file `.github/workflows/altas-ci.yml` defines Python
3.11-3.13 validation, packaging, Atlas tests, upstream regression tests, and the
smoke test. Hosted GitHub Actions did not start for draft PR #1 because of Seomzo
account payment/spending-limit infrastructure. This is **blocked before
execution**, not a repository test failure. Local evidence is authoritative for
this handoff; billing was not changed and jobs were not repeatedly rerun.

## Security and dealership guardrails

| Guardrail | Verified protection | Missing control / release consequence |
| --- | --- | --- |
| Tenant and store isolation | Device authentication derives tenant/store scope; policy, lease, claim, job, usage, and audit paths carry scope. Cross-store work was denied locally before connector/model work. | Admin bearer access is globally privileged, not a user/role boundary. A production user authorization matrix and database-enforced negative tests are release blockers. |
| Customer data | Current fixed-ops data is synthetic. Sensitive-key redaction and aggregate-only model input reduce accidental disclosure. | No data inventory, classification, contractual Tekion basis, field-level minimization policy, subject request process, or production deletion proof. Real customer data is blocked. |
| Consent | No outbound communication path exists. | No consent or suppression ledger. Any customer/staff communication is blocked until purpose-specific consent and revocation are enforceable. |
| Human approval | Consequential outbound/writeback actions are absent. | No approval resource or action-bound decision check. Adding send, export, DMS writeback, or automation that changes customer/dealership state is blocked. |
| Roles | Device, agent, entitlement, and capability scopes exist. | These are machine authorization, not dealership user roles. Manager, group-admin, and Atlas-operator roles plus explicit store grants are required. |
| Auditability | Policy, lease, job, model, usage, and device decisions create correlated audit records. | There is no human actor identity, report-view audit, tamper-evident archive, retention policy, or production export/reconciliation proof. |
| Retention | Cortex has a separate local memory lifecycle. | No Control Plane policy or enforcement for dealership jobs, reports, usage, and audit. Real data is blocked until owners approve durations and deletion obligations. |
| Secrets | Bearer secrets are hashed at rest; leases are HMAC signed; an opaque keyring abstraction exists. | The demo uses environment/bootstrap secrets and browser `sessionStorage`; production vault, rotation, operator identity, and break-glass controls are unproven. |

## Validation record

These commands were run against the audited commit during the 2026-07-25 audit.
They are recorded rather than rerun on the documentation-only branch.

| Command | Result |
| --- | --- |
| `UV_CACHE_DIR=/private/tmp/atlas-audit-uv-cache uv venv --python /Users/ethansandhu/.local/bin/python3.11 .venv` | Passed; Python 3.11.14 environment created. |
| `UV_CACHE_DIR=/private/tmp/atlas-audit-uv-cache uv pip install --python .venv/bin/python -r requirements-atlas-dev.txt -e .` | Passed; 75 packages installed and the project installed editable. An initial run without the temporary cache failed on sandbox cache permissions; changing only the cache path resolved it. |
| `make atlas-test` | Passed: 386 tests, 0 failures, 21 files. |
| `make atlas-smoke` | Passed: device revocation and unentitled-store checks were true; the fixed-ops job completed with `$8,084` sales. |
| `make atlas-dev` plus authenticated HTTP checks | Passed after waiting for startup: root `200`, unauthenticated admin overview `401`, authenticated overview returned expected keys. |
| `make atlas-worker` | Passed: `fixed_ops.daily_report` completed. A later run with the device disabled exited `2` with `DeviceAuthenticationError`, as expected. |
| Store B job submission using the Store A-scoped agent | Denied with HTTP `409` and `agent_inactive` before connector/model work. |
| `scripts/run_tests.sh tests/test_model_tools.py tests/test_model_tools_async_bridge.py tests/test_dispatch_session_id.py tests/test_transform_tool_result_hook.py -q` | Passed 57/57 outside the restricted network sandbox. The first sandboxed run had two `example.com` DNS failures, so it was not counted as a code result. |
| `python -m compileall -q altas scripts/altas-smoke.py` | Passed. |
| `python -m ruff check altas tests/altas scripts/altas-smoke.py plugins/model-providers/altas model_tools.py agent/tool_executor.py agent/agent_runtime_helpers.py` | Passed. |
| `make atlas-lint` | Failed at Ruff format check: 15 tracked files would be reformatted. Ruff lint before that step passed. |
| `node --check altas/control_plane/static/app.js` | Passed. |
| `python -m build --wheel --sdist --outdir /private/tmp/atlas-audit-dist` | Passed; wheel and source distribution built. |
| Clean temporary-environment install of the built wheel, then `altas --version` | Passed; packaged CLI started and reported Atlas 0.1.0. |
| Root dependency install previously performed with `npm ci --ignore-scripts` | Completed; it was not repeated for this documentation turn. |
| `npm --workspace web run typecheck` and `npm --workspace web test` | Passed; web tests 59/59 across 9 files. |
| `npm --workspace web run build` | Passed; Vite transformed 485 modules. It warned that the main minified JavaScript chunk was about 1.97 MB. |
| `npm --workspace desktop run typecheck` | Passed. |
| `npm --workspace desktop test` | Failed: 58 files failed, 144 passed; 151 tests failed, 1,189 passed. The failures are recorded as upstream baseline drift, not hidden or relabeled as an Atlas pass. |

The local Control Plane exercise was reset after validation. No live dealership
or customer data was introduced.

## Risk register

### P0 — blocks real data, external users, or consequential actions

1. **No human identity, RBAC, or store-membership authorization.**
2. **No consent/suppression ledger or human-approval enforcement.**
3. **No Control Plane retention, deletion, and legal-hold policy enforcement.**
4. **No production-grade data store, deployment, secrets, backup, or restore path.**
5. **No live Tekion security/data contract and no field-level minimization proof.**

### P1 — blocks a reliable pilot

1. No report/schedule domain model, idempotent scheduler, or missed-run alert.
2. No production metrics, tracing, error aggregation, SLOs, or on-call runbook.
3. No persisted manager-view audit with a human actor.
4. OS credential storage, rotation, and recovery are not cross-platform verified.
5. Hosted Actions is externally blocked before execution.

### P2 — engineering and documentation debt

1. Fifteen tracked Python files fail Ruff format check.
2. The upstream desktop suite is red in the audited local environment.
3. The web build emits a large-chunk warning.
4. Historical role/owner documents conflict with the current Ethan/Omar decision.
5. Atlas keeps the historical `altas` module/path spelling, increasing naming and
   packaging ambiguity.

## Open questions for Ethan and Omar

1. Approve or reject the proposed first vertical slice: a fixed-ops manager daily
   performance and repair-order exception web report.
2. Confirm that the two-week slice must remain synthetic-only, read-only, and
   incapable of outbound communication or DMS writeback.
3. Approve the proposed success thresholds in the companion plan.
4. Choose the initial user roles and who may grant store membership.
5. Approve retention periods and deletion/legal-hold obligations before any real
   dealership data is accepted.
6. Identify a future design partner and Tekion contracting/security owner; these
   are not dependencies for the synthetic slice.

## Recommendation

Recommend, for **Ethan and Omar's approval rather than as an agent-made product
decision**, the two-week synthetic, read-only fixed-ops manager report described
in [TWO_WEEK_VERTICAL_SLICE_PLAN.md](TWO_WEEK_VERTICAL_SLICE_PLAN.md). It uses the
working job and reporting spine, closes the smallest identity/report/audit gaps
needed for a credible web demonstration, and preserves a hard boundary against
real customer data and consequential actions.
