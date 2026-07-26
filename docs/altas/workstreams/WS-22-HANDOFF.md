# WS-22 Handoff — Development collaboration plane

## Status

- Branch: `codex/ws-22-development-collaboration-plane`
- Draft PR: <https://github.com/Seomzo/atlas-platform-experiments/pull/3>
- Baseline: `7a4d7529dc9b5ef0361800d8d94f05e79d716269`
- Last validated: 2026-07-25 America/Los_Angeles
- Protocol: `atlas.collab.protocol.v1`
- Overall: implementation and deterministic dogfood complete; live Buzz
  provisioning/dogfood blocked at human credential/owner gates

## Goal and scope

Implemented a development-only hybrid collaboration plane: GitHub remains the
durable scope/code/CI/review/merge authority, Buzz is the optional private live
record, and `atlas-collab` provides deterministic local state, bounded routing,
thin adapters, role isolation, recovery, and user-level service wrappers.

This work does not change Atlas Teams, Control Plane policy, mobile sequencing,
Cortex product behavior, model dispatch, dealership authorization, or customer
runtime dependencies.

## Decisions made

- Use one Python CLI/skill/adapters footprint, not a new model tool or core seam.
- Use SQLite under `~/.atlas/collab/` for non-secret orchestration recovery.
- Use official Buzz CLI JSON, `git`, and `gh`; never implement Buzz protocol.
- Require distinct coordinator/implementer/reviewer identities and profiles.
- Route workers through coordinator with one active routed task turn,
  `max_hops=3`, claims, failure/turn/cost/clarification budgets, and no
  self/terminal wakes.
- Keep GitHub plus an explicit human as the only merge gate.
- Store role credentials only in the OS vault; LaunchAgents call a wrapper and
  contain no secret.
- Disable periodic LLM heartbeat by default.

## Deferred decisions

- A human must rotate the Buzz Desktop identity whose credential was emitted by
  a local conversion error, then personally unlock the login Keychain.
- A Buzz owner must approve three role identities and confirm whether
  owner-reviewed agent drafts can bind the locally generated public keys.
- Choose a live implementer runtime only after Buzz Codex ACP API-key readiness
  or Claude authentication is proven. Hermes ACP is the only currently proven
  provider-configured runtime.
- GitHub branch protection/rulesets are manual-pending because the private
  repository/account plan does not expose them.
- GitHub Actions accepted the draft-PR events but did not start any job because
  the account has a payment/spending-limit gate.

## Authoritative collaborator routing

The following non-secret values were re-verified in Buzz Desktop and GitHub on
2026-07-25. They replace the ambiguous candidates in the collaborator preflight:

- Framework: <https://github.com/Seomzo/atlas-platform-experiments/pull/3>
- Protocol: `atlas.collab.protocol.v1`
- Buzz community: `atlas-platform`
- Existing coordination channel: `atlas-dealership`
- Existing coordination channel UUID:
  `154f9a6e-1d2f-459e-833e-9112e72bf06d`
- Designated primary host node: `nLaZ3gqWVb11CNTRL`
- Authoritative Fizz public identity:
  `780657f350a3fd0dbada848cda9ac9deb7c64280ae37ee074276539445a1782c`
- Authoritative Fizz npub:
  `npub10qr90u6s507smwk6sjxd4xkfm6muvs5q4cm7up6zwefeg3dp0qkqyd89h7`

The alternative Fizz candidate beginning `0b5d34` is not authoritative for
this enrollment. Until a Buzz owner explicitly approves separate development
channels, local config must map `control_channel`, `decisions_channel`, and
`reviews_channel` to the existing `atlas-dealership` name. Bootstrap will then
reuse that single channel instead of creating a duplicate collaboration plane.

The collaborator workstation must not start an alternate coordinator. It may
host the implementer or reviewer only after their distinct identities complete
owner review. The approved enrollment procedure is section 2 of
`docs/altas/COLLABORATION_RUNBOOK.md`, using
`scripts/atlas-collab agents enroll --role <role> --apply` on the designated
host. The primary coordinator fingerprint remains a one-time generated value:
it must come from that command and the OS credential vault, never be invented
or copied from Fizz.

## Files changed

- `tools/atlas_collab/`: CLI, contracts, state machine, SQLite ledger, routing,
  claims, adapters, credential vault, process control, services, roles, schemas,
  validation, and harmless dogfood fixture.
- `tests/atlas_collab/` and
  `tests/skills/test_atlas_collaboration_skill.py`: deterministic behavior,
  failure, security, recovery, dogfood, and skill coverage.
