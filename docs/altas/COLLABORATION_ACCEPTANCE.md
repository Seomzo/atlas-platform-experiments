# WS-22 Collaboration Acceptance

This evidence matrix is updated only from executed results. `pending` and
`manual-pending` are not passes.

| ID | Status | Evidence |
| --- | --- | --- |
| AC-01 Idempotent bootstrap | partial | Clean-home dry-run and fake adapter/state idempotency pass. Real dry-run is accurate; double real apply is blocked by the role credential gate. |
| AC-02 Distinct identities and vault | blocked | Fake identities/profiles and sentinel/no-argv tests pass. Login Keychain is locked, so no real role key was stored or claimed. |
| AC-03 Real Buzz connectivity | blocked | Relay/community and Buzz CLI 0.4.26 were discovered; no new role is authorized, so no live task message/deep link is claimed. |
| AC-04 Task intake | partial | Valid and vague fake intake create/reuse exactly one record/channel/canvas/comment; live intake is blocked. |
| AC-05 Shared context before work | partial | Three simulated roles acknowledged base/contract/context hashes; no live role acknowledgement. |
| AC-06 Collaborative plan before edits | partial | Deterministic plan, review challenge, implementer scope, and accepted plan precede real fixture edits; not live Buzz. |
| AC-07 Isolated execution | pass | Real integration/implementer/reviewer worktrees and branches were clean and separate; deterministic claim conflicts pass. |
| AC-08 Live during-work collaboration | blocked | Routed deterministic question/answer exists; no live Buzz processes. |
| AC-09 Bounded routing/dedup | pass | Self/hop/terminal rejection, worker mediation, one active turn, turn/cost/failure/clarification bounds, replay, and write dedup tests pass. |
| AC-10 Independent review | partial | Separate reviewer worktree/probe caught the seeded uppercase defect before approval and the implementer corrected it. No distinct live model identity participated. |
| AC-11 Integration/evidence | pass | Base ancestry, two-path overlap, ordered cherry-picks, union tests, evidence, matrix, and rollback points are recorded. |
| AC-12 GitHub recoverability | partial | Contract, four commits, templates/workflow, evidence, and handoff stand without Buzz; draft PR/CI still pending final publish. |
| AC-13 Buzz live record | blocked | Only explicitly labeled fake event history exists. |
| AC-14 Degraded recovery | partial | Deterministic Buzz/GitHub/runtime/orchestrator loss and replay pass; live relay recovery remains untested. |
| AC-15 No widening/auto merge | pass | Negative tests/code inspection pass; no such mutator exists and final state remains human-gated. |
| AC-16 Real dogfood | blocked | Real worktrees/commits/review/tests exist, but the required real Buzz identities/processes/transcript do not. |
| AC-17 Product boundary | pass | Diff is confined to development tooling, tests, docs, templates, and skill; product runtime/authorization are unchanged. |
| AC-18 Operable by both humans | partial | Exact setup-through-uninstall runbook and actionable `doctor` exist; collaborator fresh-run evidence is pending. |

## Known environment constraints

- The private GitHub repository currently has no API-visible branch protection.
  Rulesets require a different GitHub plan. This is manually pending, while the
  tooling itself exposes no merge/deploy/settings action.
- Buzz agent creation is owner-reviewed. Locally generated public identities
  are not called live until relay authorization succeeds.
- Claude Code 2.1.215 is installed but unauthenticated.
- Codex 0.144.6 is authenticated through ChatGPT; this does not prove an API key
  for the current Buzz Codex ACP requirement.
- Hermes 0.13.0 has an available ACP command and configured OpenRouter provider.
