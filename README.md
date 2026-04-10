# GyroMorpho v2

A web-based pipeline for morphometric analysis, automated taxonomic description, and phylogenetic inference for sclerotized structures of Gyrodactylidae (Monogenoidea). The system uses landmark-based geometric morphometrics with Generalized Procrustes Analysis (GPA) to compute discrete character states from continuous shape data, generate species descriptions, export phylogenetic matrices, and run end-to-end molecular phylogenetic analyses.

## Features

- **ImageJ macros**: Interactive landmarking macros for hooks and anchors — contour extraction via wand tool, 3 anatomical landmarks, equidistant resampling to 100 pseudolandmarks
- **Landmark management**: Import, visualize, and edit 2D pseudolandmarks for hooks, anchors, bars, and MCO. Upload individual CSVs or batch-import a ZIP archive of ImageJ macro outputs
- **Image management**: Upload structure images individually per specimen directly from the project page, or batch-import from a folder
- **Boundary assignment**: Define anatomical part boundaries with click, range, or lasso selection
- **Character computation**: Automatic geometric character states via Procrustes-aligned measurements (ratios, angles, curvatures, sinuosity)
- **Character workshop**: Define, edit, drag-and-drop reorder, and delete character states; view measurement explanations and specimen reference panels; print all characters to PDF
- **AI Advisor**: Send project data to Claude, GPT-4o, or Gemini and receive expert suggestions for new characters, improved state definitions, and redundancy flags — accepted suggestions are added to the workshop automatically
- **Character matrix**: Interactive matrix with confidence coloring, cell-level override, gallery views, and phylogenetic tree panel with ladderized tree, synchronized leaf–row alignment, and outgroup re-rooting
- **Gallery**: Sortable specimen gallery with color-coded landmark shapes, structure type switcher, lightbox zoom, and inline state assignment
- **Species descriptions**: Auto-generated morphological descriptions from character data
- **Taxonomic diagnoses**: Comparative diagnoses for user-defined taxonomic groups
- **Export**: Nexus, TNT, CSV, and JSON formats for downstream phylogenetic analysis
- **Phylogenetic pipeline**: NCBI sequence retrieval → MAFFT alignment → trimAl trimming → CIPRES/RAxML-NG submission → tree rooting → interactive tree viewer and matrix integration
- **Multi-user**: Role-based access (admin, annotator) with full audit logging

## Requirements

### Python
- Python 3.10 or later
- pip (Python package manager)

