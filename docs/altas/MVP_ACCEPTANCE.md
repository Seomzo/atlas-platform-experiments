# Prototype Acceptance Criteria

## Objective

Prove that Atlas can manage one local worker through a real control contract
before adding live Tekion, billing, Slack, or installer complexity.

## Required scenario

1. One command starts the Control Plane, deterministic model gateway, seeded
   database, and Control Center.
2. Seed data contains one active tenant, entitled Store A, and unentitled
   Store B.
3. A seeded worker authenticates and receives a signed, expiring lease.
4. The Control Center queues `fixed_ops.daily_report` for Store A.
5. The worker claims the job and receives `ALLOWED` from deterministic policy.
6. The workflow loads sanitized fixture data and returns a structured report.
7. Job, usage, and audit records share a correlation context and appear in the
   Control Center.
8. The same capability for Store B is denied before connector or model use.
9. Disabling the device blocks subsequent job claims, policy calls, and model
   requests.
10. Missing, expired, malformed, or tampered leases are denied.
11. A job cannot authorize an unrelated leased capability, and model access is
    available only to workflows that declare it as a static dependency.
12. A device has at most one running claim; abandoned claims can be recovered
    after their visibility timeout without accepting an old completion token.
13. Per-job model request and requested-token limits are atomically enforced.
14. Policy and model calls from a superseded claim attempt are denied.
15. Model requests cannot multiply completions or forward unreviewed provider
    extensions, and prompt/tool payloads have deterministic size ceilings.

## Quality gates

- Python tests cover authentication, lease validation, scope isolation, policy,
  job transitions, model usage, and device revocation.
- Managed-mode engine policy fails closed when the Control Plane is unavailable.
- Static product surfaces consistently spell the name “Atlas.”
- A local quickstart works without external accounts.
- The dashboard is usable at desktop and narrow viewport sizes.
- All mocked boundaries are visibly labeled.
- No sentinel secret appears in API output, audit metadata, workflow result,
  or model input.
- `git status` contains no generated database, secret, log, or environment
  files after tests.

## Not required for prototype completion

- Live Tekion API or browser login
- Stripe
- Slack/email
- Mobile app
- Production admin authentication
- Signed installers and updates
- Remote desktop or shell support
- Cloud autoscaling
- Compliance certification

## Definition of done

```text
make atlas-dev
make atlas-worker
make atlas-test
make atlas-smoke
```

Each command must be documented, deterministic, and safe to run on a developer
machine.
