# Two-Week Web Vertical-Slice Plan

## Decision requested

**Recommendation for Ethan and Omar's approval:** build a responsive fixed-ops
manager daily performance and repair-order exception report as the first Atlas
dealership web vertical slice.

This document is a proposal, not a final product decision. Implementation starts
only after Ethan and Omar approve the outcome, guardrails, and success thresholds.
The evidence behind the proposal is in
[BASELINE_AUDIT.md](BASELINE_AUDIT.md).

## User outcome

At the start of the workday, a fixed-ops manager can sign in, see one authorized
store's completed daily report, verify when and from what synthetic snapshot it
was produced, review service KPIs and repair-order exceptions, and trace the run
without preparing a spreadsheet or asking an agent to send anything.

The slice proves a complete web path:

```text
store-scoped manager
  -> scheduled, idempotent report job
  -> authorized dealership-local worker
  -> synthetic Tekion-shaped snapshot
  -> deterministic KPI and exception computation
  -> persisted report and audit trail
  -> responsive, read-only manager view
```

## Non-negotiable slice boundary

- Synthetic fixture data only; no live Tekion and no customer PII.
- Read-only report experience; no DMS writeback, export, or mutation.
- No email, SMS, Slack, push notification, or other outbound communication.
- Every authorization decision defaults to deny.
- Every report run and report view records tenant, store, actor, and correlation
  identifiers.
- A manager sees only stores explicitly granted to that user.
- Report facts are deterministic. Any model-generated narrative is optional,
  clearly labeled, derived only from aggregates, and cannot change KPI or
  exception values.
- No hidden fallback from a failed scheduled run to stale or cross-store data.
- Retention fields and purge behavior are designed into the slice; actual
  production durations remain an Ethan/Omar decision before real data.

## Scope

### In scope

1. Minimal dealership user identity, session, role, and explicit store-membership
   model for local/demo use.
2. Roles sufficient for the slice:
   - `fixed_ops_manager`: read reports for explicitly granted stores.
   - `dealer_group_admin`: manage demo memberships within its tenant.
   - `atlas_operator`: operate the platform without silently inheriting dealer
     report access.
3. First-class report, report-run, schedule, and idempotency records.
4. One daily schedule per authorized store, with an on-demand demo trigger using
   the same job path.
5. Existing `fixed_ops.daily_report` worker execution over the approved synthetic
   fixture contract.
6. Tenant/store-scoped report APIs and a responsive manager web screen.
7. KPI, advisor summary, RO exception, freshness, source, completion, and
   failure/empty/loading states.
8. Audit records for sign-in outcome, authorization decision, schedule trigger,
   job lifecycle, report publication, and report view.
9. Retention metadata, an idempotent purge path, and tests against the
   owner-approved non-production policy.
10. Metrics and local alerts for scheduled completion, duplicate prevention,
    accuracy, authorization denials, and forbidden communication attempts.

### Out of scope

- Live Tekion authentication or ingestion.
- Real dealership/customer data.
- Customer or employee communication delivery.
- Consent-based campaigns or messaging UI.
- DMS writeback, repair-order mutation, payment, scheduling, or automated
  operational decisions.
- Production cloud deployment, billing, native mobile, and generalized report
  builders.
- Broad upstream desktop/web refactors or formatting cleanup.

## Report contract

### Required header

- Tenant and store display name plus immutable identifiers.
- Business date and configured timezone.
- Source kind (`synthetic_fixture`) and fixture version/hash.
- Scheduled time, started time, completed time, and freshness state.
- Report-run identifier and status.

### Required KPI cards

- Open repair-order count.
- Closed repair-order count.
- Total labor sales.
- Total parts sales.
- Total sales.
- Average repair-order value.
- Declined-work total.
- Comeback count.

Currency is computed with decimal-safe arithmetic and displayed to cents. Counts
are integers. KPI formulas and exclusions are versioned in the report.

### Required exception table

Each row contains a synthetic repair-order identifier, advisor, age, status,
exception type, amount where applicable, and deterministic reason. Initial
exception rules remain the repository's existing rules:

- open for at least two days;
- declined work at or above `$500`;
- comeback indicator.

