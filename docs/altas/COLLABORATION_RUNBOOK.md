# Atlas Collaboration Runbook

Audience: Ethan and an authorized development collaborator. Commands run from
the intended Atlas worktree. This system never authorizes dealership/customer
actions.

## 1. Inspect before setup

```bash
scripts/atlas-collab doctor
scripts/atlas-collab bootstrap --dry-run
```

`doctor` reports each check as observed. `ready: false` is expected until all
three role credentials and relay authorization exist. Interpret common results:

- `coordinator vault credential ... missing`: enroll roles; do not use an env
  file.
- A runtime authentication probe timeout is reported as unavailable/degraded;
  it must not abort the rest of `doctor`.
- Buzz `auth error`: an owner must authorize that public identity.
- Codex `API-key auth is unproven`: do not select Codex Buzz ACP based only on
  the ChatGPT login.
- Claude `installed but not authenticated`: complete Claude's own login
  personally or choose another healthy runtime.
- GitHub not authenticated: run `gh auth login`; personally complete MFA.

Preview output is non-mutating. Review the exact paths/channels/identities, then:

```bash
scripts/atlas-collab bootstrap --apply
```

For the current `atlas-platform` workspace, use the verified existing channel
for all three logical streams until a Buzz owner approves a split:

```yaml
buzz:
  community: atlas-platform
  control_channel: atlas-dealership
  decisions_channel: atlas-dealership
  reviews_channel: atlas-dealership
```

This intentionally makes repeated bootstrap applies reuse
`atlas-dealership` (`154f9a6e-1d2f-459e-833e-9112e72bf06d`). Do not create the
three default channel names merely to satisfy a preflight.

## 2. Enroll role identities

Preview and apply each distinct identity:

```bash
scripts/atlas-collab agents enroll --role coordinator
scripts/atlas-collab agents enroll --role coordinator --apply
scripts/atlas-collab agents enroll --role implementer --apply
scripts/atlas-collab agents enroll --role reviewer --apply
scripts/atlas-collab agents list
```

Apply generates separate secp256k1 keypairs. The private value is written
directly through the OS credential-vault API under service
`io.atlas.collab.buzz`; only the public key enters config/state/inventory.

Buzz 0.4.26 requires an owner-reviewed agent form. If publication reports
`owner-approval-or-relay-pending`, use Buzz Desktop or:

```bash
buzz agents draft-create \
  --channel <CONTROL_CHANNEL_UUID> \
  --display-name atlas-coordinator \
  --system-prompt -
```

Pipe the repository role prompt on stdin. The Buzz owner verifies the proposed
name, role instructions, runtime, mention-only mode, and channel membership,
then saves. Repeat for implementer and reviewer. Do not copy the
desktop-generated private key into Atlas files or substitute one shared
identity. If owner review cannot bind the locally generated public key, keep
the service disabled and record the identity as manually pending.

Add only authorized human owner public keys to
`buzz.owner_public_keys` in `~/.atlas/collab/config.yaml`. Never add private
keys or auth tags there.

## 3. Install and control user services

```bash
scripts/atlas-collab services install
scripts/atlas-collab services install --apply
scripts/atlas-collab services status
scripts/atlas-collab services start
scripts/atlas-collab services stop
```

macOS definitions live in `~/Library/LaunchAgents/io.atlas.collab.*.plist`.
They invoke `service_runner`, which reads the role key from Keychain at runtime
and puts it only in the child environment. Definitions contain no credential.
Logs are under `~/.atlas/collab/logs/`.

Do not start a role whose `doctor` runtime/auth check is false or whose Buzz
identity is relay-pending. The default heartbeat is disabled. A recovery
heartbeat must remain opt-in and low frequency.

## 4. Submit a task

Use the GitHub issue form or create `atlas.collab.task.v1` YAML. Preview first:

```bash
scripts/atlas-collab task create --issue 123 --workstream WS-23
scripts/atlas-collab task create --file config/tasks/WS-22-dogfood.yaml
```

Verify the resolved base SHA, acceptance IDs/evidence, scope, canonical
context, roles, and gates. Then apply:

