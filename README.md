# GyroMorpho v2

A web-based pipeline for morphometric analysis and automated taxonomic description of sclerotized structures of Gyrodactylidae (Monogenoidea). The system uses landmark-based geometric morphometrics with Generalized Procrustes Analysis (GPA) to compute discrete character states from continuous shape data, generate species descriptions, and export phylogenetic matrices.

## Features

- **ImageJ macros**: Interactive landmarking macros for hooks and anchors — contour extraction via wand tool, 3 anatomical landmarks, equidistant resampling to 100 pseudolandmarks
- **Landmark management**: Import, visualize, and edit 2D pseudolandmarks for hooks, anchors, bars, and MCO
- **Boundary assignment**: Define anatomical part boundaries with click, range, or lasso selection
- **Character computation**: Automatic geometric character states via Procrustes-aligned measurements (ratios, angles, curvatures, sinuosity)
- **Character workshop**: Define, edit, reorder, and delete character states; view measurement explanations and specimen reference panels
- **Character matrix**: Interactive matrix with confidence coloring, cell-level override, and gallery views with shape/image toggle
- **Gallery**: Sortable specimen gallery with color-coded landmark shapes, structure type switcher, lightbox zoom, and inline state assignment
- **Species descriptions**: Auto-generated morphological descriptions from character data
- **Taxonomic diagnoses**: Comparative diagnoses for user-defined taxonomic groups
- **Export**: Nexus, TNT, CSV, and JSON formats for downstream phylogenetic analysis
- **Multi-user**: Role-based access (admin, annotator) with full audit logging

## Requirements

