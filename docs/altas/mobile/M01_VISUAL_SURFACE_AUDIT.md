# M01 Visual Surface Audit

Status: current-run visual audit, 2026-08-12. This document evaluates existing
Atlas surfaces for mobile reuse; it does not propose that either desktop UI be
shrunk into an iPhone app.

## Evidence boundary

The audit captured thirteen screenshots under the ignored local directory
`artifacts/mobile-readiness/current-surfaces/`:

- Screenshots `01`-`04` and `13` show the baseline Control Center served from a
  loopback-only, deterministic fixture Control Plane. Its tenant, store, worker,
  job, usage, and audit data are synthetic. The shared development bearer is
  visibly disclosed and is not production authentication.
- Screenshots `05`-`12` show a separate **dirty Desktop voice/task prototype**
  launched with isolated Atlas/Electron homes and a deterministic loopback
  OpenAI-compatible provider. Screenshot `07` exercised the real local
  JSON-RPC gateway, safe tool execution, and streaming renderer. This is useful
  interaction evidence, not a clean-baseline or release-build claim.
- No real provider key, dealership credential, customer data, hosted Atlas
  service, phone, or iOS simulator was used.
- The screenshot directory is intentionally uncommitted. Hash prefixes below
  identify the files inspected during this run; they are not a substitute for
  reproducible automated visual tests.

Health labels in the walkthrough:

- **Healthy**: the tested state communicates its purpose and status clearly.
- **Needs attention**: usable in the tested desktop state, but clarity,
  accessibility, density, or mobile adaptation is unresolved.
- **High risk**: an action or mental model could be unsafe if reused for mobile.
- **Blocked**: the user cannot complete the intended flow without a missing
  configuration or production contract.

## Numbered user-flow audit

1. **Open the local Control Center and establish context — Healthy.**
   Screenshot `01` immediately names the surface, marks it `LOCAL PROTOTYPE`,
   shows `CONTROL PLANE ONLINE`, and repeats the development-authentication
   warning. Overview metrics and the “Every worker. Under orders.” statement
   make the operator purpose legible. The very large hero consumes most of the
   first viewport, however, and the smallest uppercase labels are too dense for
   a phone summary.

2. **Inspect worker availability and assignments — Needs attention.**
   Screenshot `02` puts device connection, runtime state, assigned worker, and
   disable control in one ledger. Text labels accompany green state, so status
   is not color-only. On mobile, the wide device and agent tables must become
   prioritized records; a horizontal table or tiny “Disable” affordance should
   not be ported.

3. **Review or queue work — High risk.**
   Screenshot `03` clearly distinguishes the dispatch form from job history and
   names tenant, store, agent, device, capability, and payload. That explicit
   context is worth preserving. The page also places an operator mutation and
   dense ledger side by side, uses raw JSON, and relies on a shared development
   bearer. A phone must not inherit this admin form as a general job executor;
   consequential operations require role-aware policy and WS-09 approvals.

4. **Trace what happened — Healthy.**
   Screenshot `04` presents action, actor, outcome/reason, resource context, and
   time in a searchable audit ledger. This is the strongest Control Center
   pattern for mobile reuse: concise, immutable, plain-language state backed by
   expandable detail. Mobile still needs pagination, event grouping, localized
   times, and privacy-safe payload rules.

5. **Launch Desktop in an isolated first-run state — Blocked.**
   Screenshot `05` stops at “Memory model setup needs attention” and asks for a
   separate OpenRouter credential. The failure is explicit, but the centered
   gate leaves a new user with no safe local/offline continuation and introduces
   provider setup before Atlas identity. A companion phone should authenticate
   to Atlas and its paired worker; it must not ask for model-provider keys.

6. **Attempt deterministic custom-provider setup — Blocked.**
   Screenshot `06` adds “No API key configured for provider `custom`. First
   message will fail” while the memory-provider gate remains. Both messages are
   visible and no secret is prefilled, which is good failure hygiene. The two
   setup concepts compete, and the action hierarchy does not explain why a
   deterministic local endpoint needs a memory provider. This is Desktop setup
   debt, not a mobile onboarding model.

7. **Submit a prompt, run a safe tool, and stream the answer — Healthy.**
   Screenshot `07` proves a real gateway turn through a deterministic local
   provider: the prompt, live tool row, safe `printf` result, final streamed
   answer, session list, model label, and gateway-ready state are all visible.
   This is the clearest reusable mobile nucleus. The phone version should keep
   transcript continuity and compact tool status while dropping the permanent
   desktop rail and secondary operational chrome.