```bash
scripts/atlas-collab task create --issue 123 --workstream WS-23 --apply
```

Exactly one task record, task channel, canvas, context manifest, task-created
event, and issue milestone marker are created/reused. A vague task enters
`needs-clarification`; correct the contract instead of telling agents to infer.

## 5. Monitor and intervene

```bash
scripts/atlas-collab task status GH-123
scripts/atlas-collab task replay GH-123
scripts/atlas-collab task pause GH-123
scripts/atlas-collab task pause GH-123 --apply
scripts/atlas-collab task resume GH-123 --apply
scripts/atlas-collab task cancel GH-123 --apply
```

Watch the Buzz task channel for explicit contract/context acknowledgements,
plan/review, claims, routed question/answer, blockers, commits, findings,
corrections, integration evidence, and the human gate. Use GitHub for durable
scope, decisions, branches, CI, draft PR, and handoff.

Pause on base drift, claim/actual path overlap, unreviewed contract change,
runtime/relay failure outside documented bounds, budget/circuit exhaustion, or
missing human decision. A reaction, model answer, or channel membership is
never approval.

## 6. Review and integrate

The reviewer identity must differ from the implementation identity. It reads
the accepted contract, relevant Buzz decision thread, diff, and evidence, then
reports findings against `AC-NN`. The integrator verifies:

```bash
git merge-base --is-ancestor <expected-base> <branch>
git diff --name-only origin/main...<branch>
scripts/run_tests.sh tests/atlas_collab -q
python -m tools.atlas_collab.validation --root . --json
git diff --check
```

Keep the PR draft. A human decides whether to mark ready, merge, deploy, publish,
force-push, or delete a branch.

## 7. Recover

### Buzz unavailable

Task state and code work may continue only within already accepted,
non-overlapping claims. Queue milestone publication, do not invent decisions,
and pause any interaction that needs collaboration or a human gate. Restore
Buzz and rerun task intake/replay; stable keys prevent duplicate writes.

### GitHub unavailable

Buzz discussion may continue, but no new authoritative scope, PR, review gate,
or merge action is accepted. Commit locally in the isolated worktree, record
the SHA in SQLite, and pause before integration. Restore GitHub and publish one
milestone summary.

### Agent process lost

Pause that work package, retain its branch/worktree/session/claims, inspect
logs, replace only with the same role permissions, and resume from the compact
event/context manifest. Do not give another role broader credentials.

### Orchestrator lost

Restart the CLI/service and run `task status` then `task replay`. SQLite WAL,
idempotency keys, pending external writes, and active turns reveal the last
safe boundary. If a stale active turn remains, pause and investigate before
releasing it.

## 8. Rotate or replace a role key

1. Stop services.
2. Record the public identity being retired (never its private key).
3. Remove that identity from task/channel memberships where supported.
4. Delete the Keychain item for service `io.atlas.collab.buzz` and the exact
   role account using Keychain Access or an approved credential-vault tool.
5. Re-enroll the role, complete owner review, update only the public key, and
   re-run `doctor`.
6. Start the single role and verify mention-only behavior before the full team.

Never rotate by sending a key through chat or a shell argument.

## 9. Cleanup and uninstall

Preview per-task cleanup:

```bash
scripts/atlas-collab cleanup --task GH-123 --dry-run
scripts/atlas-collab cleanup --task GH-123 --apply
```

Apply releases leases and task process state but retains branches, worktrees,
channels, commits, and review evidence.

To uninstall:

1. `scripts/atlas-collab services stop`
2. Remove only the three exact
   `~/Library/LaunchAgents/io.atlas.collab.{coordinator,implementer,reviewer}.plist`
   files.
3. Delete only the three exact Keychain accounts for service
   `io.atlas.collab.buzz`.
4. Archive task channels through Buzz as a separate human-reviewed action.
5. Retain `~/.atlas/collab/state.db` and inventory for audit, or move that exact
   directory to Trash after confirming no recovery is needed.
6. Never delete Git branches/worktrees or GitHub records as an implied part of
   collaboration cleanup.
