# Cortex Federation — Cross-Brain Knowledge Sharing (Proposed ADR-010)

Status: **PROPOSED — awaiting Omar + Joe approval.** Drafted 2026-07-20 by
Atlas from Omar's direction: workers will work together and share knowledge,
so their brains must be connectable. Companion UI: WS-16 constellation view
(renders structural connections only until this ADR lands).

## Context

Every worker (profile) owns an isolated Cortex brain (`<profile>/cortex/
cortex.db`). The supervisor (default) brain is equally isolated. The schema
already anticipates sharing: `knowledge_spaces` carry `visibility`
(`private`/`shared`) and per-space read/write policy JSON; the `tekion` space
is already `visibility=shared`. Nothing implements cross-brain access today.

Open questions recorded in WS-15/WS-16 handoffs: duplicate entities across
brains (alias vs. independent claim), space ownership/authorization,
copied vs. referenced edges, provenance and deletion propagation, conflict
reconciliation.

## Decision (proposed)

**Shared-space subscription model.** Brains stay sovereign; knowledge
spaces become the unit of sharing.

1. **Spaces have one owner brain.** The supervisor brain owns shared
   operational spaces (e.g. `tekion`). A worker brain may also publish a
   space (future; not v1).
2. **Reads: subscription at recall time.** A worker's recall pipeline may
   read spaces it is subscribed to, live from the owner's store — no
   copying, no sync jobs. Provenance stays with the owner; deletion in the
   owner space is immediately effective everywhere (no propagation problem).
3. **Writes: proposed-then-admitted.** A worker cannot write directly into
   a shared space. It writes a *proposal* (kind=proposed memory/entity in
   its own brain, tagged with the target space). The supervisor brain's
   dream cycle (or explicit review) admits, merges, or rejects proposals —
   mirroring the existing evidence-admission machinery. Admission is the
   ONLY path knowledge crosses brains.
4. **Entities never auto-merge across brains.** Same-name entities in two
   brains are independent claims until admission explicitly aliases them
   into the shared space's canonical entity (`entity_aliases` already
   exists per-brain; admission creates the alias in the owner space).
5. **Authorization is deterministic and outside the model** (ADR-008):
   subscriptions are config/policy records enforced in `CortexStore`
   resolution code. Channel membership, skills, prompts, and recalled text
   can never grant or widen subscription. A worker in a channel with the
   Tekion worker gains no Tekion-space access.
6. **Private-by-default stands.** A worker's working memory never leaves
   its brain unless written as a proposal to a shared space.

## Consequences

- Constellation view gains real cross-brain edges: worker → subscribed
  space → owner brain, plus pending-proposal indicators.
- Recall for workers gains one cross-store read path (bounded, read-only).
- Dream cycle gains an admission queue pass for the supervisor brain.
- No sync daemons, no eventual-consistency machinery, no data duplication.

## Decisions needed from Omar + Joe

1. Approve the subscription model (vs. alternatives: full sync/copy, or a
   single shared brain for all workers — both rejected in analysis for
   cache, blast-radius, and authorization reasons).
2. v1 default: are admitted Tekion learnings auto-admitted after N days
   unreviewed, or held indefinitely until explicit review?
3. Who may create shared spaces in v1 — supervisor only?
