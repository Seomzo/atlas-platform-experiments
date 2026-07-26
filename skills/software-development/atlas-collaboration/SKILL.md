---
name: atlas-collaboration
description: Coordinate bounded Atlas development work across agents.
version: 1.0.0
author: Atlas
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [atlas, collaboration, agents, review, worktree]
    category: software-development
---

# Atlas Collaboration

Coordinate development-only multi-agent work with Buzz as the live discussion
record, GitHub as the durable scope/code/review ledger, and `atlas-collab` as
the deterministic circuit breaker. This skill never grants Atlas product
authority.

## When to Use

- A task needs coordinator, implementer, and independent reviewer roles.
- Humans want visible before/during/after work discussion in Buzz.
- Parallel work needs isolated branches/worktrees and path/interface claims.
- A stopped task must resume without duplicate events or GitHub comments.

Do not use this skill for Atlas customer runtime execution, product
authorization, automatic merge/deploy, or the workstation-local Atlas Teams
product surface.

## Prerequisites

- Run from a clean Atlas repository worktree on a non-`main` workstream branch.
- `atlas-collab doctor` identifies an authenticated GitHub session and reports
  Buzz/runtime readiness or an explicit degraded state.
- Role-specific Buzz private keys live in the OS credential vault. Never paste
  them into a prompt, file, shell argument, channel, issue, or log.
- The task has a valid `atlas.collab.task.v1` contract with observable,
  uniquely identified acceptance criteria.

## Procedure

1. Run `atlas-collab doctor` and resolve failed checks without widening
   permissions.
2. Preview with `atlas-collab bootstrap --dry-run`; a human runs
   `atlas-collab bootstrap --apply` only after reviewing the plan.
3. Preview task intake with `atlas-collab task create --file <contract>`.
   Apply it only when the base SHA and scope are current.
4. Require all roles to acknowledge the exact contract/context hashes.
5. The coordinator publishes dependencies, ownership/claims, tests, risks, and
   human gates. The reviewer challenges the plan; implementers explicitly
   accept their work packages.
6. Work only in separate role worktrees. Route worker questions through the
   coordinator and communicate contract changes before dependent work.
7. The reviewer cites findings against acceptance IDs and may request changes.
8. The integrator verifies base/overlap/order and runs union validation.
9. Leave a draft PR and handoff for human approval. Never merge, deploy, mark
   ready, force-push, delete branches, or approve your own work.

## Event Rules

- Only a `QUESTION`, contract change, or other actionable event addressed to a
  task member may wake a role.
- Acknowledgements, error echoes, evidence, completion, and handoff events are
  terminal.
- One routed turn is active per task; no self-trigger; worker-to-worker turns
  pass through the coordinator; default maximum hop count is three.
- Two repeated unanswered clarification cycles require a human gate.
- Paused, canceled, completed, circuit-broken, or budget-exhausted tasks do not
  start turns.

## Recovery

- Inspect `atlas-collab task status <task-id>` and
  `atlas-collab task replay <task-id>`.
- Restore the unavailable adapter or agent, then replay. Stable event and
  causation IDs prevent duplicate Buzz/GitHub writes.
- Use `atlas-collab task pause|resume|cancel <task-id> --apply` for coordinator
  state changes.
- Preview `atlas-collab cleanup --task <task-id> --dry-run`. Cleanup releases
  leases and stops task processes while retaining review branches/artifacts.

## Pitfalls

- Channel membership is identity, not authorization. It never changes tools,
  filesystem, network, GitHub, or Atlas product permissions.
- Buzz canvases are derived summaries; append-only discussion and GitHub
  artifacts remain the evidence.
- A ChatGPT Codex login does not prove the current Buzz Codex ACP API-key path.
- An installed Claude binary is not usable until its own auth check succeeds.
- `buzz-acp --agents N` scales one role; it does not replace distinct role
  identities.

## Verification

- `scripts/run_tests.sh tests/atlas_collab -q`
- `python -m tools.atlas_collab.validation --root . --json`
- `atlas-collab doctor`
- Confirm the draft PR and handoff retain a human merge gate and contain no
  private keys, tokens, cookies, auth tags, or credential values.