- Python 3.10 or later
- pip (Python package manager)
- [ImageJ or Fiji](https://imagej.net/ij/) (for landmark extraction macros)
- A modern web browser (Chrome, Firefox, Safari, Edge)

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

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note on PyTorch**: The `torch` and `torchvision` packages are large. If you do not plan to use the U-Net segmentation module, you can install without them:
> ```bash
> pip install Flask Flask-SQLAlchemy Flask-Login Flask-WTF Werkzeug numpy Pillow opencv-python-headless
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

The server starts at **http://127.0.0.1:5000**. Open this URL in your browser.

## Quick Start Guide

### Step 1: Register and create a project

1. Open http://127.0.0.1:5000 in your browser.
2. Click **Register** and create an account (the first user becomes admin).
3. Click **New Project**, enter a name and description.

The project dashboard shows a **Pipeline** bar at the top with the full workflow: ImageJ Macros → Import Landmarks → Assign Boundaries → Define Characters → Compute & Review Matrix.

### Step 2: Extract landmarks with ImageJ macros

Go to your project page and click **Import from Folders**. The page is organized into four steps.

**Step 1 — Download macros:**

Two ImageJ macros are included in the `macros/` directory and are also available for download (pre-configured with project directories) from the import page:

- **Hook macro** (`macrogyrolandmark_v5.5.ijm`): Landmarks L1 = Point tip, L2 = Toe tip, L3 = Junction Point-Shaft (inner face)
- **Anchor macro** (`macrogyrolandmark_v5_anchors.ijm`): Landmarks L1 = Point, L2 = External tip superficial root, L3 = Distal-most base deep root

Open the downloaded `.ijm` file in ImageJ/Fiji via **Plugins → Macros → Run...**

The macro workflow:
1. **Session setup**: Configure input/output directories, enhancement settings, wand tolerance, landmark count (100)
2. **Image review**: Accept, reject (with logged reason), or skip each image
3. **Crop & enhance**: Draw bounding rectangle; optional Gaussian blur, CLAHE contrast, Unsharp Mask; 3× upscale
4. **Orientation**: Verify structure points right (optional horizontal flip)
5. **B&W conversion**: Optional threshold-based binary mask for cleaner contour extraction
6. **Wand tool**: Click on the structure outline to extract the contour; adjustable tolerance
7. **Contour smoothing**: 3-point moving average (configurable passes)
8. **Landmark placement**: Click to place L1, L2, L3 sequentially
9. **Equidistant resampling**: 100 points starting from L1, spaced by arc length
10. **Verification & editing**: Review color-coded overlay (cyan=L1, yellow=L2, magenta=L3, green=semilandmarks); optionally edit individual points
11. **Output**: One CSV file per specimen (X,Y columns, 100 rows); plus QC and rejection logs

Full backward navigation is available at every stage.

### Step 3: Import data into GyroMorpho

**Import landmarks (CSV files):**

1. In the import page, scroll to **Step 2: Import Landmarks from Folders**.
2. Click **+ Add Folder**.
3. Enter the full path to a folder containing CSV landmark files (output from the macros above). Each CSV has X and Y columns with 100 landmark coordinates.
4. Select the structure type (Marginal Hook, Anchor, etc.).
5. Click **Scan** to preview which files will be imported and how species names are parsed from filenames.
6. Add more folders if needed (e.g., one folder for hooks, another for anchors).
7. Click **Import All**.

Filenames are automatically parsed into species names. Supported formats:
- `Gyrodactylus_salaris.csv` (underscore-separated)
- `AB063294Gyrodactylusanguillae.csv` (concatenated with accession prefix)
- `JF836137.1|Gyrocerviceanseris_passamaquoddyensis.csv` (pipe-separated)

**Import part boundaries (JSON files):**

1. Scroll to **Step 3: Import Part Boundaries from JSON**.
2. Click **+ Add JSON File**.
3. Enter the path to a JSON file containing boundary definitions. The JSON format maps specimen names to part indices:
   ```json
   {
     "Gyrodactylus_salaris": {
       "Point": [1, 2, 3, 4, 5],
       "Shaft": [6, 7, 8, 9, 10],
       "Toe": [11, 12, 13]
     }
   }
   ```
   Indices in the JSON are **1-based** (they are converted to 0-based internally).
4. Select the structure type and click **Scan** to preview.
5. Click **Import Boundaries**.

After boundary import, character states are automatically computed using Generalized Procrustes Analysis.

**Import images:**

1. Scroll to **Step 4: Import Images from Folder**.
2. Enter the path to a folder containing specimen images (PNG, JPG, GIF).
3. Images are matched to specimens by species name from the filename (e.g., `Gyrodactylus salaris.png`).
4. Click **Scan** to preview matches, then **Import Images**.

### Step 4: Review boundaries

On the project page, each specimen's structure has an **Edit Boundaries** link (visible when landmarks exist). The boundary editor provides:

- **Click mode**: Click individual landmarks to assign them to a part.
- **Range mode**: Click two landmarks to assign the entire range between them.
- **Lasso mode**: Draw a freehand selection around landmarks.
- **Keyboard shortcuts**: Press 1-6 to select parts, C/R/L to switch modes, Ctrl+Z to undo.
- **Copy from similar**: Automatically copy boundaries from the most morphologically similar specimen that already has confirmed boundaries.

Click **Confirm** to save boundaries and trigger character computation.

### Step 5: Character matrix

Click **Character Matrix** from the project page to view the matrix:

- **Rows** = specimens (species), **columns** = characters.
- Cells are color-coded by confidence: green (high), yellow (medium), red (low), gray (not applicable).
- Click any cell to see details (raw value, confidence, computation type) and override the state if needed.
- Use the filter buttons (Hook, Anchor, Bar, MCO, All) to show specific structure types.
- Click a character code in the header to open the **Gallery** view:
  - Specimens sorted by raw value (geometric) or state (manual).
  - Landmark-derived shape outlines colored by anatomical parts, with a color legend.
  - **Structure type switcher**: View other structure images/shapes for context (e.g., see anchors while coding hook characters).
  - **Shapes/Images toggle**: Switch between landmark outlines and uploaded specimen photographs.
  - **Lightbox**: Click any thumbnail to zoom in with state assignment buttons.
  - **Inline state buttons**: Assign states directly from the gallery grid without opening a modal.

**Compute All Characters**: Click this button on the project page to batch-recompute all geometric characters using Generalized Procrustes Analysis. This aligns all specimens of the same structure type before computing measurements.

### Step 6: Character workshop

Click **Character Workshop** from the project page to manage character definitions:

- View all characters grouped by structure type.
- Toggle characters active/inactive (inactive characters are excluded from the matrix and exports).
- **Edit character**: Opens a two-panel view:
  - **Left**: Edit name, description, parts, geometric operation, formula, states (with drag-and-drop reorder, up/down arrows, and delete buttons), and dependencies.
  - **Right**: Reference panel showing all specimen thumbnails with state badges, structure type switcher, shapes/images toggle, and click-to-zoom lightbox.
  - **Measurement explanation**: A collapsible box at the top explains exactly how the system computes the character — which geometric operation is used, what it measures, how Procrustes alignment is applied, and how raw values map to states via thresholds.
- Create new characters with custom geometric operations or manual coding.
- View the value distribution for any character to check threshold placement.

### Step 7: Species descriptions

Click **Descriptions** from the project page:

- View auto-generated morphological descriptions for each specimen based on its character states.
- Descriptions are formatted in standard taxonomic prose.
- Click **Regenerate** to update a description after changing character values.

### Step 8: Taxonomic diagnoses

Click **Diagnoses** from the project page:

- Create taxonomic groups (e.g., genus, subfamily) by selecting which species belong to each group.
- The system generates comparative diagnoses highlighting distinguishing features among the included species.
- Edit diagnoses manually if needed.

### Step 9: Export

Click **Export** from the project page. Available formats:

| Format | Description | Use case |
|--------|-------------|----------|
| **CSV** | Simple matrix (species x characters) | Spreadsheet analysis |
| **CSV Detailed** | Matrix with raw values and confidence | Detailed analysis |
| **Nexus** | Standard phylogenetic format | MrBayes, PAUP*, Mesquite |
| **TNT** | TNT format | TNT parsimony analysis |
| **JSON** | Complete project data | Backup, re-import |
| **Descriptions** | Formatted species descriptions | Publications |
| **Diagnoses** | Formatted group diagnoses | Publications |

All matrix exports support optional filters: structure type, DNA-only specimens.

## How Character States Are Computed

### Generalized Procrustes Analysis (GPA)

Before computing character values, all specimens of the same structure type are aligned using GPA:

1. **Center**: Each specimen's landmarks are translated so the centroid is at the origin.
2. **Scale**: Landmarks are scaled to unit centroid size (removes size differences).
3. **Rotate**: Specimens are iteratively rotated to minimize the sum of squared distances to the mean shape.

This ensures that character measurements are **scale-independent** and **orientation-independent**.

### Geometric operations

Characters are computed from the Procrustes-aligned landmarks using these operations:

| Operation | Description | Example |
|-----------|-------------|---------|
| `ratio_arc_length` | Ratio of arc lengths of two parts | C01: Point length / Shaft length |
| `sinuosity` | Arc length / chord length | C03: Point waviness |
| `mean_curvature` | Mean Menger curvature along a part | C05: Shaft curvature |
| `junction_angle` | Angle between direction vectors at part junction | C02: Point-Shaft angle |
| `direction_angle` | Angle between direction vectors of two parts | C06: Shaft-Base angle |
| `relative_position` | Normalized vertical displacement between part tips | C04: Point vs Toe level |
| `max_curvature` | Maximum local curvature along a part | Sharpest bend detection |
| `presence_threshold` | Part arc length as fraction of total | C10: Heel conspicuousness |
| `sinuosity_with_direction` | Signed sinuosity (positive = outward bow, negative = inward) | C04: Point direction |
| `angle_between_parts` | Angle at the fork between two diverging parts | A09: Shaft-root angle |

### State mapping

Each raw numeric value is mapped to a discrete state using threshold ranges defined in the character definition. A confidence score is computed based on the distance from the nearest threshold boundary (farther from boundary = higher confidence).

## Default Character Library

The system ships with 36 pre-defined characters:

- **C01-C12**: Marginal hook (12 geometric characters)
- **A01-A09**: Anchor (8 geometric + 1 manual)
- **B01-B06**: Superficial bar (6 manual)
- **D01-D03**: Deep bar (3 manual)
- **M01-M06**: MCO (6 manual)

Thresholds for geometric characters are calibrated for Procrustes-normalized data from Gyrodactylidae. You can adjust thresholds in the Character Workshop to fit your dataset.

## Project Structure

```
AI_morpho/
  run.py                 # Entry point
  config.py              # Configuration (DB path, upload folder, structure parts)
  requirements.txt       # Python dependencies
  macros/                # ImageJ landmarking macros
    macrogyrolandmark_v5.5.ijm        # Hook landmarking (interactive)
    macrogyrolandmark_v5_anchors.ijm  # Anchor landmarking (interactive)
  app/
    __init__.py          # Flask app factory
    models.py            # SQLAlchemy data models
    characters.py        # Character computation engine + default library
    descriptions.py      # Species description generator
    export.py            # Export format generators
    geometry.py          # Geometric functions (curvature, angles, etc.)
    procrustes.py        # GPA, PCA, Procrustes alignment
    routes/              # Flask blueprints (auth, project, matrix, etc.)
    templates/           # Jinja2 HTML templates
    static/css/          # Stylesheet
  unet/                  # U-Net segmentation module (optional)
  tests/                 # Test suite
  data/                  # Created at runtime (database + uploads)
```

## Configuration

Edit `config.py` to customize:

- `SECRET_KEY`: Set a secure random key for production.
- `UPLOAD_FOLDER`: Where specimen images are stored.
- `STRUCTURE_PARTS`: Part names for each structure type.
- `LANDMARK_COUNTS`: Fixed landmark counts (hook=100, anchor=100).
- `ADAPTIVE_RANGES`: Landmark count ranges for adaptive structures.

## Multi-user Workflow

1. The first registered user becomes **admin**.
2. Admin creates projects and invites team members via the project page.
3. Members can be assigned **admin** (full access) or **annotator** (data entry) roles.
4. All actions are logged in the activity log for audit purposes.
5. Character overrides record who changed what, when, and why.

## Troubleshooting

**"Database is locked" error**: This occurs when multiple processes access the SQLite database simultaneously. The app is configured with a 30-second timeout. Ensure only one instance of `run.py` is running. If the error persists, stop all Python processes and delete any stale `data/db.sqlite-journal` file.

**Characters show all "?"**: Click **Compute All Characters** on the project page. Characters require both landmarks and part boundaries to be present.

**Species names not matching on import**: Use the **Scan** button to preview how filenames are parsed before importing. The parser handles underscores, concatenated names, accession prefixes, and pipe-separated formats.

**Thresholds seem wrong for my data**: After importing new data, check the distribution of raw values via the Character Workshop. Adjust thresholds based on the actual data range for your taxon.

## Citation

If you use GyroMorpho in your research, please cite:

> Boeger, W.A.P. (2026). GyroMorpho: A pipeline for morphometric analysis and automated taxonomic description of Gyrodactylidae. https://github.com/wboeger/AI_morpho

## License

This project is provided for academic and research use.