8. **Inspect model settings — Needs attention.**
   Screenshot `08` shows a coherent flat settings system, current provider/model,
   reasoning, and auxiliary routes. It is information-dense, uses small tertiary
   text, and exposes engine configuration that a managed companion should not
   own. Mobile may reuse a read-only worker/model identity label; provider and
   auxiliary routing stay with Desktop/operator surfaces.

9. **Inspect voice settings — Needs attention.**
   Screenshot `09` exposes STT/TTS provider, transcript echo, auto-speak, local
   model, language, shortcut, and maximum duration. Controls are consistently
   aligned and states have text labels. It is still a desktop configuration
   sheet: keyboard shortcut and worker-side transcription settings do not map
   directly to iOS. Mobile needs a smaller permission-and-behavior surface with
   editable transcript, captions, audio route, and privacy explanation.

10. **Inspect provider account connection — Needs attention.**
    Screenshot `10` distinguishes subscription sign-in from API keys and labels
    a recommended provider. This is not Atlas account identity, enrollment, or
    worker pairing. Reusing it as phone sign-in would conflate a model vendor
    with the Atlas user and violate the WS-05 boundary.

11. **Inspect local versus remote gateway settings — High risk.**
    Screenshot `11` explains local and remote gateway modes and offers a remote
    URL plus reconnect. The explanatory copy and diagnostic link are useful.
    The direct remote-gateway model is not the intended mobile architecture:
    entering a worker URL on the phone would bypass the authenticated Control
    Plane relay and expose a reusable connection target. Mobile should show only
    the enrolled worker/pairing and its availability.

12. **Reach and reject an approval request — High risk.**
    Screenshot `12` captures the later live approval row for a harmless
    `python3 -c print(...)` check. The request was explicitly rejected and the
    tool ended blocked (`exit -1`); no consequential command was authorized.
    An earlier synthetic `git push` row visible above it did execute from an
    empty isolated `/tmp` workspace and failed with exit `128` because it was
    not a Git repository; it did **not** trigger the approval gate. No repository
    or remote changed, but this is a policy-coverage gap, not proof that the
    command was safely intercepted. The live Run/Reject affordance is good
    interaction evidence; it lacks the production action target, job, actor,
    expiry, single-use, and audit contract required by WS-09.

13. **Lose the Control Plane connection — Healthy.**
    Screenshot `13` changes the header to `CONNECTION FAILED`, adds an inline
    `CONTROL PLANE UNAVAILABLE` alert, retains the last ledger, and offers retry.
    Preserving last-known context while clearly marking it stale is a strong
    pattern. A mobile relay also needs explicit worker-versus-plane outage,
    last successful sync, queued-message state, and non-destructive retry.

## Screenshot inventory

| # | Local evidence file | Pixels | SHA-256 prefix | Surface and state |
| --- | --- | --- | --- | --- |
| 01 | `artifacts/mobile-readiness/current-surfaces/01-control-center-overview.png` | 1073x768 | `00812617a26d` | Control Center overview, fixture plane online |
| 02 | `artifacts/mobile-readiness/current-surfaces/02-control-center-fleet.png` | 1073x768 | `2d46df442ce6` | Fleet device and agent assignments |
| 03 | `artifacts/mobile-readiness/current-surfaces/03-control-center-jobs.png` | 1073x768 | `46b16b356874` | Job dispatch form and succeeded fixture job |
| 04 | `artifacts/mobile-readiness/current-surfaces/04-control-center-audit.png` | 1073x768 | `135a920d3121` | Allowed policy/job audit events |
| 05 | `artifacts/mobile-readiness/current-surfaces/05-desktop-first-launch-memory-gate.png` | 1172x768 | `8cd30daa20d1` | Isolated Desktop memory-provider first-run gate |
| 06 | `artifacts/mobile-readiness/current-surfaces/06-desktop-setup-failure.png` | 1172x768 | `fe613fc8fd87` | Custom provider plus memory setup failure |
| 07 | `artifacts/mobile-readiness/current-surfaces/07-desktop-tool-streaming.png` | 1172x768 | `e810f061ac28` | Deterministic real gateway/tool/stream proof |
| 08 | `artifacts/mobile-readiness/current-surfaces/08-desktop-model-settings.png` | 1172x768 | `41d5897d0315` | Model and auxiliary route settings |
| 09 | `artifacts/mobile-readiness/current-surfaces/09-desktop-voice-settings.png` | 1172x768 | `ddd5c5ecab94` | STT/TTS and voice behavior settings |
| 10 | `artifacts/mobile-readiness/current-surfaces/10-desktop-provider-account.png` | 1172x768 | `b2293e7d14c6` | Model-provider subscription account screen |
| 11 | `artifacts/mobile-readiness/current-surfaces/11-desktop-gateway-settings.png` | 1172x768 | `d7f0a59414f4` | Local/remote direct gateway settings |
| 12 | `artifacts/mobile-readiness/current-surfaces/12-desktop-approval-request.png` | 1172x768 | `7fc959edf510` | Live local approval UI; later harmless probe rejected |
| 13 | `artifacts/mobile-readiness/current-surfaces/13-control-center-disconnected.png` | 1073x768 | `12684695f122` | Control Plane unavailable with stale ledger retained |

