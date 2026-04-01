# GyroMorpho v2 — Pipeline Specification

## Overview

A web-based collaborative platform for taxonomists to describe sclerotized structures of Gyrodactylidae species and produce phylogenetic character matrices. Takes microscopy images as input, extracts landmarks automatically (hooks/anchors), supports manual visual coding (bars/MCO), and outputs character matrices in standard phylogenetic formats plus auto-generated taxonomic descriptions and diagnoses.

**Technology stack**: Python 3.10+ / Flask / SQLite / HTML+CSS+JS / D3.js / PyTorch (U-Net) / NumPy / Pillow / OpenCV

**Working directory**: `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho2`

**Reference data from previous work**: `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/` (existing pipeline with hooks and anchors implemented — use as reference for geometric computation logic, NOT as code to import)

---

## Development Phases

Build each phase fully before moving to the next. Each phase should be testable independently.

### Phase 1: Core Infrastructure

#### 1.1 Flask Application Skeleton

Create a Flask app with:

- `app/` directory with `__init__.py`, `routes/`, `models/`, `templates/`, `static/`
- SQLite database via SQLAlchemy
- Flask-Login for user authentication
- Blueprint-based route organization (one blueprint per pipeline stage)
- `run.py` entry point
- `requirements.txt`

#### 1.2 Data Model (SQLAlchemy)

```
User
  - id, username, email, password_hash, role (admin | annotator | reviewer)

Project
  - id, name, description, created_by (FK User), created_at

ProjectMembership
  - user_id (FK User), project_id (FK Project), role

Specimen
  - id, project_id (FK Project), species_name, specimen_id_label, image_path, notes
  - created_by (FK User), created_at

DNASequence
  - id, specimen_id (FK Specimen), marker (ITS | 18S | COI | other), accession, available (bool)

Structure
  - id, specimen_id (FK Specimen), structure_type (hook | anchor | superficial_bar | deep_bar | mco)
  - image_path (cropped image of this structure)
  - landmarks_json (array of [x,y] coordinates, nullable)
  - landmarks_confirmed (bool, default false)
  - boundary_json (dict mapping part_name -> [landmark_indices], nullable)
  - boundary_confirmed (bool, default false)

CharacterDefinition
  - id, project_id (FK Project)
  - code (e.g. "C01", "B03", "M_NEW_01")
  - name (e.g. "Point length")
  - structure_type (hook | anchor | superficial_bar | deep_bar | mco)
  - computation_type (geometric | manual)
  - parts_involved (JSON array of part names, e.g. ["Point", "Shaft"])
  - geometric_operation (nullable — for geometric characters only)
  - formula (nullable — custom formula string)
  - states_json (array of {code, name, description, threshold_min, threshold_max})
  - dependencies_json (array of {if_character, if_state, then: "inapplicable"})
  - active (bool, default true)
  - created_by (FK User), created_at, modified_at
  - history_json (array of {user, action, timestamp, details})

CharacterValue
  - id, structure_id (FK Structure), character_id (FK CharacterDefinition)
  - raw_value (float, nullable — for geometric characters)
  - state (string — the assigned state code, e.g. "0", "1", "2", or "-" for inapplicable)
  - confidence (float 0-1, nullable)
  - auto_assigned (bool)
  - override_by (FK User, nullable)
  - override_reason (text, nullable)
  - override_at (datetime, nullable)

TaxonomicGroup
  - id, project_id (FK Project)
  - name (e.g. "Gyrodactylus"), rank (genus | subfamily | family | etc.)
  - included_species (JSON array of species names)
  - diagnosis_text (text, nullable — auto-generated + editable)

CorrectionHistory
  - id, project_id (FK Project)
  - structure_id (FK Structure), character_id (FK CharacterDefinition)
  - old_state, new_state, reason, user_id (FK User), timestamp
```

#### 1.3 Project & Specimen Registry (UI)

Pages:

- **Dashboard** (`/`): list projects, create new project
- **Project view** (`/project/<id>`): specimen list, member management, DNA filter toggle ("show only species with DNA sequences")
- **Add specimen** (`/project/<id>/specimen/new`): upload image(s), enter species name, specimen ID, notes
- **Add structure** (`/specimen/<id>/structure/new`): crop or select region from specimen image, tag structure type
- **DNA sequences** (`/specimen/<id>/dna`): add/edit marker, accession, availability flag
- **Bulk import** (`/project/<id>/import`): upload CSV with columns: species_name, specimen_id, structure_type, image_filename, dna_markers

---

### Phase 2: Landmark System

#### 2.1 Manual Landmark Upload & Editor

