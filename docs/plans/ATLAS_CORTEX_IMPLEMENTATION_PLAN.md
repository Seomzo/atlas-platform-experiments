# Atlas Cortex Cognitive Memory and Knowledge Graph

## Product architecture, implementation status, and follow-on plan

Status: native baseline implemented and default-enabled for Atlas; production-scale and product-expansion work remains.

Original design: 2026-07-13

Implementation snapshot: 2026-07-14

Primary runtime reviewed: `/Users/omaralsadoon/Desktop/altas-platform`

External systems reviewed:

- GBrain (`garrytan/gbrain`, current `master` as inspected on 2026-07-13)
- Microsoft GraphRAG (current repository and documentation as inspected on 2026-07-13)
- Obsidian Graph view interaction model
- Atlas/Hermes memory, session, context-engine, plugin, profile, and desktop visualization code

Research snapshot:

- Atlas: commit `4352187d57532698b7a9c56d33b68c18a3f88500`
- GBrain: release `0.42.59.0`, commit `5008b287e47bf791132eedfebf66bdef11e9398c`
- Microsoft GraphRAG: commit `dac4f721ddc14adae9d3183cdc99e7c6ad7d9fca`

GBrain is moving quickly enough that some narrative documents describe older phase counts or older operation lists while the inspected source contains additional dream-cycle phases. Cortex therefore adopted selected mechanisms rather than taking a runtime dependency. If GBrain code is vendored later, that dependency must pin a reviewed release/commit, generate an operation compatibility report in CI, and treat current source plus tests—not an old install guide—as the executable contract.

---

## 0. Implementation snapshot

This document began as the architecture proposal and now records both the shipped Atlas Cortex baseline and its remaining product plan. This section is the source of truth for what exists in this repository. Later sections preserve the original design rationale, target-state details, and acceptance criteria; future-tense language there is not a claim that every target-state feature has shipped.

The native subsystem is named **Atlas Cortex**. Semantic consolidation is a session-end operation performed asynchronously by the dedicated cheap `atlas-cortex-memory` route. The existing **Cortex Dream** command, job names, and configuration keys are compatibility surfaces for recovery and integrity maintenance, not a nightly semantic cycle.

### Shipped in the repository

| Area | Current implementation |
|---|---|
| Default activation | Atlas-branded entrypoints set `cortex.enabled: true` and `memory.provider: cortex`; upstream Hermes remains opt-in. Existing explicit profile/managed settings still win. |
| Native integration | `CortexMemoryProvider` is registered as an Atlas-owned provider. Automatic recall is injected as volatile turn context; the stable prompt contains only compact memory doctrine. The Atlas default `SOUL.md` describes evidence, correction, privacy, and inference behavior. Selecting Cortex reserves semantic-memory ownership and suppresses the legacy mid-session frontier-model memory/skill review fork even while provider initialization is degraded, so semantic judgment cannot silently fall back to chat. |
| Lifecycle durability | Primary-agent user, assistant, tool-call, tool-result, and delegated-result evidence is captured durably as each turn completes, without semantic/model work on the foreground path. Interrupted/error Codex turns preserve the authoritative user row and completed tool evidence while excluding partial assistant output. Pre-compression flushes evidence and returns a bounded deterministic preservation note to the standard compressor; it never invokes or enqueues semantic consolidation. Required capture/rebind failures abort Atlas-controlled compaction before rewrite or rotation and are exposed through health. Codex-native auto-compaction has no preservation-note injection or pre-cancel seam, but completed turns are captured synchronously before that later observed boundary and a failed hook fails the turn. Logical finalize/reset atomically captures the final transcript epoch, hashes the complete compressed-session lineage, writes an immutable semantic admission, and creates one idempotent root job. Generic queue APIs cannot authorize semantic work. Gateway reset waits for an interrupted turn's durability barrier, reset-policy expiry defers active turns, and an inbound-before-watcher race persists the exact predecessor until finalization succeeds; context-exhaustion reset obeys the same barrier. Only a successfully owned reset-policy transition is a logical end. Max-age-only routing retention drops stale routing state without hooks, SessionDB finalization, Cortex admission, or model work. Branches atomically retain their Cortex parent lineage. A filesystem-backed boundary marker binds recovery to that exact evidence epoch, so later resumed turns cannot leak into an older finalization. Session rewind reconciliation is represented. |
| Local system of record | A profile-scoped SQLite database stores brains, principals, spaces, sessions, evidence, work events, observations, durable memories, provenance, entities, aliases, temporal relations, compiled views, communities, retrieval audits, jobs, leases, receipts, GraphRAG indexes, health, and audit rows. Parent/database permissions and symlink/path checks are enforced. |
| Identity and isolation | Local ownership is derived from the resolved profile home. Managed mode fails closed unless deployment-controlled customer or tenant/store/agent identity is present. Client requests can choose an Atlas profile but never a brain ID. Knowledge-space authorization happens before retrieval. |
| Capture and customer control | Explicit “remember” language creates a protected durable memory. `cortex_memory_control` supports status, remember, correct, and forget; every mutation requires intent from the authoritative current user row, never model/tool/recalled text, and the shared `memory.write_approval` policy applies. Forget permanently scrubs the selected memory, linked Cortex evidence, affected derived projections/query traces, and SQLite free space while retaining only a non-content audit receipt. “Don't save this” is code-enforced across normal turn, checkpoint, compression, and finalize capture. Assistant output is evidence, but cannot be promoted as customer truth by session-end consolidation. |
| Retrieval | Bounded automatic retrieval fuses lexical matches across personal evidence/memories, typed entities/relations, Atlas capability records, active Tekion GraphRAG documents, and derived community reports. Retrieval runs record source labels, selected IDs, route, timing, and match explanations. A deep-recall tool is also available for explicit exploration. |
| Session-end worker | The durable asynchronous worker uses leases, heartbeats, checkpoints, retries/dead-letter handling, operation receipts, and atomic mutation/audit transactions. Before preparation or route resolution it verifies the immutable admission plus the complete root/parent continuation chain; invalid or legacy unadmitted rows fail closed without a model call. Atlas defaults all semantic passes to the dedicated cheap `atlas-cortex-memory` utility alias, separate from the conversational model. Local overrides must name an explicit provider and an explicit different model; `auto`, `main`, blank, and the exact chat pair fail closed. Outputs are versioned, source-validated JSON; one repair attempt is allowed; cross-provider and chat-model fallback are forbidden. One job considers at most 100 due candidates in bounded prompt batches and emits an idempotent admission-scoped continuation when overflow remains. |
| Scheduling and recovery | Logical finalization is the primary and only authority for a new semantic consolidation chain. It wakes a scoped process-local worker for non-managed routes or the native profile-local managed supervisor, which creates an idempotent, dedicated `cortex.memory_maintenance` control-plane job carrying a strict privacy-safe local-job provenance envelope and processes it only after receiving a fresh claim. The official worker matches that envelope to the exact next admitted local job and leases only that job; generic queue/requeue paths reject Cortex, and claim/model/usage-reservation paths require the same dedicated payload and request-local header binding. This is official-runtime path integrity, not remote attestation of a potentially malicious authenticated device. A profile-scoped no-agent cron and multiplex ticker perform retention, deterministic reconciliation/checkpoints, lease recovery, and reconstruction of a missing canonical root only from the immutable admission ledger. Continuation repair uses a bounded newest lane plus a rotating historical page with compare-and-swap acknowledgement after complete success; crashes and capacity deferrals replay the page, while invalid leases/orphans converge in bounded targeted repairs. The 04:00 marker is integrity-only; scheduling never authorizes an independent nightly semantic pass. No-route jobs remain queued at attempt zero. Managed claims are bound to an exact profile identity, context-local, and never persisted, process-global, or borrowed from an ordinary chat turn. |
| GraphRAG | The adapter validates, snapshots, stages, atomically publishes, and rolls back immutable Tekion index artifacts. It reads no-follow regular-file snapshots under aggregate/materialization budgets, rejects nested Parquet, and sanitizes primary fields before projection. It accepts the portable Atlas JSON/JSONL format and current Microsoft GraphRAG JSON, with bounded Parquet when an engine is installed. SHA-256 is mandatory; optional Ed25519 signing becomes mandatory in managed mode. Transactional core/FTS attestations fail closed on missing or tampered retained projections, quarantine bad active indexes, and tolerate a safe first FTS capability baseline. Active version and manifest provenance are attached to retrieval. |
| Customer graph | Versioned, profile-scoped, schema-enforced graph/detail/health/job APIs power a Cortex-first desktop Starmap. It renders typed nodes/edges, chronology, selection, bounded source detail, a keyboard-accessible node list, health, index status, and manual maintenance. Graph overviews omit raw evidence/document bodies, reject internal storage fields, and legacy share codes are disabled for Cortex data. |
| Setup and operations | Desktop first run requires a dedicated memory-model choice after the chat model and atomically saves both Cortex routes from an authenticated structured-output-specific catalog. It can activate OpenRouter for memory only without changing a Nous/Anthropic chat route, and every request/cache/completion is profile-scoped and stale-response guarded. Returning or previously skipped installs are revalidated and enter visible migration when the route is invalid. Desktop, dashboard, and terminal model surfaces share the Cortex catalog; generic auxiliary/global resets preserve a verified route or fail closed; chat/memory route collisions are rejected bidirectionally; and user-owned Desktop New Chat waits for canonical semantic finalization before clearing the transcript. Stored/no-runtime and stale-runtime recovery are guarded, while drafts, attachments, and branch state remain unchanged if close fails. Setup also creates Cortex paths, backup includes the database, doctor reports Cortex health, and `atlas cortex` exposes status, dream, and GraphRAG index operations. |

### External inputs and remaining scope

