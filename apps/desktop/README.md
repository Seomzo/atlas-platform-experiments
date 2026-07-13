# Atlas Desktop ☤

**The native desktop app for [Atlas](../../README.md).** Same agent, same skills,
same memory as the CLI and gateway, in a polished native window—chat with
streaming tool output, side-by-side previews, a file browser, voice, and
settings, with no terminal required.

<table>
<tr><td><b>Chat with the full agent</b></td><td>Streaming responses, live tool activity, structured tool summaries, and the same conversation history as every other Atlas surface.</td></tr>
<tr><td><b>Side-by-side previews</b></td><td>Render web pages, files, and tool outputs in a right-hand pane while you keep chatting.</td></tr>
<tr><td><b>File browser</b></td><td>Explore and preview the working directory without leaving the app.</td></tr>
<tr><td><b>Voice</b></td><td>Talk to Atlas and hear it back.</td></tr>
<tr><td><b>Settings & onboarding</b></td><td>Manage providers, models, tools, and credentials from a real UI. First-run setup gets you to your first message in seconds.</td></tr>
<tr><td><b>Stays current</b></td><td>Built-in updates pull the latest agent and rebuild the app in place.</td></tr>
</table>

---

## Install

### Install with Atlas (recommended)

Already have the Atlas CLI? Just run:

```bash
atlas desktop
```

It builds and launches the GUI against your existing install — same config, keys, sessions, and skills. On first launch Atlas walks you through picking a provider and model; nothing else to configure.

Prebuilt installers are not published yet; use the source build during product
development.

---

## Updating

The app checks for updates in the background and offers a one-click update when one is ready. You can also update any time from the CLI:

```bash
atlas update
```

---

## Requirements

The installer handles everything for you (Python 3.11+, a portable Git, ripgrep).

---

## Development

Want to hack on the app itself? Install workspace deps from the repo root once, then run the dev server from this directory:

```bash
npm install          # from repo root — links apps/desktop, web, apps/shared
cd apps/desktop
npm run dev          # Vite renderer + Electron, which boots the Python backend
```

Point the app at a specific source checkout, or sandbox it away from your real config:

```bash
HERMES_DESKTOP_HERMES_ROOT=/path/to/clone npm run dev
HERMES_HOME=/tmp/throwaway npm run dev
npm run dev:fake-boot   # exercise the startup overlay with deterministic delays
```

### Building installers

```bash
npm run dist:mac     # DMG + zip
npm run dist:win     # NSIS + MSI
npm run dist:linux   # AppImage + deb + rpm
npm run pack         # unpacked app under release/ (no installer)
```

Installers are built and uploaded to GitHub Releases manually. macOS/Windows signing & notarization happen automatically when the relevant credentials are present in the environment (`CSC_LINK` / `CSC_KEY_PASSWORD` / `APPLE_*` for macOS, `WIN_CSC_*` for Windows).

### How it works

The packaged app ships the Electron shell and a native React chat surface. On first launch it can install the Atlas runtime into `HERMES_HOME` (`~/.atlas`, or `%LOCALAPPDATA%\atlas` on Windows)—the same layout the `atlas` CLI uses. The `HERMES_*` environment-variable and Python-module names are retained as internal upstream compatibility interfaces. The renderer talks to a headless engine process through the JSON-RPC/WebSocket gateway and reuses the agent runtime rather than embedding a second agent.

### Verification

Run before opening a PR (lint may surface pre-existing warnings but must exit cleanly):

```bash
npm run fix
npm run typecheck
npm run lint
npm run test:desktop:all
```

### Troubleshooting

Boot logs land in `HERMES_HOME/logs/desktop.log` (includes backend output and recent Python tracebacks) — check it first if the app reports a boot failure.

**macOS / Linux:**

```bash
# Force a clean first-launch setup
rm "$HOME/.atlas/hermes-agent/.hermes-bootstrap-complete"
# Rebuild a broken Python venv
rm -rf "$HOME/.atlas/hermes-agent/venv"
# Reset a stuck macOS microphone prompt (macOS only)
tccutil reset Microphone com.dealerbox.atlas
```

**Windows (PowerShell):**

```powershell
# Force a clean first-launch setup
Remove-Item "$env:LOCALAPPDATA\atlas\hermes-agent\.hermes-bootstrap-complete"
# Rebuild a broken Python venv
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\atlas\hermes-agent\venv"
```

> The default Atlas home on Windows is `%LOCALAPPDATA%\atlas`. Set the `HERMES_HOME` compatibility variable if you've relocated it.

---

## License

MIT — see [LICENSE](../../LICENSE).

Atlas is built by DealerBox on the MIT-licensed Hermes Agent engine.
