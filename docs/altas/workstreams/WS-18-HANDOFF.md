# WS-18 Handoff — Live Atlas Teams channel routing

## Status

- Branch: `codex/ws-18-channel-routing`
- Draft PR: none (this workstream must not open one)
- Baseline: `3a271d5ce` (`origin/main` when WS-18 began)
- Last validated: 2026-07-21

## Scope delivered

Atlas Teams channels and DMs now run real worker turns. A channel send is recorded once, stable worker-ID mentions are resolved, the turn fans out concurrently over real per-profile gateway connections, and real streamed/final worker output is rendered and persisted with worker attribution. No response is simulated.

The WS-14 draft-only state and DM handoff were replaced with:

- one durable channel-owned backend session per `(channelId, workerId)`;
- lazy `session.create`, followed by `session.resume` on later channel turns;
- mention-only group routing and implicit single-target DM routing;
- append-only renderer-persisted transcripts and explicit error events;
- a Slack-style inline member picker with avatars, filtering, ID-bound mention pills, and keyboard navigation;
- concurrent response streaming with per-worker failure isolation;
- English, Simplified Chinese, Japanese, and Traditional Chinese copy.

No Electron main/preload, Python engine, normal chat transcript, profile-management UI, starmap, or model-tool surface changed.

## Session-binding design

A channel remains a routing layer over isolated worker sessions. It never creates one shared model context.

For each target worker:

1. The renderer asks `ensureGatewayForProfile(workerId, false)` for that profile's real gateway. The new `false` mode opens/reuses the profile socket without changing the foreground chat's active gateway pointer, so several workers can run concurrently without racing visible profile state.
2. If the channel has no binding for the worker, routing calls `session.create` with `profile: workerId`, `source: desktop`, and a channel-identifying title. The returned durable `stored_session_id` is persisted immediately in the channel record.
3. If a binding exists, routing calls `session.resume` with that stored ID and profile. The returned live runtime ID is used only for the current `prompt.submit` and stream filtering.
4. The channel text is sent through ordinary `prompt.submit` as one normal user turn. No history is rewritten and no synthetic user message is injected mid-loop.
5. `message.delta`, `message.complete`, and `error` events are filtered by the live runtime session ID. Final results become append-only channel transcript events.

A transient gateway/resume failure retains the durable binding and its history. A definite `session not found` error clears only that stale binding; the failed turn is recorded honestly, and a later user turn may establish a new session because the old backend row no longer exists.

This preserves prompt-cache prefixes and strict user/assistant role alternation independently for every channel worker.

## Turn and response ordering

Group channels enforce mention-only routing in v1. Stable mentioned IDs are de-duplicated and filtered against channel membership in mention order. DMs always target their one member, so an `@` mention is optional.

Fan-out uses `Promise.all`, so worker sessions run concurrently. Live stream rows appear independently as deltas arrive. Persisted final response/error entries are appended in target order after the fan-out settles, giving the transcript deterministic ordering while allowing one worker's failure to coexist with every other result.

A group message with no stable mention is still appended once and wakes nobody. The composer shows a subtle “message recorded” hint.

## Channel prompt envelope

Every targeted worker receives the same compact, stable user-message envelope:

```text
[Atlas channel v1 | channel=#service-reports | sender=user | mentioned-worker-ids=parts,service]
Please compare [mention:Parts Manager (parts)] with [mention:Service Advisor (service)].
```

The envelope is part of the normal user prompt, not the system prompt. This names the channel, sender, and mentioned stable IDs without mutating cached system context. Routing tokens are converted to readable identity markers before submission so Hermes context-reference preprocessing does not interpret them as file/tool directives.

## Mention token and autocomplete contract

The persisted token is:

```text
<@${encodeURIComponent(workerId)}>
```

The composer and transcript render that token as an `@Display Name` pill, but routing always decodes and resolves the embedded stable worker ID. Typing a display name as plain text (for example, `@Parts Manager`) does not wake a worker.

Typing `@` opens an inline list of current channel members with avatar, display name, and stable ID. The query filters by display name or ID. Up/Down wrap the active row, Enter or Tab inserts the pill, and Escape closes the list. The same composer is used for DMs.

## Transcript schema and v1 → v2 migration

Storage moved from `atlas.desktop.channels.v1` to `atlas.desktop.channels.v2`. The v2 value remains a JSON array of channel records and adds:

```text
sessionBindings: {
  [workerId]: {
    sessionId: string,     // durable stored_session_id, not a live runtime ID
    createdAt: ISO-8601
  }
}

transcript: [{
  id: string,
  createdAt: ISO-8601,
  turnId?: string,
  sender: { kind: "user" } | { kind: "worker", workerId: string },
  status: "message" | "error",
  text: string
}]
```

Transcript mutation has one public operation: append. Binding/archive updates preserve existing transcript entries unchanged.

On first v2 load with no v2 key, valid v1 channel metadata is hydrated, `sessionBindings` and `transcript` start empty, and the record is written to v2. The exploratory WS-14 `all-members` value migrates to `mention-only`, which is the only active v1 routing policy. Invalid rows and duplicate channel/message IDs remain ignored. The legacy key is then removed.

This remains workstation-local renderer persistence. A future backend-owned transcript migration should read v2 once, import channels/events/bindings into an authoritative backend store with idempotent IDs, then retire renderer writes. It must not keep two writable transcript authorities.

## Loop prevention

Worker output is terminal in v1. A worker response is appended directly to the transcript and is never passed back through `sendChannelTurn`. `channelEventTargets()` returns no targets for every worker-authored event, including a response containing valid `<@workerId>` tokens. Bounded worker-to-worker hops require a separate policy, hop metadata, deduplication, and budget.

## Authorization and provenance