- **No live Tekion corpus is bundled.** The import/publish path is operational, but a governed corpus must be built, evaluated, signed as policy requires, placed under the configured index root, and explicitly published.
- **The shipped local canonical store is SQLite.** It is appropriate for the dedicated-machine baseline and pilot. A managed PostgreSQL/service adapter, row-level security, encryption-key service, and production migration/restore program are not implemented in this runtime.
- **This is GraphRAG and a typed temporal knowledge graph, not a graph neural network.** Cortex does not train or run a GNN.
- **Personal retrieval is currently lexical plus typed-graph/community fusion.** Personal vector embeddings, a cross-encoder reranker, and measured A/B rollout gates remain follow-on work.
- **The desktop Cortex graph is currently an inspect/health surface.** It does not yet expose correction, pin/privacy, permanent-delete, entity merge/split, answer-path browsing, every planned projection, or Obsidian export. Remember/correct/forget are currently available through the agent's explicit memory-control path. Forget is a permanent live-Cortex record erasure. Native explicit session deletion separately coordinates full local SessionDB compression/delegate scope with Cortex admission revocation, raw/derived content scrubbing, and live SQLite free-space cleanup; provider logs, external systems, and historical/offline backups remain separate deletion domains.
- **A model credential is not required for capture, ordinary lexical/graph recall, health, or visualization.** Finalized-session promotion waits in the durable queue when no approved `atlas-cortex-memory` route is configured; Cortex never silently sends personal evidence to a fallback or conversational provider.
- **GBrain was used as architectural research, not vendored as a runtime dependency.** Cortex implements selected hot/cold memory, evidence, temporal, dream, and audit mechanisms natively so Atlas lifecycle and privacy rules remain authoritative.
- **Codex-native compaction exposes capture but not preservation-note injection.** Automatic recall still reaches Codex as protocol-native untrusted additional context, and Cortex controls are native dynamic tools. Explicit Codex compaction fails closed if the durability barrier fails. For Codex's own automatic compaction event, completed turns are already synchronously durable and the observed boundary checkpoint can degrade health, but only the standard Atlas compressor can place Cortex's bounded preservation note directly into its summarization prompt.

The concise operating guide is [Atlas Cortex: Customer and Operator Guide](../ATLAS_CORTEX.md).

---

## 1. Executive decision

Atlas now ships with a cognitive memory system enabled as part of the default customer agent, not as an optional prompt convention the agent may or may not follow.

The system should present itself as one personal brain, but internally it should be a federation of three knowledge domains:

1. **Personal brain** — facts, preferences, commitments, relationships, session outcomes, customer-created workflows, corrections, and long-term user context. This is private to one customer/agent identity and is never promoted into shared knowledge.
2. **Dealer/Tekion knowledge** — manuals, policies, dealership process documentation, structured Tekion concepts, and approved operational knowledge. This is versioned, centrally curated, and read-only from the customer agent's perspective.
3. **Atlas capability graph** — skills, workflows, tools, artifacts, sessions, and their observed use. This explains what Atlas can do and how work was performed.

These domains are fused by one native Atlas retrieval path and graph API, with authorization applied before retrieval. They are not physically merged into one tenant-agnostic graph.

The recommended reuse boundary is:

| Concern | Recommended owner | Decision |
|---|---|---|
| Per-turn and cross-session lifecycle | Atlas/Hermes | Reuse and extend |
| Context-window compression | Atlas/Hermes context engine | Retain; do not replace with memory |
| Raw session durability and FTS recall | Atlas/Hermes session store | Retain as evidence/history |
| Personal hot/cold memory | Native Atlas Cortex | Implement selected GBrain-derived mechanisms behind the native provider lifecycle |
| Tekion/manual corpus graph | Microsoft GraphRAG | Use as an offline/versioned indexing pipeline |
| Unified retrieval policy | Atlas Cortex | Baseline built; advanced ranking remains planned |
| Customer memory visualization | Existing Atlas Memory Graph/Starmap | Cortex-first typed view shipped; continue evolving |
| Obsidian | Interaction inspiration and optional Markdown export | Do not use as the product database or embedded UI |
| Graph neural network | None in v1 | Defer until measured retrieval or prediction needs justify it |

This is a knowledge graph and GraphRAG system, not initially a graph neural network. A GNN would be a learned model operating over graph structure. Nothing in the initial problem requires training one, and adding one prematurely would make the system harder to explain and audit.

---

## 2. Atlas foundation extended by Cortex

The proposal did not start from zero: Atlas already contained the correct lifecycle seams and a meaningful graph UI. Cortex now uses and extends these foundations as described below.

### 2.1 Identity and directives

- `SOUL.md` is loaded as the stable identity slot in `agent/system_prompt.py` and `agent/prompt_builder.py`.
- Project directives are discovered from `.hermes.md`/`HERMES.md`, `AGENTS.md`, `CLAUDE.md`, and Cursor rule files. The current loader uses a priority path rather than composing every directive file indiscriminately.
- `MEMORY.md` and `USER.md` are injected as bounded, volatile memory/profile snapshots.
- The system prompt is deliberately split into stable, contextual, and volatile sections to preserve provider prompt caching.

Implication: personality and operating doctrine belong in the stable identity/directive layer. Dynamic recall must stay out of `SOUL.md`; it belongs in volatile per-turn context.

### 2.2 Durable sessions and context compression

- Full sessions are persisted and searchable through Atlas's session store and `session_search`.
- The context engine compacts an active context window and emits a handoff summary.
- Compression is not durable semantic memory. It solves a different problem and should remain independently testable.
- `MemoryProvider.on_pre_compress(messages)` is already called before old context is summarized/discarded.

Implication: context rot is addressed by two cooperating systems:

- compression preserves short-term conversational continuity;
- cognitive memory promotes selected evidence into cross-session recall.

Neither should silently stand in for the other.

### 2.3 External memory provider seam

`agent/memory_provider.py` and `agent/memory_manager.py` already provide the right integration lifecycle:

- `initialize`
- `system_prompt_block`
- `prefetch`
- `queue_prefetch`
- `sync_turn`
- `on_turn_start`
- `on_session_end`
- `on_session_switch`
- `on_pre_compress`
- `on_memory_write`
- `on_delegation`
- `shutdown`
- `backup_paths`

Atlas also limits the runtime to one external memory provider, which is desirable: it prevents overlapping recall tools and inconsistent storage policies.

Implementation: Cortex is connected through a first-class native `MemoryProvider` and implements the selected GBrain mechanisms itself. It is not installed as 43 prompt-driven skills. Lifecycle enforcement is stronger than agent initiative.

### 2.4 Correct session boundary

Atlas exposes multiple superficially similar hooks:

- `on_session_end` in the generic plugin/turn path can occur at a run boundary and must not be treated as the only source of truth for logical conversation completion.
- `on_session_finalize` represents the explicit logical session-finalization boundary.
- `MemoryProvider.on_session_end` is wired to actual memory-provider shutdown/session rotation paths.
- `on_session_reset` covers a new/reset boundary.

Implication: semantic distillation must be keyed only to the explicit logical finalization boundary, not to every completed response, a physical session rotation caused by compression, or the scheduled recovery clock. The idempotency input covers every physical session in the logical conversation's compression lineage.

### 2.5 Existing customer-facing Memory Graph

Before Cortex, Atlas already shipped a `/starmap` “Memory Graph” surface:

- radial, time-based Canvas visualization;
- `d3-force` simulation;
- graph/timeline replay and scrubbing;
- nodes for learned skills and `MEMORY.md`/`USER.md` chunks;
- profile-scoped backend data;
- node hover/focus and adjacency highlighting;
- edit/delete/archive actions;
- share-code support that excludes memory text;
- backend endpoint `GET /api/learning/graph`;
- mutation endpoints for individual learning nodes.

Relevant files include:

- `agent/learning_graph.py`
- `agent/learning_mutations.py`
- `agent/learning_graph_render.py`
- `hermes_cli/web_server.py`
- `apps/desktop/src/app/starmap/*`
- `apps/desktop/src/store/starmap.ts`
- `apps/desktop/src/types/hermes.ts`

Implementation: the existing Memory Graph remains the visual foundation. Cortex adds a bounded, typed, provenance-aware graph contract while preserving its distinctive time-radial experience; the legacy graph remains a compatibility fallback.

---

## 3. What GBrain contributes

GBrain is a strong substrate, but it is not a drop-in product architecture for Atlas.

### 3.1 Mechanisms worth reusing

- PostgreSQL/pgvector at scale and PGLite for local development or small pilots.
- Hybrid retrieval: vector similarity, lexical/BM25-style matching, reciprocal-rank fusion, graph traversal, source boosts, reranking, aliases, and evidence-aware scoring.
- Typed links and entity-centric pages.
- Hot conversation facts versus consolidated cold takes.
- Append-only evidence/timeline paired with rewritten “compiled truth.”
- Synchronous evidence capture plus asynchronous lifecycle-driven consolidation.
- Idempotency, LLM response caching, cooldowns, content hashes, and retryable job execution.
- Contradiction detection and temporal supersession without deleting historical evidence.
- OAuth-scoped HTTP MCP surface and separate local-only administrative operations.
- Schema packs for domain-specific page and link types.
- Visibility controls and source/brain ownership boundaries.
- Human correction, review queues, forgetting, and audit trails.

### 3.2 Mechanisms that need adaptation

#### Agent-initiative memory

GBrain's installation guidance relies heavily on “brain-first” instructions and signal-detector skills. Atlas should not depend on the model remembering to run a search or background detector. Recall and capture should be lifecycle-driven by the memory provider.

#### Filesystem/Git as universal system of record

GBrain treats Markdown/frontmatter in Git as the canonical home for most user knowledge, with the database as a derived index. That is excellent for a technical personal brain but is a poor universal default for a commercial customer agent:

- customer deletion and privacy policies are harder to guarantee across Git history;
- concurrent writes and multiple devices need transactional semantics;
- user-facing corrections need stable record identities;
- access policy belongs at row/query boundaries;
- large raw session histories do not belong in Git.

The shipped dedicated-machine baseline uses SQLite as the canonical transactional store for cognitive records. A managed production service may move that contract to Postgres when scale, row-level security, and service operations justify it. A future sanitized Markdown/Obsidian export can use stable IDs and backlinks; GBrain's Markdown conventions can still influence compiled views.

