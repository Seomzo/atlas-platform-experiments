# Hermes Upstream Baseline

## Provenance

- Repository: <https://github.com/NousResearch/hermes-agent>
- Remote name in this checkout: `upstream` (fetch enabled; push URL disabled)
- Default branch: `main`
- Pinned prototype commit:
  `a7f65e3bcd937cd095ba599ab5927af2093a0d95`
- Python requirement at baseline: `>=3.11,<3.14`
- License: MIT; preserved at `/LICENSE`

## Why internal names remain

Hermes embeds its name in Python distributions, imports, environment variables,
protocol handlers, plugins, tests, and update assumptions. Renaming those engine
interfaces would create high regression risk and permanent merge debt.

Atlas therefore keeps those engine interfaces private while presenting Atlas
directly at every customer surface. The `atlas` executable calls the engine in
process; there is no forwarding bridge or second agent process. References to
Hermes are permitted only in:

- Upstream engine code and tests
- Internal adapter/diagnostic output
- Migration tooling
- Legal attribution
- Upstream maintenance documentation

Customer-facing UI, configuration, workflow names, product docs, and the
prototype quick start use Atlas. Legacy upstream commands remain available to
engine developers until the commercial distribution is split from this fork.

## Prototype distribution boundary

This checkout deliberately retains the upstream `hermes-agent` Python
distribution name, `0.18.2` engine version, and legacy `hermes`,
`hermes-agent`, and `hermes-acp` developer entry points. Changing those in the
first integration pass would break upstream extras, lockfile assumptions, and
update tooling while hiding which engine release is embedded.

The Atlas control plane has its own `0.1.0` version. The public commands are
`atlas` for the complete agent and `atlas-control` for managed fleet services.
The legacy commands are compatibility surfaces for engine maintenance; they
are not the dealership install or onboarding flow. The desktop and bootstrap
installer now build as Atlas, while publishing and signing release artifacts
remains a distribution milestone.

## Patch policy

Unavoidable upstream edits must be:

- Small and generic
- Covered by a focused Atlas regression test
- Recorded below
- Free of Tekion, billing, customer UI, or plan-specific business logic
- Candidates for an upstreamable extension seam where appropriate

### Patch ledger

| Patch | Purpose | Removal condition |
|---|---|---|
| Managed dispatch guard in `model_tools.py` | Fail closed before any managed tool executes | Upstream provides a mandatory, non-bypassable external policy hook |
| Agent-owned tool guard in `agent/tool_executor.py` | Cover memory, recall, delegation, terminal reads, and provider-owned tools that bypass `model_tools.py` | All agent-owned dispatch routes share the upstream mandatory policy hook |
| Concurrent dispatch reuse in `agent/agent_runtime_helpers.py` | Send concurrent tool execution through the same guarded last-mile boundary | Upstream exposes one shared sequential/concurrent execution boundary |
| `atlas` console entry point and runtime brand seam | Direct public product launcher without a bridge | Retained as the stable product entry point |
| Atlas provider plugin | Route engine inference through Atlas gateway | Retained as supported integration |

## Update process

1. Fetch a reviewed upstream release; do not float on `main` in production.
2. Record the candidate commit and release notes.
3. Run pristine upstream tests before applying Atlas patches.
4. Run Atlas control-plane, adapter, policy, worker, and UI tests.
5. Review changes to security policy, dependencies, installers, browser tools,
   plugin loading, credential handling, network surfaces, and update logic.
6. Build and smoke-test signed desktop artifacts on supported platforms.
7. Roll out to internal workers, then a canary design partner, then the fleet.
8. Retain a rollback path.

The upstream `hermes update` flow is not a production Atlas update mechanism.
Customer deployments will eventually use a signed Atlas release feed.
