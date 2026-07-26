# WS-22 Dogfood Evidence

## Scope and mode

Task: `WS-22-DOGFOOD` from `config/tasks/WS-22-dogfood.yaml`.

The executed scenario is a deterministic fake-adapter collaboration plus real
Git worktrees, branches, commits, tests, and integration. It is **not** a live
Buzz dogfood. Real Buzz role enrollment was stopped because the macOS login
Keychain requires personal unlock and Buzz 0.4.26 requires owner-reviewed agent
authorization.

## Shared context

- Base SHA: `7a4d7529dc9b5ef0361800d8d94f05e79d716269`
- Task contract hash:
  `84a63e1cbde77499af75b6d20f7ad5dec6758ef1c5ee96fe0154a6785012ccb5`
- Context-manifest hash:
  `ee60c920ca344363f7507071ed4a59bdb1a729e4f6a5f6ff6428a00db7b31ec3`
- Simulated role profiles: `fake-coordinator-profile`,
  `fake-implementer-profile`, and `fake-reviewer-profile`
- Durable fake event count: 14
- Final deterministic state: `human_approval_required`

The fake identities are deliberately obvious test values and are not claimed
as Buzz identities.

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
59 tests passed

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
`atlas-collab-reviewer` were provisioned and left stopped. Hermes 0.18.2 then
completed a real Buzz ACP protocol-v2 initialization/model handshake through
the credential-stripping runtime shim. This proves the model-runtime seam, not
Buzz role identity authorization.

All three user LaunchAgent definitions were installed with `RunAtLoad=false`
and confirmed `installed: true`, `loaded: false`. No service was started.

## Deliberately unmet live requirements

- No real `atlas-coordinator`, `atlas-implementer`, or `atlas-reviewer` Buzz
  public identity was created.
- No role credential was written to Keychain.
- No real Buzz development channel/canvas/event or deep link was created.
- No live ACP role process or user LaunchAgent was started.
- The existing Buzz Desktop identity must be rotated after a local conversion
  failure emitted its credential once. That value was not reused or committed.
- Claude is unauthenticated. Codex Buzz ACP API-key readiness is unproven.
- The three Hermes profiles are runtime-ready but deliberately cannot become
  live roles without separate Buzz keys, owner approval, author allowlisting,
  and task/worktree bindings.
- The real AC-16 live dogfood remains blocked on personal Keychain unlock,
  desktop identity rotation, and Buzz owner approval.

The deterministic evidence never substitutes for these live gates.
