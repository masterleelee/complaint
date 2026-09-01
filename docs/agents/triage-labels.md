# Triage Labels

This repo uses the following canonical role labels for issue triage:

| Role | Label | Description |
|------|-------|-------------|
| needs-triage | `needs-triage` | Maintainer needs to evaluate |
| needs-info | `needs-info` | Waiting on reporter for more details |
| ready-for-agent | `ready-for-agent` | Fully specified, AI-ready |
| ready-for-human | `ready-for-human` | Needs human implementation |
| wontfix | `wontfix` | Will not be actioned |

No custom overrides — the defaults match the existing convention.

## How these apply to a local-markdown tracker

There is no label API. Record the role as a `Status:` line near the top of an issue file:

```markdown
Status: ready-for-agent
```

## Coexisting severity levels

This project's register also tags every entry with a **severity** level (`P0`–`P3`, defined in `docs/agents/issue-tracker.md`). Severity and triage role are orthogonal:

- `P1` + `ready-for-agent` → agent can implement it now, and it matters.
- `P3` + `needs-triage` → nobody has looked at it yet.

When triaging, set the `Status:` role and leave the existing `Pn` level in the title untouched.