No customer name, phone, email, address, free-form technician note, or message
action appears in the slice.

### Required states

- Loading.
- No report scheduled.
- Scheduled but not yet due.
- Run in progress.
- Completed and fresh.
- Completed but stale, with the exact business date shown.
- Failed, with a bounded error class and retry status.
- Empty source snapshot.
- Access denied.

## Data and authorization additions

The implementation should introduce explicit domain resources rather than embed
new meaning in the generic job payload.

| Resource | Minimum fields |
| --- | --- |
| User | `id`, `tenant_id`, normalized login identifier, display name, status, created/updated timestamps |
| Store membership | `user_id`, `tenant_id`, `store_id`, role, granted/revoked timestamps, granting actor |
| Session | opaque hashed token reference, `user_id`, expiry, revocation, last-used timestamp |
| Report schedule | `id`, tenant/store, timezone, local time, enabled state, next due time, policy version |
| Report run | `id`, tenant/store, business date, schedule, job, idempotency key, status, attempt, timing, bounded error |
| Report | immutable run reference, formula/source versions, KPI JSON, exception JSON, generated time, `expires_at` |
| Approval | schema and default-deny evaluator reserved for future consequential actions; the slice creates no approvable send/writeback action |
| Consent | schema and default-deny evaluator reserved for future communication purposes; the slice creates no communication purpose or recipient |

Authorization rules:

1. Derive tenant and user from the authenticated session, never from request
   parameters.
2. Require an active membership matching both tenant and store for every report
   list, detail, and trigger operation.
3. Keep worker device/agent/capability authorization separate from user
   authorization; both must pass for an on-demand run.
4. Scope every query by tenant and store in the repository layer.
5. Return indistinguishable not-found/denied behavior where revealing a store's
   existence would leak tenant data.
6. Record the actor, role, store grant, decision, reason code, request
   correlation ID, and target resource for every allow and deny.

## Retention proposal for approval

For the synthetic non-production slice, use a policy table and explicit
`expires_at` values rather than hard-coded deletion logic. Proposed defaults:

- sessions: expire after 12 hours and delete 30 days after revocation/expiry;
- report payloads and generic job results: 30 days;
- report-run metadata and usage events: 365 days;
- security/audit decisions: 365 days.

The purge job must be idempotent, tenant-aware, auditable, and covered by
clock-controlled tests. Legal hold must prevent deletion where applicable.
These values are reversible development defaults only. Ethan and Omar must approve
production durations with legal/security input before live data.

## Ten-day implementation sequence

| Day | Deliverable | Acceptance proof |
| --- | --- | --- |
| 1 | Decision lock, threat/data-flow review, formulas, fixture contract, and screen wireframe | Ethan/Omar approve the slice boundary and success criteria; repository decision record captures formulas, prohibited actions, and unresolved production retention. |
| 2 | User, session, role, and store-membership schema plus repository/API authorization seam | Positive same-store and negative cross-store/cross-tenant tests pass; no global admin bearer is accepted by manager routes. |
| 3 | Schedule, report-run, report, policy-version, idempotency, and retention schema | Migration/rollback test passes; duplicate `(tenant, store, business date, policy version)` creation is rejected or returns the original run. |
| 4 | Scheduler and existing worker integration | Clock-controlled tests create exactly one due job; disabled/not-due schedules create none; worker authorization remains fail-closed. |
| 5 | Tenant-scoped report list/detail/trigger APIs and audit emission | API contract tests cover allow, deny, missing, stale, failed, and empty states; every report view has an actor-linked audit record. |
| 6 | Responsive manager report page | Desktop and narrow/mobile-width render checks show KPI, exception, provenance, freshness, loading, empty, denied, stale, and failure states without horizontal data loss. |
| 7 | Accuracy oracle and narrative boundary | Approved fixture scenarios produce exact KPIs/exceptions; optional narrative cannot alter facts and contains no unsupported claim. |
| 8 | Security and consequential-action negative suite | Cross-store/user-role attacks fail; no communication/writeback route or visible action exists; direct forbidden action attempts are denied and audited. |
| 9 | Retention purge, scheduled-completion metrics, duplicate/missed-run detection, and runbook | Time-controlled purge and legal-hold tests pass; local dashboard/alert identifies late, duplicate, failed, and unauthorized attempts. |
| 10 | Full vertical-slice evaluation, accessibility review, demo script, operator runbook, and release decision packet | A clean environment runs the supported setup and full package validation; visible proof and residual-risk register are ready for Ethan/Omar review. |

