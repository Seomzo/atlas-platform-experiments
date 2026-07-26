# WS-22 Dogfood Evidence

## Scope and mode

Task: `WS-22-DOGFOOD` from `config/tasks/WS-22-dogfood.yaml`.

The completed scenario remains a deterministic fake-adapter collaboration plus
real Git worktrees, branches, commits, tests, and integration. It is **not yet**
a live Buzz dogfood. The macOS login Keychain is now unlocked and all three
role credentials are enrolled, but the private relay returns
`403 relay_membership_required` until an actual `atlas-platform` owner/admin
adds the three public identities.

## Shared context

- Base SHA: `7a4d7529dc9b5ef0361800d8d94f05e79d716269`
- Task contract hash:
  `84a63e1cbde77499af75b6d20f7ad5dec6758ef1c5ee96fe0154a6785012ccb5`
- Context-manifest hash:
  `ee60c920ca344363f7507071ed4a59bdb1a729e4f6a5f6ff6428a00db7b31ec3`
- Simulated role profiles: `fake-coordinator-profile`,
  `fake-implementer-profile`, and `fake-reviewer-profile`
- Real role public identities:
  - coordinator:
    `b899d99472dff5de95c1c0358dd798b8d337b54a3bad3406b6ab4cefd9422086`
  - implementer:
    `2d653a50657aa74146a5bfcd04cb4178d36d09d97eee8929e02aa757fa18cc17`
  - reviewer:
    `fb4821cfac469529ffae1b9b19a005c6e4355be8583ebd112dd50939dd18e450`
- Durable fake event count: 14
- Final deterministic state: `human_approval_required`

The fake identities are deliberately obvious test values. The three real
fingerprints above are public identity material only; their private halves
remain in Keychain and are not printed, logged, committed, or passed to the
model runtime.

## Timeline

1. `TASK_CREATED` made one task/channel/canvas/comment through fake adapters.
2. `CONTEXT_READY` recorded the same contract/context hashes for all roles.
3. `PLAN_PROPOSED` assigned only the fixture and focused test to implementer.
4. `PLAN_FEEDBACK` required strict canonical lowercase role labels and a
   controlled review challenge.
5. `PLAN_ACCEPTED` retained distinct worktrees/claims and a human merge gate.
6. Implementer `QUESTION` asked whether uppercase labels must fail closed.
7. Coordinator routed a causally linked `QUESTION` to reviewer.
8. Reviewer `ANSWER` required exact lowercase labels.
9. Implementer produced seeded-defect commit `802e88d68`.
10. The reviewer worktree ran a focused regression probe; it failed because
    `REVIEWER` was accepted. `REVIEW_FINDING` cited AC-03.
11. Implementer corrected the defect in `5500bd0a7` and added three regression
    cases.
12. The same reviewer probe passed, followed by `REVIEW_APPROVED`.
13. Integration cherry-picked the intended chain as `bda52df6e` then
    `9def05135`.
14. Union validation passed and `HUMAN_GATE_REQUIRED` left the result
    unmerged.

## Real worktrees and commits

| Role | Branch/worktree | Result |
| --- | --- | --- |
| Integration | `codex/ws-22-development-collaboration-plane` | Owns final ordered commits and union validation. |
| Implementer | `codex/ws-22-dogfood-implementer` | Seeded defect `802e88d68`; correction `5500bd0a7`. |
| Reviewer | `codex/ws-22-dogfood-reviewer` | Stayed at reviewed base and made no product edits. |

The implementation branch was based on integration commit `cdc849c19`.
`git merge-base --is-ancestor` passed. Its only changed paths were:

```text
tests/atlas_collab/test_dogfood_fixture.py
tools/atlas_collab/dogfood_fixture.py
```

## Review probe

Before correction:

```text
review regression probe: FAIL - uppercase role was accepted
```

After correction:

```text
review regression probe: pass - noncanonical labels fail closed
```

## Validation

Executed on the integration branch:

```text
scripts/run_tests.sh tests/atlas_collab tests/skills/test_atlas_collaboration_skill.py -q
66 tests passed

scripts/run_tests.sh tests/test_project_metadata.py tests/test_packaging_metadata.py -q
22 tests passed

python -m tools.atlas_collab.validation --root . --json
ok: true

uv run ruff check tools/atlas_collab tests/atlas_collab tests/skills/test_atlas_collaboration_skill.py
All checks passed

uv run ruff format --check tools/atlas_collab tests/atlas_collab tests/skills/test_atlas_collaboration_skill.py
39 files already formatted

git diff --check
passed

uv build --wheel --sdist --out-dir <external-work-directory>
wheel and source distribution built; wheel contains atlas-collab entry point,
schemas, and role prompts
```

