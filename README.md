# GyroMorpho v2

A web-based pipeline for morphometric analysis, automated taxonomic description, and phylogenetic inference for sclerotized structures of Gyrodactylidae (Monogenoidea). The system uses landmark-based geometric morphometrics with Generalized Procrustes Analysis (GPA) to compute discrete character states from continuous shape data, generate species descriptions, export phylogenetic matrices, and run end-to-end molecular phylogenetic analyses.

## Features

- **ImageJ macros**: Interactive landmarking macros for hooks and anchors — contour extraction via wand tool, 3 anatomical landmarks, equidistant resampling to 100 pseudolandmarks
- **Landmark management**: Import, visualize, and edit 2D pseudolandmarks for hooks, anchors, bars, and MCO
- **Boundary assignment**: Define anatomical part boundaries with click, range, or lasso selection
- **Character computation**: Automatic geometric character states via Procrustes-aligned measurements (ratios, angles, curvatures, sinuosity)
- **Character workshop**: Define, edit, reorder, and delete character states; view measurement explanations and specimen reference panels
- **Character matrix**: Interactive matrix with confidence coloring, cell-level override, gallery views, and optional phylogenetic tree panel with synchronized leaf–row alignment
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
> pip install Flask Flask-SQLAlchemy Flask-Login Flask-WTF Werkzeug numpy Pillow opencv-python-headless biopython requests
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

## Quick Start Guide

### Step 1: Register and create a project

1. Open http://127.0.0.1:5001 in your browser.
2. Click **Register** and create an account (the first user becomes admin).
3. Click **New Project**, enter a name and description.

The project dashboard shows a **Pipeline** bar at the top with the full workflow.

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

### Step 3: Import data into GyroMorpho

**Import landmarks** — click **+ Add Folder**, enter the CSV folder path, select structure type, click **Scan** then **Import All**.

Supported filename formats:
- `Gyrodactylus_salaris.csv`
- `AB063294Gyrodactylusanguillae.csv`
- `JF836137.1|Gyrocerviceanseris_passamaquoddyensis.csv`

**Import boundaries** — import JSON files mapping specimen names to part indices (1-based):
```json
{ "Gyrodactylus_salaris": { "Point": [1,2,3,4,5], "Shaft": [6,7,8,9,10] } }
```

**Import images** — enter path to folder with PNG/JPG/GIF; images matched by species name.

### Step 4: Review boundaries

Each specimen's structure has an **Edit Boundaries** link. The boundary editor provides:
- **Click / Range / Lasso** modes; keyboard shortcuts 1–6 (select part), C/R/L (mode), Ctrl+Z (undo)
- **Copy from similar**: auto-copy boundaries from the most morphologically similar confirmed specimen

Click **Confirm** to save and trigger character computation.

### Step 5: Character matrix

Click **Character Matrix** from the project page:
- Rows = species, columns = characters; confidence coloring: green/yellow/red/gray
- Click any cell for details and override options
- Filter by structure type (Hook, Anchor, Bar, MCO) or DNA-only / unconfirmed
- **Upload a tree** (Newick/NEXUS) via the tree upload form — matrix rows reorder to match phylogenetic leaf order and an SVG clade panel appears alongside the matrix with synchronized scrolling

### Step 6: Character workshop

Click **Characters** from the project page:
- Toggle characters active/inactive; create new characters; edit thresholds
- Two-panel editor: form (left) + reference specimen panel (right)
- **Measurement explanation**: collapsible box explaining the geometric operation and state mapping

### Step 7: Species descriptions

Click **Descriptions** — auto-generated morphological descriptions in standard taxonomic prose. Click **Regenerate** after updating character values.

### Step 8: Taxonomic diagnoses

Click **Diagnoses** — create taxonomic groups (genus, subfamily) and generate comparative diagnoses.

### Step 9: Export

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

### Step 10: Phylogenetic pipeline

Click **Phylogeny** in the top navigation bar. Two modes are available:

#### NCBI Pipeline (full automated pipeline)

Replicates the R v8/v9 pipeline entirely in Python:

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
5. When completed, click **Download & Root** — downloads `infile.txt.raxml.support`, roots with `ape::root()` using the configured outgroup genera. All files saved to `Results/phylogeny/` within the job directory
6. A **phylogenetic tree popup** appears automatically showing the rooted phylogram with tip labels, bootstrap values, and a scale bar
7. Click **Import into Project** to set the tree as the project reference phylogeny — the character matrix will then reorder rows by tree leaf order and display the clade panel

#### Upload FASTA

Upload a pre-trimmed aligned FASTA directly and proceed from CIPRES submission onward.

#### CIPRES credentials

Set as environment variables to avoid entering them each time:
```bash
export CIPRES_USER=wboeger
export CIPRES_PASSWORD=your_password
export CIPRES_APP_KEY=your_app_key
```

## How Character States Are Computed

### Generalized Procrustes Analysis (GPA)

All specimens of the same structure type are aligned before measurement:
1. **Center**: translate centroid to origin
2. **Scale**: scale to unit centroid size (removes size differences)
3. **Rotate**: iterative rotation to minimize sum of squared distances to mean shape

This ensures measurements are scale-independent and orientation-independent.

### Geometric operations