### External tools (must be installed and on PATH)
- [MAFFT](https://mafft.cbrc.jp/alignment/software/) — multiple sequence alignment
- [trimAl](http://trimal.cgenomics.org/) — alignment trimming
- [R](https://www.r-project.org/) with the `ape` package — phylogenetic tree rooting
- `curl` — CIPRES REST API communication (pre-installed on macOS/Linux)

### Optional
- [ImageJ or Fiji](https://imagej.net/ij/) — for landmark extraction macros
- A modern web browser (Chrome, Firefox, Safari, Edge)

Install external tools on macOS with Homebrew:

```bash
brew install mafft trimal r
Rscript -e 'install.packages("ape", repos="https://cran.r-project.org")'
```

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/wboeger/AI_morpho.git
cd AI_morpho
```

### 2. Create a virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate          # Windows
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

> **Note on PyTorch**: The `torch` and `torchvision` packages are large. If you do not plan to use the U-Net segmentation module, you can install without them:
> ```bash
> pip install Flask Flask-SQLAlchemy Flask-Login Flask-WTF Werkzeug numpy Pillow opencv-python-headless biopython requests anthropic
> ```

### 4. Initialize the data directory

```bash
mkdir -p data/uploads
```

The SQLite database (`data/db.sqlite`) is created automatically on first run.

### 5. Run the application

```bash
python run.py
```

The server starts at **http://127.0.0.1:5001**. Open this URL in your browser.

> **macOS note**: Port 5000 is occupied by AirPlay Receiver (System Settings → General → AirDrop & Handoff). GyroMorpho uses port 5001 by default.

---

## Quick Start Guide

### Step 1: Register and create a project

1. Open http://127.0.0.1:5001 in your browser.
2. Click **Register** and create an account (the first user becomes admin).
3. Click **New Project**, enter a name and description.

The project dashboard shows a **Pipeline** bar at the top with the full workflow.

---

### Step 2: Extract landmarks with ImageJ macros

Go to your project page and click **Import from Folders**. Two ImageJ macros are included in the `macros/` directory and are downloadable (pre-configured with project directories) from the import page:

- **Hook macro** (`macrogyrolandmark_v5.5.ijm`): L1 = Point tip, L2 = Toe tip, L3 = Junction Point-Shaft (inner face)
- **Anchor macro** (`macrogyrolandmark_v5_anchors.ijm`): L1 = Point, L2 = External tip superficial root, L3 = Distal-most base deep root

Open the `.ijm` file in ImageJ/Fiji via **Plugins → Macros → Run...**

The macro workflow:
1. **Session setup**: configure input/output directories, wand tolerance, landmark count (100)
2. **Image review**: accept, reject (with reason), or skip
3. **Crop & enhance**: bounding rectangle; optional Gaussian blur, CLAHE, Unsharp Mask; 3× upscale
4. **Orientation**: verify structure points right; optional horizontal flip
5. **B&W conversion**: optional threshold-based binary mask
6. **Wand tool**: click on the structure outline; adjustable tolerance
7. **Contour smoothing**: 3-point moving average (configurable passes)
8. **Landmark placement**: click L1, L2, L3 sequentially
9. **Equidistant resampling**: 100 points from L1, spaced by arc length
10. **Verification**: color-coded overlay (cyan=L1, yellow=L2, magenta=L3, green=semilandmarks); edit individual points
11. **Output**: one CSV per specimen (X, Y columns, 100 rows); QC and rejection logs

Full backward navigation at every stage.

---

### Step 3: Import data into GyroMorpho

#### Import landmarks from folder

Click **Import from Folders**, enter the CSV folder path, select structure type, click **Scan** then **Import All**.

Supported filename formats:
- `Gyrodactylus_salaris.csv`
- `AB063294Gyrodactylusanguillae.csv`
- `JF836137.1|Gyrocerviceanseris_passamaquoddyensis.csv`

#### Batch import landmarks from ZIP archive

On the project page, use the **Import Landmarks from ImageJ Macro** card: select the structure type, choose a ZIP file containing the CSVs produced by the macro, and click **Import ZIP**. Each CSV must be named `GenusName_speciesName.csv`. The system matches files to specimens by name (fuzzy matching) and resamples automatically.

#### Import images

**From folder**: Enter path to folder with PNG/JPG/GIF via **Import from Folders** → Images tab; images matched by species name.

**Per structure**: In the specimen list, each structure row has an **img** button. Click it to upload or replace the image for that specific structure directly from the project page.

#### Import boundaries

Import JSON files mapping specimen names to part indices (1-based):
```json
{ "Gyrodactylus_salaris": { "Point": [1,2,3,4,5], "Shaft": [6,7,8,9,10] } }
```

---

### Step 4: Review boundaries

Each specimen's structure has an **Edit Boundaries** link. The boundary editor provides:
- **Click / Range / Lasso** modes; keyboard shortcuts 1–6 (select part), C/R/L (mode), Ctrl+Z (undo)
- **Copy from similar**: auto-copy boundaries from the most morphologically similar confirmed specimen

Click **Confirm** to save and trigger character computation.

---

### Step 5: Character matrix

Click **Character Matrix** from the project page:
- Rows = species, columns = characters; confidence coloring: green/yellow/red/gray
- Click any cell for details and override options
- Filter by structure type (Hook, Anchor, Bar, MCO) or DNA-only / unconfirmed
- **Tree panel**: upload a Newick/NEXUS file or import from the Phylogeny pipeline — matrix rows reorder to match the **ladderized** phylogenetic leaf order and an SVG clade panel appears with synchronized scrolling
- **Outgroup re-rooting**: select a species from the dropdown or click a leaf dot on the tree to re-root the phylogeny

---

### Step 6: Character workshop

Click **Characters** from the project page:
- Toggle characters active/inactive; create new characters; edit thresholds
- **Drag-and-drop reorder** rows to control column order in the matrix
- **Print Characters**: opens a print-friendly page with all active characters, their states, and method explanations — use browser Print / Save as PDF
- **Distribution**: view histogram of raw values across all specimens for any character
- Two-panel editor: form (left) + reference specimen panel (right)

---

### Step 7: AI Advisor

Click **AI Advisor** in the top navigation bar (available within any project). The advisor sends your project's character definitions and value statistics to an AI model and returns:

- **Suggested new characters**: biologically motivated measurements not yet defined, with proposed formula and states. Click **Add to Project** to create the character immediately in the workshop.
- **State improvements**: proposed refinements to existing character discretizations based on value distributions.
- **Redundant/problematic characters**: characters flagged as correlated, zero-variance, or otherwise uninformative.
- **General observations**: overall commentary on the completeness and quality of the morphometric scheme.

**Supported providers**:

| Provider | Model | API key source |
|----------|-------|----------------|
| Claude (Anthropic) | claude-opus-4-6 | https://console.anthropic.com |
| GPT-4o (OpenAI) | gpt-4o | https://platform.openai.com/api-keys |
| Gemini (Google) | gemini-2.0-flash / 1.5-flash | https://aistudio.google.com/app/apikey |

API keys are entered once per session and are never stored in the database.

> **Note**: No images are transmitted — only character definitions, specimen counts, and value statistics are sent to the AI provider.

---

### Step 8: Species descriptions

Click **Descriptions** — auto-generated morphological descriptions in standard taxonomic prose. Click **Regenerate** after updating character values.

---

### Step 9: Taxonomic diagnoses

Click **Diagnoses** — create taxonomic groups (genus, subfamily) and generate comparative diagnoses.

---

### Step 10: Export

Click **Export**:

| Format | Description | Use case |
|--------|-------------|----------|
| CSV | Simple matrix | Spreadsheet analysis |
| CSV Detailed | Matrix with raw values and confidence | Detailed analysis |
| Nexus | Standard phylogenetic format | MrBayes, PAUP*, Mesquite |
| TNT | TNT format | TNT parsimony analysis |
| JSON | Complete project data | Backup, re-import |
| Descriptions | Formatted species descriptions | Publications |
| Diagnoses | Formatted group diagnoses | Publications |

---

### Step 11: Phylogenetic pipeline

Click **Phylogeny** in the top navigation bar. Two modes are available:

#### NCBI Pipeline (full automated pipeline)

1. **Configure** target taxon (e.g. *Gyrodactylidae*), gene/marker query (e.g. 18S terms), minimum sequence length, accessions to exclude, outgroup families with selection mode (`each_genus` = top N per genus; `top_species` = top N longest overall), NCBI email, and CIPRES credentials.
2. Click **Start Pipeline** — the system runs in the background:
   - Searches NCBI nuccore and downloads sequences in batches of 200
   - Filters: removes excluded accessions, sequences below minimum length, exact-sequence duplicates; keeps the **longest sequence per species**
   - Fetches outgroup families with the same filtering and selection logic
   - Aligns with **MAFFT** (`--auto --adjustdirection`)
   - Trims with **trimAl** (`-gappyout`)
   - Progress updates automatically every 6 seconds
3. When stage reaches **trimmed**, click **Submit to CIPRES** to run RAxML-NG on XSEDE
4. Click **Check Status** to poll CIPRES; the job auto-polls while running
5. When completed, click **Download & Root** — downloads `infile.txt.raxml.support`, roots with `ape::root()` using the configured outgroup genera
6. Click **Import into Project** to set the tree as the project reference phylogeny

#### Upload FASTA

Upload a pre-trimmed aligned FASTA directly and proceed from CIPRES submission onward.

#### CIPRES credentials

Set as environment variables to avoid entering them each time:
```bash
export CIPRES_USER=wboeger
export CIPRES_PASSWORD=your_password
export CIPRES_APP_KEY=your_app_key
```

---

## How Character States Are Computed

### Generalized Procrustes Analysis (GPA)

All specimens of the same structure type are aligned before measurement:
1. **Center**: translate centroid to origin
2. **Scale**: scale to unit centroid size (removes size differences)
3. **Rotate**: iterative rotation to minimize sum of squared distances to mean shape

### Geometric operations

| Operation | Description |
|-----------|-------------|
| `ratio_arc_length` | Ratio of arc lengths of two parts |
| `sinuosity` | Arc length / chord length |
| `mean_curvature` | Mean Menger curvature |
| `junction_angle` | Angle at part junction |
| `direction_angle` | Angle between direction vectors |
| `relative_position` | Normalized vertical displacement |
| `max_curvature` | Maximum local curvature |
| `presence_threshold` | Part arc as fraction of total |
| `sinuosity_with_direction` | Signed sinuosity |
| `angle_between_parts` | Angle at fork between two parts |
| `point_curvature` | Deviation angle between point midline and middle shaft axis |

### State mapping

Raw numeric values are mapped to discrete states via threshold ranges. Confidence = distance from nearest threshold boundary.

---

## Default Character Library

36 pre-defined characters:
- **C01–C12**: Marginal hook (12 geometric characters)
- **A01–A09**: Anchor (8 geometric + 1 manual)
- **B01–B06**: Superficial bar (6 manual)
- **D01–D03**: Deep bar (3 manual)
- **M01–M06**: MCO (6 manual)

---

## Project Structure

```
AI_morpho2/
  run.py                        # Entry point (port 5001)
  config.py                     # Configuration
  requirements.txt              # Python dependencies
  macros/                       # ImageJ landmarking macros
  phylogeny/                    # Reference R pipeline scripts (v8, v9)
  app/
    __init__.py                 # Flask app factory + DB migration + SQLite WAL mode
    models.py                   # SQLAlchemy data models
    characters.py               # Character computation engine + default library
    geometry.py                 # Geometric functions (curvature, angles, etc.)
    procrustes.py               # GPA, PCA, Procrustes alignment
    descriptions.py             # Species description generator
    export.py                   # Export format generators
    routes/
      auth.py                   # Authentication
      project.py                # Project dashboard, specimen/image import
      landmarks.py              # Landmark editor + ImageJ ZIP batch import
      boundaries.py             # Boundary editor
      characters.py             # Character workshop (reorder, print, distribution)
      matrix.py                 # Character matrix, tree upload, outgroup re-rooting
      descriptions.py           # Species descriptions & diagnoses
      export.py                 # Export routes
      phylogeny.py              # Phylogenetic pipeline (NCBI→MAFFT→trimAl→CIPRES)
      ai_advisor.py             # AI Advisor (Claude / GPT-4o / Gemini integration)
    templates/
      ai_advisor/advisor.html   # AI Advisor UI
      characters/workshop.html  # Character workshop with drag-and-drop reordering
      characters/print_characters.html  # Print-friendly character list
      landmarks/editor.html     # Landmark editor (canvas coordinate fix)
      landmarks/batch_import_result.html  # ZIP import results summary
      matrix/matrix_view.html   # Matrix with ladderized tree panel
    static/
      css/style.css
      diagrams/                 # SVG measurement diagrams
  data/                         # Created at runtime (DB + uploads)
```

---

## Data Storage

- **`data/db.sqlite`** — all structured data (WAL journal mode enabled for concurrency)
- **`data/uploads/`** — specimen images
- **`phylogeny/Results/job_TIMESTAMP/`** — per-job phylogenetic pipeline files

---

## Configuration

Edit `config.py` to customize:
- `SECRET_KEY`: set a secure random key for production
- `UPLOAD_FOLDER`: where specimen images and pipeline files are stored
- `CIPRES_BASE_URL`, `CIPRES_USER`, `CIPRES_APP_KEY`: CIPRES defaults
- `STRUCTURE_PARTS`: part names for each structure type
- `LANDMARK_COUNTS`: fixed landmark counts (hook=100, anchor=100)

---

## Multi-user Workflow

1. The first registered user becomes **admin**
2. Admin creates projects and invites team members from the project page
3. Members can be **admin** (full access) or **annotator** (data entry)
4. All actions are logged in the activity log; character overrides record who, when, and why

---

## Troubleshooting

**Port already in use**: GyroMorpho runs on port 5001. Disable AirPlay Receiver in System Settings → General → AirDrop & Handoff if port 5000 conflicts.

**"Database is locked"**: SQLite WAL mode is enabled automatically. If the error persists, ensure only one instance is running and delete any stale `data/db.sqlite-journal` file.

**Characters show all "?"**: Click **Compute All Characters** on the project page. Both landmarks and confirmed boundaries must be present.

**Species names not matching on import**: Use the **Scan** button to preview filename parsing before importing. For ZIP batch import, ensure CSVs are named `Genus_species.csv`.

**Landmark shape wrong scale vs. image**: Fixed in the current version (canvas coordinate system mismatch corrected). If the issue recurs, check that the browser zoom level is 100%.

**Thresholds wrong for my data**: Check raw value distributions via the **Distribution** button in the Character Workshop after importing new data.

**AI Advisor — 400 Bad Request (Gemini)**: The free Gemini tier has limited quota. Enable billing at https://aistudio.google.com or switch to Claude/OpenAI.

**AI Advisor — API key invalid**: The API key field is cleared automatically on error. Paste your key fresh each time — do not copy error messages into the field.

**Gallery shapes have no colors**: Ensure boundaries are confirmed for the structure type being viewed.

**NCBI pipeline stuck**: If the server restarts while a pipeline is running, the job will be stuck. Delete the stuck job and resubmit.

**Tree not displaying in matrix**: Import a tree via the Phylogeny page or upload a Newick file from the matrix page.

**trimAl not found**: `brew install trimal` (macOS) or http://trimal.cgenomics.org/

**MAFFT not found**: `brew install mafft` (macOS) or https://mafft.cbrc.jp/

---

## Citation

If you use GyroMorpho in your research, please cite:

> Boeger, W.A.P. (2026). GyroMorpho v2: A pipeline for morphometric analysis, automated taxonomic description, and phylogenetic inference of Gyrodactylidae. https://github.com/wboeger/AI_morpho

## License

This project is provided for academic and research use.