- Upload CSV (100 rows × 2 columns: X, Y) for any structure — matches format from existing ImageJ macros
- **Landmark editor page** (`/structure/<id>/landmarks`):
  - Display structure image with landmark points overlaid (HTML5 Canvas or SVG)
  - Drag any point to reposition
  - Click to add a point (re-interpolates to maintain even spacing)
  - Right-click to delete a point (re-interpolates)
  - "Reset" button to revert to original
  - "Confirm" button to save (sets `landmarks_confirmed = true`)
  - Display current point count prominently

#### 2.2 Boundary Assignment Tool

- **Boundary editor page** (`/structure/<id>/boundaries`):
  - Display outline with numbered landmark points
  - For hooks: assign points to parts — Point, Shaft, Toe, Shelf, Base, Heel
  - For anchors: assign points to parts — Point, Shaft, Superficial root, Deep root
  - For bars/MCO: no boundary assignment needed (characters are manual)
  - Click on a point to set it as boundary between two parts
  - Color-code each part differently on the outline
  - "Copy from similar" button: find nearest specimen in PCA space that has confirmed boundaries, pre-fill boundaries for this specimen
  - "Confirm" button saves
- Part names per structure type are fixed (see table below) and stored in a config file

**Fixed part terminology:**

| Structure | Parts |
|-----------|-------|
| Hook | Point, Shaft, Toe, Shelf, Base, Heel |
| Anchor | Point, Shaft, SuperficialRoot, DeepRoot |
| Superficial bar | BarProper, Shield, ShieldDistalEnd, AnterolateralProcesses |
| Deep bar | (single unit — no part subdivision needed) |
| MCO | Bulb, PrincipalSpine, Spinelets |

#### 2.3 Procrustes Alignment & PCA

Implement in Python (NumPy only, no sklearn):

- Generalized Procrustes Analysis: translate to centroid, scale to unit centroid size, rotate to minimize sum of squared distances
- PCA via SVD on the aligned landmark matrix
- Used for: specimen ordering by similarity, "copy from similar" boundary propagation, morphospace visualization
- Reference implementation: `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/scripts/procrustes_pca_analysis.py`

#### 2.4 Adaptive Landmark Count (Bars and MCO only)

For superficial bar, deep bar, and MCO:

1. User uploads or draws an initial 50-point outline
2. System computes curvature variance along the outline (using Menger curvature at each interior point)
3. If curvature variance > threshold → suggest higher count (up to 200)
4. User can accept suggested count or set manually
5. System re-interpolates to the chosen count with equidistant spacing
6. Hooks and anchors are always 100 landmarks (fixed)

---

### Phase 3: Character Engine

#### 3.1 Pre-loaded Character Library

On project creation, populate `CharacterDefinition` table with the following default characters. All are `active=true` by default. The taxonomist can deactivate, modify, or delete any of them.

##### Hook Characters (C01–C12) — computation_type: geometric

All hook characters use 100-point landmarks with boundary assignments. Reference implementation for geometric functions: `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/pipeline/characters.py` and `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/pipeline/geometry.py`

**C01 — Point length**
- parts_involved: ["Point", "Shaft"]
- geometric_operation: "ratio_arc_length"
- formula: `arc_length(Point) / arc_length(Shaft)`
- states: [
    {code: "0", name: "moderate", description: "Point approximately half shaft length", threshold_min: 0.49, threshold_max: 0.84},
    {code: "1", name: "long", description: "Point longer than shaft", threshold_min: 1.08, threshold_max: null},
    {code: "2", name: "subequal", description: "Point and shaft approximately equal", threshold_min: 0.84, threshold_max: 1.08},
    {code: "3", name: "very short", description: "Point much shorter than shaft", threshold_min: null, threshold_max: 0.49}
  ]

**C02 — Point curvature**
- parts_involved: ["Point", "Shaft"]
- geometric_operation: "junction_angle"
- formula: `angle at Shaft→Point junction`
- states: [
    {code: "0", name: "evenly curved", description: "Smooth transition, no abrupt bend"},
    {code: "1", name: "recurved", description: "Point curves back sharply"},
    {code: "2", name: "approximately 90°", description: "Near right-angle junction"}
  ]

**C03 — Point waviness**
- parts_involved: ["Point"]
- geometric_operation: "sinuosity"
- formula: `arc_length(Point) / chord_length(Point)`
- states: [
    {code: "0", name: "straight", description: "Sinuosity near 1.0"},
    {code: "1", name: "slightly wavy", description: "Moderate sinuosity"},
    {code: "2", name: "wavy", description: "High sinuosity"}
  ]

**C04 — Point vs Toe level**
- parts_involved: ["Point", "Toe"]
- geometric_operation: "relative_position"
- formula: `vertical displacement between Point tip and Toe tip, normalized`
- states: [
    {code: "0", name: "point below toe", description: "Point tip extends below toe level"},
    {code: "1", name: "approximately level", description: "Point and toe at similar height"},
    {code: "2", name: "point above toe", description: "Point tip above toe level"}
  ]

