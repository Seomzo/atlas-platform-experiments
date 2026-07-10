# Testing the Altas Prototype

## Safety note

This prototype uses synthetic fixed-ops data and a deterministic local model
response. Do not add live Tekion credentials, dealership exports, customer
data, or production provider keys.

## Install

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-altas-dev.txt
```

Supported Python versions follow the pinned engine baseline: 3.11–3.13.

## Fast confidence check

Run the in-process walking-skeleton smoke test:

```bash
make altas-smoke
```

It creates a temporary database, issues a lease, claims the seeded job,
authorizes Store A, denies Store B, calls the deterministic model gateway,
builds the fixture report, completes the job, and proves device revocation.

Expected final status:

```json
{
  "status": "passed",
  "report_sales": 8084.0,
  "unentitled_store_denied": true,
  "device_revocation_verified": true
}
```

## Interactive Control Center

Start the local Control Plane:

```bash
make altas-dev
```

Open <http://127.0.0.1:8787>. The development admin bearer is:

```text
altas-demo-admin-token-v1
```

It is a public demo value with no external permissions. Control Center keeps it
in `sessionStorage` only.

Seeded records:

| Record | ID | Expected state |
|---|---|---|
| Tenant | `tenant_demo_fixed_ops` | active |
| Store A | `store-sunrise-vw` | active and entitled |
| Store B | `store-harbor-toyota` | active and unentitled |
| Device | `device_demo_local_worker` | active |
| Agent | `agent_demo_altas` | assigned to Store A |
| Job | `job_demo_daily_report` | queued after a clean reset |

In a second terminal:

```bash
source .venv/bin/activate
make altas-worker
```

The worker should print one safe JSON line:

```json
{"detail": "fixed_ops.daily_report", "job_id": "job_demo_daily_report", "status": "succeeded"}
```

Refresh Control Center to inspect the report, model usage, and audit chain.
Running the worker again with no queued job returns `idle`.

## Revocation check

In Control Center:

1. Open **Fleet**.
2. Disable the demo device.
3. Run `make altas-worker` again.
4. Confirm the heartbeat is denied and no workflow executes.
5. Re-enable the device before further testing.

This kill switch revokes Altas services. It does not claim to erase software or
customer-owned credentials from a customer-administered machine.

## Store-entitlement check

Store B is visible but deliberately has no worker assignment or report
entitlement. Attempts to queue or authorize its fixed-ops report must be
denied before fixture/connector or model execution.

## Automated tests

```bash
make altas-test
```

The focused suite covers:

- Hashed device authentication
- Signed lease tamper and expiry behavior
- Live device revocation
- Tenant/store/agent policy scope
- Entitlement enforcement
- Job state transitions
- Job/capability binding, one-claim-per-device behavior, and stale-claim recovery
- OpenAI-compatible deterministic gateway and usage metering
- Atomic per-job model request and requested-token limits
- Claim-attempt binding plus strict message/request-size and provider-field limits
- Fixed-ops aggregation
- Credential-vault contract
- Managed Hermes dispatch fail-closed behavior
- Altas provider registration

## Reset local demo state

Stop the Control Plane, then:

```bash
make altas-reset
```

The next `make altas-dev` creates and seeds a clean database.

## Configuration

The complete local configuration example is [/.env.altas.example](../../.env.altas.example).
The Make targets inject explicit prototype values; the application itself does
not silently generate long-lived admin or signing credentials.

Job safety defaults are a 900-second visibility timeout, eight model requests
per job, 4,096 requested output tokens per job, and 400 output tokens when the
caller omits a limit. These are guardrails for the prototype, not a replacement
for production tenant billing and provider-side spend caps.

## Troubleshooting

### `.venv/bin/python` does not exist

Create the virtual environment and install `requirements-altas-dev.txt`.

### Worker returns a scope or authorization error

Reset the database, confirm the Control Plane uses the Make target, and verify
the seeded environment values in `.env.altas.example`.

### Control Center shows unauthorized

Enter `altas-demo-admin-token-v1` in the development-auth control.

### Worker is idle

The seeded job was already consumed. Queue another Store A daily report in
Control Center or reset the local database.