#### Guardrails

GBrain's documented guardrail seams are observe-only and fail-open. They cannot be the sole enforcement boundary for dealership/customer data. Atlas must enforce tenant, source, sensitivity, retention, and tool-result policies before ingestion and before retrieval reaches the model.

#### Confidence-led consolidation

GBrain's current deterministic consolidation clusters facts and selects the highest-confidence fact as the promoted take, with average confidence used as weight. Atlas should not let a scalar confidence score decide what deserves to become durable memory.

For Atlas:

- provenance, speaker, timestamp, tenant, source, and temporal validity are deterministic metadata;
- “is this useful enough to remember?” is a semantic judgment made by the dedicated cheap model after the logical session ends;
- uncertainty/status is retained when it reflects evidence quality, but it is not a popularity score or the promotion gate;
- a second bounded pass through the same dedicated cheap route handles ambiguous clusters, contradictions, and compiled-truth rewrites; the conversational frontier model is never used.

### 3.3 Hot versus cold memory retained

Atlas should keep GBrain's epistemic split:

- **Hot observations** are recent, attributable, and quickly retrievable. They may be wrong, transient, duplicated, or too narrow for permanent storage.
- **Durable memories** are selected, normalized, linked, and integrated into an entity/workflow/session model.
- **Evidence** is immutable apart from explicit deletion policy.
- **Compiled views** are replaceable summaries derived from evidence and durable records.

This prevents the graph from becoming a transcript-shaped landfill.

---

## 4. What Microsoft GraphRAG contributes

Microsoft GraphRAG should be used for the Tekion and dealership knowledge corpus, where documents are comparatively stable and whole-corpus reasoning matters.

### 4.1 Capabilities to use

- document and text-unit creation with provenance links;
- LLM-based entity and relationship extraction;
- optional claim/covariate extraction after prompt tuning;
- hierarchical Leiden community detection;
- LLM-generated community reports;
- embeddings for text units, entity descriptions, and community reports;
- local search for entity-specific questions;
- global map-reduce search for corpus-wide themes;
- DRIFT search for questions needing both community overview and local traversal;
- basic vector retrieval as a baseline/fallback;
- LLM call caching and configurable providers/workflows;
- bring-your-own-graph workflows;
- versioned output artifacts and incremental update support.

### 4.2 Where not to use it

Do not run a full GraphRAG indexing job after every customer turn. Its standard pipeline is an offline document-intelligence pipeline, not a transactional conversational memory store.

Do not treat its generated entity descriptions or community reports as primary evidence. They are derived artifacts and must link back to text units/documents.

Do not enable default claim extraction without a Tekion-specific tuned prompt and evaluation set. Microsoft documents claim extraction as optional and disabled by default because it usually needs domain tuning.

### 4.3 Recommended query routing

| Query shape | Route |
|---|---|
| “How do I perform this exact Tekion action?” | local search + source text |
| “What does Atlas know about this customer/contact?” | personal hybrid/graph retrieval |
| “What are the major themes across these dealership policies?” | GraphRAG global search |
| “Explain this Tekion workflow and connect it to my last attempt” | federated local personal + local/DRIFT Tekion |
| “What did we decide last Tuesday?” | personal session/evidence search |
| “Show everything connected to this workflow” | graph traversal with bounded depth |
| Exact quotation or audit question | source-text retrieval only; summaries cannot be sole evidence |

---

## 5. Target architecture

```text
Customer message / tool activity / session lifecycle
                    |
                    v
        Atlas Conversation Runtime
        - SOUL and directives
        - context engine
        - tool execution
                    |
         MemoryProvider lifecycle
                    |
                    v
       Native Atlas Cortex Provider
       - authenticated/profile identity
       - synchronous durable capture
       - recall prefetch
       - compression durability barrier
       - logical-lineage finalization enqueue
                    |
          +---------+----------+
          |                    |
          v                    v
 Profile Cortex Store      Session-End Cortex Worker
 - evidence/events         - cheap-model triage
 - hot observations        - entity resolution
 - durable memories        - consolidation
 - entities/relations      - contradiction review
 - SQLite/FTS              - compiled views
 - privacy/deletion        - validation/audit
          |                    |
          +---------+----------+
                    |
                    v
          Atlas Cognitive Gateway
          - authorization first
          - intent routing
          - lexical/graph fusion
          - advanced reranking (follow-up)
          - context budgeting
          - citations/explanations
          /          |           \
         /           |            \
 Personal graph  Atlas capability  Shared Tekion GraphRAG
 and evidence    graph/session     versioned index
         \           |            /
          \          |           /
           +---------+----------+
                     |
                     v
       Atlas answer context + Memory Graph API
```

### 5.1 Deployment shape

The shipped dedicated-customer-computer baseline is:

- Atlas/Hermes runs locally.
- The native provider and profile-scoped SQLite store run in-process; no separate loopback service or PGLite dependency is required.
- Finalization wakes a profile/secret-scoped process-local supervisor. Gateway startup also reattaches that supervisor for every served managed profile, so admitted work survives a device restart without waiting for a foreground agent. Startup retains only the stable device/control-plane binding; each semantic unit obtains a fresh dedicated `cortex.memory_maintenance` lease and claim and consumes at most one local job under that exact context-local authorization. No separate customer-run worker command is required.
- A no-agent system cron performs deterministic retention, reconciliation, lease recovery, integrity checkpoints, and repair of a missing canonical job for a lineage that was already finalized. On self-managed profiles it may then resume the dedicated memory model for that exact already-admitted session-end chain; managed work still requires a fresh managed claim. Multiplex gateways enumerate all served profiles. The scheduler never creates an independent nightly semantic pass and never treats compression as finalization.
- The shared Tekion index is delivered as a versioned local artifact. It is hash-verified everywhere and signature-verified when configured; managed mode always requires a trusted Ed25519 signature.
- Local personal data is never uploaded into the shared Tekion index.
- Session-end model calls receive bounded evidence through the explicit `atlas-cortex-memory` auxiliary route and never use the conversational model or cross-provider fallback. Capture and recall continue without that route.

For managed production, the planned database/service adapter, encryption and row-level-security controls, and hosted-index option remain separate hardening work. The present runtime rejects a non-SQLite storage backend rather than pretending that a Postgres adapter exists.

### 5.2 One brain per customer

A customer brain is an ownership and security boundary, not merely a `profile_id` filter.

Minimum production isolation:

- unique brain ID and database/schema boundary;
- unique encryption material;
- unique service credentials;
- OS/container boundary for the agent worker when practical;
- no cross-customer federation route;
- tenant ID required and validated at every API boundary;
- database row-level security as defense in depth;
- automated cross-tenant negative tests.

Atlas profiles are useful UX/configuration isolation but should not be advertised as a security boundary by themselves.

---

## 6. Canonical data model

The first schema should be explicit enough to support provenance, correction, temporal reasoning, deletion, retrieval explanations, and UI traversal.

### 6.1 Identity and scope

#### `brains`

- `id`
- `owner_customer_id`
- `display_name`
- `timezone`
- `retention_policy_id`
- `encryption_key_ref`
- `created_at`
- `deleted_at`

#### `principals`

- user, agent identity, dealership role, or service identity;
- stable external identity mappings;
- never infer authorization from a display name.

#### `knowledge_spaces`

Examples:

- `personal`
- `dealer:<dealer_id>`
- `atlas-capabilities`
- `tekion:<index_version>`

Fields include owner, visibility, source policy, and read/write rules.

### 6.2 Immutable evidence

#### `evidence_items`

Every durable claim must resolve to one or more evidence items.

- stable ID;
- brain and knowledge-space ID;
- source type: user message, assistant message, tool call, tool result, uploaded file, email, meeting, manual, web source, correction, or system event;
- source locator: session/message/tool/document/text-unit IDs;
- speaker/actor principal;
- exact content or encrypted content pointer;
- content hash;
- occurred-at and ingested-at timestamps;
- sensitivity label;
- retention class;
- deletion/tombstone state;
- parent/lineage IDs for branches and compressed sessions.

Evidence should be append-only under normal operation. Corrections add new evidence and supersession links. Hard deletion follows explicit user/legal policy and cascades through derived artifacts.

### 6.3 Sessions and work

#### `sessions`

- Atlas session ID;
- logical conversation ID;
- parent/branch lineage;
- start/finalize timestamps;
- title and short derived summary;
- workspace/project/dealership context;
- state: active, finalized, reset, interrupted, deleted;
- evidence range/hash for idempotent distillation.

#### `work_events`

- goal/request;
- plan step;
- tool invocation/result;
- decision;
- artifact created or changed;
- approval/correction;
- success/failure outcome.

This supports “what did Atlas do?” independently from personal beliefs.

### 6.4 Hot observations

#### `observations`

Short-lived, attributed candidates extracted from turns or session fragments.

- kind: event, preference, commitment, relationship, decision, fact, hypothesis, correction, workflow signal;
- normalized text;
- exact evidence IDs;
- entity candidates;
- valid-from/valid-until when stated;
- processing state: pending, retained-hot, promoted, merged, rejected, expired, needs-review;
- semantic utility label from the session-end cheap model;
- triage reason and model/prompt version;
- deterministic idempotency key.

No generic numeric confidence is required for promotion. If a source is uncertain, preserve an epistemic status such as `reported`, `inferred`, `verified`, or `disputed`.

### 6.5 Durable memory

#### `memory_records`

- kind: preference, commitment, relationship, stable fact, decision, pattern, workflow lesson, project state, correction;
- subject and optional object entity IDs;
- canonical statement;
- status: active, superseded, disputed, forgotten, deleted;
- epistemic status;
- valid-from/valid-until;
- first-seen/last-confirmed;
- source evidence IDs;
- created-by job/model/prompt version;
- replacement/supersession chain;
- pin/protect state;
- user-visible/private state.

### 6.6 Entities, relations, and aliases

#### `entities`

Initial domain types:

- person;
- customer/contact;
- company/dealership;
- vehicle;
- Tekion concept/screen/action;
- workflow;
- skill;
- tool;
- project;
- session;
- artifact/document;
- policy;
- concept;
- place;
- time/event.

Fields include canonical name, aliases, type, knowledge space, compiled description, source evidence, first/last seen, visibility, and deletion state.

#### `relations`

- typed directed edge;
- subject/object IDs;
- evidence IDs;
- valid-from/valid-until;
- status and epistemic state;
- source knowledge space;
- derived-by version;
- optional weight only for ranking/layout, never as a substitute for evidence.

Example verbs:

- `works_at`
- `knows`
- `prefers`
- `committed_to`
- `decided`
- `uses_skill`
- `produced_artifact`
- `performed_step`
- `requires`
- `supersedes`
- `derived_from`
- `mentioned_in`
- `applies_to`
- `explains`

#### `entity_aliases`

Aliases are scoped. “Service” in a dealership workflow and a person's nickname must not be globally conflated.

### 6.7 Derived summaries and communities

#### `compiled_views`

- entity/workflow/community target;
- current concise synthesis;
- structured sections appropriate to type;
- supporting evidence/memory IDs per claim;
- generated-at, model, prompt, and input hash;
- stale flag.

Compiled views can be rewritten. Their evidence cannot.

#### `communities`

- hierarchy level and parent;
- member entities/relations;
- algorithm/version;
- label and report;
- generated-at and stale state.

Personal graph communities should be recomputed incrementally from completed consolidation chains or during deterministic integrity repair. Tekion communities come from the versioned GraphRAG index.

### 6.8 Retrieval and audit

#### `retrieval_runs`

- query hash and redacted query preview;
- principal/brain/session IDs;
- route selected;
- candidate IDs per retriever;
- scores and fusion positions;
- authorization filters applied;
- selected context IDs/tokens;
- latency/cost;
- citations used in the final answer;
- user feedback/correction linkage.

#### `cognitive_jobs`

- type and input range/hash;
- immutable session-end admission, root, and parent IDs for semantic jobs;
- state/attempt/lease;
- scheduled/start/end timestamps;
- model/prompt versions;
- token/cost counters;
- output IDs;
- failure and dead-letter information.

This audit trail is essential for explaining a wrong memory and replaying a failed session-end consolidation chain.

#### `session_distill_admissions`

- brain, logical-conversation, and terminal-boundary identity;
- canonical root input hash and evidence hash;
- exact immutable v2 session/evidence snapshot;
- boundary state and admission timestamp.

This ledger is the sole authority for model-backed memory work. Session rows may later reopen, but no turn, compression hook, scheduler, generic queue caller, or forged job row can create or expand an admission.

---

## 7. Real-time memory lifecycle

### 7.1 Session initialization

1. Resolve the customer brain from authenticated runtime identity.
2. Initialize the cognitive provider with brain, profile, agent, workspace, platform, and session IDs.
3. Confirm service health and schema compatibility.
4. Load only a small static provider block into the system prompt: what the memory system is, citation rules, and user controls.
5. Do not load the graph or large memory snapshots into the stable prompt.

Failure behavior: memory is fail-soft for ordinary conversation, but the UI must show degraded memory state. It must never silently fall back to another customer's brain or a global store.

### 7.2 Before each turn

1. Normalize the user query and current task context.
2. Apply tenant/space authorization.
3. Route the query:
   - personal/session recall;
   - Tekion/local knowledge;
   - capability/workflow recall;
   - federated;
   - no recall needed.
4. Run retrieval concurrently where allowed.
5. Fuse and rerank within a strict context budget.
6. Inject a compact, source-labeled recall block into volatile context.
7. Record retrieval explanation data for the Memory Graph and “why did Atlas remember this?” UI.

Recall must be automatic for clearly relevant queries. The model may also receive a small explicit deep-recall tool for user-requested exploration, but basic memory quality cannot depend on tool invocation.

### 7.3 After each completed turn

1. Persist raw user/assistant/tool evidence.
2. Create deterministic, source-linked observation/index rows needed for later processing.
3. Return control without invoking a memory model or enqueuing semantic work.
4. Keep explicit customer memory commands as the only immediate promotion path:
   - “remember this”;
   - corrections;
   - forget/delete controls.
5. Queue bounded recall prefetch candidates for the next turn independently of consolidation.

Explicit user memory commands may be written directly as protected durable records, but still retain evidence and allow later correction/deletion.

### 7.4 Before compression

`on_pre_compress` must:

1. flush the soon-to-be-compacted message range to evidence storage;
2. deterministically inventory unresolved commitments, decisions, entity changes, artifact references, and task state for the active-window handoff;
3. return a compact structured preservation note to the context compressor;
4. preserve the logical-conversation ID when the physical session ID rotates;
5. perform no model call and enqueue no semantic consolidation job;
6. complete within a timeout and fail closed before explicit compaction if durability is not established.

This closes the main context-rot failure mode: content can leave the active model window only after it is durable. Semantic interpretation waits for the logical session end, when Cortex can reason over the full compressed-session lineage instead of isolated fragments.

### 7.5 Session finalization/reset

At logical finalization:

- resolve every physical session that belongs to the same logical conversation;
- mark the complete lineage finalized and hash its non-deleted evidence;
- enqueue exactly one initial `session_distill` chain for that lineage and evidence hash;
- flush pending writes;
- preserve branch/reset lineage;
- wake the dedicated asynchronous worker without waiting for model completion;
- notify the UI of pending consolidation work.

At reset, finalize the old logical session before binding the provider to the new session ID.

If the finalization callback commits but its worker wake is missed, startup/scheduled recovery reconstructs the same canonical lineage hash and repairs the missing job idempotently. Recovery does not reinterpret compression as a session end. Recovery pages combine a small newest slice with a rotating historical slice and acknowledge their cursor only after full success, so new admissions stay prompt and a crash cannot skip older work. Each job considers at most 100 due observations; remaining or deferred work is continued with a lineage-scoped idempotency key so a bounded batch never truncates the logical session.

---

## 8. Session-end consolidation chain

Session-end consolidation is not a reset that erases conversation. It is an idempotent asynchronous pipeline that converts the complete finalized-session lineage into useful, compact, linked knowledge. It runs independently of the conversational frontier model through the dedicated cheap `atlas-cortex-memory` route.

Implementation note: the repository ships the durable queue, lifecycle wake, scheduled recovery, dedicated model routing, structured triage/review path, validation, transactional operation executor, temporal supersession/dispute handling, raw-retention pass, bounded overflow continuation, deferred-candidate backoff, and health accounting. The finer-grained phases below remain the target model for expanding that worker. Automatic personal embedding generation, incremental personal community detection, customer review notifications, and every proposed pattern/compiled-view heuristic are not all present in the baseline.

### 8.1 Trigger and asynchronous execution

- Primary trigger: explicit logical session finalization/reset.
- Enqueue one initial job for the full logical lineage, then wake the dedicated worker immediately.
- Do not block session finalization on model latency or require Atlas chat to remain open.
- Enforce one active consolidation job per brain using a database lease.
- Process at most 100 due observations per job in prompt-size-bounded sub-batches.
- Enqueue a scoped continuation only when overflow remains; defer unresolved candidates with bounded exponential backoff so they cannot starve newer sessions.
- Use scheduled/startup work only for deterministic retention, integrity, lease recovery, and repair of a missing already-finalized lineage job.
- Allow manual “Run memory maintenance now” from the Memory Graph health panel.

### 8.2 Phase 0 — inventory and recovery

- reclaim expired job leases;
- locate finalized but unprocessed sessions;
- reconstruct the complete compressed-session lineage and canonical evidence hash;
- repair a missing initial/continuation job without creating a second chain;
- verify evidence hashes and source availability;
- calculate a bounded batch and checkpoint.

### 8.3 Phase 1 — deterministic preparation

- remove exact duplicates by source/content hash;
- normalize timestamps/timezones and stable IDs;
- segment long sessions at semantic/task boundaries;
- attach speaker/tool/session provenance;
- redact or withhold fields disallowed from model processing;
- resolve known aliases deterministically;
- collect existing related entities/memories for comparison.

### 8.4 Phase 2 — cheap-model utility triage

A low-cost model evaluates every candidate batch using structured output.

For each candidate it chooses one action:

- `discard_transient`
- `retain_hot_until`
- `promote_new`
- `merge_existing`
- `supersede_existing`
- `mark_disputed`
- `needs_deeper_review`

It also emits:

- memory kind;
- normalized atomic statement;
- subject/object entity mentions;
- temporal bounds;
- exact evidence IDs;
- a short plain-language rationale;
- sensitivity/retention suggestion;
- missing-information note.

The model is deciding utility and durability, not inventing provenance. Evidence IDs, speaker, tenant, and timestamps are validated against deterministic input.

Promotion rubric:

- Will this likely improve a future answer or action?
- Is it still useful outside the original turn?
- Is it specific enough to retrieve?
- Is it already represented?
- Does it change a known entity, commitment, workflow, preference, or decision?
- Is it an original customer insight worth preserving verbatim?
- Is it merely politeness, repetition, speculative assistant text, or transient execution noise?

### 8.5 Phase 3 — entity resolution and clustering

- link high-certainty aliases using deterministic rules;
- cluster remaining candidates by embeddings plus shared entity/time/session signals;
- never merge across knowledge spaces or tenants;
- send ambiguous identity clusters to deeper review;
- preserve original customer phrasing alongside canonical normalized text.

### 8.6 Phase 4 — bounded ambiguity review

Use a second bounded review pass through the same dedicated cheap route only for:

- ambiguous entity identity;
- competing or temporally changing claims;
- multi-turn decisions with implicit conclusions;
- workflow lessons requiring outcome interpretation;
- recurring patterns supported by multiple sessions;
- rewriting compiled entity/workflow views;
- candidates explicitly escalated by the cheap model.

The model receives bounded evidence and related active memories. It must return structured operations, never issue free-form database writes. This review pass never borrows the conversational frontier model; both configured Cortex stages default to `atlas-cortex-memory`.

