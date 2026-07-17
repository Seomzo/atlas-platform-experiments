# Atlas CLI — Customer-Visible Hermes/Nous Branding Inventory

Generated 2026-07-16 by delegated sweep (verified against source and live
`.venv/bin/atlas` output). Internal module names (`hermes_cli/`, `HERMES_HOME`,
etc.) are intentionally excluded per ADR-001 unless the string is
customer-visible.

> **STATUS UPDATE 2026-07-17:** Sections 2 and 3 below are largely RESOLVED —
> Nous Portal fully gated out of Atlas builds (`portal` subcommand, `setup
> --portal`, Quick Setup option, status rows, login/model flags, debug --nous,
> dashboard OAuth option, tool error strings); cli.py banner/label/epilogue
> leaks fixed; tips wrapped in `brand_text`; first-class `atlas` skin shipped
> as Atlas-build default; banner "Nous Research" → "DealerBox"; uninstaller
> wrapped; config.py update hints + banner PyPI/GitHub-release checks gated.
> Still open: §1 entry points, hermes-agent.nousresearch.com docs URLs,
> install.sh/ps1 headers, `atlas login` deep OAuth internals (auth.py runtime
> messages route through brand_text already).


## 0. What already works (Atlas branding done)

- `atlas` entry point exists; `hermes_cli/brand.py` provides
  `is_atlas_branded()` / `brand_text()` runtime translation — argparse help,
  `atlas status`, `atlas setup` non-interactive output, and the banner
  (ATLAS_LOGO/ATLAS_HERO, dark-blue palette) all correctly render "Atlas"
  when invoked as `atlas`.
- Install scripts export `HERMES_PUBLIC_BRAND=atlas`, `HERMES_BIN=atlas`,
  default home `~/.atlas`.
- Setup docs base is properly gated for Atlas via `ATLAS_DOCS_BASE_URL`
  (`setup.py:34`).

## 1. Shipped entry points

- **pyproject.toml:307-314** — `hermes`, `hermes-agent`, `hermes-acp` console
  scripts ship alongside `atlas`/`atlas-control`/`altas`; invoking `hermes`
  presents full Hermes branding.
  **Suggested:** drop or gate the upstream aliases in the DealerBox
  distribution.

## 2. Nous Portal reachable from `atlas` (the cancel-path leak Omar hit)

- `atlas --help` lists `portal — Set up Nous Portal…` (`hermes_cli/main.py`
  subparser; `hermes_cli/subcommands/setup.py:52-56` for `setup --portal`).
- `hermes_cli/setup.py:2950-2961` — first-time wizard default choice #1 is
  "Quick Setup (Nous Portal) — OAuth login…"; `:3029-3053` prints "Nous
  Portal" header, "Sign up: https://portal.nousresearch.com/manage-subscription",
  and on cancel "Nous Portal setup cancelled." ← the exact leak observed.
- `_run_portal_one_shot` (`hermes_cli/setup.py:2754-2806`): box header
  "⚕ Hermes Setup — Nous Portal (one-shot)", portal signup URL, "retry with
  `hermes portal`".
- `atlas login --help`: "default: production portal", client-id default
  `hermes-cli` (`hermes_cli/auth.py:76-78,180-184`); `hermes_cli/auth.py:7869`
  "Starting Hermes login via …" + `Portal: https://portal.nousresearch.com`.
- `atlas status` prints "Nous Portal ✗ not logged in (run: atlas portal)" and
  "◆ Nous Tool Gateway" (`hermes_cli/status.py:236-356`).
- `tools/tool_backend_helpers.py:66-67`, `tools/image_generation_tool.py:517-521`
  — error strings referencing Nous Portal / `hermes model`.
- `hermes_cli/diagnostics_upload.py:30-31` — `atlas debug share` uploads to
  `https://portal.nousresearch.com`; `subcommands/debug.py:33,83-85`
  "--nous / Nous-internal storage" help.
- **Suggested:** remove/rename `portal` subcommand and Quick Setup option for
  Atlas builds, or point at a DealerBox-owned portal.

## 3. User-facing strings not routed through `brand_text`

### cli.py
- `:3754-3755` compact banner "⚕ NOUS HERMES - AI Agent Framework"; `:3769`
  "Hermes Agent v…"; `:3776` "- Nous Research" tiny-banner suffix.
- `:14318-14320` exit epilogue "Resume this session with:
  `hermes --resume <id>`" (hardcoded `hermes`; should use `command_name()`).
- `:13255` streaming response label " ⚕ Hermes "; `:5515` status-bar fallback
  "Hermes"; `:7067` "Hermes CLI Status"; `:7158` "Tip: …chat with Hermes!";
  `:17646` "Starting Hermes Gateway…"; `:6716-6717` non-agentic model warning
  "…not designed for use with Hermes Agent".
- Hardcoded skin fallbacks bypassing `get_branding()`:
  `cli.py:6238,13701,14610-14615,15570`.

### banner.py
- `:703,711` — every full banner appends "· Nous Research" (even
  Atlas-branded).

### hermes_cli/tips.py
- 93 of ~470 tips mention Hermes/Nous/`hermes` commands; `get_random_tip()`
  (line 478) doesn't apply `brand_text`.
  **Suggested:** wrap tip output in `brand_text()`.

### hermes_cli/skin_engine.py
- `:191-194` (+ skins at 302/341/378/415): `agent_name: "Hermes Agent"`,
  `welcome: "Welcome to Hermes Agent!…"`, `response_label: " ⚕ Hermes "` —
  mitigated by `get_branding()` calling `brand_text` (line 158) but defeated
  by the cli.py hardcoded fallbacks above. No built-in "atlas" skin exists;
  "default" skin description is "Classic Hermes".
  **Suggested:** ship a first-class `atlas` skin and make it the default for
  Atlas builds.

### hermes_cli/main.py
- `:2734` "⚕ Hermes post-install bootstrap"; `:3715-3716` aux-model help
  mentions "Hermes… Nous Portal"; `:7166-7167` "not tracking the official
  Hermes repository / NousResearch/hermes-agent"; `:9438,10100,11357`
  update/gateway messages; `:12222` dashboard auth "[2] OAuth via Nous Portal".

### hermes_cli/uninstall.py
- `:518-694` entirely Hermes-branded ("⚕ Hermes Agent Uninstaller", "delete
  ALL Hermes data").

## 4. Config/paths shown to users

- Update hints `brew upgrade hermes-agent`,
  `docker pull nousresearch/hermes-agent:latest`,
  `pip install --upgrade hermes-agent` (`hermes_cli/config.py:362-548`).
- Banner release link → github.com/NousResearch/hermes-agent
  (`banner.py:139,476`); PyPI update check against `hermes-agent`
  (`banner.py:281`).
- Install dir `~/.atlas/hermes-agent` shown in `install.sh:183-195,2395-2397`
  and `install.ps1:8,28,145-146`; header URLs `hermes-agent.nousresearch.com`.

## Notes

- Sweep hit its tool-call budget before writing this file; content was
  returned in the delegation summary and written by the parent agent.
- The repo's `search_files` tool mishandled alternation regexes on
  single-file paths during the sweep; `grep` via terminal was used instead.