- `scripts/atlas-collab`, `config/atlas-collab.example.yaml`, and
  `config/tasks/WS-22-dogfood.yaml`.
- GitHub issue/PR templates and credential-free validation workflow.
- Canonical skill under
  `skills/software-development/atlas-collaboration/`.
- Architecture, runbook, acceptance, evidence, workstream, and `AGENTS.md`
  guidance.

## Contracts and migrations

- Protocol: `atlas.collab.protocol.v1`
- Task: `atlas.collab.task.v1`
- Event: `atlas.collab.event.v1`
- Config: `atlas.collab.config.v1`
- Context manifest: `atlas.collab.context.v1`
- Inventory: `atlas.collab.inventory.v1`
- SQLite schema version 1 creates missing tables idempotently and adds
  clarification/cost columns to an existing WS-22 database.
- No Atlas product or upstream Hermes data migration.

## Validation

Latest intended final commands:

```text
scripts/run_tests.sh tests/atlas_collab tests/skills/test_atlas_collaboration_skill.py -q
scripts/run_tests.sh tests/test_project_metadata.py tests/test_packaging_metadata.py -q
python -m tools.atlas_collab.validation --root . --json
python -m ruff check tools/atlas_collab tests/atlas_collab tests/skills/test_atlas_collaboration_skill.py
python -m ruff format --check tools/atlas_collab tests/atlas_collab tests/skills/test_atlas_collaboration_skill.py
git diff --check
```

Executed dogfood details, hashes, worktrees, commits, reviewer failure/probe,
correction, and deliberately unmet live requirements are in
`docs/altas/evidence/WS-22-DOGFOOD.md`.

## Security and privacy checks

- No private key/auth tag/API key/token/cookie is committed in WS-22.
- Sentinel scanner and semantic config/task rejection pass.
- Role public keys are the only identity material allowed in config/state.
- Process secrets are environment-only and launch definitions contain none.
- Task cancellation matches PID plus process create time before terminating an
  explicitly registered process tree.
- No code path merges, deploys, publishes, marks ready, force-pushes, deletes a
  branch, changes repository settings, or widens product permissions.
- Security incident: one existing Buzz Desktop credential appeared in a local
  conversion-error tool output. It was immediately abandoned and is not
  repeated here. Rotate that identity before further use.

## Visual evidence

Buzz Desktop was inspected read-only during preflight. It showed the private
`atlas-dealership` channel, existing agent membership, a healthy Fizz response,
and another agent reporting that Codex login configuration was missing.

No live task-channel visual is claimed. The provisioning gate prevented safely
creating it. No screenshot with credential material was captured.

## Known risks and limitations

- Live AC-02, AC-03, AC-05 through AC-08, AC-13, and AC-16 are not satisfied.
- Keychain is currently locked; secure role credential creation is blocked.
- Buzz agent creation is owner-reviewed and may not bind a pre-generated key.
- No collaborator fresh-operator run was observed.
- Branch protection is not configured.
- Draft-PR CI jobs did not start because of the repository account's
  payment/spending-limit gate; this is an infrastructure block, not a test
  failure.
- Codex ACP and Claude cannot currently be claimed as authenticated Buzz
  runtimes.
- Live relay recovery behavior remains unexercised; deterministic adapter
  recovery is covered.

## Integration order and conflicts

Workstream commit order:

1. `fcbfdf485` — bounded orchestrator
2. `cdc849c19` — docs/GitHub/operator workflow
3. `bda52df6e` — seeded dogfood fixture
4. `9def05135` — independent-review correction
5. `adf52ff99` — final evidence/safety hardening

The dogfood implementer branch is descended from `cdc849c19` and changes only
the fixture/test pair. The reviewer branch has no edits. The integration branch
cherry-picked the implementation chain in order and ran union validation.

## Exact next actions

1. Human rotates the current Buzz Desktop identity and unlocks login Keychain.
2. Re-run `scripts/atlas-collab doctor`.
3. Enroll coordinator, implementer, and reviewer; verify three public keys and
   profiles, then complete Buzz owner review.
4. Add only the rotated human owner public key to local config.
5. Run bootstrap apply twice, verify channel reuse, install but do not start
   services until every runtime check is true.
6. Execute the same WS-22 task live in Buzz with at least two real processes,
   append real deep links/evidence, and update the AC matrix.
7. Resolve the GitHub Actions payment/spending-limit gate and rerun draft-PR CI.
8. Human reviews the draft PR/CI; do not mark ready or merge until the live
   requirements and identity incident are resolved.