### 8.7 Phase 5 — validation and transaction

Before applying model-proposed operations:

- validate all referenced evidence and entity IDs;
- re-check authorization and knowledge-space boundaries;
- reject unsupported claims;
- ensure every durable statement has evidence;
- detect exact/semantic duplicates;
- validate temporal ordering;
- validate schema/link verbs;
- simulate deletions/supersessions;
- write operations and audit rows in one transaction;
- make reruns idempotent by input hash + prompt/model version.

### 8.8 Phase 6 — contradictions and temporal history

- compare new active records with related active records;
- distinguish contradiction from legitimate change over time;
- set `valid_until` when newer evidence supersedes older state;
- retain both sides of genuine disagreement;
- never silently choose between customer evidence and external/assistant inference;
- queue high-impact ambiguity for customer review.

### 8.9 Phase 7 — compiled truth and patterns

- rewrite concise entity/workflow summaries from active records;
- cite evidence for each claim;
- keep append-only history separate from the current synthesis;
- create a recurring pattern only with multiple distinct supporting sessions/evidence items;
- label model-derived patterns as inferences and make them easy to reject.

### 8.10 Phase 8 — graph maintenance

- create/update typed relations;
- compute embeddings and lexical indexes;
- run incremental community detection when graph change exceeds a threshold;
- regenerate stale community reports;
- find orphans, broken evidence links, duplicate entities, and invalid edges;
- update visualization projections.

### 8.11 Phase 9 — health report

Produce a user-readable summary:

- sessions processed;
- memories promoted/merged/superseded/rejected;
- unresolved contradictions;
- entities created/merged;
- graph/index health;
- model cost/tokens/latency;
- failures requiring retry;
- proposed memories awaiting review.

Do not send a notification during quiet hours unless the run failed critically or requires imminent user action.

---

## 9. Retrieval design

### 9.1 Authorization before relevance

The retrieval order is:

1. authenticate principal;
2. resolve brain and allowed knowledge spaces;
3. build authorized candidate relations/rows;
4. run lexical/vector/graph retrieval within that set;
5. rerank;
6. construct context.

Never retrieve globally and then remove unauthorized results after ranking. That leaks through timing, counts, caches, and derived summaries.

### 9.2 Personal hybrid retrieval

Candidate channels:

- full-text/lexical search over evidence, entities, compiled views, and memory records;
- vector search over the same distinct content classes;
- exact alias/title matching;
- temporal lookup;
- session lineage/search;
- typed one- or two-hop graph expansion;
- recency and user-pin boosts;
- source-quality and evidence-completeness signals;
- optional cross-encoder reranking.

Fuse ranked lists using reciprocal-rank fusion rather than trying to compare raw scores from unrelated retrievers.

### 9.3 Context composition

The injected recall block should contain:

- concise memory statement;
- source/space label;
- when it was true/observed;
- evidence locator;
- dispute/staleness marker;
- why it matched the current query;
- no hidden internal chain-of-thought.

Budget by category so one content class cannot consume the full context:

- direct evidence/quotes;
- durable personal memory;
- relevant session outcome;
- Tekion source text/entity context;
- workflow/skill context;
- community-level overview only when query shape requires it.

### 9.4 Retrieval explanation

Every recalled item returned to the model should have an explanation record containing:

- matched query concepts/entities;
- retrievers that found it;
- graph path if traversal was used;
- source evidence;
- temporal status;
- authorization space;
- whether it was actually included in final model context.

This powers customer trust and debugging without exposing private reasoning traces.

---

## 10. Tekion GraphRAG pipeline

Implementation note: Cortex ships the secure artifact import, validation, typed projection, retrieval, publication, version provenance, and rollback layers. Microsoft GraphRAG indexing itself remains an offline corpus-build responsibility. This repository contains no approved Tekion source corpus and therefore has no live Tekion index until an operator imports and publishes one.

### 10.1 Corpus governance

Each input document needs:

- stable document ID;
- title/version/effective date;
- product area and dealership applicability;
- source owner;
- sensitivity/classification;
- superseded-by relation;
- extraction permission;
- checksum.

### 10.2 Index build

1. Import approved documents into a versioned build workspace.
2. Preserve structural metadata in every chunk.
3. Auto-tune, then manually review, extraction prompts against a representative Tekion set.
4. Use standard GraphRAG for high-fidelity entity/relationship exploration.
5. Tune entity types and relationship verbs for dealership operations.
6. Keep claim extraction disabled until a dedicated evaluation proves it useful.
7. Generate communities and reports.
8. Embed text units, entities, and community reports.
9. Run local/global/DRIFT/basic evaluation questions.
10. Publish a signed immutable index version plus manifest.

### 10.3 Tekion taxonomy seed

- module/product area;
- screen/page;
- field;
- action;
- workflow;
- prerequisite;
- permission/role;
- error/exception;
- customer/deal/vehicle concept;
- policy;
- integration;
- report;
- document/version.

Relationship examples:

- `contains`
- `navigates_to`
- `requires_role`
- `precedes`
- `updates`
- `reads_from`
- `writes_to`
- `blocked_by`
- `resolved_by`
- `documented_in`
- `supersedes`
- `applies_to`

### 10.4 Publication and rollback

- never mutate the active index in place;
- stage and evaluate a new version;
- atomically move a dealership/channel pointer to the approved version;
- retain the previous version for rollback;
- tag every retrieval/citation with index version;
- invalidate query caches when the pointer changes;
- delete obsolete versions only after retention and rollback windows expire.

---

## 11. Memory Graph product experience

Implementation note: the current Cortex Starmap preserves radial chronology and adds typed rendering, bounded graph and node-detail APIs, source evidence detail, health/manual-maintenance controls, profile-switch safety, and a non-canvas accessible node list. It is read-only for Cortex knowledge today. The multi-mode layouts and mutation/export interactions below remain planned product expansion.

### 11.1 Preserve Atlas's visual identity

Keep the existing radial chronology as the default “growth” view. It is differentiated and already communicates time well.

Extend the graph to support multiple modes rather than forcing every job into one layout:

1. **Growth** — current radial time map; see the brain accumulate over time.
2. **Local graph** — Obsidian-style selected-node neighborhood with adjustable depth.
3. **Communities** — cluster view for people, workflows, Tekion areas, projects, and concepts.
4. **Why this answer** — highlight only the evidence and paths used by the current response.
5. **Timeline** — changes, decisions, corrections, and supersessions for one entity.
6. **Workflow** — ordered steps, skills, tools, outcomes, and Tekion prerequisites.
7. **Brain health** — pending jobs, contradictions, orphans, stale summaries, and model maintenance runs.

### 11.2 Customer interactions

Clicking a node should open a detail drawer containing:

- current compiled view;
- source evidence/timeline;
- connected entities and typed edges;
- first/last seen;
- knowledge space and privacy state;
- why Atlas recalled it recently;
- ask Atlas about this;
- correct;
- pin/protect;
- mark private;
- forget;
- permanently delete where policy allows;
- merge duplicate;
- split mistaken merge;
- inspect source.

### 11.3 Visual semantics

- color: knowledge domain/community;
- shape: entity/memory/evidence/workflow/document type;
- ring/position: time in Growth mode;
- size: bounded connectivity or usage, with a legend;
- edge style: relationship type and temporal status;
- dotted/faded: inferred, stale, disputed, or superseded;
- lock icon: private/protected;
- citation badge: evidence-backed;
- warning badge: unresolved contradiction.

### 11.4 Scaling strategy

The existing Canvas renderer is adequate for the first richer prototype and preserves current art direction. Do not render an entire mature brain at once.

Required API behavior:

- server-side filters;
- bounded node/edge count;
- neighborhood expansion;
- semantic/time/type search;
- level-of-detail community aggregation;
- stable layout seeds;
- cursor pagination for timelines and evidence;
- cancellation for stale graph requests.

Performance gate:

- keep the custom Canvas renderer while interactive performance meets targets;
- evaluate Sigma.js if the visible graph must regularly reach thousands/tens of thousands of nodes because it is WebGL-focused;
- evaluate Cytoscape.js if compound nodes, extensive built-in graph analysis, or more varied layouts become more important than very large rendering scale;
- avoid replacing the renderer solely for novelty.

### 11.5 Obsidian relationship

Reuse the useful ideas:

- global versus local graph;
- adjustable local depth;
- search filters and groups;
- directional arrows;
- time-lapse;
- click-through into source content.

Do not embed Obsidian as the product graph. It models note links, not Atlas's typed, temporal, permissioned, evidence-backed cognitive model.

Offer an optional export:

- one Markdown file per entity/workflow/session summary;
- stable frontmatter IDs and types;
- wikilinks for relations;
- evidence links with timestamps;
- no secrets/raw tool payloads by default;
- regenerated export manifest;
- explicit warning that the export is a snapshot, not the transactional source of truth.

---

## 12. Agent personality and default directives

Memory architecture should not be implemented as personality text, but the default customer identity must explain how Atlas behaves around memory.

### 12.1 `SOUL.md` responsibilities

Keep stable and compact:

- Atlas's role as a calm dealership operator/copilot;
- communication voice and initiative level;
- preference for evidence and operational clarity;
- explicit separation of known, remembered, inferred, and unknown;
- respect for customer correction and privacy;
- refusal to claim memory when no supporting record exists;
- continuity across sessions without pretending perfect recall.

### 12.2 Runtime directive responsibilities

- cite recalled facts when consequential;
- ask before treating ambiguous personal information as durable;
- never write assistant speculation as customer fact;
- distinguish user statement, tool observation, external knowledge, and model inference;
- honor “remember,” “forget,” “don't save this,” and correction language;
- use Tekion sources for Tekion claims;
- surface conflicts instead of silently resolving them;
- do not reveal internal/private graph content across customers or unauthorized users.

### 12.3 What must remain code-enforced

- capture and recall lifecycle;
- tenant isolation;
- PII/sensitivity policy;
- deletion;
- source attribution;
- pre-compression durability;
- no semantic/model execution on turn or compression paths;
- idempotency;
- one full-lineage semantic chain at logical session finalization;
- model data minimization;
- query authorization;
- audit logging.

