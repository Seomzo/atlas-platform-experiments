# Atlas Rebrand Punch List

Actionable, customer-blocking rebrand items. Full inventories with file:line
detail live in `INVENTORY_CLI.md` and `INVENTORY_DESKTOP.md`; this file tracks
only what must be FIXED, in priority order. Check items off with the commit
that fixes them.

Status legend: `[ ]` open · `[x]` fixed (link commit) · `[-]` accepted/deferred.

## P0 — Release blockers (customer must never see these)

- [ ] **Self-update mechanism points at upstream Hermes, not Atlas.**
  Found live 2026-07-20: Omar's desktop showed an "update available"
  notification counting ~16.5k upstream commits on
  `NousResearch/hermes-agent`, and tapping update would fast-forward the
  managed runtime (`~/.atlas/hermes-agent`) onto untested upstream main.
  - `apps/desktop/electron/update-remote.ts:15-16` — official update remote
    pinned to `github.com/NousResearch/hermes-agent.git`.
  - `apps/desktop/electron/bootstrap-runner.ts:119` — bootstrap scripts
    fetched from `raw.githubusercontent.com/NousResearch/hermes-agent/...`.
  - `hermes_cli` update paths (`hermes update` hints in `config.py`,
    `banner.py` release checks) — see INVENTORY_CLI.md.
  - **Fix direction:** repoint update/bootstrap remotes at the DealerBox
    distribution channel (private fork or release artifacts). Until that
    channel exists, DISABLE passive update checks + the update UI in Atlas
    builds entirely. Policy: upstream merges are performed by us, on a
    branch, with tests (`docs/altas/UPSTREAM.md` patch policy) — never by a
    customer-facing updater.
  - **Policy (Omar, 2026-07-20):** customers must never see an update
    notification sourced from upstream Hermes. Update UX returns only when
    it points at signed Atlas releases (Roadmap Phase 3: "signed
    worker/workflow updates with staged rollout and rollback").

- [ ] **About panel / onboarding link to NousResearch GitHub.**
  `apps/desktop/src/app/settings/about-settings.tsx:24` (release notes),
  `apps/desktop/src/components/onboarding/index.tsx:85` (docs URL).

## P1 — Visible strings the brand layer misses (desktop)

See INVENTORY_DESKTOP.md §1 for the full table. Highlights:

- [ ] `hermes gateway` timeout error (`src/i18n/en.ts:2479` + locale mirrors).
- [ ] `hermes curator restore` toast (`en.ts:834`).
- [ ] `/hermes` path example + `HERMES_DESKTOP_REMOTE_*` help copy
  (`en.ts:532,539`).
- [ ] `exit hermes` slash-help (`en.ts:1662`), `@hermes:example.org`
  placeholder (`en.ts:1234`), zh `hermes gateway setup` (`zh.ts:1428`).
- [ ] `gateway-settings.tsx:494` placeholder URL `…/hermes`.
- [ ] `'Hermes RPC failed'` error text (`apps/shared/src/json-rpc-gateway.ts:330`
  — bypasses the i18n rewriter).

## P2 — Structural (coordinate across components; not string fixes)

- [ ] `pyproject.toml` still ships `hermes`, `hermes-agent`, `hermes-acp`
  entry points (each presents full Hermes branding when run).
- [ ] `hermes-agent.nousresearch.com` docs URLs in CLI (INVENTORY_CLI.md).
- [ ] Install script headers (INVENTORY_CLI.md).
- [ ] `scripts/test-desktop.mjs:45` linux binary name + `~/.hermes` default.
- [-] `hermes_cli` module name, `.hermes-*` markers, `hermes_session_*`
  cookies, `HERMES_*` env vars — backend contracts; rename only as a
  coordinated cross-component effort (see INVENTORY_DESKTOP.md §"do not
  change from the desktop side alone").
