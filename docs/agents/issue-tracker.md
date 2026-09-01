# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files in `.scratch/`.

**Why not GitHub:** `git remote` points at `github.com/masterleelee/autofaka`, but that repository does not resolve on GitHub (`gh repo view` fails with `Could not resolve to a Repository`, despite an authenticated `gh` session). GitHub Issues is therefore not available. Local markdown is the only workable tracker here.

## Reality check: `.scratch/` is not a throwaway

`.scratch/` is **tracked by git and not in `.gitignore`** (~546 files committed). Treat its contents as durable project record — regression evidence, fix plans, and issue registers are referenced by path from other docs. Never bulk-delete it.

## Conventions actually in use

### Tier 1 — aggregate register (the default home for defects)

`.scratch/issue-list.md` is the main index. Issues are numbered `ISS-<round>-<NN>` (e.g. `ISS-B-01`) and carry a **severity level**, not a triage role:

- `P0` — main flow blocked or data corrupted, no workaround
- `P1` — important functional error or data risk
- `P2` — behavioural inconsistency / robustness defect
- `P3` — low-risk experience problem

Each entry uses these body sections, in this order:

```markdown
### ISS-B-01 ｜ P1 ｜ <one-line title>
- **现象**：what the user/API caller sees
- **根因**：file:line level cause
- **复现**：numbered steps
- **证据**：path to the evidence artifact
- **修复建议**：proposed fix
```

The file opens with a level-definition blockquote and an evidence-root line. Keep that header when appending a new round.

### Tier 2 — per-feature directories (for efforts with multiple tickets)

```
.scratch/<feature-slug>/
├── spec.md                        ← the spec (/to-spec output)
├── issues/
│   ├── 01-<slug>.md               ← one file per ticket, numbered from 01
│   └── 02-<slug>.md
└── ...evidence, screenshots
```

Never combine tickets into a single file. Triage state goes in a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings). Comments and conversation history append to the bottom under a `## Comments` heading.

Existing examples: `ui-audit/`, `layout-test/`, `timeline-display-standardize/`, `test-evidence/`, `test-files/`, `backup/`.

### Tier 3 — flat scratch files

Ad-hoc scripts, server-restart logs, screenshots, and one-off plans sit flat at `.scratch/*.py`, `*.log`, `*.png`, `*-plan.md`, `*-report.md`. This is accepted practice; don't "clean up" other agents' flat files into directories.

### Evidence

Regression evidence is one JSON per case under `.scratch/test-evidence/<suite>/` (request / response / elapsed ms). Reference it by relative path from the issue entry.

## When a skill says "publish to the issue tracker"

- **Single defect found during a review/regression sweep** → append an `ISS-<round>-<NN>` entry to `.scratch/issue-list.md`.
- **A feature or bugfix that will spawn multiple implementation tickets** → create `.scratch/<feature-slug>/` with `spec.md` and `issues/NN-<slug>.md`.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.
