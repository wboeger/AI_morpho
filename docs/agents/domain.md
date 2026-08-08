# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root (does not exist yet).
- **`docs/adr/`** — read ADRs that touch the area you're about to work in (none exist yet).

Neither exists yet — proceed silently, per convention; don't flag their absence, don't suggest creating them upfront. The `/domain-modeling` skill creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo (this repo — one Flask app, no monorepo signals):

```
/
├── CONTEXT.md          (not yet created)
├── docs/adr/            (not yet created)
└── app/
```

## Use the glossary's vocabulary

Until `CONTEXT.md` exists, the closest thing to a domain glossary is `CLAUDE.md`'s Architecture section — structure/landmark terminology (`STRUCTURE_PARTS`, hook/anchor/superficial_bar/deep_bar/mco) is fixed in `config.py` and must stay consistent across `config.py`, `app/characters.py`, and the ImageJ macros.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding. (No ADRs exist yet in this repo.)
