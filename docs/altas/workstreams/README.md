# Workstream Handoffs

Each parallel Atlas workstream writes one uniquely named handoff here:

```text
WS-00-HANDOFF.md
WS-01-HANDOFF.md
...
```

Do not make every session edit one shared status document. Separate handoffs
reduce merge conflicts and preserve the reasoning attached to each draft pull
request.

## Required handoff format

```markdown
# WS-XX Handoff — Title

## Status

- Branch:
- Draft PR:
- Baseline:
- Last validated:

## Goal and scope

## Decisions made

## Deferred decisions

## Files changed

## Contracts and migrations

## Validation

## Security and privacy checks

## Visual evidence

## Known risks and limitations

## Integration order and conflicts

## Exact next actions
```

Never include credentials, raw customer data, proprietary skill contents,
browser cookies/storage, or unredacted support logs in a handoff.