Prompts guide behavior. They do not constitute a security or durability guarantee.

---

## 13. API contracts

The shipped local runtime uses a native in-process store/provider rather than exposing the proposed `/v1` personal-brain service. The customer-safe HTTP surface currently consists of:

- `GET /api/cognitive/graph`
- `GET /api/cognitive/node/{id}`
- `GET /api/cognitive/health`
- `POST /api/cognitive/dream/run`
- `GET /api/cognitive/dream/{job_id}`

These routes resolve the active profile on the server and never accept a brain ID. The literal `dream` paths are compatibility names for manual recovery/integrity maintenance; normal semantic processing is created by logical session finalization. The additional operations in this section are target contracts, not currently available public endpoints.

### 13.1 Internal personal-brain API

Target managed-service endpoints or equivalent typed RPC operations:

- `POST /v1/evidence/batch`
- `POST /v1/recall`
- `POST /v1/observations/batch`
- `POST /v1/sessions/{id}/finalize`
- `POST /v1/jobs/session-distill`
- `GET /v1/jobs/{id}`
- `GET /v1/entities/{id}`
- `PATCH /v1/entities/{id}`
- `POST /v1/entities/merge`
- `POST /v1/entities/{id}/split`
- `POST /v1/memories/{id}/correct`
- `POST /v1/memories/{id}/forget`
- `DELETE /v1/memories/{id}`
- `GET /v1/graph/neighborhood`
- `GET /v1/graph/timeline`
- `GET /v1/health`

Every request includes authenticated brain/principal context outside user-editable payload fields.

### 13.2 Atlas cognitive gateway API

- **Implemented now:** graph, node detail, health, compatibility-named manual maintenance enqueue, and maintenance job status listed above.
- **Implemented inside the native provider:** automatic/deep recall and explicit status/remember/correct/forget controls.
- **Planned public API:**

- `POST /api/cognitive/recall`
- `GET /api/cognitive/path/{retrieval_run_id}`
- `GET /api/cognitive/timeline/{id}`
- mutation operations for correction, pin, privacy, forget, deletion, merge, and split.

The existing `/api/learning/graph` remains as a compatibility adapter during migration.

### 13.3 Graph response shape

Replace the current minimal `StarmapGraph` over time with:

- `nodes`: ID, label, type, domain, community, timestamps, status, badges, summary, degree/usage;
- `edges`: stable ID, source, target, type, direction, status, timestamps;
- `communities`: hierarchy and aggregate counts;
- `timeline_window`;
- `facets`;
- `next_cursor`;
- `projection`: growth/local/community/answer-path;
- `retrieval_run_id` when applicable;
- `redaction_summary` so the UI can explain hidden counts without exposing content.

Do not send full raw memory/evidence content in the graph overview response. Fetch details after user selection.

---

## 14. Security, privacy, and deletion

Implementation note: the baseline enforces profile-contained paths, restrictive local filesystem permissions, symlink rejection, owner binding, managed identity requirements, knowledge-space filtering, secret redaction, source provenance, assistant-truth exclusion, no-save suppression, managed GraphRAG signing, overview redaction, audit records, permanent erasure within the live Cortex store, and fail-closed coordination between native explicit session deletion and the matching local SessionDB lineage. Cross-system erasure orchestration for provider logs, external systems, and historical/offline backups, whole-database application-level encryption, production row-level security, cloud-processing opt-out UI, and a formal legal-hold system remain managed-product work. Local full-disk encryption should be enabled independently where required.

### 14.1 Ingestion controls

- classify source and sensitivity before LLM processing;
- exclude secrets, credentials, raw auth headers, and protected tool fields;
- support source-specific retention and “do not learn” policy;
- store raw evidence encrypted at rest;
- log model/provider and region used for processing;
- make cloud-processing opt-out enforceable in code.

### 14.2 Retrieval controls

- tenant/brain resolution from authenticated context;
- row-level security;
- source/space ACLs;
- no shared cache keys across brains;
- cache key includes brain, principal, source closure, schema/index version, and query-policy version;
- derived summaries inherit the strictest visibility of their evidence;
- no cross-space community report if its members are not jointly visible.

### 14.3 User memory controls

Semantics must be precise:

- **forget for answers**: mark inactive and exclude from retrieval, retain auditable tombstone as policy allows;
- **correct**: add correction evidence and supersede the old active record;
- **delete permanently**: remove content and derived embeddings/summaries, then rebuild affected indexes; retain only the minimum non-content deletion receipt required;
- **don't save this**: prevent promotion and apply raw-evidence retention policy;
- **private/protected**: restrict export/sharing and automatic mutation.

### 14.4 Threat tests

- prompt injection inside uploaded/Tekion documents;
- malicious tool result asking the memory worker to change policy;
- cross-customer alias collision;
- graph traversal crossing unauthorized space;
- cached answer/retrieval leakage;
- export containing private evidence;
- deletion leaving vectors, summaries, or community reports behind;
- model proposal referencing evidence outside its input batch;
- forged tenant/profile IDs from renderer requests.

---

## 15. Observability and evaluation

### 15.1 Operational metrics

- capture queue lag and failures;
- pre-compression flush latency/failure;
- finalization coverage;
- session-end consolidation duration, cost, retries, continuation count, and backlog;
- observations promoted/merged/rejected/disputed;
- orphan/duplicate/contradiction counts;
- embedding/index freshness;
- recall p50/p95 latency;
- context tokens injected by source/domain;
- graph endpoint and render performance;
- customer corrections and forget/delete completion time.

### 15.2 Memory-quality evaluation set

Build DealerBox-specific tests with synthetic isolated customers and longitudinal sessions.

Question classes:

- stable preference recall;
- changed preference and temporal supersession;
- commitment/deadline recall;
- relationship/entity alias resolution;
- decision and rationale recall;
- workflow outcome/lesson reuse;
- exact session/event recall;
- original insight preservation;
- irrelevant/transient message rejection;
- assistant speculation exclusion;
- correction/forget/delete;
- multi-hop personal + Tekion question;
- global Tekion theme question;
- unknown/gap behavior.

Metrics:

- retrieval precision/recall at K;
- answer citation support;
- wrong-memory rate;
- stale-memory rate;
- contradiction handling accuracy;
- temporal answer accuracy;
- durable-promotion precision and recall;
- entity merge/split accuracy;
- cross-tenant leak rate (must be zero);
- context-token efficiency;
- user correction rate;
- abstention quality.

Use a longitudinal benchmark such as LongMemEval as inspiration, but treat dealership workflows and customer privacy tests as the release gate.

### 15.3 Session-end model evaluation

Create a labeled corpus of session fragments with expected actions:

- discard;
- retain hot;
- promote;
- merge;
- supersede;
- dispute;
- escalate.

Run every prompt/model change against it. Track confusion by memory kind. Reject changes that improve total accuracy while harming corrections, privacy, or assistant-speculation exclusion.

### 15.4 Retrieval A/Bs

Compare:

- session FTS only;
- vector only;
- lexical + vector;
- hybrid + graph;
- hybrid + graph + reranker;
- federated GraphRAG routing.

Graph complexity is justified only when measured against simpler baselines.

---

## 16. Phased implementation plan

Each phase ends in a reviewable customer outcome and has an explicit rollback boundary.

Current status:

| Phase | Status on 2026-07-14 | Remaining release work |
|---|---|---|
| 0 — contracts/evaluation | Partial | Core contracts and a focused automated test suite exist; the longitudinal DealerBox evaluation corpus, formal quality thresholds, and A/B baseline program do not. |
| 1 — evidence durability | Shipped local baseline | Native SQLite store, ownership, idempotent evidence, jobs/audit, recovery, setup, doctor, and backup are present. Managed Postgres, application-level encryption, and service migration drills remain. |
| 2 — automatic recall | Shipped baseline | Automatic bounded source-labeled lexical/typed-graph retrieval and audit are present. Personal vectors, reranking, and benchmark-based tuning remain. |
| 3 — compression/finalization | Shipped baseline | Pre-compression durability without semantic work, preservation notes, complete compressed-session lineage, one logical finalize/reset chain, rewind reconciliation, and recovery are wired across runtimes. Ongoing lifecycle regression coverage remains a release gate. |
| 4 — session-end durable memory | Shipped baseline | Dedicated cheap-model routing, bounded continuation, validation, atomic idempotent operations, leases/retries/health, manual recovery, remember/correct, live-Cortex permanent forget, and local session/Cortex privacy-delete coordination exist. Cross-system legal erasure orchestration and customer review UI remain. |
| 5 — typed temporal graph | Shipped baseline | Entities, aliases, evidence-backed typed relations, temporal memories, disputes/supersession, compiled/community records, and graph retrieval exist. Personal embedding/community algorithms and merge/split UI remain. |
| 6 — Tekion GraphRAG | Adapter shipped; corpus blocked externally | Secure portable/Microsoft artifact import, staging, publish, rollback, typed projection, local/global-style community recall, and version provenance exist. Corpus governance, tuned build, signatures/keys, expert evaluation, and a live artifact are external inputs. |
| 7 — federated retrieval | Shipped baseline | One authorized recall path spans personal, capability, and Tekion spaces with bounded fusion and explanations. Public recall/path APIs, advanced reranking, and full answer-path UI remain. |
| 8 — Memory Graph | Partial | Cortex-first typed Starmap, detail/evidence drawer, health, maintenance, accessibility, privacy-safe overview, and legacy fallback exist. Rich projections, search/filter expansion, mutations, and export remain. |
| 9 — hardening/default rollout | Partial | Atlas defaults, existing-profile SOUL upgrade, setup paths, backup/doctor, managed identity constraints, signed-index enforcement, retention, cron, failure handling, and isolation tests exist. Security review, production service/storage, deployment cohorts, restore/deletion drills, and quality gates remain. |

The deliverables and acceptance criteria below remain the release checklist. A phase marked as a shipped baseline is not a claim that every aspirational bullet beneath it is complete.