## Design-language findings

### Control Center

The Control Center has a distinct operator language: graphite/bone surfaces,
orange signal accents, grid lines, condensed display type, monospace metadata,
hard-edged ledgers, and explicit environment stamps. The implementation in
[`styles.css`](../../../altas/control_plane/static/styles.css) reinforces the
visual hierarchy with shared tokens, visible focus, responsive breakpoints,
reduced-motion handling, and a higher-contrast mode. The structure in
[`index.html`](../../../altas/control_plane/static/index.html) uses a skip link,
landmarks, labels, live regions, alerts, and a route announcer.

This language is effective for a staffed operations console. Its weakest mobile
traits are the oversized editorial headlines, very small uppercase metadata,
fixed information density, table-first hierarchy, and the visual prominence of
admin mutations. The orange/green/red semantics are accompanied by text in the
captured states and should remain so.

### Desktop

The Desktop prototype uses a lighter, calmer conversation language: pale blue
chrome, restrained accents, flat settings rows, borderless overlays, compact
tool activity, and persistent transcript/composer structure. Its documented
system in [`apps/desktop/DESIGN.md`](../../../apps/desktop/DESIGN.md) emphasizes
tokens, one primitive per concern, flat hierarchy, shared feedback states,
focus behavior, reduced motion, and internationalization.

The captured light theme feels closer to an everyday phone conversation app,
but it is still desktop software: permanent navigation, multi-pane assumptions,
tiny icon chrome, mouse/keyboard vocabulary, provider/gateway administration,
and long settings sheets. Only the light theme was captured; dark theme quality
was not evaluated. The visual gap between this surface and the dark industrial
Control Center is large enough that a mobile client needs an explicit shared
design decision, not an accidental hybrid.

## What the mobile client should reuse

- Atlas identity marks and a restrained signal accent, with native semantic
  colors and text labels for every state.
- Desktop's transcript, composer, session continuity, progressive tool rows,
  flat controls, error surfaces, and explicit gateway/worker status.
- Control Center's environment/context disclosure, store/worker identity,
  immutable audit vocabulary, stable outcome reasons, last-known state, and
  prominent unavailable/retry treatment.
- The shared gateway event concepts: message deltas, tool lifecycle, clarify,
  approval, background completion, error, steer, and interrupt. Mobile DTOs
  still need authorization, ordering, replay, redaction, and limits.
- Plain-language explanations before consequential actions, with the human
  summary primary and technical/audit detail progressively disclosed.
- Native platform behaviors: safe-area navigation, Dynamic Type, VoiceOver,
  system sheets, standard back behavior, haptics, microphone permission, and
  secure authentication. These should be first-class, not approximated with
  scaled desktop CSS.

## What must not be ported

- The Control Center's fixed left rail, giant hero copy, wide ledgers, raw JSON
  job form, shared `DEV AUTH`, or one-tap device-disable control.
- Desktop provider credentials, model-routing matrix, remote gateway URL entry,
  filesystem paths, terminal assumptions, or local-agent installation/setup.
- A direct phone-to-worker/public gateway connection. The product contract is
  phone -> authenticated Control Plane relay -> outbound worker connection.
- Approval UI that shows only a command and Run/Reject. Mobile approval must
  show the human action, store/worker, bounded target, requesting workflow,
  expiry, and current state, and it must re-fetch server truth before response.
- Full raw tool output in the primary transcript. Show safe status/summary
  first, with bounded detail on demand and an unavailable-local-artifact state.
