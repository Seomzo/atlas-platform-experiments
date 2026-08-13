# Atlas for iPhone — Design System

**Direction:** Premium native iOS glass quality with restraint. Glass belongs to navigation, controls, and transient overlays; transcript content and consequential decisions remain calm, legible, and structurally flat.

## 1. Evidence and translation

M02 inspected the M01 desktop tool-streaming, approval, and disconnected control-center surfaces in their rendered state. The transferable qualities are the Atlas mark, precise state language, visible tool lifecycle, strong hierarchy, quiet neutral surfaces, and explicit failure context. Desktop rails, dense audit tables, tiny monospace metadata, provider/settings chrome, and raw command presentation do not transfer to iPhone.

The iOS system must feel related rather than miniaturized:

- Atlas mark plus wordmark on enrollment and About; mark alone in compact chrome.
- Dark navy/ink foundation with a restrained signal accent, not an always-glowing interface.
- Native bars, sheets, menus, search, haptics, and typography.
- Operational states expressed with icon + label + timestamp, never color alone.
- Tool progress as typed rows; approvals as deliberate system sheets, not inline desktop command controls.

Reject visual directions that read as a generic chatbot, crypto wallet, terminal in a phone frame, glowing-glass collection, AI-gradient template, compressed desktop dashboard, dealership marketing site, or Hermes-branded clone.

## 2. Platform baseline

- Minimum deployment target: **iOS 18.0**.
- Build reference: **Xcode 26.6** with the scoped `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`; the machine-wide selection currently points at Command Line Tools.
- Use APIs available on iOS 18 for the full product. Add iOS 26 visual-material enhancements behind `#available(iOS 26, *)` and keep equivalent hierarchy, contrast, and motion on iOS 18.
- The intended first physical test device runs iOS 26.6; that is test evidence, not the deployment floor.

## 3. Principles

1. **Clarity before atmosphere.** Content must remain readable over every material.
2. **Truth has texture.** Live, stale, replaying, offline, revoked, failed, and uncertain are visibly distinct.
3. **One intent per surface.** A screen or sheet has one dominant action.
4. **Native behavior is premium behavior.** Standard gestures, navigation, keyboard, focus, selection, and accessibility are not optional polish.
5. **Consequences slow the interface down.** Managed approvals remove decorative motion and add context, confirmation, and biometric reauthentication.
6. **Motion explains continuity.** Never animate merely to imply intelligence or hide latency.

## 4. Semantic color tokens

Exact values are starting tokens and must be verified with automated and rendered contrast checks in both appearances.

| Token | Light reference | Dark reference | Use |
|---|---|---|---|
| `surface.canvas` | `#F5F7FA` | `#080C14` | Main background |
| `surface.content` | `#FFFFFF` | `#111723` | Transcript/content surfaces |
| `surface.elevated` | system material | system material | Bars, sheets, menus |
| `text.primary` | `#111827` | `#F4F7FB` | Primary copy |
| `text.secondary` | `#566174` | `#A9B4C7` | Metadata |
| `signal.primary` | `#365CFF` | `#7895FF` | Selection and bounded primary action |
| `status.healthy` | `#087A4B` | `#48D597` | Live/complete with label |
| `status.attention` | `#A65E00` | `#FFB95C` | Waiting/approval/reconnecting |
| `status.failure` | `#B42336` | `#FF7D8D` | Failed/revoked |
| `status.uncertain` | `#6D3CCB` | `#BCA0FF` | Outcome unknown; never reuse as success |
| `separator` | system separator | system separator | Native dividers |

Rules:

- Keep brand/signal color below roughly 15% of a dense screen.
- Never put body copy directly over a saturated gradient.
- Do not use glass tint as the only status indicator.
- Respect Increase Contrast and Differentiate Without Color.

## 5. Typography

Use San Francisco through semantic SwiftUI text styles; do not hardcode point sizes in product views.

| Role | SwiftUI style | Guidance |
|---|---|---|
| Screen title | `.largeTitle` / `.title` | Native navigation behavior |
| Section | `.title3.weight(.semibold)` | Short, sentence case |
| Message body | `.body` | Selectable where safe |
| Tool/approval label | `.callout.weight(.semibold)` | One line when possible |
| Metadata | `.footnote` | Never essential information alone |
| Diagnostic code | `.caption.monospaced()` | Correlation/version only, not long raw payloads |

Support every Dynamic Type size including accessibility categories. At large sizes, cards become vertical, tool metadata wraps, and decision buttons stack. No meaningful label truncates without an accessible full value.

## 6. Spacing, shape, and layout

- Base spacing scale: 4, 8, 12, 16, 20, 24, 32, 40 points.
- Standard page horizontal inset: 16 points compact, adapting with readable width on iPad without creating an iPad information architecture.
- Minimum touch target: 44 × 44 points; prefer 48 for primary controls.
- Content container radius: 16; compact row radius: 12; sheets follow system detents and corner treatment.
- Avoid nested rounded rectangles. A thread is primarily typography and spacing with separators; only tool, approval, error, and attachment objects need contained treatment.
- Safe areas, keyboard avoidance, scroll-to-bottom affordance, and interactive dismissal follow platform conventions.

## 7. Materials and “glass”