**C05 — Shaft curvature**
- parts_involved: ["Shaft"]
- geometric_operation: "mean_curvature"
- formula: `mean local curvature along Shaft`
- states: [
    {code: "0", name: "straight", description: "Shaft nearly straight"},
    {code: "1", name: "slightly curved", description: "Gentle curvature"},
    {code: "2", name: "curved", description: "Conspicuous curvature"}
  ]

**C06 — Shaft angle**
- parts_involved: ["Shaft", "Base"]
- geometric_operation: "direction_angle"
- formula: `angle between Shaft direction vector and Base direction vector`
- states: [
    {code: "0", name: "divergent", description: "Shaft angled away from base axis"},
    {code: "1", name: "aligned", description: "Shaft roughly aligned with base"}
  ]

**C07 — Shelf profile**
- parts_involved: ["Shelf"]
- geometric_operation: "sinuosity"
- formula: `arc_length(Shelf) / chord_length(Shelf)`
- states: [
    {code: "0", name: "straight", description: "Shelf outline straight"},
    {code: "1", name: "slightly wavy", description: "Minor undulation"},
    {code: "2", name: "wavy", description: "Conspicuously wavy shelf"}
  ]

**C08 — Base profile**
- parts_involved: ["Base"]
- geometric_operation: "sinuosity"
- formula: `arc_length(Base) / chord_length(Base)`
- states: [
    {code: "0", name: "straight", description: "Base outline straight"},
    {code: "1", name: "slightly wavy", description: "Minor undulation"},
    {code: "2", name: "wavy", description: "Conspicuously wavy base"}
  ]

**C09 — Base-Heel ratio**
- parts_involved: ["Base", "Heel"]
- geometric_operation: "ratio_arc_length"
- formula: `arc_length(Base) / arc_length(Heel)`
- states: [
    {code: "0", name: "base much longer", description: "Base dominates"},
    {code: "1", name: "subequal", description: "Base and heel approximately equal"},
    {code: "2", name: "heel longer", description: "Heel dominates"}
  ]

**C10 — Heel conspicuousness**
- parts_involved: ["Heel"]
- geometric_operation: "presence_threshold"
- formula: `arc_length(Heel) / total_arc_length`
- states: [
    {code: "0", name: "absent", description: "No discernible heel"},
    {code: "1", name: "conspicuous", description: "Heel clearly present"}
  ]

**C11 — Heel profile**
- parts_involved: ["Heel"]
- geometric_operation: "sinuosity"
- formula: `arc_length(Heel) / chord_length(Heel)`
- dependencies: [{if_character: "C10", if_state: "0", then: "inapplicable"}]
- states: [
    {code: "0", name: "smooth", description: "Heel outline smooth"},
    {code: "1", name: "wavy", description: "Heel outline undulating"}
  ]

**C12 — Heel-Shaft transition**
- parts_involved: ["Heel", "Shaft"]
- geometric_operation: "junction_angle"
- formula: `angle at Heel→Shaft junction`
- states: [
    {code: "0", name: "abrupt", description: "Sharp angle at transition"},
    {code: "1", name: "gradual", description: "Smooth transition"}
  ]

##### Anchor Characters (A01–A09) — computation_type: geometric (except A08)

Reference: `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/pipeline/characters_anchors.py`

**A01 — Point length**
- parts_involved: ["Point", "Shaft"]
- geometric_operation: "ratio_arc_length"
- formula: `arc_length(Point) / arc_length(Shaft)`
- states: [
    {code: "0", name: "short", description: "0.5–1.0 of shaft", threshold_min: 0.5, threshold_max: 1.0},
    {code: "1", name: "long", description: "≥1.0 of shaft", threshold_min: 1.0, threshold_max: null},
    {code: "2", name: "very short", description: "<0.5 of shaft", threshold_min: null, threshold_max: 0.5},
    {code: "3", name: "approximately half shaft", description: "~0.5 of shaft"}
  ]

**A02 — Point curvature**
- parts_involved: ["Point", "Shaft"]
- geometric_operation: "junction_angle"
- states: [{code: "0", name: "evenly curved"}, {code: "1", name: "recurved"}, {code: "2", name: "approximately 90°"}]

**A03 — Superficial root length**
- parts_involved: ["SuperficialRoot", "Shaft"]
- geometric_operation: "ratio_arc_length"
- formula: `arc_length(SuperficialRoot) / arc_length(Shaft)`
- states: [{code: "0", name: "shorter"}, {code: "1", name: "subequal"}, {code: "2", name: "longer"}]