Channel membership grants routing only. Every turn still runs inside the target worker's existing profile backend, configuration, tools, credentials, memory, and authorization policy; sharing a channel cannot widen those capabilities. Transcript events retain stable worker attribution, timestamps, turn correlation, delivery status, and the worker's own durable session binding. This follows the federation policy's correction-over-permission posture without auto-merging memory or moving secrets between profiles.

## Failure behavior

- Missing/deleted target: append an attributed worker error; do not call a gateway.
- Backend/session creation failure: append the real error for that worker.
- Resume/submit/stream failure: append the real error and retain other workers' results.
- Empty successful completion: append an explicit empty-response error instead of inventing content.
- Channel with all workers deleted: show a persistent honest banner; user messages can still be recorded.
- No stable mention in a group channel: store the user message, run no worker, and show a subtle hint.

## Files changed

- `apps/desktop/src/store/channels.ts`
- `apps/desktop/src/store/channels.test.ts`
- `apps/desktop/src/store/gateway.ts`
- `apps/desktop/src/app/channels/index.tsx`
- `apps/desktop/src/app/channels/channel-composer.tsx`
- `apps/desktop/src/app/channels/transcript.tsx`
- `apps/desktop/src/app/channels/routing.ts`
- `apps/desktop/src/app/channels/routing.test.ts`
- `apps/desktop/src/app/channels/routing-runtime.test.ts`
- `apps/desktop/src/app/channels/desktop-channel-gateway.test.ts`
- `apps/desktop/src/app/channels/mention-autocomplete.ts`
- `apps/desktop/src/app/channels/mention-autocomplete.test.ts`
- `apps/desktop/src/app/chat/sidebar/channels-section.tsx`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/zh-hant.ts`
- `docs/altas/workstreams/WS-18-HANDOFF.md`

## Automated validation

Passed on 2026-07-21:

```text
npx vitest run --environment jsdom --config apps/desktop/vite.config.ts \
  apps/desktop/src/app/channels apps/desktop/src/store/channels.test.ts

Test Files  5 passed (5)
Tests      22 passed (22)
```

Coverage includes stable-ID mention resolution, mention-only targets, implicit DM targets, terminal worker-output loop prevention, context-envelope formatting, v1 → v2 migration, transcript attribution/persistence, durable binding reuse through resume, no-mention behavior, concurrent partial failure, real gateway RPC parameters/event filtering, and autocomplete filtering/keyboard state.

```text
npx vitest run --environment jsdom --config apps/desktop/vite.config.ts \
  apps/desktop/src/app/gateway/hooks/use-gateway-boot.test.tsx \
  apps/desktop/src/store/profile.test.ts

Test Files  2 passed (2)
Tests      13 passed (13)
```

```text
npx tsc --noEmit -p apps/desktop
```

TypeScript completed with exit code 0 and no output. Focused Desktop ESLint completed with exit code 0. `git diff --check` completed with exit code 0.

## Manual Electron E2E walkthrough

This was not run in the workstream session; follow these exact steps on a machine with a configured model provider:

1. Start Atlas Desktop from this branch and wait for the backend-ready state.
2. Open Workers and create two configured workers with distinguishable names, for example `service-advisor` / “Service Advisor” and `parts-manager` / “Parts Manager”. Ensure each can answer a normal direct chat first; this verifies its provider credentials independently of channels.
3. In the chat sidebar, click the Channels `+`, choose Channel, name it `service-reports`, select both workers, and create it.
4. In the channel composer type `@par`. Verify the inline picker contains Parts Manager with its avatar and stable ID. Press Down/Up, then Enter. Verify the typed trigger becomes an `@Parts Manager` pill.
5. Add `Summarize your role in one sentence.` and send. Verify exactly one user message appears, Parts Manager shows a live “responding” row/deltas, Service Advisor does not wake, and the final real response is attributed to Parts Manager.
6. Mention Parts Manager again and ask `What did I ask you in the previous channel turn?`. Verify the answer reflects the prior turn, proving the channel resumed its bound session rather than creating a fresh one.
7. Send a message that mentions both workers. Verify both stream concurrently and each final response has the correct avatar/name. If practical, temporarily break one worker's provider configuration and repeat; verify the healthy worker's real answer remains and the failed worker gets an attributed error entry.
8. Send `This is just a note` without a mention. Verify the message remains in the transcript, no worker starts, and the composer reports that nobody was mentioned.
9. Create a DM to Service Advisor. Send `Reply with the word live.` without an `@`. Verify the reply appears inside the DM transcript (not a handoff to normal chat), then send a follow-up and verify context is retained.
10. Quit and relaunch Atlas Desktop. Reopen both conversations and verify messages, attribution, errors, and DM/channel session bindings survived. Send another follow-up to confirm resume still works after backend restart.
11. Delete one worker used by a DM, refresh/reopen the DM, and send once. Verify the channel shows its unavailable-member state and appends an honest delivery error rather than a simulated response.

## Deferred items

- Worker-to-worker hops, bounded routing budgets, hop provenance, and deduplication.
- Backend-owned/synced transcripts and channel metadata, including cross-device migration from renderer v2.
- Unread counts, read cursors, notifications, and sidebar badges.
- Per-worker cancellation/retry controls and channel-native handling for interactive approval/clarify prompts.
- A future opt-in all-members policy; v1 intentionally enforces mention-only for group channels.

## Known limitations

- Renderer `localStorage` is workstation-local, quota-bound, and not part of Hermes profile/session backup.
- Live delta rows are transient. Deterministically ordered final events are persisted after the current fan-out settles; closing the app mid-turn can lose unfinalized stream text.
- Channel-owned backend sessions may also appear in the ordinary session inventory because no Python/session-list source filter was added in this workstream.
- Unread counts are intentionally absent rather than fixed/simulated.
