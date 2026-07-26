# WS-22 Collaboration Acceptance

This evidence matrix is updated only from executed results. `pending` and
`manual-pending` are not passes.

| ID | Status | Evidence |
| --- | --- | --- |
| AC-01 Idempotent bootstrap | partial | An isolated clean home passed doctor, dry-run, apply, second apply, reuse, and `0600` state/config/inventory checks. Real Buzz double apply remains blocked by the role credential gate. |
| AC-02 Distinct identities and vault | blocked | Three distinct real Hermes profiles are provisioned and role-specific Git identity binding is enforced in real temporary worktrees. Login Keychain is locked, so no real Buzz role key was stored or claimed. |
| AC-03 Real Buzz connectivity | blocked | Relay/community and Buzz CLI 0.4.26 were discovered; no new role is authorized, so no live task message/deep link is claimed. |
| AC-04 Task intake | partial | Valid and vague fake intake create/reuse exactly one record/channel/canvas/comment; live intake is blocked. |
| AC-05 Shared context before work | partial | Three simulated roles acknowledged the exact base/contract/context hashes, and exact requested-role coverage is tested; no live role acknowledgement exists. |
| AC-06 Collaborative plan before edits | partial | Deterministic plan, review challenge, implementer scope, and accepted plan precede real fixture edits; not live Buzz. |
| AC-07 Isolated execution | pass | Real integration/implementer/reviewer worktrees and branches were separate. Temporary-repository subprocess tests reject dirty worktrees, wrong bases, and non-unique branch assignment; configure distinct worktree-local Git identities; and detect path overlap and actual Git conflicts. Deterministic claim conflicts also pass. |
| AC-08 Live during-work collaboration | blocked | Routed deterministic question/answer exists; no live Buzz processes. |
| AC-09 Bounded routing/dedup | pass | Self/hop/terminal rejection, worker mediation, one active turn, bounded exponential retries, turn/cost/failure/clarification bounds, concurrent atomic claim leases, fake-clock expiry, replay, and write dedup tests pass. |
| AC-10 Independent review | partial | Separate reviewer worktree/probe caught the seeded uppercase defect before approval and the implementer corrected it. No distinct live model identity participated. |
| AC-11 Integration/evidence | pass | Base ancestry, two-path overlap, ordered cherry-picks, union tests, evidence, matrix, and rollback points are recorded. |
| AC-12 GitHub recoverability | partial | Draft PR #3, branch history, templates/workflow, evidence, and handoff stand without Buzz. The CLI idempotently creates/updates one draft PR and refuses human-ready PRs. CI events fired, but GitHub did not start the jobs because of an account payment/spending-limit gate. |
| AC-13 Buzz live record | blocked | Only explicitly labeled fake event history exists. |
| AC-14 Degraded recovery | partial | Deterministic Buzz/GitHub/runtime/orchestrator loss and replay pass. SQLite migrations v1 through v4 preserve state, pending external writes reconcile remote idempotency markers before resending, and duplicate active claims are prevented. Live relay recovery remains untested. |
| AC-15 No widening/auto merge | pass | Negative tests/code inspection pass; no such mutator exists and final state remains human-gated. |
| AC-16 Real dogfood | blocked | Real worktrees/commits/review/tests exist, but the required real Buzz identities/processes/transcript do not. |
| AC-17 Product boundary | pass | Diff is confined to development tooling, tests, docs, templates, and skill; product runtime/authorization are unchanged. |
| AC-18 Operable by both humans | partial | Exact setup-through-recoverable-uninstall runbook and actionable `doctor` exist. Three LaunchAgent definitions were installed with `RunAtLoad=false` and confirmed unloaded; collaborator fresh-run evidence is pending. |

## Known environment constraints

- The private GitHub repository currently has no API-visible branch protection.
  Rulesets require a different GitHub plan. This is manually pending, while the
  tooling itself exposes no merge/deploy/settings action.
- Draft-PR Actions jobs are infrastructure-blocked before startup by the
  repository account's payment/spending-limit gate.
- Buzz agent creation is owner-reviewed. Locally generated public identities
  are not called live until relay authorization succeeds.
- Claude Code 2.1.215 is installed but unauthenticated.
- Codex 0.144.6 is authenticated through ChatGPT; this does not prove an API key
  for the current Buzz Codex ACP requirement.
- Hermes 0.18.2 has an available ACP command, configured OpenRouter provider,
  three isolated role profiles, and a successful Buzz ACP protocol-v2 handshake
  through the credential-stripping runtime shim.