- Use system navigation/tab bar materials and system sheets. On iOS 26, adopt the current platform glass APIs only where availability and rendered tests prove readable parity.
- Do not approximate new glass with blurred screenshots, custom shader-heavy layers, glowing borders, or translucent content cards.
- Transcript and approval body backgrounds are opaque or near-opaque enough to preserve contrast.
- Reduce Transparency replaces material with semantic opaque surfaces and visible separators.
- Screenshots and app-switcher privacy overlays must not depend on blur alone; use a branded opaque shield.

## 8. Components

### Scope chip

Shows the selected store/worker with a chevron. It is a navigation control, not an authority field. Scope changes always show a refetch transition.

### Connectivity banner

Compact when live; expands for reconnecting, stale, worker unavailable, or revoked. Includes icon, label, last-known timestamp, and one recovery action. It never auto-dismisses while the state is unresolved.

### Thread row

Title, one-line safe summary, state label, timestamp, and attention indicator. No avatar theater or generated “agent personality” imagery.

### Message block

Atlas and user roles use typography, alignment, and subtle surface changes. Streaming uses a stable block with a restrained activity indicator; it does not shimmer every token. Final content replaces the same accessibility element.

### Tool row

Typed icon, safe human label, lifecycle state, duration, expandable redacted detail. Unknown/unclassified payloads render an unsupported marker, never raw JSON or shell.

### Approval card and sheet

Attention-colored border/marker, plain-language effect, expiry, and Review action. The sheet uses native grouped sections, clear Deny/Approve buttons, no swipe-to-approve, and no success animation until the server confirms resolution.

### Composer

Native text editor, attachment slot reserved but inactive until specified, push-to-talk control only when implemented, interrupt affordance only during an interruptible active turn. Send reflects draft/submitting/accepted states. Offline drafts are local and never labeled queued.

### Empty, error, and revoked states

Use an SF Symbol, one-sentence title, one-sentence explanation, and one primary recovery action. Revoked state removes sensitive content and uses no generic Retry loop.

### Charts and reports

Prefer a summary value, comparison label, and compact native chart only when the chart answers a user question. Use semantic series colors, direct labels, visible axes/units, and a selectable/table equivalent. Do not use 3D charts, unlabeled sparklines, decorative gradients, red-vs-green alone, or a dense desktop dashboard. Report cards show provenance/freshness and open a server-authorized detail; they do not imply that a push snapshot is the report.

## 9. Iconography and brand

- Use SF Symbols for platform actions and status; choose one symbol per semantic concept and centralize mappings.
- Use the existing Atlas mark as a vector asset with light/dark variants; do not redraw it from a screenshot.
- Never use a robot head, magic sparkle, or pulsing orb as the default representation of Atlas work.
- Symbols pair with text for critical states and managed decisions.

## 10. Motion and haptics

Default duration tokens are `motion.quick = 120ms`, `motion.standard = 220ms`, and `motion.emphasis = 320ms`; use system spring/transition behavior when it communicates navigation. Loading loops may repeat but content transitions do not. Reduce Motion replaces movement with a 120ms opacity change or no animation. Never delay a state update to finish animation.

| Moment | Motion | Haptic |
|---|---|---|
| Prompt accepted | Composer resolves into stable sent message | Light impact only after durable acceptance |
| Reconnect/replay | Banner progress and stable list reconciliation | None |
| Tool completes | Subtle state crossfade | Optional light success, rate-limited |
| Approval arrives | Card insertion without bounce | Warning notification if foreground and allowed |
| Approval confirmed | Sheet state transition after server response | Success/error notification |
| Revocation | Immediate privacy shield, then recovery state | Warning |

Respect Reduce Motion: replace spatial transitions, wave animations, and live reordering with fades or instant state changes. Coalesce streaming updates to avoid layout thrash and VoiceOver noise.

## 11. Voice treatment

- Push-to-talk, never ambient listening.
- A solid center control communicates hold/record/release; elapsed time and text state accompany the waveform.
- Recording starts only after permission and an explicit press/tap. Haptic start/stop cues have visible and spoken equivalents.
- Transcript review is an ordinary editable text surface. Sending it uses the same prompt component and idempotency semantics as typed text.
- Reduce Motion uses a static level meter. VoiceOver uses start/stop buttons rather than hold-only interaction.

## 12. Accessibility requirements

- VoiceOver order, labels, values, hints, headings, rotors, modal focus, and live-region announcements are part of component acceptance tests.
- Never announce streaming tokens individually; announce “Atlas is responding,” then a concise completion.
- Support Voice Control, Switch Control, keyboard navigation where the system supplies it, Bold Text, Button Shapes, Increase Contrast, Reduce Motion, Reduce Transparency, and content-size categories.
- Charts/report summaries include textual equivalents and selectable values.
- Color contrast targets WCAG 2.2 AA at minimum: 4.5:1 normal text, 3:1 large text and meaningful UI boundaries.
- Permission copy and approval consequences use plain language at an eighth-grade reading level where possible.

## 13. Visual evidence required from M03

Capture named light/dark renders at standard and accessibility text sizes for: sign-in, enrollment, Today live/offline/revoked, Threads empty/populated, active thread with tool progress, reconnect/replay, approval pending/expired/confirmed, and Settings diagnostics. Capture the same core path with Reduce Motion and Reduce Transparency. Physical-device video must show background/foreground continuity and biometric approval without revealing biometric or account secrets.
