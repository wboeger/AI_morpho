# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

GyroMorpho v2 — Flask web app for morphometric analysis, automated taxonomic description, and phylogenetic inference of Gyrodactylidae (Monogenoidea) sclerotized structures (hooks, anchors, bars, MCO). Landmark-based geometric morphometrics (GPA) → discrete character states → species descriptions/diagnoses → phylogenetic matrices → NCBI/MAFFT/trimAl/CIPRES-or-Galaxy tree pipeline.

## Commands

```bash
# Run locally (port 5001; port 5000 conflicts with macOS AirPlay)
python run.py

# Run tests
python -m pytest tests/
python -m pytest tests/test_geometry.py::test_sinuosity_curved   # single test

# Install deps (torch/torchvision/opencv intentionally excluded — only needed
# for offline U-Net training in unet/, not the web app)
pip install -r requirements.txt

# Production entrypoint (Railway; see Procfile)
gunicorn run:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 180
```

No linter/formatter config is present in this repo.

## Architecture

**App factory**: `app/__init__.py` builds the Flask app, registers all blueprints, and on every startup runs a chain of idempotent `_migrate_*` functions (schema/data backfills for older DBs — e.g. `_migrate_character_states`, `_migrate_structures`) plus `_ensure_admin`. When adding a model field that needs backfilling on existing databases, follow this same pattern rather than writing a separate migration tool — there is no Alembic.

**Data model** (`app/models.py`): `Project` → `Specimen` → `Structure` (one row per structure type: hook/anchor/superficial_bar/deep_bar/mco) → `CharacterValue` per `CharacterDefinition`. `PhylogenyJob` tracks the NCBI→align→trim→tree pipeline per project. `TaxonomicGroup`/`CorrectionHistory`/`ActivityLog` support diagnoses and audit trail. `ReliabilityCriterion`/`MCOReliabilityRating` support the reliability scoring feature.

**Routes** (`app/routes/*.py`, one blueprint per concern): `project` (dashboard, specimen/image import), `landmarks` (editor + ImageJ ZIP batch import), `boundaries` (boundary editor), `characters` (workshop: reorder/print/distribution), `matrix` (character matrix, tree upload, outgroup re-rooting), `descriptions` (species descriptions & diagnoses), `export`, `phylogeny` (NCBI→MAFFT→trimAl→CIPRES/Galaxy pipeline), `ai_advisor` (Claude/GPT-4o/Gemini integration), `optimization`, `reliability`, `backup`.

**Core computation pipeline**:
1. `app/geometry.py` — pure geometric functions (arc length, curvature, angles, resampling). No Flask/DB dependencies; this is the file `tests/` exercises directly.
2. `app/procrustes.py` — Generalized Procrustes Analysis (center → scale to unit centroid size → iterative rotation) and PCA, run per structure type across all specimens before any character is computed.
3. `app/characters.py` — the character computation engine + the default 36-character library (C01–C12 hook, A01–A09 anchor, B01–B06 superficial bar, D01–D03 deep bar, M01–M06 MCO). Maps raw geometric values to discrete states via threshold ranges; confidence = distance from nearest threshold boundary. This is the largest and most change-sensitive file in the app.
4. `app/descriptions.py` — generates taxonomic prose from computed character states.
5. `app/export.py` — Nexus/TNT/CSV/JSON generators for downstream phylogenetics software.

**Structure/landmark model is fixed terminology**, defined once in `config.py`: `STRUCTURE_PARTS` (part names per structure type) and `LANDMARK_COUNTS` (hook/anchor = fixed 100; bar/MCO = adaptive, see `ADAPTIVE_RANGES`). Changes to anatomical part names must stay consistent across `config.py`, `app/characters.py`, and the ImageJ macros in `macros/`.

**Phylogenetic pipeline** (`app/routes/phylogeny.py`, background thread per job): NCBI nuccore search/download → filter (exclude accessions, min length, dedupe exact sequences, keep longest per species) → same filtering for outgroup families → align (MAFFT, local binary or Galaxy fallback) → trim (trimAl, local or Galaxy) → submit to CIPRES (RAxML-NG on XSEDE) or Galaxy → poll → download support tree → root with R `ape::root()` using configured outgroup genera → import as project reference tree. `config.py` holds Galaxy tool IDs (env-overridable since usegalaxy.eu updates tool versions) and `PHYLO_FORCE_GALAXY` to skip local binaries even when present. Per-job intermediate files (raw/aligned/trimmed FASTAs, trees) live under `PHYLO_RESULTS_DIR`, which **must** be on the persistent volume, not the ephemeral repo checkout — this matters for any code that writes pipeline artifacts.

**Data storage**: SQLite in WAL mode at `DATA_DIR/db.sqlite`; `DATA_DIR` resolves to `RAILWAY_VOLUME_MOUNT_PATH` in production (Railway volume) or `./data` locally — see `config.py`. Never assume the repo directory is writable/persistent in production; it is wiped on every Railway redeploy. `data.zip` (db + uploads) is the seed archive downloaded on first boot via `scripts/seed_data.py` — see `DEPLOY.md` for the full Railway deployment flow (volume setup, `DATA_SEED_URL`, admin bootstrap env vars).

**AI Advisor** (`app/routes/ai_advisor.py`): sends only character definitions and value statistics (never images) to Claude/GPT-4o/Gemini and offers to add suggested characters/states directly into the workshop.

## Notes for changes here

- `unet/` is an offline U-Net training module, decoupled from the web app; its heavy deps (torch, opencv) are deliberately excluded from `requirements.txt`.
- `phylogeny/*.R` are reference R pipeline scripts (not invoked by the app directly) — the app's own Python-orchestrated pipeline lives in `app/routes/phylogeny.py`.
- Publication Methods & Materials text is maintained at `docs/methods_section.md` — update it after any change to the pipeline or the character library, per project convention.
- Git pushes to `origin/main` in this repo do not require confirmation before running.


## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues on `wboeger/AI_morpho`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), label strings equal to role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context. See `docs/agents/domain.md`.