## Measurable success criteria

### Report accuracy

- **100%** exact KPI and exception classification across the approved synthetic
  fixture matrix.
- Currency values match the independently calculated oracle to **$0.01**; hour
  values match to **0.1 hour**; counts match exactly.
- **Zero** unsupported narrative facts and **zero** row-level customer fields in
  model input, logs, audit metadata, or rendered output.

### Scheduled completion

- **20/20** deterministic simulated daily schedules create exactly one report for
  the intended store and business date.
- For a synthetic design-partner trial, at least **95%** of scheduled reports are
  complete and visible within **15 minutes** of configured delivery time.
- **Zero duplicate reports** for the same tenant, store, business date, and policy
  version.
- Every missed, failed, or late run becomes visible to an operator within
  **5 minutes**; no stale report is presented as current.

### Manager time saved

- Capture a timed baseline for the manager's current manual daily-report
  preparation before the trial.
- Target a median of at least **15 minutes saved per manager per workday**.
- Target median report review time of **5 minutes or less** and manager usefulness
  of at least **4/5** after five trial days.

### Authorization and communication safety

- **Zero unauthorized cross-store or cross-tenant reads, writes, report runs, or
  metadata disclosures** in automated negative tests and audit review.
- **Zero customer-communication events** and **zero staff-communication events**
  from the vertical slice.
- **100%** of allowed and denied sign-in, report-run, and report-view decisions
  have actor, tenant, store, reason, target, timestamp, and correlation metadata.

## Required validation

Implementation must run the repository's full relevant validation at the exact
candidate commit, including:

1. full Atlas Python test suite;
2. Atlas smoke path;
3. compile, Ruff lint, and Ruff format check;
4. Control Center JavaScript syntax check;
5. upstream dispatch regression files required by Atlas CI;
6. wheel and source-distribution build plus clean packaged-CLI startup;
7. manager API contract and negative authorization suite;
8. scheduler/idempotency/retention clock-controlled tests;
9. responsive rendered checks at desktop and narrow widths;
10. end-to-end schedule-to-report-to-view proof with audit reconciliation.

The implementation PR must record exact commands, candidate SHA, environment,
results, screenshots or other visible proof, and any unrelated baseline failure.
Hosted CI being blocked by account infrastructure is reported as blocked, never
as a passing or repository-failing run.

## Dependencies and blockers

- **Human decision:** Ethan and Omar must approve the recommended slice, fixed
  guardrails, roles, and success thresholds.
- **Working agreement:** merge of draft PR #1 is recommended for durable process
  clarity but is not a technical dependency on this plan.
- **Hosted CI:** Seomzo account payment/spending-limit infrastructure currently
  blocks GitHub Actions before job execution. Billing is outside this work.
- **Production/legal:** real data additionally requires Tekion authorization,
  data classification/minimization, approved retention/deletion, user identity,
  consent where applicable, approval enforcement, deployment, backups, and
  production observability.
- **No dependency for synthetic proof:** live Tekion, outbound delivery, billing,
  and mobile applications are deliberately excluded.

## Stop conditions

Pause implementation and request Ethan/Omar direction if any change would:

- introduce real customer or dealership data;
- add an outbound communication, export, or DMS writeback path;
- weaken tenant/store scoping or use the admin bearer as manager identity;
- select production retention periods or legal obligations;
- expand beyond the fixed-ops daily report into a generalized workflow builder;
- require a consequential product decision not already approved.

## Completion handoff

At Day 10, report:

- user-visible behavior and rendered proof;
- branch, commit, PR, and exact base;
- schema, API, worker, web, audit, and operations files changed;
- exact validation commands and outcomes;
- accuracy, scheduling, time-saved, authorization, and communication metrics;
- known risks and open questions;
- documentation and channel-canvas updates;
- the next Ethan/Omar decision: proceed to a synthetic design-partner trial,
  revise the slice, or stop.