**A04 — Deep root form**
- parts_involved: ["DeepRoot", "Shaft"]
- geometric_operation: "ratio_arc_length"
- formula: `arc_length(DeepRoot) / arc_length(Shaft)`
- states: [{code: "0", name: "knob-shaped", description: "Rudimentary, barely protruding"}, {code: "1", name: "distinct root", description: "Clearly formed root"}]

**A05 — Root divergence angle**
- parts_involved: ["SuperficialRoot", "DeepRoot"]
- geometric_operation: "angle_between_parts"
- formula: `angle between root direction vectors at fork point`
- states: [{code: "0", name: "acute", description: "<70°"}, {code: "1", name: "right angle", description: "70–120°"}, {code: "2", name: "obtuse", description: ">120°"}]

**A06 — Superficial root profile**
- parts_involved: ["SuperficialRoot"]
- geometric_operation: "sinuosity_with_direction"
- states: [{code: "0", name: "straight"}, {code: "1", name: "curved inward"}, {code: "2", name: "curved outward"}]

**A07 — Deep root profile**
- parts_involved: ["DeepRoot"]
- geometric_operation: "sinuosity"
- dependencies: [{if_character: "A04", if_state: "0", then: "inapplicable"}]
- states: [{code: "0", name: "straight"}, {code: "1", name: "wavy"}]

**A08 — Sclerite at superficial root tip** — computation_type: manual
- parts_involved: ["SuperficialRoot"]
- states: [{code: "0", name: "absent"}, {code: "1", name: "present"}]

**A09 — Shaft–superficial root angle**
- parts_involved: ["Shaft", "SuperficialRoot"]
- geometric_operation: "junction_angle"
- states: (defined interactively by taxonomist on first use)

##### Superficial Bar Characters (B01–B06) — computation_type: manual (ALL)

All superficial bar characters are coded visually by the taxonomist. The system presents the structure image and state definitions with reference exemplar images.

**B01 — Membrane shape**
- structure_type: superficial_bar
- states: [
    {code: "0", name: "thin, long, tapering distally", description: "Proximal margin does not reach extremities of bar (as in G. elegans)"},
    {code: "1", name: "thin, short", description: "As in Cichlidae nyanzae"},
    {code: "2", name: "subrectangular"},
    {code: "3", name: "subtriangular"},
    {code: "4", name: "distally round"},
    {code: "5", name: "subquadrate with midlength constriction", description: "Spathulated (as in G. stunkardi)"}
  ]

**B02 — Shield morphology**
- structure_type: superficial_bar
- states: [
    {code: "0", name: "absent"},
    {code: "1", name: "two ribbon-like projections"},
    {code: "2", name: "thin plate"},
    {code: "3", name: "thin ribbon-like structure"}
  ]

**B03 — Supporting ribs along shield**
- structure_type: superficial_bar
- dependencies: [{if_character: "B02", if_state: "0", then: "inapplicable"}]
- states: [{code: "0", name: "absent"}, {code: "1", name: "present"}]

**B04 — Posterior knob-like structure near midlength**
- structure_type: superficial_bar
- states: [{code: "0", name: "absent"}, {code: "1", name: "present"}]

**B05 — Margin of distal end of shield**
- structure_type: superficial_bar
- dependencies: [{if_character: "B02", if_state: "0", then: "inapplicable"}]
- states: [{code: "0", name: "smooth"}, {code: "1", name: "clefted"}]

**B06 — Anterolateral projections**
- structure_type: superficial_bar
- states: [
    {code: "0", name: "absent"},
    {code: "1", name: "incipient", description: "Barely visible"},
    {code: "2", name: "conspicuous", description: "< 0.5 of bar width"},
    {code: "3", name: "long", description: "≥ 0.5 of bar width"}
  ]

##### Deep Bar Characters (D01–D03) — computation_type: manual (ALL)

**D01 — Extremity ornaments**
- structure_type: deep_bar
- states: [
    {code: "0", name: "absent"},
    {code: "1", name: "single, uniform", description: "Extremities with simple uniform expansion"},
    {code: "2", name: "bifid", description: "Extremities with subterminal expansion or bifid (as in G. guatopotei)"},
    {code: "3", name: "tapering", description: "Tapering at extremities following a slight expansion"}
  ]

**D02 — Midlength notch**
- structure_type: deep_bar
- states: [{code: "0", name: "absent"}, {code: "1", name: "present", description: "As in G. mediotorus"}]

**D03 — Overall shape**
- structure_type: deep_bar
- states: [
    {code: "0", name: "straight"},
    {code: "1", name: "gently arched"},
    {code: "2", name: "saddle-shaped"},
    {code: "3", name: "with median notch"}
  ]

