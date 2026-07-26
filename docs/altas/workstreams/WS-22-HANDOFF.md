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
- Three isolated Hermes profiles now exist for coordinator, implementer, and
  reviewer. Hermes ACP 0.18.2 completed a real Buzz ACP protocol-v2 handshake
  through the credential-stripping runtime shim. Codex and Claude remain
  optional and must not be selected until their own Buzz ACP authentication is
  proven.
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
- SQLite schema version 4 creates missing tables idempotently. Version 2 added
  role/task/branch/worktree/session bindings, version 3 repaired legacy live
  claim uniqueness while preserving rows transactionally, and version 4 added
  role-specific Git name/email bindings. Fresh and upgraded databases are
  permissioned `0600`.
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
- Each bound worktree is checked for cleanliness, exact branch/base ancestry,
  uniqueness, conflict/overlap risk, and a role-specific local Git identity
  before a role process can start.
- External Buzz and GitHub writes persist an idempotency marker before the call
  and reconcile the remote record before any recovery resend.
- The runtime shim removes Buzz private-key/auth-tag and vault variables before
  launching Hermes; real Buzz ACP protocol-v2 initialization passed through
  that shim.
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

- Live AC-02, AC-03, AC-05, AC-06, AC-08, AC-10, AC-13, and AC-16 are not
  satisfied.
- Keychain is currently locked; secure role credential creation is blocked.
- Buzz agent creation is owner-reviewed and may not bind a pre-generated key.
- The three isolated Hermes profiles exist and authenticate to their configured
  provider, but no role may start until Buzz identity enrollment, task
  binding, and owner allowlisting all pass.
- The three macOS LaunchAgent definitions are installed with `RunAtLoad=false`
  and confirmed unloaded. Their fingerprints/status are durable inventory
  records; the CLI refuses start/restart while `doctor.ready` is false.
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
6. `955f708a4` — draft-PR infrastructure gate evidence
7. `d9d35e020` — completion-audit hardening for worktree/Git
   identity validation, real runtime profiles/shim, schema/recovery, retries,
   prompt-injection rejection, service lifecycle, and exact handoff behavior

The dogfood implementer branch is descended from `cdc849c19` and changes only
the fixture/test pair. The reviewer branch has no edits. The integration branch
cherry-picked the implementation chain in order and ran union validation.

## Exact next actions

1. Human rotates the current Buzz Desktop identity and unlocks login Keychain.
2. Re-run `scripts/atlas-collab doctor`.
3. Enroll coordinator, implementer, and reviewer; verify their three public
   keys against the already provisioned isolated Hermes profiles, then complete
   Buzz owner review.
4. Add only the rotated human owner public key to local config.
5. Run bootstrap apply twice and verify channel reuse. The three LaunchAgent
   definitions may remain installed but stopped; do not start them until every
   identity, owner, channel, profile, worktree, and Git identity check is true.
6. Execute the same WS-22 task live in Buzz with at least two real processes,
   append real deep links/evidence, and update the AC matrix.
7. Resolve the GitHub Actions payment/spending-limit gate and rerun draft-PR CI.
8. Human reviews the draft PR/CI; do not mark ready or merge until the live
   requirements and identity incident are resolved.

## Collaborator enrollment follow-up — runtime probe timeout

On a fresh collaborator workstation at framework commit
`955f708a4926084778f69cf3943ff25eca59649e`, the real
`claude auth status` probe exceeded its 15-second deadline. The uncaught
`subprocess.TimeoutExpired` aborted `atlas-collab doctor`, preventing the
remaining non-mutating readiness checks from being reported.

The follow-up fix converts any bounded command timeout into a sanitized
exit-124 result when the caller requested `check=False`, and into the existing
`CommandError` boundary otherwise. This is general to all external probes and
does not change credentials, permissions, product behavior, or applying
bootstrap semantics.

The collaborator validation also found that fake-orchestrator tests which
write an inventory did not isolate `ATLAS_COLLAB_HOME`. They could replace a
developer's real non-secret inventory with fake task paths. The collaboration
test fixture now redirects that home to a per-test temporary directory and the
intake test verifies the inventory is created there.

Finally, a real non-applying `agents enroll` preview created `state.db` in an
isolated reproduction home. The CLI now uses in-memory state for every
documented non-applying command when no persistent state exists. Parameterized
coverage verifies bootstrap, agent, service, task-state, and cleanup previews
leave no database behind.

Validation for the follow-up:

```text
scripts/run_tests.sh tests/atlas_collab/test_adapters_orchestrator.py -q
scripts/atlas-collab doctor
scripts/atlas-collab bootstrap --dry-run
```

The collaborator branch and draft PR remain separate from the WS-22 branch and
must not be merged automatically.
