# WS-22 Handoff — Development collaboration plane

## Status

- Branch: `codex/ws-22-development-collaboration-plane`
- Draft PR: <https://github.com/Seomzo/atlas-platform-experiments/pull/5>
- Baseline: `7a4d7529dc9b5ef0361800d8d94f05e79d716269`
- Last validated: 2026-07-25 America/Los_Angeles
- Protocol: `atlas.collab.protocol.v1`
- Overall: implementation, deterministic dogfood, three Keychain-backed role
  identities, services, GitHub draft/CI, and the live resume path are ready.
  Live Buzz provisioning/dogfood remains blocked at the external community
  owner/admin membership grant.

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

- An actual `atlas-platform` owner/admin must add the enrolled coordinator,
  implementer, and reviewer public identities as relay members.
- Three isolated Hermes profiles now exist for coordinator, implementer, and
  reviewer. Hermes ACP completed a real Buzz ACP protocol-v2 handshake
  through the credential-stripping runtime shim. Codex and Claude remain
  optional; Claude's authentication probe succeeds, while Codex must not be
  selected until Buzz ACP API-key authentication is proven.
- GitHub branch protection/rulesets are manual-pending because the private
  repository/account plan does not expose them.
- Rotation of the unrelated Buzz Desktop identity is explicitly deferred by
  the operator. The framework never reads or reuses that private identity, so
  rotation is not a WS-22 prerequisite.

## Authoritative collaborator routing

The following non-secret values were re-verified locally and in GitHub on
2026-07-25:

- Framework: <https://github.com/Seomzo/atlas-platform-experiments/pull/5>
- Protocol: `atlas.collab.protocol.v1`
- Buzz community: `atlas-platform`
- Stable channels to create/reuse after relay authorization:
  `atlas-dev-control`, `atlas-dev-decisions`, and `atlas-dev-reviews`
- Designated primary host node: `nLaZ3gqWVb11CNTRL`
- Coordinator public fingerprint:
  `b899d99472dff5de95c1c0358dd798b8d337b54a3bad3406b6ab4cefd9422086`
- Implementer public fingerprint:
  `2d653a50657aa74146a5bfcd04cb4178d36d09d97eee8929e02aa757fa18cc17`
- Reviewer public fingerprint:
  `fb4821cfac469529ffae1b9b19a005c6e4355be8583ebd112dd50939dd18e450`

Each identity was generated once on the designated host. Its private half is
stored only in the macOS login Keychain under service
`io.atlas.collab.buzz` and its role account. State, config, inventory, and this
handoff contain only public fingerprints. Do not generate replacements merely
to clear the membership gate.

The current Ethan Buzz Desktop identity can use the private community but does
not expose the owner/admin `Invites` control. The actual owner/admin must use
Buzz Desktop **Settings → Invites** to add all three fingerprints with role
`member`. Builderlab signup is not required for that owner action. After the
grant, re-running each applying enrollment reuses the existing identity and
retries profile publication; it cannot rotate the key accidentally.

Bootstrap then creates/reuses the three stable private channels and
idempotently adds every configured role as a channel bot. Task intake does the
same for the private WS-22 task channel. The three LaunchAgent definitions
remain installed and stopped until `doctor` verifies relay auth, channel IDs,
profiles, task/worktree bindings, and author gates.

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
- One unrelated Buzz Desktop credential appeared in a local conversion-error
  tool output. It was immediately abandoned and is not repeated here. The
  operator explicitly deferred rotation; WS-22 uses three separately generated
  Keychain credentials and never reads or reuses the Desktop identity.

## Visual evidence

Buzz Desktop was inspected read-only during preflight. The installed app and a
locally built current official-source app both opened the existing private
`atlas-platform` session without copying identity material. The current-source
settings did not expose `Invites`, which is direct UI evidence that this
Desktop identity is not recognized as an owner/admin. The temporary build was
quit and the normal `/Applications/Buzz.app` instance was restored.

No live task-channel visual is claimed. The provisioning gate prevented safely
creating it. No screenshot with credential material was captured.

## Known risks and limitations

- Live AC-03, AC-05, AC-06, AC-08, AC-10, AC-13, and AC-16 are not satisfied.
- Keychain is unlocked and contains all three role credentials. The live gate
  is relay membership, not local credential creation.
- The three exact pre-generated public identities must be added as ordinary
  relay members by an owner/admin; creating replacement managed agents would
  break the identity/vault binding.
- The three isolated Hermes profiles exist and authenticate to their configured
  provider, but no role may start until relay membership, channel/profile
  publication, task binding, and author allowlisting all pass.
- The three macOS LaunchAgent definitions are installed with `RunAtLoad=false`
  and confirmed unloaded. Their fingerprints/status are durable inventory
  records; the CLI refuses start/restart while `doctor.ready` is false.
- No collaborator fresh-operator run was observed.
- Branch protection is not configured.
- The prior GitHub billing gate is resolved; the contract and full public CI
  have both passed on the draft branch.
- Claude authentication currently probes healthy. Codex ACP API-key readiness
  is still unproven and is not selected.
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
8. `e96f15139` — async/sync session finalizer compatibility that restored the
   full public CI run
9. `857a7806f` — idempotent relay-profile publication retry for already
   enrolled identities

The dogfood implementer branch is descended from `cdc849c19` and changes only
the fixture/test pair. The reviewer branch has no edits. The integration branch
cherry-picked the implementation chain in order and ran union validation.

## Exact next actions

1. An actual `atlas-platform` owner/admin adds the three fingerprints in
   **Buzz Desktop → Settings → Invites** with role `member`.
2. Re-run applying enrollment for all three roles. It must reuse each Keychain
   identity and report `profile-published`; then re-run `doctor`.
3. Run bootstrap apply twice. Verify the same private
   `atlas-dev-control`, `atlas-dev-decisions`, and `atlas-dev-reviews` channel
   IDs are reused and all three role fingerprints are members. Verify the
   configured human/operator author allowlist against the actual owner/admin
   public identity and correct the public allowlist if needed.
4. Apply the WS-22 dogfood task, bind the three exact branches/worktrees and
   role-local Git identities, require `doctor.ready: true`, then start the
   stopped LaunchAgents.
5. Execute the WS-22 task live with at least two real role processes, retaining
   explicit plan, routed question/answer, seeded-defect review, correction,
   union-test, recovery, and human-gate events/deep links.
6. Append the live evidence, update every partial/blocked AC honestly, run the
   full validation suite, and push the scoped draft-PR update.
7. A human reviews draft PR #5 and CI. Do not mark ready, merge, deploy, or
   delete branches automatically.

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
