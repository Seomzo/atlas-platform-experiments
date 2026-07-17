# DealerBox / Atlas — Project History and Business Context

Distilled from Omar's working sessions (Codex CLI, July 13–16, 2026) and direct
conversation with Omar. This file exists so a contextless session can absorb
the business story without access to prior chats. Facts below are
source-labeled; treat them as reference, not authorization.

## The company and the people

- **DealerBox** is the company. Founders: **Omar Alsadoon** and his partner
  **Joe**. Major product decisions require Omar and Joe together.
- **Atlas** is the product: a managed AI worker platform for auto dealerships,
  sold to dealership higher-ups (fixed-ops managers, dealer-group admins).
- First product slice: **Atlas for Fixed Ops** (see `PRODUCT.md`).

## Where Atlas came from

- **Jay** is a high-capability agent Omar built at his dealership job. Atlas is
  in large part a productized rebuild of Jay. Jay's existing skill packs become
  the **Jay Premium** tier of Atlas skills (governed product assets, ADR-007).
- Omar also built a proprietary **skill indexer** at work — per-skill
  description, usage counts, and metadata for retrieval — which informs the
  Atlas skill index/entitlement design.
- Atlas started as a fork of the MIT-licensed **Hermes Agent** engine. An early
  design used a bridge process forwarding `atlas` → `hermes`; it was **scrapped
  for latency** in favor of a direct rebrand (`atlas` enters the engine
  directly, ADR-002). Repo dir `altas-platform` and package `altas/` keep the
  historical spelling deliberately.

## Timeline of build decisions (July 2026)

- **July 13** — Atlas repo established as rebranded Hermes. Terminal setup
  wizard rebranded (dark-blue Atlas aesthetic requested; stray Nous-portal
  routing flagged for cleanup). Doctrine set: **customers never use the
  terminal** — the desktop app is the customer surface. Workstream system
  (WS-00..WS-12) created to parallelize contextless sessions.
- **July 14–15** — **Atlas Cortex** built: the default cognitive memory system
  for every customer agent. Plan of record:
  `~/Documents/DealerBox/ATLAS_COGNITIVE_MEMORY_IMPLEMENTATION_PLAN.md`
  (also `docs/ATLAS_CORTEX.md` and `docs/plans/` in-repo). Key decisions:
  - Three federated domains: **personal brain / dealer-Tekion knowledge /
    Atlas capability graph** — fused by one retrieval gateway, authorization
    before retrieval, never physically merged.
  - GBrain-derived hot/cold personal memory mechanics; **Microsoft GraphRAG**
    for the Tekion/manual corpus; existing Starmap desktop UI evolved for
    visualization; **no GNN in v1**.
  - A **dedicated cheap model** performs memory deconstruction (not the
    frontier chat model); it runs **at end of session** (Omar considered and
    rejected concurrent/parallel processing).
  - **OpenRouter** is the working model provider choice; the memory-layer
    model should be pickable during setup.
  - The `remember` write path has a deterministic intent gate that fails
    closed — consistent with ADR-008 (prompts cannot authorize).
- **July 16** — Atlas (this assistant) brought up on Cortex for first live
  testing; observed cold-start recall noise (capability entities returned for
  conversational turns). First Cortex tuning target: recall relevance gating.

## Business goalposts

- Stated target (July 13): **production launch with multiple stores using the
  product by February**.
- Trial/pilot philosophy, deployment modes, entitlements, and launch gates are
  specified in `PRODUCT.md` and `docs/altas/ROADMAP.md`.
- Open commercial decisions (channels, billing, hardware, identity, model
  providers, pricing) are tracked in `docs/altas/DECISIONS.md` and belong to
  Omar and Joe.

## Working model

- Omar sets direction. Atlas (assistant/CTO instance) holds context,
  architecture, and review. **Codex CLI** is used for parallel implementation
  tasks (sessions readable under `~/.codex/sessions/`; dispatchable via
  `codex exec`).
- The repository — not chat history — is the project database. Durable context
  belongs in `docs/altas/` and this file.
