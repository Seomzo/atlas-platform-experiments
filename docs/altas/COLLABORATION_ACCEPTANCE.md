# WS-22 Collaboration Acceptance

This evidence matrix is updated only from executed results. `pending` and
`manual-pending` are not passes.

| ID | Status | Evidence |
| --- | --- | --- |
| AC-01 Idempotent bootstrap | partial | An isolated clean home passed doctor, dry-run, apply, second apply, reuse, and `0600` state/config/inventory checks. Stable- and task-channel membership is now idempotently ensured in fake-adapter coverage. Real Buzz double apply remains blocked by relay membership. |
| AC-02 Distinct identities and vault | pass | Coordinator, implementer, and reviewer have distinct real public identities, separate Hermes profiles, and separate private credentials in the unlocked macOS login Keychain. Config/state/inventory contain only the public fingerprints. Role-specific Git identity binding remains fail-closed until live task binding. |
| AC-03 Real Buzz connectivity | blocked | The official Buzz CLI is available and the private `atlas-platform` relay answers, but each new role receives exact `403 relay_membership_required`. A real owner/admin must add the three public identities before any live message/deep link can be claimed. |
| AC-04 Task intake | partial | Valid and vague fake intake create/reuse exactly one record/channel/canvas/comment; live intake is blocked. |
| AC-05 Shared context before work | partial | Three simulated roles acknowledged the exact base/contract/context hashes, and exact requested-role coverage is tested; no live role acknowledgement exists. |
| AC-06 Collaborative plan before edits | partial | Deterministic plan, review challenge, implementer scope, and accepted plan precede real fixture edits; not live Buzz. |
| AC-07 Isolated execution | pass | Real integration/implementer/reviewer worktrees and branches remain separate and clean. Temporary-repository subprocess tests reject dirty worktrees, wrong bases, and non-unique branch assignment; configure distinct worktree-local Git identities; and detect path overlap and actual Git conflicts. Deterministic claim conflicts also pass. |
| AC-08 Live during-work collaboration | blocked | Routed deterministic question/answer exists; no live Buzz processes. |
| AC-09 Bounded routing/dedup | pass | Self/hop/terminal rejection, worker mediation, one active turn, bounded exponential retries, turn/cost/failure/clarification bounds, concurrent atomic claim leases, fake-clock expiry, replay, and write dedup tests pass. |
| AC-10 Independent review | partial | Separate reviewer worktree/probe caught the seeded uppercase defect before approval and the implementer corrected it. No distinct live model identity participated. |
| AC-11 Integration/evidence | pass | Base ancestry, two-path overlap, ordered cherry-picks, union tests, evidence, matrix, and rollback points are recorded. |
| AC-12 GitHub recoverability | pass | Draft PR #5 is open, draft, mergeable, and points at the scoped branch. The collaboration contract and full public CI run successfully after the billing gate was resolved. The CLI idempotently creates/updates one draft PR and refuses human-ready PRs. No merge/ready/deploy action exists. |
| AC-13 Buzz live record | blocked | Only explicitly labeled fake event history exists. |
| AC-14 Degraded recovery | partial | Deterministic Buzz/GitHub/runtime/orchestrator loss and replay pass. SQLite migrations v1 through v4 preserve state, pending external writes reconcile remote idempotency markers before resending, and duplicate active claims are prevented. Live relay recovery remains untested. |
| AC-15 No widening/auto merge | pass | Negative tests/code inspection pass; no such mutator exists and final state remains human-gated. |
| AC-16 Real dogfood | blocked | Real worktrees/commits/review/tests and three role credentials exist, but owner authorization, live processes, and a real Buzz transcript are still missing. |
| AC-17 Product boundary | pass | Diff is confined to development tooling, tests, docs, templates, and skill; product runtime/authorization are unchanged. |
| AC-18 Operable by both humans | partial | Exact setup-through-recoverable-uninstall runbook and actionable `doctor` exist. The documented repository wrapper now re-executes through the locked `.venv` and passes from a clean system-Python shell. Three LaunchAgent definitions are installed with `RunAtLoad=false` and remain unloaded; collaborator fresh-run and live-operation evidence are pending. |

## Known environment constraints

- The private GitHub repository currently has no API-visible branch protection.
  Rulesets require a different GitHub plan. This is manually pending, while the
  tooling itself exposes no merge/deploy/settings action.
- The prior GitHub Actions payment/spending-limit gate is resolved. The
  collaboration contract and full public CI both started and passed.
- All three role identities are enrolled locally. The actual `atlas-platform`
  owner/admin must grant each public key relay membership with role `member`;
  the current Ethan Desktop identity does not expose the owner/admin invite
  control.
- Claude Code is installed and its current authentication probe succeeds.
- Codex 0.144.6 is authenticated through ChatGPT; this does not prove an API key
  for the current Buzz Codex ACP requirement.
- Hermes has an available ACP command, configured provider,
  three isolated role profiles, and a successful Buzz ACP protocol-v2 handshake
  through the credential-stripping runtime shim.
