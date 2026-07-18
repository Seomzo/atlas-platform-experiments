# Multi-Agent Channels ("Atlas Teams") — future product direction

Status: **future direction, captured July 18, 2026**. No workstream or
schedule. Does not override the active milestone (WS-00). Related:
`MOBILE_COMPANION.md` (channels should eventually surface there too).

## 1. Product intent (Omar, July 18, 2026)

A Slack-inspired collaboration layer inside Atlas Desktop (and later the
mobile companion):

- **Channels / group chats** — persistent named conversations, each with
  its **own context** (history, memory scope, files), tracking different
  topics or work areas.
- **Multi-agent membership** — a channel can contain the user plus
  multiple agents/profiles (e.g. Atlas the supervisor, "Claude" and
  "Codex" coding agents, a fixed-ops worker). Agents can address each
  other, not just the human.
- **Profile management UX** — creating, configuring, and switching
  profiles/agents as first-class desktop citizens (roster, avatars,
  status), not config-file surgery.

Two tiers:

1. **Omar's setup (now, no product work):** named coding agents Claude
   (Claude Code) and Codex (Codex CLI) that Omar or Atlas can talk to —
   implemented today with skills + tmux sessions + profiles
   (`atlas-coding-agent-team` skill). Personal scaffolding, not shipped.
2. **Product scaffolding (default in the app, later):** the channel/
   group-chat system built into Atlas Desktop for every customer —
   e.g. a dealership GM with a #service-reports channel (Atlas worker +
   service manager) and a #parts channel with a different context.

## 2. What the engine already provides (leverage)

- **Profiles** — fully isolated instances (own HERMES_HOME, memory,
  skills, sessions). Multi-agent = multi-profile; exists today.
- **Sessions with isolated context per conversation** — the session
  store already scopes context per chat; a "channel" is close to a named
  persistent session bound to specific participants.
- **Kanban board** — existing multi-profile work queue (dispatcher,
  workers, comments) proves cross-profile coordination works; channels
  are the conversational sibling of that machinery.
- **Gateway group-chat handling** — Telegram/Discord adapters already
  handle multi-party rooms, @-mentions, and turn-taking heuristics; the
  interaction model is solved, just not surfaced natively in desktop.
- **Delegation** — `delegate_task` gives agent→agent tasking; channels
  make that conversation visible and steerable instead of hidden.

## 3. What would be genuinely new

1. **Channel model** — named, persistent, multi-participant conversations
   with per-channel context/memory scoping and retention rules.
2. **Agent-to-agent turns in one transcript** — today one agent owns a
   session; a channel needs routing (mentions, turn rules, loop
   prevention: two agents must not ping-pong forever).
3. **Desktop UX** — sidebar with channels/DMs, roster, unread state,
   per-channel settings. Slack-shaped, Atlas-branded.
4. **Profile management UI** — create/configure/retire agents visually
   (name, model, skills, toolsets, workdir).
5. **Cost/attention policy** — every agent in a channel is an LLM; policy
   for when each member wakes (mention-only default) is a cost control,
   not a nicety.

## 4. Constraints

- Per-conversation prompt caching is sacred (AGENTS.md): a channel member
  keeps its own stable message history; the channel is a routing/fan-out
  layer OVER per-agent sessions, not one shared mutable context all
  agents rewrite. Design must respect strict role alternation per agent.
- Authorization stays deterministic and per-profile: channel membership
  must never widen what an agent may do (a coding agent in a channel with
  the Tekion worker gains no Tekion access).
- Customer tier ships AFTER the core managed-worker milestones; this is a
  differentiator, not the wedge. Sequencing: WS-00 → Control Plane
  phases; channels slot naturally alongside/after WS-06 (Control Center)
  once profiles are commercially meaningful (multi-worker rooftops).

## 5. Open decisions

1. Naming/metaphor: channels vs. rooms vs. teams.
2. Is a channel a new gateway "platform" adapter (reuses everything) or a
   first-class desktop-native construct?
3. Turn policy defaults: mention-only vs. always-listening members.
4. Whether Omar's Claude/Codex agents remain terminal-side or become
   desktop profiles visible in the roster (nice dogfood for the feature).
5. Cross-device sync when the mobile companion arrives (channels are the
   obvious mobile surface).