| Operation | Description | Example |
|-----------|-------------|---------|
| `ratio_arc_length` | Ratio of arc lengths of two parts | C01: Point/Shaft |
| `sinuosity` | Arc length / chord length | C03: Point waviness |
| `mean_curvature` | Mean Menger curvature | C05: Shaft curvature |
| `junction_angle` | Angle at part junction | C02: Point-Shaft angle |
| `direction_angle` | Angle between direction vectors | C06: Shaft-Base angle |
| `relative_position` | Normalized vertical displacement | C04: Point vs Toe |
| `max_curvature` | Maximum local curvature | Sharpest bend |
| `presence_threshold` | Part arc as fraction of total | C10: Heel presence |
| `sinuosity_with_direction` | Signed sinuosity | C04: Point direction |
| `angle_between_parts` | Angle at fork between two parts | A09: Shaft-root angle |
| `point_curvature` | Deviation angle between point midline and middle shaft axis | A02: Point curvature |

### State mapping

Raw numeric values are mapped to discrete states via threshold ranges. Confidence = distance from nearest threshold boundary (farther = higher confidence).

## Default Character Library

36 pre-defined characters:
- **C01–C12**: Marginal hook (12 geometric characters)
- **A01–A09**: Anchor (8 geometric + 1 manual)
- **B01–B06**: Superficial bar (6 manual)
- **D01–D03**: Deep bar (3 manual)
- **M01–M06**: MCO (6 manual)

## Project Structure

```
AI_morpho2/
  run.py                        # Entry point (port 5001)
  config.py                     # Configuration
  requirements.txt              # Python dependencies
  macros/                       # ImageJ landmarking macros
  phylogeny/                    # Reference R pipeline scripts (v8, v9)
  app/
    __init__.py                 # Flask app factory + DB migration
    models.py                   # SQLAlchemy data models (incl. PhylogenyJob)
    characters.py               # Character computation engine + default library
    geometry.py                 # Geometric functions (curvature, angles, etc.)
    procrustes.py               # GPA, PCA, Procrustes alignment
    descriptions.py             # Species description generator
    export.py                   # Export format generators
    routes/
      auth.py                   # Authentication
      project.py                # Project dashboard, specimen import
      landmarks.py              # Landmark editor
      boundaries.py             # Boundary editor
      characters.py             # Character workshop
      matrix.py                 # Character matrix, tree upload
      descriptions.py           # Species descriptions & diagnoses
      export.py                 # Export routes
      phylogeny.py              # Phylogenetic pipeline (NCBI→MAFFT→trimAl→CIPRES)
    templates/
      phylogeny/phylogeny.html  # Phylogeny pipeline UI
      matrix/matrix_view.html   # Matrix with tree panel
    static/
      css/style.css
      diagrams/                 # SVG measurement diagrams
  data/                         # Created at runtime (DB + uploads)
```

## Data Storage

- **`data/db.sqlite`** — all structured data: specimens, structures, landmarks, boundaries, character definitions and values, DNA sequences, descriptions, diagnoses, phylogeny jobs, activity logs
- **`data/uploads/`** — specimen images
- **`phylogeny/Results/job_TIMESTAMP/`** — per-job phylogenetic pipeline files:
  - `18S_raw.fa`, `18S_aligned.fa`, `18S_trimmed.fa`
  - `infile.txt.raxml.support` and all other CIPRES output files
  - `rooted_tree.tre` — rooted phylogenetic tree

## Configuration

Edit `config.py` to customize:
- `SECRET_KEY`: set a secure random key for production
- `UPLOAD_FOLDER`: where specimen images and pipeline files are stored
- `CIPRES_BASE_URL`, `CIPRES_USER`, `CIPRES_APP_KEY`: CIPRES defaults (overridable via environment variables or the UI form)
- `STRUCTURE_PARTS`: part names for each structure type
- `LANDMARK_COUNTS`: fixed landmark counts (hook=100, anchor=100)

## Multi-user Workflow

1. The first registered user becomes **admin**
2. Admin creates projects and invites team members from the project page
3. Members can be **admin** (full access) or **annotator** (data entry)
4. All actions are logged in the activity log; character overrides record who, when, and why

## Troubleshooting

**Port already in use**: On macOS, AirPlay Receiver occupies port 5000. GyroMorpho runs on port 5001. Disable AirPlay Receiver in System Settings → General → AirDrop & Handoff if you need port 5000.

**"Database is locked"**: Ensure only one instance of `run.py` is running. Delete any stale `data/db.sqlite-journal` file.

**Characters show all "?"**: Click **Compute All Characters** on the project page. Both landmarks and confirmed boundaries must be present.

**Species names not matching on import**: Use the **Scan** button to preview filename parsing before importing.

**Thresholds wrong for my data**: Check raw value distributions via the Character Workshop after importing new data.

**Gallery shapes have no colors**: Ensure boundaries are confirmed for the structure type being viewed.

**NCBI pipeline stuck**: If the server restarts while a pipeline is running (fetching/aligning/trimming), the job will be stuck in that stage. Delete the stuck job and resubmit.

**CIPRES "Response ended prematurely"**: All CIPRES communication uses `curl` subprocess calls. Ensure `curl` is on your PATH and your credentials are correct.

**Tree not displaying in matrix**: The tree panel only shows if a tree has been imported into the project. Use the **Phylogeny** page to run a pipeline and import, or upload a Newick/NEXUS file directly from the matrix page.

**trimAl not found**: Install with `brew install trimal` (macOS) or from http://trimal.cgenomics.org/

**MAFFT not found**: Install with `brew install mafft` (macOS) or from https://mafft.cbrc.jp/

## Citation

If you use GyroMorpho in your research, please cite:

> Boeger, W.A.P. (2026). GyroMorpho v2: A pipeline for morphometric analysis, automated taxonomic description, and phylogenetic inference of Gyrodactylidae. https://github.com/wboeger/AI_morpho

## License

This project is provided for academic and research use.