### Phase 0 — freeze contracts and build the evaluation harness

Deliverables:

- architecture decision records for ownership, canonical store, privacy, and GraphRAG scope;
- canonical schema and API types;
- synthetic multi-customer longitudinal dataset;
- memory-promotion labels and retrieval questions;
- threat-test suite skeleton;
- cost/latency telemetry schema;
- feature flags for capture, recall, dream, Tekion GraphRAG, and rich graph UI.

Acceptance:

- team can replay the same conversations deterministically;
- expected memory operations and answers are versioned;
- zero implementation path can bypass authenticated brain resolution;
- baseline session-search results are recorded.

Rollback: documentation/tests only.

### Phase 1 — personal-brain service and raw evidence durability

Deliverables:

- Postgres schema/migrations for brains, principals, spaces, evidence, sessions, jobs, and audit;
- local service process and health endpoint;
- Atlas profile/customer bootstrap;
- encrypted evidence batch ingestion;
- idempotent session/message/tool IDs;
- startup recovery for interrupted sessions;
- backup/restore integration.

Atlas seams:

- create the external provider under the existing `plugins/memory` mechanism or a narrow Atlas-owned provider package;
- register it through current memory-provider loading;
- wire backup paths/health into existing setup surfaces.

Acceptance:

- every completed turn is durable without delaying the answer materially;
- duplicate delivery creates no duplicate evidence;
- crash/restart loses no acknowledged evidence;
- profile/customer isolation tests pass;
- memory disabled flag returns Atlas to current behavior.

Rollback: disable provider and preserve database for later replay.

### Phase 2 — automatic recall MVP

Deliverables:

- lexical/vector indexes over evidence and session outcomes;
- pre-turn intent router;
- automatic provider `prefetch`;
- bounded recall block with source labels;
- retrieval-run audit;
- deep-recall tool for explicit user exploration;
- degraded-memory status in desktop UI.

Acceptance:

- stable preference, decision, and prior-session questions beat session-FTS baseline;
- irrelevant turns do not receive large recall blocks;
- recall p95 meets interactive target;
- citations resolve to evidence;
- zero cross-customer results in adversarial tests.

Rollback: disable recall while continuing evidence capture.

### Phase 3 — compression durability and true session finalization

Deliverables:

- `on_pre_compress` durable flush and structured preservation note;
- explicit assertion that compression performs no model call or semantic enqueue;
- idempotent `on_session_finalize` enqueue over the full compressed-session lineage;
- reset/branch/resume lineage handling and canonical evidence hashing;
- missed-finalization-job recovery sweeper;
- lifecycle trace diagnostics.

Acceptance:

- forced tiny-context tests retain decisions and commitments after multiple compressions;
- exactly one initial consolidation chain per finalized logical lineage/evidence hash;
- overflow continuation processes every due candidate without duplicating the initial chain;
- `/new`, `/reset`, resume, branch, exit, crash, gateway expiry, and cron contexts behave as specified;
- cron/subagent/system contexts do not contaminate personal memory.

Rollback: provider retains raw turn capture; disable distillation enqueue.

### Phase 4 — asynchronous session-end utility triage and durable memory

Deliverables:

- observations and durable-memory schema;
- deterministic preparation;
- cheap-model structured triage;
- validation and transactional operation executor;
- dedupe/merge/supersede logic;
- model/prompt cache and cost accounting;
- manual run and health report;
- user correction/forget controls.

Acceptance:

- promotion benchmark meets per-kind precision/recall gates;
- assistant speculation is never promoted as user fact;
- every durable memory has valid evidence;
- rerunning the same finalized-lineage input produces no duplicate mutation;
- corrections supersede rather than erase history;
- permanent deletion removes derived content.

Rollback: retain observations/evidence; stop promotion worker; rebuild durable tables from replay.

### Phase 5 — typed entities, temporal graph, and compiled views

Deliverables:

- Atlas dealership schema pack/taxonomy;
- entity/alias resolution;
- typed relations;
- temporal validity and contradiction classification;
- bounded ambiguity review through the dedicated Cortex utility route;
- compiled views with evidence links;
- incremental communities and graph-health jobs.

Acceptance:

- alias/entity benchmark passes;
- legitimate temporal changes are not mislabeled as contradictions;
- disputed claims remain visible as disputes;
- compiled claims all resolve to evidence;
- merge/split operations are reversible and audited;
- graph retrieval beats hybrid-only baseline on designated multi-hop tests.

Rollback: disable graph expansion/compiled views; retain hybrid record retrieval.

### Phase 6 — Tekion GraphRAG pilot

Deliverables:

- approved pilot corpus and metadata governance;
- tuned extraction prompts;
- standard GraphRAG build configuration;
- versioned index artifact/service;
- local/global/DRIFT/basic router;
- source citation resolver;
- rollback pointer and cache versioning.

Acceptance:

- expert-labeled Tekion evaluation passes support/citation thresholds;
- exact source text is available for consequential instructions;
- corpus-wide questions improve over vector RAG;
- wrong-version and superseded-document tests pass;
- personal content cannot enter the shared build.

Rollback: point back to previous index or vector/keyword Tekion baseline.

### Phase 7 — cognitive gateway and federated answers

Deliverables:

- single intent/router contract;
- authorized concurrent retrieval across personal, capability, and Tekion domains;
- rank fusion and context budgeting;
- cross-domain citation model;
- “why this answer” retrieval paths;
- cache isolation/versioning.

Acceptance:

- mixed personal + Tekion workflow questions outperform either source alone;
- domain/source attribution is clear in the answer;
- unauthorized domains are never probed;
- latency budget remains acceptable with concurrent retrieval;
- failed one-domain retrieval degrades explicitly and safely.

Rollback: route domains independently through feature flags.

### Phase 8 — evolve the existing Memory Graph

Deliverables:

- compatibility adapter from rich graph contract to current Starmap;
- Growth, Local, Communities, Why-this-answer, Timeline, Workflow, and Health modes;
- node detail drawer and evidence viewer;
- correction, pin, privacy, forget, delete, merge, and split interactions;
- filters, groups, adjustable local depth, and search;
- server-side expansion/pagination;
- optional sanitized Obsidian export.

Acceptance:

- no full-brain payload is required to open the graph;
- initial render and interaction stay within performance targets on a mature synthetic brain;
- every destructive action has clear semantics and an audit/recovery path where applicable;
- shared-map/export functions never contain raw private text by default;
- keyboard and screen-reader navigation cover all non-canvas actions.

Rollback: keep current `/api/learning/graph` and Starmap view behind compatibility flag.

### Phase 9 — hardening and default-on rollout

Deliverables:

- migration/bootstrap for existing Atlas profiles;
- setup UI and brain health diagnostics;
- resource limits, backpressure, and dead-letter recovery;
- security review and data-deletion audit;
- backup/restore/upgrade/rollback drills;
- staged pilot cohorts;
- support runbooks;
- default customer `SOUL.md` and directives finalized after behavioral evaluation.

Rollout gates:

- zero cross-tenant leaks in automated and manual testing;
- deletion and restore drills pass;
- memory quality beats current baseline with an acceptable wrong-memory rate;
- session-end consolidation jobs recover from process/device/network/model failures;
- Tekion citations meet expert review;
- opt-out and “don't save this” work end to end;
- resource use is safe for the target dedicated computer.

Rollback: per-component flags, previous database migration compatibility, previous Tekion index pointer, and current memory files/session search remain available.

---

## 17. File-level implementation map for Atlas

The baseline is implemented at these boundaries.

### Runtime and provider

- `altas/cortex/provider.py` — native lifecycle-driven capture, recall, explicit memory controls, no-save enforcement, compression/finalization, lineage, and rewind handling.
- `altas/cortex/runtime.py` and `altas/cortex/config.py` — profile/managed identity resolution, store opening, safe paths, and bounded configuration.
- `altas/cortex/models.py` — typed evidence, recall, dream, and graph contracts.
- `agent/memory_provider.py`, `agent/memory_manager.py`, and `agent/native_memory_providers.py` — generic lifecycle, durable synchronous provider delivery, and native registration.
- `agent/turn_context.py`, `agent/context_compressor.py`, `agent/conversation_compression.py`, and `agent/codex_runtime.py` — volatile recall and pre-compression durability across native runtimes.
- `agent/agent_init.py`, `agent/auxiliary_client.py`, `run_agent.py`, and `cli.py` — initialization, explicit model routing, stable turn metadata, and logical lifecycle coverage.

### Store, retrieval, and maintenance

- `altas/cortex/store.py` — secure local schema, provenance, temporal memory, graph records, lexical retrieval, jobs, leases, receipts, retention, rewind reconciliation, and audit.
- `altas/cortex/capabilities.py` — safe Atlas skill/tool capability projection without storing directive bodies.
- `altas/cortex/graphrag.py` and `altas/cortex/tekion_taxonomy.py` — artifact validation/signing, version management, Tekion projection, and taxonomy normalization.
- `altas/cortex/dream.py`, `altas/cortex/worker.py`, and `altas/cortex/scheduler.py` — structured consolidation, transaction boundary, retries/health, no-agent cron wake, and supervisor.
- `altas/cortex/lifecycle.py` — detached finalization and rewind reconciliation for runtimes without a cached agent.
- `gateway/run.py`, `gateway/session.py`, `gateway/slash_commands.py`, `tui_gateway/server.py`, and `hermes_state.py` — gateway/TUI/session ownership, finalize, reset, expiry, and backup integration.

### Backend API

- `altas/cortex/graph.py` — versioned, bounded, privacy-safe graph/detail/health/job DTO builders.
- `hermes_cli/web_server.py` — profile-scoped cognitive graph, detail, health, dream enqueue, and job-status routes.
- `altas/cortex/cli.py` and `hermes_cli/subcommands/cortex.py` — operator status, maintenance, and GraphRAG commands.
- The legacy `/api/learning/graph` remains the desktop fallback when Cortex is unavailable or explicitly disabled.

### Desktop