##### MCO Characters (M01–M06) — computation_type: manual (ALL)

**M01 — Bulb morphology**
- structure_type: mco
- states: [
    {code: "0", name: "elongate, muscular", description: "As in Afrogyrodactylus, Citharodactylus"},
    {code: "1", name: "bulbous, spherical", description: "Typical of many Gyrodactylus"}
  ]

**M02 — Principal spine**
- structure_type: mco
- states: [
    {code: "0", name: "absent", description: "As in Gyrdicotylus gallieni"},
    {code: "1", name: "straight", description: "Embedded in bulbous musculature (as in Scleroductus, Macrogyrodactylus)"},
    {code: "2", name: "recurved basally", description: "As in many Gyrodactylus spp."}
  ]

**M03 — Spinelet armature**
- structure_type: mco
- states: [{code: "0", name: "unarmed", description: "No spinelets"}, {code: "1", name: "armed", description: "Spinelets present"}]

**M04 — Spinelet arrangement**
- structure_type: mco
- dependencies: [{if_character: "M03", if_state: "0", then: "inapplicable"}]
- states: [
    {code: "0", name: "single row"},
    {code: "1", name: "one row, mixed sizes", description: "Larger spinelets mingled with smaller"},
    {code: "2", name: "scattered", description: "Small spinelets randomly distributed"},
    {code: "3", name: "one row, equally sized"},
    {code: "4", name: "one row, anterior pair larger", description: "Anteriormost bilateral pair visually larger"},
    {code: "5", name: "two well-defined rows"}
  ]

**M05 — Spinelet count**
- structure_type: mco
- dependencies: [{if_character: "M03", if_state: "0", then: "inapplicable"}]
- states: integer (0–n), not discrete bins — stored as raw count

**M06 — Bulb shape**
- structure_type: mco
- states: [
    {code: "0", name: "spherical"},
    {code: "1", name: "ovoid"},
    {code: "2", name: "pyriform", description: "Pear-shaped"},
    {code: "3", name: "irregular", description: "Asymmetric or complex"}
  ]

#### 3.2 Geometric Computation Module

Implement the following geometric functions in a `geometry.py` module (NumPy only):

```python
def arc_length(coords: np.ndarray) -> float:
    """Sum of Euclidean distances between consecutive points."""

def chord_length(coords: np.ndarray) -> float:
    """Straight-line distance from first to last point."""

def sinuosity(coords: np.ndarray) -> float:
    """arc_length / chord_length. 1.0 = straight."""

def local_curvature(coords: np.ndarray) -> np.ndarray:
    """Menger curvature at each interior point using 3-point circle."""

def mean_curvature(coords: np.ndarray) -> float:
    """Mean of absolute local curvature values."""

def angle_between_vectors(v1: np.ndarray, v2: np.ndarray) -> float:
    """Angle in degrees between two 2D direction vectors (0–180)."""

def junction_angle(part_a_coords: np.ndarray, part_b_coords: np.ndarray) -> float:
    """Angle at the junction between two consecutive parts.
    Uses direction vectors at the last N points of part_a and first N points of part_b."""

def direction_vector(coords: np.ndarray, n_points: int = 5) -> np.ndarray:
    """Average direction vector over the first or last n_points."""

def relative_vertical_position(coords_a: np.ndarray, coords_b: np.ndarray) -> float:
    """Normalized vertical displacement between endpoints of two parts."""

def circularity(coords: np.ndarray) -> float:
    """4 * pi * area / perimeter^2. Requires closed contour."""
```

Reference implementation: `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/pipeline/geometry.py`

#### 3.3 Character Assignment Engine

For each structure with confirmed landmarks and boundaries:

1. Extract coordinates for each anatomical part using boundary indices
2. For each active **geometric** character:
   a. Compute raw value using the specified formula
   b. Map to state using thresholds
   c. Compute confidence: distance from nearest threshold boundary (normalized 0–1)
   d. Store in `CharacterValue`
3. For each active **manual** character:
   a. Present to taxonomist for visual coding (see Phase 5)
   b. Store taxonomist's assignment in `CharacterValue` with `auto_assigned=false`
4. Enforce dependencies: if a dependency condition is met, set state to "-" (inapplicable)

Recomputation: when a new character is added or thresholds are modified, recompute only affected `CharacterValue` rows. Preserve manual overrides unless the taxonomist explicitly requests full recomputation.

---

### Phase 4: Character Builder

#### 4.1 Character Workshop Page (`/project/<id>/characters`)

Display all characters in a table:
- Code, Name, Structure type, Computation type, # States, Active (toggle), Actions (edit, delete, view distribution)
- "Add New Character" button
- Filter by structure type

#### 4.2 New Character Creation (geometric)

