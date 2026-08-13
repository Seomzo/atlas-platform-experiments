# Atlas mobile contracts

These Draft 2020-12 JSON Schemas are the language-neutral source of truth for the native iPhone/control-plane/worker boundary planned by M02.

| Schema | Root contract | Purpose |
|---|---|---|
| `atlas-mobile-events-v1.schema.json` | `atlas.mobile.event.v1` | Ordered, redacted phone-visible events |
| `atlas-mobile-commands-v1.schema.json` | `atlas.mobile.command.v1` | Narrow phone command requests |
| `atlas-mobile-approvals-v1.schema.json` | `atlas.mobile.approval.v1` | Managed-action projection and decisions |
| `atlas-mobile-errors-v1.schema.json` | `atlas.mobile.error.v1` | Stable safe error envelope |
| `atlas-mobile-identity-v1.schema.json` | `atlas.mobile.identity.v1` | Enrollment, device proof, and proof-bound account renewal messages |
| `atlas-mobile-stream-v1.schema.json` | `atlas.mobile.stream.v1` | Foreground stream control and event frames |
| `atlas-mobile-threads-v1.schema.json` | `atlas.mobile.thread.v1` | Product thread resources and lifecycle mutations |

## Rules

- Treat these files as code. Contract-owner review and compatibility tests are required.
- Generate Swift `Codable`/`Sendable` and backend validators; do not hand-maintain divergent DTOs.
- M03 supports exactly major version 1. Additive optional fields may be introduced within v1 only when older consumers safely ignore them. Removing, requiring, or changing the meaning of a field/event/state requires a new major version.
- Producers reject data that fails schema, size, scope, or redaction policy. Unknown/raw payload fallback is prohibited.
- Examples are synthetic. IDs are opaque strings; they are not authorization claims.
- Serialized event/request limits in the protocol apply in addition to schema limits.

The inspected draft endpoints use strict operation-specific Pydantic bodies rather than every envelope in this directory. These schemas are the proposed M03 public mobile facade and generated-model source; M03 must implement explicit adapters and compatibility tests. Their presence is not evidence that the current backend already accepts the wrappers.

The schemas deliberately do not choose production replay retention, cloud/vendor, identity provider, speech provider, notification detail, or real connector permissions.

Synthetic valid examples live under `examples/` and are validated against their named schema during M02/M03 contract checks.