- `apps/desktop/src/types/hermes.ts` and `apps/desktop/src/hermes.ts` — versioned Cortex contracts and API client.
- `apps/desktop/src/store/onboarding.ts` and `apps/desktop/src/components/onboarding/*` — first-run and returning-install dedicated-memory selection, profile-scoped validation/cache epochs, memory-only provider activation, retryable fail-closed setup, and one atomic two-pass assignment.
- `apps/desktop/src/app/settings/model-settings.tsx` — memory-specific catalog editing; chat-follow and bulk helper reset actions cannot target Cortex.
- `apps/desktop/src/app/session/hooks/use-session-actions/index.ts` — canonical `session.close` for user-owned New Chat, with atomic idle/failure/race guards, guarded stored-session recovery, and separate nonsemantic renderer reset paths; workspace handoff commits drafts, attachments, and branch intent only after close succeeds.
- `apps/desktop/src/store/starmap.ts` — Cortex-first loading, stale-profile protection, health/job polling, and legacy fallback.
- `apps/desktop/src/app/starmap/cortex.ts`, `render.ts`, `geometry.ts`, and `constants.ts` — typed Cortex-to-Starmap adaptation and visual semantics.
- `apps/desktop/src/app/starmap/cortex-detail.tsx`, `cortex-node-list.tsx`, and `cortex-status.tsx` — bounded evidence/detail, keyboard navigation, health, index state, and manual maintenance.
- `apps/desktop/src/app/starmap/share-code.ts` — explicit refusal to serialize private Cortex graphs through legacy share codes.

### Configuration and setup

- `hermes_cli/config.py` — Atlas-only default activation, capture/recall/dream/GraphRAG/security defaults, profile directory creation, and explicit auxiliary routes.
- `hermes_cli/inventory.py` and `hermes_cli/web_server.py` — dedicated authenticated structured-output memory catalog, live-metadata omission cache, managed-binding gate, profile-scoped atomic assignment, advanced-route status, route-policy validation, and bidirectional chat/memory separation.
- `hermes_cli/default_soul.py` — stable Atlas personality and evidence/memory doctrine; profile migration preserves customer-authored SOUL files.
- `hermes_cli/profiles.py` and `hermes_state.py` — profile copy/backup inclusion.
- `hermes_cli/doctor.py` — Cortex configuration, database, durable-memory, and queued-job health.
- [Atlas Cortex: Customer and Operator Guide](../ATLAS_CORTEX.md) — current behavior, configuration, artifacts, interfaces, privacy, and limitations.

---

## 18. Test matrix

### Unit

- evidence and operation idempotency keys;
- content/source redaction;
- alias normalization;
- temporal intervals and supersession;
- rank fusion;
- context budgets;
- authorization scopes;
- cache keys;
- graph projection pagination;
- deletion cascade plan;
- prompt structured-output validation.

### Integration

- provider lifecycle from turn start through shutdown;
- compression flush under timeout/failure;
- session finalize/reset/resume/branch;
- Postgres transaction and job leasing;
- LLM retry/cache/versioning;
- GraphRAG index publish/rollback;
- gateway multi-domain retrieval;
- desktop graph API and mutations;
- backup/restore and schema upgrade.

### End-to-end

- a new customer teaches Atlas preferences and relationships over several sessions;
- context compresses multiple times;
- per-turn and pre-compression paths make no memory-model calls;
- logical finalization consolidates the complete compressed-session lineage asynchronously;
- a device restart after finalization repairs/resumes the same idempotent chain;
- more than 100 due observations complete through bounded continuation jobs;
- a later question recalls the right fact and source;
- user corrects it;
- graph timeline displays both old and new evidence;
- later answers use only the corrected active record;
- user permanently deletes it;
- record, embedding, compiled view, community, export, and cache no longer expose it.

### Isolation

Run every read/mutation/query with:

- correct tenant;
- wrong tenant ID;
- missing tenant;
- forged profile;
- shared Tekion-only principal;
- cached response from another tenant;
- graph neighbor whose other edges are unauthorized;
- community containing mixed visibility;
- alias collision across tenants.

Expected cross-customer disclosure count: zero.

### Failure injection

- local brain unavailable;
- database read-only/full;
- queue worker killed mid-transaction;
- LLM timeout/rate limit/malformed JSON;
- embedding provider failure;
- corrupt GraphRAG artifact;
- index pointer swap during query;
- Atlas crash during compression/finalize;
- system clock/timezone change;
- duplicate event delivery;
- deletion during a session-end consolidation run.

---

## 19. Resolved implementation decisions and open product decisions

1. **Canonical local storage — resolved for baseline.** The native dedicated-machine runtime uses one profile-scoped SQLite database. It does not use PGLite. Managed Postgres/service storage remains a separate production adapter and migration decision.
2. **Cloud model routing — partially resolved.** Session-end work uses explicitly configured `cortex_triage` and `cortex_reasoning` routes and never crosses providers as fallback. Atlas defaults both to the separate cheap `atlas-cortex-memory` utility alias; the control plane requires an explicit cheap upstream mapping and never substitutes the conversational model. Managed mode accepts only the Atlas gateway with dedicated maintenance-job authorization. The actual cheap provider/model, region policy, and a supported local-model product mode still require deployment decisions.
3. **Raw transcript retention — default selected, policy still open.** Local default is `0`, meaning retain until explicit deletion. A bounded 1–3650 day policy scrubs eligible raw evidence during deterministic maintenance independently of model availability or success. Dealer/legal defaults, legal hold, and customer-facing erasure workflows remain to be defined.
4. **Shared Tekion delivery — local adapter selected.** Cortex consumes signed/versioned local artifacts with atomic publication and rollback. A hosted read-only query service remains optional future architecture.
5. **Customer review posture — baseline selected.** Ordinary useful observations enter automatic triage after their logical session ends; explicit remembers promote immediately as protected records; configured sensitive evidence requires review. The complete review-queue UI is not shipped.
6. **Identity granularity — baseline selected.** One local profile maps to one customer brain with separate personal, capability, and Tekion knowledge spaces. Managed mode keys identity from deployment context. Dealership-shared contribution/promotion is not implicit and is not implemented in this baseline.
7. **Consolidation timing — resolved.** Logical session finalization is the sole authority for a new semantic chain and wakes a dedicated asynchronous worker immediately. The compatibility-named 04:00 slot and 15-minute no-agent wake perform deterministic recovery/integrity and can repair a missing canonical job for an already-finalized lineage. A self-managed wake may resume the dedicated memory model for that exact admitted chain after interruption; it never creates a separate nightly semantic cycle.
8. **Export — open.** Obsidian/Markdown export is not implemented and is post-baseline scope.
9. **Tekion corpus and signing ownership — open external input.** Product owners must supply the governed source corpus, build/evaluation pipeline, signing key custody, trusted public-key distribution, and publication approval process.
10. **Production rollout gates — open.** Longitudinal memory quality, Tekion expert review, restore/permanent-erasure drills, security review, resource limits, and staged pilot criteria must be completed before calling the managed product generally available.

---

## 20. Final recommendation

Continue evolving Atlas Cortex as the default Atlas subsystem with code-enforced lifecycle behavior, not as a collection of prompt instructions.

The repository now makes each turn and compression boundary durable without foreground semantic work, performs automatic bounded recall, consolidates the full logical-session lineage asynchronously through a dedicated cheap model at finalization, represents typed temporal knowledge, consumes approved GraphRAG artifacts, and exposes a customer graph. Scheduled work is a recovery/integrity safety net rather than a second semantic cycle. The next priorities are a governed live Tekion corpus, measured longitudinal retrieval/promotion evaluation, managed storage/security/erasure, and the remaining customer graph controls.

The resulting product will remember more, but the more important improvement is that it will remember **selectively, temporally, privately, and explainably**. That is what prevents context compression and long-running use from turning the agent either amnesiac or confidently wrong.

---

## 21. Primary research references

### GBrain

- Repository and current product architecture: <https://github.com/garrytan/gbrain>
- Brain versus prompt memory: <https://github.com/garrytan/gbrain/blob/master/docs/guides/brain-vs-memory.md>
- Agent integration surfaces: <https://github.com/garrytan/gbrain/blob/master/docs/guides/agent-to-gbrain.md>
- Hot facts versus cold takes: <https://github.com/garrytan/gbrain/blob/master/docs/takes-vs-facts.md>
- Source attribution: <https://github.com/garrytan/gbrain/blob/master/docs/guides/source-attribution.md>
- Compiled truth and evidence timeline: <https://github.com/garrytan/gbrain/blob/master/docs/guides/compiled-truth.md>
- Brain/source ownership boundaries: <https://github.com/garrytan/gbrain/blob/master/docs/architecture/brains-and-sources.md>
- Deployment topologies: <https://github.com/garrytan/gbrain/blob/master/docs/architecture/topologies.md>
- Schema packs: <https://github.com/garrytan/gbrain/blob/master/docs/architecture/schema-packs.md>

### Microsoft GraphRAG

- Project and support/cost warning: <https://github.com/microsoft/graphrag>
- Architecture and provider extension points: <https://microsoft.github.io/graphrag/index/architecture/>
- Indexing dataflow and knowledge model: <https://microsoft.github.io/graphrag/index/default_dataflow/>
- Standard versus FastGraphRAG: <https://microsoft.github.io/graphrag/index/methods/>
- Output schemas: <https://microsoft.github.io/graphrag/index/outputs/>
- Bring your own graph: <https://microsoft.github.io/graphrag/index/byog/>
- Query modes: <https://microsoft.github.io/graphrag/query/overview/>
- Prompt tuning: <https://microsoft.github.io/graphrag/prompt_tuning/overview/>
- Visualization/GraphML guide: <https://microsoft.github.io/graphrag/visualization_guide/>

### Visualization references

- Obsidian global/local Graph view: <https://obsidian.md/help/plugins/graph>
- Sigma.js WebGL graph renderer: <https://www.sigmajs.org/docs/>
- Cytoscape.js visualization and graph-analysis library: <https://js.cytoscape.org/>