Step-by-step wizard:

1. **Select structure type** (hook or anchor — only these support geometric characters)
2. **Name the character** and write a description
3. **Select anatomical parts involved** (checkboxes from the part list for that structure)
4. **Choose geometric operation** from dropdown:
   - Ratio of arc lengths: `arc_length(Part A) / arc_length(Part B)`
   - Sinuosity of part: `arc_length(Part) / chord_length(Part)`
   - Mean curvature of part: `mean(|local_curvature(Part)|)`
   - Max curvature of part: `max(|local_curvature(Part)|)`
   - Junction angle between parts: `junction_angle(Part A, Part B)`
   - Direction angle between parts: `angle(direction(Part A), direction(Part B))`
   - Relative position: `vertical_displacement(Part A, Part B)`
   - Presence/absence: `arc_length(Part) / total_arc_length > threshold`
   - Custom formula: free-text using the function names above
5. **Preview**: system computes the raw value for ALL specimens that have confirmed landmarks+boundaries for this structure. Display:
   - Histogram of raw values (D3.js interactive)
   - Each bar clickable to show specimen image
   - Summary statistics (min, max, mean, median, quartiles)
6. **Define states**:
   - Taxonomist drags vertical threshold lines on the histogram
   - Each region between thresholds becomes a state
   - Name and describe each state in text fields
   - System shows specimen count per state
   - Taxonomist can click "Show specimens" for any state to see a gallery
7. **Set dependencies** (optional): "This character is inapplicable when [other character] = [state]"
8. **Save**: character is added to the library; matrix recomputes for this character across all specimens

#### 4.3 New Character Creation (manual)

Simpler wizard:

1. **Select structure type** (any of the 5)
2. **Name and describe**
3. **Define states**: for each state, provide code, name, description, and optionally upload a reference exemplar image
4. **Set dependencies** (optional)
5. **Save**: character appears in the manual coding queue for all specimens of that structure type

#### 4.4 Edit Existing Character

- Modify name, description, thresholds, states, dependencies
- **Threshold adjustment**: same histogram view as creation; drag thresholds to new positions
- **Add/remove states**: split a state by adding a threshold, or merge by removing one
- When thresholds change, affected `CharacterValue` rows are recomputed (geometric) or flagged for re-review (manual)
- All modifications logged in `history_json`

#### 4.5 Deactivate/Remove Character

- **Deactivate**: character column hidden from matrix but data preserved
- **Remove**: character and all its values deleted (with confirmation dialog)

---

### Phase 5: Matrix Computation & Review

#### 5.1 Matrix View (`/project/<id>/matrix`)

- Spreadsheet-like table: rows = species, columns = characters (grouped by structure type)
- Cell content: state code (0, 1, 2, ... or "-" for inapplicable, "?" for unassigned)
- **Color coding**:
  - Green: confidence ≥ 0.85 (auto-assigned) or manually confirmed
  - Yellow: confidence 0.60–0.84
  - Red: confidence < 0.60
  - Gray: inapplicable
  - White: not yet coded
- Click any cell → popup showing:
  - Structure image with landmarks and boundaries overlaid
  - For geometric characters: raw computed value, threshold boundaries, state assignment
  - For manual characters: state description and reference exemplar
  - "Override" button → select different state + enter reason
- Filter: show only species with DNA, show only unconfirmed cells, show only a specific structure type

#### 5.2 Gallery Review Mode (`/project/<id>/matrix/gallery/<character_id>`)

- Review one character across all specimens
- Grid of structure images, sorted by raw value (geometric) or current state (manual)
- Each image labeled with species name and current state
- Click to change state
- Especially useful for manual characters: see all bars or MCOs side by side

#### 5.3 Manual Coding Interface (for bar and MCO characters)

When a structure has uncoded manual characters:

- Display structure image prominently
- Below: list of manual characters for that structure type
- For each character: show state options as labeled buttons with descriptions
- Taxonomist clicks to assign state
- "Skip" button for uncertain cases (leaves as "?")
- Progress bar showing how many specimens remain uncoded for this character

#### 5.4 Consensus Mode (collaborative)

- Multiple reviewers can independently code the same specimens
- System tracks each reviewer's assignments separately
- **Disagreement view**: show only cells where reviewers disagree
- For each disagreement: show who coded what, allow discussion (text comment field), resolve by admin decision
- Final matrix uses the resolved consensus

#### 5.5 Reference Phylogeny Overlay

- Upload a Newick tree file (`/project/<id>/tree/upload`)
- Tree displayed alongside matrix (D3.js phylogram on the left, matrix on the right)
- Tree tip labels matched to species names in the matrix
- Character states color-mapped on tree tips for any selected character
- Helps visually check phylogenetic signal of morphological characters
- Species in matrix but not in tree (or vice versa) are clearly marked