- Hover-only discovery, keyboard-shortcut labels, tiny icon-only controls,
  color-only connection state, or destructive actions adjacent to navigation.

## Accessibility, density, and responsiveness

### Confirmed in source or current visuals

- Control Center includes a skip link, labelled navigation, `aria-live` regions,
  `role="alert"`, visible focus, reduced-motion rules, contrast rules, and
  text-plus-color status. Its CSS includes breakpoints at 1180, 980, 760, and
  520 pixels and a 320-pixel minimum.
- Desktop contains labelled icon controls, status/live regions, alert roles,
  focus conventions, reduced-motion handling, and a collapsing sidebar
  breakpoint. The voice activity UI includes text/live status in addition to a
  waveform.
- Both captured surfaces keep failure information visible and actionable rather
  than silently replacing content.

### Risks for mobile

- No narrow viewport was captured in this run. Control Center breakpoints exist,
  but their focus order, overflow, table transformation, and touch ergonomics
  remain unverified.
- Desktop defines a roughly 28rem chat minimum and retains desktop-oriented
  multi-pane/layout assumptions. It is not evidence of an iPhone layout.
- Many captured metadata labels, tertiary descriptions, title-bar icons,
  segmented controls, and approval actions appear smaller or lower-contrast
  than a dependable 44x44-point touch target/Dynamic Type layout. Exact point
  sizes were not measured.
- Condensed all-caps display type is effective for short operator headlines but
  should not carry mobile body copy, long status, or accessibility-critical
  labels.
- Settings screens place meaning in spatial columns; larger text could cause
  truncation or ambiguous control/label association.
- Approval, voice, and disconnection states need screen-reader announcements
  that do not repeatedly interrupt a streaming transcript.

### Mobile acceptance checks

Before calling an iPhone surface visually ready, verify at minimum:

1. Dynamic Type through the largest accessibility sizes without clipped
   transcript, composer, approval target, or navigation.
2. VoiceOver reading/focus order for streaming messages, tools, approval state,
   error recovery, and connection changes.
3. 44x44-point minimum interactive targets, visible keyboard focus where
   applicable, and non-color status cues.
4. Portrait/landscape, small/large iPhone, safe areas, keyboard, sheet detents,
   and long localized strings.
5. Reduce Motion, Increase Contrast, Bold Text, Button Shapes, Voice Control,
   Switch Control, and full text alternatives for every voice action.
6. Offline/reconnect/replay, stale state, duplicate events, expired approvals,
   revoked phone, unavailable worker, and resumed-authentication flows.

## Unverified surfaces and states

- Any native iOS app, simulator, physical-device build, TestFlight package, or
  App Store artifact.
- Phone enrollment, Atlas user sign-in, organization/store selection, pairing,
  biometric reauthentication, Keychain persistence, rotation, and revocation.
- A hosted TLS Control Plane, authenticated relay, ordered multiplexed stream,
  cursor replay, backpressure, offline queue, or real network latency/loss.
- APNs registration, lock-screen privacy, notification preferences, deep links,
  report/approval/offline pushes, and token revocation.
- iOS microphone permission, Bluetooth and route changes, phone calls, audio
  interruptions, background/lock-screen behavior, streaming audio, captions,
  barge-in, and battery impact.
- Production WS-09 approval binding, expiry, single use, replay resistance,
  reauthentication, audit, cancellation, and cross-device races.
- Clean-baseline Desktop voice/task behavior. Screenshots `05`-`12` are from a
  dirty prototype checkout and deterministic provider, not a clean release.
- Real model/provider behavior, live dealership connectors, real customer data,
  multi-store accounts, production RBAC, billing, support access, and retention.
- Desktop dark theme, Control Center narrow-screen visual QA, high-contrast OS
  modes, all supported locales, and end-to-end assistive-technology testing.

## Visual recommendation for the first mobile slice

Design only four primary states after the WS-05 and relay contracts are fixed:

1. Atlas sign-in/enrollment and one explicit paired worker/store.
2. Session list plus availability/last-sync status.
3. Transcript/composer with compact typed tool activity, steer, and interrupt.
4. Server-backed approval sheet and unavailable/reconnect state.

Prove that text-only loop before adding provider settings, operational ledgers,
push actions, or voice. The existing surfaces already show the pieces worth
preserving; the missing work is trustworthy orchestration and mobile hierarchy,
not another desktop dashboard.