After rebasing onto the independently pushed self-contained Atlas CI fixes, the
combined branch also passed 443 focused Atlas/upstream-dispatch regression
tests. The two emitted warnings are pre-existing deprecation/resource warnings,
not failures.

After the GitHub billing gate was resolved, the collaboration contract workflow
(`30189152764`) and full public CI (`30189153095`) both passed at
`857a7806f32c08d987bcfae4d875ee710f2a9a49` on the live draft-PR branch. The
documented `scripts/atlas-collab` wrapper was also exercised from
`/usr/bin/python3` with no active virtualenv; it re-executed through the locked
repository `.venv` and returned a valid doctor report.

Additional safety coverage includes clean-home bootstrap/idempotency and
permissions; task/event validation; deterministic prompt-injection rejection;
vague intake; coordinator-only transitions; exact role acknowledgements;
replay/idempotency; pre-call external markers and remote reconciliation;
deduplicated Buzz/GitHub writes; worker mediation; self/hop/terminal wake
rejection; one active turn; bounded exponential retry; turn/cost/failure/
clarification budgets; atomic concurrent leases and fake-clock expiry;
path/interface claims; pause/resume/cancel; credential sentinel rejection;
credential-stripping runtime launch; role/profile/task/worktree/Git identity
binding; real temporary-repository ancestry/overlap/conflict tests; draft-PR
reuse/refusal; service preview/apply/archive; adapter loss/recovery; and exact
task-scoped process-tree cancellation that leaves an unrelated process alive.

The three real non-secret Hermes profiles
`atlas-collab-coordinator`, `atlas-collab-implementer`, and
`atlas-collab-reviewer` were provisioned and left stopped. Hermes then
completed a real Buzz ACP protocol-v2 initialization/model handshake through
the credential-stripping runtime shim. This proves the model-runtime seam, not
Buzz role identity authorization.

All three user LaunchAgent definitions were installed with `RunAtLoad=false`
and confirmed `installed: true`, `loaded: false`. No service was started.

The real `WS-22-DOGFOOD` contract was also applied to the durable local store.
The command created/reused exactly one task in `intake`, preserved contract hash
`84a63e1cbde77499af75b6d20f7ad5dec6758ef1c5ee96fe0154a6785012ccb5`,
and then failed closed on the expected `403 relay_membership_required` before
recording a Buzz channel, canvas, or event. This leaves an auditable,
idempotently resumable recovery boundary rather than a second task.

All three exact live role bindings are already persisted:

| Role | Branch | Persistent session | Worktree-local Git identity |
| --- | --- | --- | --- |
| Coordinator | `codex/ws-22-development-collaboration-plane` | `ws22-coordinator-live` | `Atlas Coordinator <atlas-coordinator@users.noreply.github.com>` |
| Implementer | `codex/ws-22-dogfood-implementer` | `ws22-implementer-live` | `Atlas Implementer <atlas-implementer@users.noreply.github.com>` |
| Reviewer | `codex/ws-22-dogfood-reviewer` | `ws22-reviewer-live` | `Atlas Reviewer <atlas-reviewer@users.noreply.github.com>` |

Each binding currently fails readiness for one explicit reason only:
`bound task has no live Buzz channel`. Its branch/worktree identity checks will
run again automatically after task intake resumes and records that channel.

## Deliberately unmet live requirements

- The three real public identities exist and their private credentials are in
  Keychain, but none is yet a relay member.
- No real Buzz development channel/canvas/event or deep link was created.
- No live ACP role process or user LaunchAgent was started.
- The existing Buzz Desktop identity is not used by the framework. Per operator
  direction, rotating it is explicitly deferred and is not treated as a WS-22
  prerequisite; the previously observed value was never reused or committed.
- Claude's authentication probe now succeeds. Codex Buzz ACP API-key readiness
  remains unproven and Codex is not selected.
- The three Hermes profiles are runtime-ready but cannot become live roles
  without relay membership, profile publication, and channel membership. Exact
  task/worktree/session/Git bindings are already persisted.
- Stable and task channel creation now idempotently adds every configured role
  as a channel bot in deterministic coverage. The live apply remains gated on
  relay membership.
- The real AC-16 live dogfood is blocked only at the external owner/admin
  membership grant and the live setup that is designed to resume from the
  pre-staged task and bindings.

The deterministic evidence never substitutes for these live gates.