#### 5.6 Confidence Scoring

For geometric characters:
- confidence = 1.0 - (distance_to_nearest_threshold / max_possible_distance)
- Capped at [0, 1]. Characters far from any threshold get high confidence.

For manual characters:
- confidence = 1.0 when coded by taxonomist
- confidence = 0.0 when uncoded ("?")

---

### Phase 6: U-Net Landmark Detection

#### 6.1 Model Architecture

- Standard U-Net (encoder-decoder with skip connections)
- Input: grayscale image (256×256 resized)
- Output: heatmap with Gaussian blobs at each landmark position
- One model per structure type (5 models total)
- PyTorch implementation

Reference (archived): `/Users/walterapboeger/Desktop/Gyromorphometry/arquivo2_26/gyrodactylidae_pipeline/src/landmarks/unet_model.py`

#### 6.2 Bootstrap Training

- Hooks: use existing ~174 landmark CSVs from `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/data/landmarks/ITS csv 100/` paired with images from `/Users/walterapboeger/Desktop/Gyromorphometry/ITS-Project_Images/`
- Anchors: use existing CSVs from `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/data/landmarks/18S_csv_100/anchors/` paired with images from `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/data/Images/`
- Bars, MCO: no training data initially — models trained after ~30 manual annotations accumulate

#### 6.3 Progressive Training

- After each batch of confirmed landmarks (e.g., every 10 new specimens), offer to retrain the model
- Data augmentation: rotation (±30°), flip, brightness/contrast adjustment, slight scaling
- Training triggered manually by admin ("Retrain model" button) or on schedule
- Model versioning: keep previous model weights so rollback is possible

#### 6.4 Inference Pipeline

1. User uploads structure image
2. System runs U-Net → predicted heatmap → extract peak coordinates → order along outline
3. Resample to target count (100 for hooks/anchors, adaptive for bars/MCO)
4. Display predicted landmarks overlaid on image
5. User corrects if needed → confirms → landmark data saved
6. Confirmed corrections feed back into training data

---

### Phase 7: Descriptions & Diagnoses

#### 7.1 Species Descriptions (`/project/<id>/descriptions`)

For each species in the matrix, auto-generate a morphological description:

Template structure:
```
[Species name]

Marginal hooks: Point [C01 state] relative to shaft, [C02 state]; point outline [C03 state].
Point extending [C04 state] relative to toe. Shaft [C05 state], [C06 state] relative to base.
Shelf [C07 state]. Base [C08 state]; base [C09 state] relative to heel. Heel [C10 state].
[If C10=present:] Heel [C11 state]; heel–shaft transition [C12 state].

Anchors: Point [A01 state] relative to shaft, [A02 state]. Superficial root [A03 state]
relative to shaft; [A06 state]. Deep root [A04 state]. [If A04=distinct:] Deep root [A07 state].
Root divergence [A05 state]. [If A08=present:] Sclerite present at superficial root tip.

Superficial bar: Membrane [B01 state]. Shield [B02 state]. [If B02≠absent:]
Shield ribs [B03 state]; shield distal margin [B05 state].
Posterior knob [B04 state]. Anterolateral projections [B06 state].

Deep bar: Shape [D03 state]. Extremities [D01 state]. Midlength notch [D02 state].

Male copulatory organ: Bulb [M01 state], [M06 state]. Principal spine [M02 state].
[If M03=armed:] Spinelets [M04 state]; [M05 value] spinelets.
```

- Taxonomist can edit the generated text freely
- "Regenerate" button to reset to auto-generated version
- Export as plain text or PDF

#### 7.2 Higher-Taxon Diagnoses (`/project/<id>/diagnoses`)

- **Define groups**: create taxonomic group, assign species, set rank (genus, subfamily, etc.)
- **Auto-compute**:
  - For each character, determine state distribution within the group
  - **Invariant**: all species share the same state → diagnostic
  - **Variable**: multiple states present → report range
  - **Autapomorphic**: state(s) present in this group but absent in all others at the same rank
- **Generate diagnosis text**:

```
[Group name] ([rank])

Distinguished from other [parent rank] by: [list autapomorphic character states].

Characterized by: [list invariant character states across all structures].

Variable in: [character name] ([state A] in [species list], [state B] in [species list]); ...
```

- **Comparison table**: matrix of groups × characters, showing which states are diagnostic vs. variable
- Editable by taxonomist
- Export as text or PDF

#### 7.3 Full Monograph Export

- Combine all species descriptions + group diagnoses into a single document
- Ordered by taxonomic hierarchy (family → subfamily → genus → species)
- Include character catalog as appendix (character definitions with reference images)
- Export as PDF or structured text

