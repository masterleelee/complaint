# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Current state

- **Layout**: single-context (no `pnpm-workspace.yaml`, no `package.json`, no `packages/*` — not a monorepo).
- **`CONTEXT.md`**: not yet created.
- **`CONTEXT-MAP.md`**: not applicable (single-context).
- **`docs/adr/`**: not yet created.

Until `/grill-with-docs` or `/domain-modeling` produces `CONTEXT.md`, use **`CLAUDE.md` at the repo root as the interim domain-language reference** — in particular its `## 项目关键路径` section, which lists the canonical entry points (`app.py`, `templates/index.html`, `static/js/composables/`, `services/contract_service.py`, `crawlers/`, `database.py`).

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, or
- **`CONTEXT-MAP.md`** at the repo root if it exists: it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── CONTEXT.md                ← created lazily by /domain-modeling
├── CLAUDE.md                 ← interim domain reference (read this today)
├── docs/adr/                 ← created lazily
│   ├── 0001-<decision>.md
│   └── 0002-<decision>.md
├── app.py
├── templates/
├── static/js/
├── services/
└── crawlers/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md` — or in `CLAUDE.md` while `CONTEXT.md` is absent. Don't drift to synonyms the glossary explicitly avoids.

Known term traps in this project (recorded so you don't rediscover them):

- There is no `name` or `branch` column on `complaint_tickets`; it is `student_name` + `organization_unit_code` (网点代号, e.g. 中/麻/栅D) + `organization_unit_name`.
- The view called `workbench` renders **学员信息**, and `kanban` renders **投诉统计** — the view ids do not match their display names. Confirm view titles in `static/js/app.js` rather than guessing.
- 驾校简称 (e.g. ) is **not** the same thing as 网点代号 (南/麻/栅D). Never substitute one for the other.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (<decision>), but worth reopening because…_
