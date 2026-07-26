# WS-22 Collaboration Acceptance

This evidence matrix is updated only from executed results. `pending` and
`manual-pending` are not passes.

| ID | Status | Evidence |
| --- | --- | --- |
| AC-01 Idempotent bootstrap | pending | Focused tests plus clean temporary home and real-machine double apply required. |
| AC-02 Distinct identities and vault | pending | Three public identities, OS-vault proof, process/config/log sentinel scan required. |
| AC-03 Real Buzz connectivity | pending | Relay/channel/profile message and deep link required. |
| AC-04 Task intake | pending | Valid and vague fake/live task intake evidence required. |
| AC-05 Shared context before work | pending | Three role acknowledgements of identical hashes required. |
| AC-06 Collaborative plan before edits | pending | Plan, reviewer feedback, implementer acceptance, final plan required. |
| AC-07 Isolated execution | pending | Clean role worktrees/branches and non-overlapping claims required. |
| AC-08 Live during-work collaboration | pending | Coordinator-routed question, peer answer, dependent continuation required. |
| AC-09 Bounded routing/dedup | pending | Self/hop/terminal/active-turn/retry tests and replay evidence required. |
| AC-10 Independent review | pending | Separate reviewer catches seeded defect and implementer fixes it. |
| AC-11 Integration/evidence | pending | Ancestry, overlap, order, union tests, matrix, rollback evidence required. |
| AC-12 GitHub recoverability | pending | Issue/commits/draft PR/CI/handoff must stand without Buzz. |
| AC-13 Buzz live record | pending | Task-channel timeline and GitHub links required. |
| AC-14 Degraded recovery | pending | Buzz/GitHub/runtime/orchestrator loss/replay tests required. |
| AC-15 No widening/auto merge | pending | Negative tests, code inspection, draft human gate required. |
| AC-16 Real dogfood | pending | Three identities, available real processes, Buzz, worktrees/commits/tests/PR required. |
| AC-17 Product boundary | pending | Diff and architecture review required. |
| AC-18 Operable by both humans | pending | Runbook and fresh `doctor` interpretation evidence required. |

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