---

### Phase 8: Collaboration & Polish

#### 8.1 User Management

- Registration, login, password reset (Flask-Login)
- Project-level roles: admin (full control), annotator (can code and annotate), reviewer (read-only + can add comments)
- Activity log: who did what, when

#### 8.2 Data Import/Export

- **Import existing data**:
  - Landmark CSVs from ImageJ
  - Boundary JSON files (format from AI_morpho: `{specimen_id: {part_name: [indices], coordinates: [[x,y],...]}}`). Reference files: `/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho/data/part_boundaries_hooks.json` and `part_boundaries_anchors.json`
  - Character matrices from CSV
  - Species lists from CSV
- **Export project**: full JSON dump of all data (landmarks, boundaries, characters, matrix, descriptions, correction history) for reproducibility and backup

#### 8.3 UI Polish

- Responsive layout (works on laptop screens)
- Keyboard shortcuts for gallery review (arrow keys to navigate, number keys to assign states)
- Progress dashboard: per-structure completion stats (how many specimens have landmarks, boundaries, all characters coded)
- Search and filter throughout

---

## File Structure

```
AI_morpho2/
├── run.py                          # Flask entry point
├── requirements.txt                # Dependencies
├── config.py                       # App configuration (DB path, upload folder, etc.)
├── PIPELINE_SPEC.md                # This file
│
├── app/
│   ├── __init__.py                 # Flask app factory
│   ├── models.py                   # SQLAlchemy models (all tables above)
│   ├── geometry.py                 # Geometric computation functions
│   ├── characters.py               # Character assignment engine
│   ├── descriptions.py             # Auto-description and diagnosis generation
│   ├── procrustes.py               # Procrustes alignment + PCA (NumPy SVD)
│   ├── export.py                   # CSV, NEXUS, TNT, JSON, PDF writers
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py                 # Login, register, user management
│   │   ├── project.py              # Project CRUD, specimen registry
│   │   ├── landmarks.py            # Landmark upload, editor, U-Net inference
│   │   ├── boundaries.py           # Boundary assignment tool
│   │   ├── characters.py           # Character workshop, builder, editing
│   │   ├── matrix.py               # Matrix view, gallery, review, consensus
│   │   ├── descriptions.py         # Descriptions, diagnoses, monograph
│   │   └── export.py               # Export endpoints
│   │
│   ├── templates/
│   │   ├── base.html               # Base layout with navigation
│   │   ├── auth/                   # Login, register pages
│   │   ├── project/                # Dashboard, project view, specimen forms
│   │   ├── landmarks/              # Landmark editor, upload
│   │   ├── boundaries/             # Boundary assignment tool
│   │   ├── characters/             # Workshop, builder wizard, edit forms
│   │   ├── matrix/                 # Matrix view, gallery, consensus
│   │   ├── descriptions/           # Description editor, diagnosis view
│   │   └── export/                 # Export options page
│   │
│   └── static/
│       ├── css/
│       │   └── style.css
│       ├── js/
│       │   ├── landmark_editor.js  # Canvas-based landmark editing
│       │   ├── boundary_tool.js    # Boundary assignment interaction
│       │   ├── histogram.js        # D3.js histogram for character builder
│       │   ├── matrix_view.js      # Matrix spreadsheet interaction
│       │   ├── gallery.js          # Gallery review mode
│       │   ├── tree_viewer.js      # D3.js Newick phylogram
│       │   └── utils.js            # Shared utilities
│       └── img/                    # Static assets
│
├── unet/
│   ├── model.py                    # U-Net architecture (PyTorch)
│   ├── train.py                    # Training script with augmentation
│   ├── predict.py                  # Inference: image → landmarks
│   ├── dataset.py                  # Training data loader
│   └── weights/                    # Saved model weights per structure type
│
├── data/
│   ├── uploads/                    # Uploaded specimen images
│   └── db.sqlite                   # SQLite database
│
└── tests/
    ├── test_geometry.py
    ├── test_characters.py
    ├── test_procrustes.py
    ├── test_export.py
    └── test_descriptions.py
```

---

## Key Design Principles

1. **Taxonomist is always in control**: every automated assignment can be reviewed, overridden, or rejected. New characters can be added at any moment.
2. **Manual characters are first-class**: bar and MCO characters are coded visually — the system provides the interface and exemplars, not automated assignments.
3. **Progressive automation**: start manual, automate as training data accumulates. The system gets better over time without requiring it to be good from the start.
4. **Reproducibility**: all corrections, overrides, and character definition changes are logged with user, timestamp, and reason.
5. **Simplicity**: SQLite (no server setup), Flask (lightweight), minimal dependencies. A taxonomist can run this on a laptop.
