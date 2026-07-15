# Materials and Methods — Geometric Morphometrics of Gyrodactylid Sclerotized Structures

*This document is auto-maintained. Update after any major change to character definitions, measurement algorithms, or pipeline architecture.*

*Last revised: 2026-07-04*

---

## Pipeline Overview

Morphometric data were acquired and processed using **GyroMorpho v2**, a custom web-based platform developed for the collaborative morphometric analysis of gyrodactylid sclerotized structures (hooks, anchors, bars, and MCO). The system integrates image-based landmark annotation, automated geometric measurement, discrete character coding, phylogenetic matrix export, and AI-assisted character evaluation into a single reproducible workflow.

The platform runs as a local Flask application (Python 3.10+) backed by an SQLite database. All user data — microscopy images, landmark coordinates, character values, and phylogenetic trees — are stored within the project directory. Users access the interface through a web browser; no internet connection is required for morphometric analysis.

---

## File Storage Layout

All uploaded and generated files reside under a single root defined in `config.py`:

```
AI_morpho2/
│
├── data/
│   ├── db.sqlite                        ← SQLite database (all project metadata, specimens,
│   │                                      characters, character values, matrix states)
│   └── uploads/                         ← root upload folder (UPLOAD_FOLDER in config)
│       ├── <project_id>/                ← one folder per project (numeric ID)
│       │   └── structures/              ← structure images added via "Add Structure" or
│       │       └── <filename>.png         "Replace Image" on the Specimen page
│       ├── project_<project_id>/        ← batch-import staging area
│       │   ├── hooks/                   ← images imported via folder scan (scan_images /
│       │   ├── anchors/                   import_images routes), indexed by structure type
│       │   ├── superficial_bars/
│       │   ├── deep_bars/
│       │   ├── mcos/
│       │   └── landmarks/
│       │       └── <type>s/             ← landmark CSVs from ImageJ macro batch import
│       └── <structure_id>_<filename>    ← flat-root images from folder import (import_images)
│
└── phylogeny/
    └── Results/
        └── job_<YYYYMMDD_HHMMSS>/      ← one folder per phylogenetic analysis job
            ├── input.fa / input_filtered.fa   ← FASTA sequences (uploaded or fetched)
            ├── <marker>_raw.fa                ← raw downloaded sequences
            ├── <marker>_aligned.fa            ← MAFFT-aligned sequences
            ├── <marker>_trimmed.fa            ← trimAl-trimmed alignment
            ├── infile.txt.raxml.*             ← RAxML-NG output files
            ├── nj_tree.nwk                    ← neighbour-joining tree (if NJ run)
            └── rooted_tree.tre                ← re-rooted tree after outgroup selection
```

> **Database vs. files:** Landmark coordinates (`landmarks_json`), boundary assignments (`boundary_json`), character state codes (`CharacterValue.state`), and the active project tree (`Project.tree_newick`) are all stored as columns in the SQLite database. The files on disk are microscopy images and sequence alignments only; losing the database loses all analytical results even if the image files are intact. **Back up `data/db.sqlite` regularly.**

---

## Structures Examined

For each specimen, up to five categories of sclerotized structures were digitized:

- **Hooks** (*Hamuli*): The marginal hooks were annotated with up to six part boundaries (Point, Shaft, Base, Shelf, Heel, Toe) defining the major morphological regions.
- **Anchors**: Haptor anchors were annotated with up to four regions (Point, Shaft, SuperficialRoot, DeepRoot).
- **Superficial bars**: The superficial (dorsal) haptor bar was annotated as BarProper with optional shield structures (Shield, ShieldDistalEnd, AnterolateralProcesses).
- **Deep bars**: The deep (ventral) haptor bar, annotated as a single BarProper region.
- **Male copulatory organ (MCO)**: The sclerotized MCO was annotated with bulb and armature regions (Bulb, PrincipalSpine, Spinelets).

---

## Specimen Management

Specimens are created per project and identified by a binomial species name. Each specimen record optionally carries a specimen ID / accession label and free-text notes. Specimen names can be edited at any time from the Specimen detail page (Edit Names button). Specimens can also be created automatically when a phylogenetic tree is imported into the matrix: tip labels from the Newick string are parsed and any name not already present in the specimen list is added.

When importing data from another project (cross-project import), specimen matching is performed by **normalised epithet key**: accession prefixes, colon/semicolon suffixes, structural suffixes (e.g., `-hooks`, `-anchors`), concatenated genus+species strings, and leading numeric tokens are all stripped before comparison, leaving a canonical `"genus epithet"` key. The same normalisation is applied when matching image filenames or landmark CSVs from a folder to existing specimens.

---

## Image Acquisition and Landmark Placement

Microscopy images of individual sclerotized structures were uploaded to GyroMorpho v2. Landmark contours were placed manually using the integrated image editor, which provides pan/zoom and semi-automated contour tracing. Landmarks were placed as ordered sequences of 2D coordinate points tracing the outer contour of each structure. Part boundaries were then defined by marking the index ranges of landmark points corresponding to each morphological region (e.g., Point, Shaft, Heel). All landmark placements and boundary assignments were confirmed by the annotator before characters were computed.

Landmark CSVs (ImageJ Results table format, X and Y columns) may be uploaded individually per structure or in batch via a ZIP archive of CSVs produced by the Gyro-Landmark ImageJ macro. Batch import uses fuzzy name matching (exact → starts-with → substring) against `Specimen.species_name` and auto-resamples to the target landmark count for each structure type (`Config.LANDMARK_COUNTS`). Images may similarly be imported in batch from a local folder using the Scan / Import Images tools, which apply the same epithet-key normalisation to match filenames to specimens.

---

## Geometric Character Measurement

All geometric characters were computed from the confirmed landmark coordinates using the algorithms described below. Unless noted, measurements are scale-invariant (ratios) or expressed in degrees.

### Contour Geometry Functions

**Arc length**: The total path length of a part contour was computed as the sum of Euclidean distances between consecutive landmark points:
$$L_{\text{arc}} = \sum_{i=1}^{n-1} \|p_{i+1} - p_i\|$$

**Chord length**: The straight-line distance between the first and last points of a part contour:
$$L_{\text{chord}} = \|p_n - p_1\|$$

**Sinuosity**: The ratio of arc length to chord length, capturing the overall undulation of a part outline:
$$S = L_{\text{arc}} / L_{\text{chord}}$$
Because the chord is the shortest path between two points, *S* ≥ 1.0 by definition. A value of 1.0 indicates a perfectly straight outline; higher values indicate increasing waviness.

**Signed sinuosity (A06)**: An extension of sinuosity that also encodes the direction in which a contour bows, used specifically for the superficial root profile character. The superficial root boundary is a closed loop traversing both the inner and outer edges of the root. The profile is extracted as follows:

1. The fork point is taken as the first landmark in the SuperficialRoot boundary sequence.
2. The tip is identified as the point of maximum Euclidean distance from the fork.
3. The loop is split at the tip into two half-sequences (side A: fork→tip; side B: tip→end).
4. Each half is assigned as *inner* or *outer* based on mean distance to the Point centroid: the half closer to the Point is the inner edge (it faces the anchor shaft and Point, i.e., the medial surface of the root).
5. The inner-edge profile — oriented fork-to-tip — is used to compute arc length and chord length.

The sign is determined by the cross product of the chord vector with the displacement from the chord midpoint to the arc midpoint:
$$\text{signed sinuosity} = S \times \text{sign}$$
where sign = +1 if the arc midpoint lies on the same side of the chord as the Point centroid (bowing *inward*, toward the Point) and sign = −1 if it lies on the opposite side (bowing *outward*, away from the Point). Because *S* ≥ 1.0, the absolute value of the reported statistic is always ≥ 1.0; values close to ±1 indicate a nearly straight profile, and larger absolute values indicate greater curvature. Specimens whose raw sinuosity exceeds 2.0 (indicative of incomplete or erroneous landmark tracing) are excluded from character assignment and coded as missing ("?").

**Local curvature**: At each interior landmark point *i*, the signed Menger curvature was computed from three consecutive points using the formula:
$$\kappa_i = \frac{4 \cdot A_{i-1,i,i+1}}{d_{i-1,i} \cdot d_{i,i+1} \cdot d_{i-1,i+1}}$$
where *A* is the signed area of the triangle formed by three consecutive points and *d* values are pairwise Euclidean distances. The sign indicates the direction of curvature (positive = left-turning, negative = right-turning). **Mean curvature** is the mean of absolute local curvature values; **maximum curvature** is the peak absolute value.

**Relative vertical position**: The normalized vertical displacement between the distal tips of two parts, measured as:
$$\delta = \frac{Y_{\text{tip,A}} - Y_{\text{tip,B}}}{Y_{\text{max}} - Y_{\text{min}}}$$
where *Y*_max and *Y*_min span the full vertical extent of the structure outline. Positive values indicate that part A tip lies below (more distal in image coordinates) part B tip.

### Direction and Angle Functions

**Direction vector**: The gross trajectory of a part at its proximal or distal end was estimated as the mean of unit difference vectors over the first or last five landmark points, normalized to unit length.

**Angle between vectors**: The angle between any two direction vectors was computed as:
$$\theta = \arccos\!\left(\frac{\vec{v}_1 \cdot \vec{v}_2}{\|\vec{v}_1\|\,\|\vec{v}_2\|}\right) \in [0°, 180°]$$

**Junction angle**: The angle at the junction between two consecutive parts, computed from the direction vector at the end of the proximal part and the direction vector at the start of the distal part.

### Central-Axis Computation

For characters requiring a midline direction (fork angle, point curvature), a **central axis** was computed from each part's landmark contour as follows. If a part has two edges (e.g., the Shaft, bounded by inner and outer contour sequences), the two sequences were resampled to equal point density along arc length, and the central axis was taken as the element-wise mean. If a part is bounded by a single edge (e.g., the SuperficialRoot), the tip (point of maximum distance from the part's base junction) was identified, the contour was split into two sequences running from base to tip, and their mean was computed. In both cases, the resulting axis is an ordered sequence of midpoints progressing from the junction (fork) end to the distal tip.

**Best-fit midline direction** of a set of axis points was estimated using principal component analysis (PCA via singular value decomposition); the first principal component provides the direction of maximum variance. For fewer than three points, the endpoint-to-endpoint vector is used. The PCA sign was resolved by requiring the vector to point from the junction toward the distal end (or toward the root, as required by the character).

### Fork Angle (A09 and A05)

For characters measuring the divergence of two parts at their common fork (e.g., A09, Shaft–superficial root angle), the following procedure was applied:

1. The central axis of each part was computed and oriented so that index 0 corresponds to the fork-end (minimal distance from the junction midpoint).
2. Only the **proximal half** of each axis (nearest the fork) was used, to capture the departure angle near the junction while ignoring distal curvature.
3. The best-fit direction vector was extracted from each proximal half using PCA.
4. The **deviation angle** was computed as 180° minus the angle between the two direction vectors, yielding 0° for parts that continue in a straight line from one another and larger values for increasing divergence.

### Point Curvature (A02)

The curvature of the anchor point relative to the shaft was quantified as the **acute exterior angle** at the point–shaft junction, following the procedure below:

1. **Point midline**: A direct line was drawn from the midpoint of the most basal cross-section of the point (at the shaft junction) to the tip of the point. This line serves as the point direction vector (*v*₁).
2. **Shaft midline**: The central axis of the shaft was computed and oriented so that index 0 is at the junction. Only the **middle third** of the shaft axis (indices *n*/3 to 2*n*/3, where *n* is the total number of axis points) was used to estimate the shaft direction vector (*v*₂) via PCA, avoiding distortion from the curving extremities near the root and junction. The PCA sign was resolved so that *v*₂ points from the junction toward the root.
3. **Bend angle**: The full bend angle was computed as *β* = 180° − *∠*(*v*₁, *v*₂), representing the angular deflection when traveling from the shaft through the junction into the point (0° = straight continuation; 90° = right-angle bend; >90° = recurved past perpendicular).
4. **Acute exterior angle**: The reported value is the acute angle at the external junction: *α* = min(*β*, 180° − *β*), which is always in [0°, 90°] and equals *β* for specimens where the point does not recurve past a right angle.

---

## Character Discretization

Continuous geometric measurements were assigned to ordered discrete states using threshold intervals applied to raw values. For a character with *k* states defined by threshold boundaries *t*₁ < *t*₂ < … < *t*_{k−1}:

- State 0: raw value < *t*₁
- State *j* (1 ≤ *j* < *k*−1): *t*_j ≤ raw value < *t*_{*j*+1}
- State *k*−1: raw value ≥ *t*_{k−1}

A **confidence score** was computed for each assigned state as the normalized distance from the nearest threshold boundary: values near a boundary receive low confidence; values near the centre of an interval receive high confidence. This score is used to flag borderline assignments for manual review but does not affect the state code itself.

Characters with inapplicability dependencies (e.g., C11 Heel profile is inapplicable when C10 Heel conspicuousness = 0) were coded as "−" rather than scored.

### Threshold Optimization

State boundary thresholds were optimized using the **Fisher-Jenks natural breaks** algorithm, a one-dimensional dynamic-programming method that partitions *n* observations into *k* ordered classes by minimizing the within-class sum of squared deviations (analogous to one-dimensional *k*-means with guaranteed global optimality). For each character, solutions for *k* = 2, 3, and 4 classes were computed and evaluated using the **Goodness of Variance Fit** (GVF):

$$\text{GVF} = \frac{\sigma^2_{\text{total}} - \text{WCSS}_k}{\sigma^2_{\text{total}}}$$

where *σ*²_total is the total variance of the raw values and WCSS_k is the minimized within-class sum of squares for *k* classes. GVF ranges from 0 (no improvement over a single class) to 1 (perfect separation). The suggested boundaries were presented to the taxonomist graphically (overlaid on a histogram of observed values) for expert validation and biologically motivated adjustment before being committed to the character matrix.

---

## Character Definitions

### Hook Characters (C01–C12)

| Code | Name | Formula | States |
|------|------|---------|--------|
| C01 | Point length | arc(Point) / arc(Shaft) | 0: <0.49; 1: 0.49–0.84; 2: 0.84–1.08; 3: ≥1.08 |
| C02 | Point curvature | junction angle Shaft→Point | 0: ≥140° (evenly curved); 1: 80–140° (~90°); 2: <80° (recurved) |
| C03 | Point waviness | sinuosity(Point) | 0: <18; 1: 18–35; 2: >35 |
| C04 | Point vs Toe level | vertical displacement, normalized | 0: <−0.85; 1: −0.85–−0.70; 2: −0.70–−0.40; 3: ≥−0.40 |
| C05 | Shaft curvature | mean absolute local curvature | 0: <11; 1: 11–16; 2: >16 |
| C06 | Shaft angle | direction angle Shaft, Base | 0: <60°; 1: 60–100°; 2: 100–140°; 3: >140° |
| C07 | Shelf profile | sinuosity(Shelf) | 0: <1.05; 1: 1.05–1.15; 2: >1.15 |
| C08 | Base profile | sinuosity(Base) | 0: <1.05; 1: 1.05–1.15; 2: >1.15 |
| C09 | Base–Heel ratio | arc(Base) / arc(Heel) | 0: ≥1.5; 1: 0.67–1.5; 2: <0.67 |
| C10 | Heel conspicuousness | arc(Heel) / arc(total) | 0: <0.08 (reduced); 1: 0.08–0.18 (moderate); 2: >0.18 (prominent) |
| C11 | Heel profile | sinuosity(Heel) | 0: <1.15; 1: 1.15–1.35; 2: >1.35 [inapplicable if C10=0] |
| C12 | Heel–Shaft transition | junction angle Heel→Shaft | 0: <15° (abrupt); 1: 15–40° (moderate); 2: >40° (gradual) |

### Anchor Characters (A01–A09)

| Code | Name | Formula | States |
|------|------|---------|--------|
| A01 | Point length | arc(Point) / arc(Shaft) | 0: <0.45; 1: 0.45–0.55; 2: 0.55–1.0; 3: ≥1.0 |
| A02 | Point curvature | acute exterior angle (see above) | 0: <30°; 1: 30–60°; 2: >60° |
| A03 | Superficial root length | arc(SuperficialRoot) / arc(Shaft) | 0: <0.8; 1: 0.8–1.2; 2: >1.2 |
| A04 | Deep root form | arc(DeepRoot) / arc(Shaft) | 0: <0.3 (knob); 1: ≥0.3 (distinct root) |
| A05 | Root divergence angle | fork angle SuperficialRoot, DeepRoot | 0: <70°; 1: 70–120°; 2: >120° |
| A06 | Superficial root profile | signed sinuosity of inner edge (see above); sign = +1 if arc midpoint bows toward Point, −1 if away | 0: <−0.509 (curved outward); 1: −0.509 to 0.5125 (straight†); 2: ≥0.5125 (curved inward) |
| A07 | Deep root profile | sinuosity(DeepRoot) | 0: <1.08; 1: ≥1.08 [inapplicable if A04=0] |
| A08 | Sclerite at superficial root tip | manual | 0: absent; 1: present |
| A09 | Shaft–superficial root angle | fork angle Shaft, SuperficialRoot | 0: <25° (nearly aligned); 1: 25–60° (moderately divergent); 2: >60° (widely divergent) |

† The "straight" state (code 1) is defined by the threshold interval produced by Fisher-Jenks optimization on the observed data. Because signed sinuosity has |value| ≥ 1.0 by definition, no specimen can occupy a state whose interval lies entirely within (−1, +1). This state therefore remains unoccupied in the present dataset and is retained in the matrix only to preserve the three-state coding scheme for potential future specimens with near-zero curvature (e.g., if data from additional taxa with nearly straight roots are added).

### Bar and MCO Characters (B01–B06, D01–D03, M01–M06)

All bar (superficial and deep) and MCO characters were scored manually based on morphological criteria described in the character state definitions within the platform. Manual characters are not subject to threshold-based discretization; state assignment is made directly by the annotator.

---

## Character Matrix and Export

The discrete state codes for all specimens and characters were assembled into a standard morphological character matrix. The matrix was exported in NEXUS format for phylogenetic analysis. Inapplicable characters were coded as "−" and missing data as "?". The platform also supports direct export to TNT and raw CSV formats.

### Cross-Project Import

Character states from another GyroMorpho v2 project may be imported into the matrix using the **Import States** tool (matrix toolbar). Matching is performed by character code (exact, case-insensitive) and species name (normalised epithet key). A dry-run preview reports how many states will be imported, how many are skipped because the source lacks a matching character or specimen, and how many are skipped because a value already exists (overridable with the Overwrite toggle).

Similarly, specimen data (images, landmark CSVs, boundary assignments) from another project can be bulk-imported from the Specimens page using the **Import from Project** tool, with the same epithet-key matching logic.

---

## MCO Illustration Reliability Index

The male copulatory organ (MCO) of Gyrodactylidae is small, weakly sclerotized (except for the spines and spinelets), and highly susceptible to orientation, flattening, and preservation artefacts. Because the MCO character scores depend almost entirely on published illustrations of variable quality, a formal, reproducible reliability index was computed to make the confidence of each species' MCO scoring explicit and to permit sensitivity analyses.

Reliability was assessed by blind expert scoring. Each participating scientist, logged into the platform, was presented with the MCO illustration of each species with the species identity withheld, and scored it against a set of ordinal criteria. The criteria are fully user-configurable per project — they may be added, edited, reweighted, or removed at any time, and the index is recomputed accordingly — but the default scheme follows four criteria adapted from the protocol for gyrodactylid MCO illustrations: **source fidelity (S)** (original photomicrograph/DIC image = 2, line drawing traced from a specimen = 1, redrawn/schematic = 0), **orientation/aspect (O)** (clear en‑face aspect with visible symmetry axis = 2, oblique but interpretable = 1, lateral/folded/ambiguous = 0), **structural completeness (C)** (bulb, principal spine, and full spinelet crown all visible = 2, one region obscured = 1, everted/collapsed/damaged = 0), and **optical/rendering resolution (R)** (individual spinelets and their bases resolvable = 2, spinelets visible but bases uncertain = 1, spinelet zone blurred = 0). Because the most information‑rich but most error‑prone MCO characters (spinelet counts and arrangement) depend directly on orientation and resolution, criteria O and R were weighted 1.5 and S and C weighted 1.0.

For each scoring, a composite reliability index normalised to the unit interval was computed as

CRI = Σᵢ (wᵢ · scoreᵢ) / Σᵢ (wᵢ · maxᵢ),

where *wᵢ*, *scoreᵢ*, and *maxᵢ* are the weight, assigned score, and maximum score of criterion *i*, taken over the criteria actually scored. The per‑species index is the mean CRI across all scientists who scored that species, and is interpreted in three confidence bands: **high** (CRI ≥ 0.80; suitable for diagnosis and phylogeny), **moderate** (0.50–0.79; usable, with orientation/resolution‑dependent characters flagged), and **low** (< 0.50; count and arrangement characters treated as uncertain). Inter‑observer agreement was quantified as the mean pairwise linearly‑weighted Cohen's κ computed over the ordinal confidence band of each shared species, and the per‑species dispersion (standard deviation of CRI across scientists) was retained as an additional flag. An automatic concordance check (K) flags any species whose raw MCO spinelet count and its binned count character resolve inconsistently, triggering re‑examination.

The per‑species reliability index is stored as a species‑level attribute and displayed alongside the taxa on the Specimens page and in the character matrix, and is included in the CSV and JSON matrix exports (and as a per‑taxon comment block in the NEXUS export). This allows downstream analyses to be repeated on nested subsets of decreasing stringency (all species vs. CRI ≥ 0.5 vs. CRI ≥ 0.8) as a sensitivity test. Consistent with its purpose, the index is treated primarily as a transparency and reproducibility instrument rather than as an absolute measure of morphological accuracy.

---

## Phylogenetic Analysis

Molecular phylogenies were estimated from DNA sequence data downloaded from GenBank or supplied by the user. The pipeline supports a single marker (18S rRNA, ITS, COI, or a user-defined query), a concatenated 18S + ITS (ITS1–5.8S–ITS2) analysis, or a **multi-fragment** analysis over any combination of five markers (18S, ITS, 28S, COI, COII), and proceeds through the following automated steps, with mandatory checkpoints for expert review before any sequence is aligned and before any tree is treated as final.

In the multi-fragment mode, each selected marker is searched across the whole target taxon on GenBank and the results are assembled into a **species × fragment matrix** presented side by side: for every species, the available accessions for each fragment are listed, species present on the project's Specimens page and additional species found only in GenBank are distinguished, and the user chooses, per cell, which accession (if any) to use. Species can be renamed inline at any point at which their sequences are shown (in the matrix and on the neighbour-joining review screen), the retained sequences for each fragment can be downloaded separately, and the user decides explicitly which species and which fragments enter the concatenation. Each fragment is then aligned and trimmed independently (below), the best-fit substitution model is selected per fragment, the chosen fragments are concatenated (gap-filling any taxon missing a fragment), and the tree is inferred either by model-corrected neighbour-joining or by partitioned maximum likelihood (below).

### 1. Sequence Retrieval

For each marker, ingroup sequences were retrieved from NCBI GenBank through the Entrez E-utilities (an NCBI API key was supplied, raising the request ceiling to 10 s⁻¹) using a recovery-first strategy whose explicit goal is that every species selected on the project's Specimens page for which GenBank holds *any* record of the marker is represented in the final alignment:

1. **Relaxed bulk query**: A single taxon-level query (`"<Taxon>"[Organism] AND (<marker query>)`) retrieves all candidate ingroup records. The marker query is applied in *relaxed* form — its exclusion clause (`NOT (...)`, e.g. `NOT (internal transcribed spacer)` for 18S or `NOT (18S OR 28S)` for ITS) is stripped before searching. The strict exclusion exists only to keep the two marker searches from overlapping during concatenation, but it also discards every species whose only deposit is a combined ribosomal cassette (18S + ITS1 + 5.8S + ITS2 + 28S in one accession) — a large fraction of the family. Using the relaxed query for the ingroup search recovers these taxa; per-marker alignment and trimming (below) subsequently retain only the region relevant to each partition. Records are filtered by a minimum length, deduplicated by species and by exact sequence (retaining, per species, the longest sequence not exceeding a configurable length-outlier cutoff, but never dropping a species outright), and restricted to the Specimens-page selection. Outgroup searches retain the strict (exclusion-bearing) query.
2. **Recorded-accession pinning**: If a specimen on the Specimens page already lists a GenBank accession for the marker, that exact accession is fetched directly and substituted for whichever record the bulk search happened to pick, guaranteeing the curated sequence — not an arbitrary conspecific — is the one used.
3. **No-floor per-species rescue**: Any selected species still unrepresented is searched individually with its own organism-name query (strict, then, if empty, exclusion-stripped) and **no minimum-length filter**, so that even a short partial record is recovered rather than lost to the bulk length cutoff. Sequences recovered here are included automatically. This single per-species pass is the mechanism that enforces the coverage guarantee; only species for which GenBank holds no marker record at all remain unresolved.
4. **Human-reviewed flexible search**: For species that still have no record of the target marker, a broad, marker-unrestricted organism-name search is run as a last resort. Candidates from this tier are never added automatically — they are presented on the Review Sequences screen (species name, candidate accession, length, description) for an explicit accept/reject decision, since a marker-unrestricted hit may be an unrelated gene. Genuinely absent species are reported by name so that gaps in taxon sampling are always visible rather than silently reducing the ingroup.

Every ingroup accession detected in GenBank is written back to the project's Specimens page as a marker-tagged sequence record (creating the specimen entry if none existed), so that the molecular sampling accumulated across runs is captured on the Specimens page rather than only inside a transient job. The user's resolutions of ambiguous cases are likewise remembered per project: the accession accepted (or rejected) for a given species and fragment, and any species relabelling, are stored and re-applied automatically in later jobs — the corresponding fragment-matrix cell is pre-selected with the accepted accession, rejected accessions are suppressed, and the preferred label is pre-filled, so a curator resolves each awkward species once rather than on every re-analysis while retaining the ability to override any remembered choice.

Outgroup sequences are retrieved separately per configured outgroup family/genus (each defined as `Family | mode | n`, where *mode* selects either the *n* longest sequences per genus or the *n* longest across the family) and are not subject to the Specimens-page restriction.

### 2. Sequence Quality and Direction Check

Before alignment, every retrieved sequence (from the bulk search, either retry tier, or a user-accepted flexible-search candidate) is passed through an automated quality and orientation check, applied independently to each marker:

- **Orientation**: Each sequence is compared, via shared *k*-mer content (*k* = 8), to the longest sequence in its batch. A sequence whose reverse complement shares more *k*-mers with the reference than its as-deposited orientation is reverse-complemented in place. This runs prior to and independently of MAFFT's own `--adjustdirection` reorientation (see below), so a reversed sequence is corrected consistently regardless of whether alignment subsequently runs locally or on the Galaxy platform (which has no equivalent built-in option).
- **Quality flagging**: Sequences with more than 5% ambiguous IUPAC bases (N and related codes) are flagged for review; they are retained in the alignment but reported to the user rather than silently included.

Sequences reverse-complemented or flagged at this stage, together with any sequence MAFFT itself subsequently reorients, are reported to the user on the sequence review screens.

### 3. Alignment and Trimming

Multiple sequence alignment was performed using MAFFT (v7+, `--auto --adjustdirection`) where a local binary is available, or via the equivalent Galaxy MAFFT tool otherwise (with the pre-alignment orientation check above substituting for `--adjustdirection` on that path). Ambiguously aligned columns were then removed with trimAl. The trimming stringency is user-selectable per analysis: *standard* (`-gappyout`), *gentle* (`-automated1`), or *none* (retain the full untrimmed alignment).

Because column-based trimming can, in rare cases, leave an individual sequence entirely gap-only in the surviving columns — silently dropping it from the trimmed output with no error — the pipeline enforces a strict no-loss guarantee at every trimming step (including each marker of a concatenated analysis): the trimmed sequence count must equal the aligned count. If the selected trimAl mode would drop any sequence, trimming falls back to the gentler `-automated1` heuristic and, failing that, to the unmodified alignment, so a sequence is never removed by the trimming step itself. Analogously, if the alignment step (MAFFT, local or via Galaxy) returns fewer sequences than were submitted, the job fails explicitly with a diagnostic message rather than continuing silently with a truncated dataset. Sequence removal is therefore never automatic beyond the explicit fetch-stage length/duplicate filters: the final decision to keep or drop any individual sequence rests with the user at the neighbour-joining review stage (below), where every taxon in the final alignment is listed with its real (non-gap) base count, its partition membership in concatenated analyses, and an explicit flag for any taxon whose alignment row is entirely gaps (present in the alignment but contributing no sequence data). After trimming, the final sequence set is compared against the full list of species selected on the Specimens page, and any specimen still absent from the final alignment is reported by name.

For the concatenated 18S + ITS mode, each marker is fetched, quality/direction-checked, aligned, and trimmed independently; alignments are then concatenated by species (gap-filling any taxon missing one marker), and the number of taxa represented by both markers, by 18S only, and by ITS only is reported. A variant *18S-guided* mode is also available: all unique 18S sequences (one per species) are retrieved first, and the ITS search then targets the **union** of the species recovered by 18S and the species selected on the Specimens page. This anchors the concatenation on the more completely sampled 18S partition while still admitting any specimen that has ITS but no 18S (its 18S columns are gap-filled), so no listed specimen is dropped merely for lacking the anchor marker.

### 4. Tree Inference

The best-fit nucleotide substitution model is selected with ModelTest-NG (by BIC) — for a single-marker or concatenated analysis on the full alignment, and independently per fragment (per partition) in the multi-fragment mode, where local binaries are available.

A rapid neighbour-joining tree (via BioPython, computed from the trimmed alignment) is generated automatically as a **preview only**, allowing a quick sanity check of the sequence set (rooting, obvious misplacements, remaining direction issues) before committing to a full analysis. In the multi-fragment mode this preview uses **model-corrected distances**: pairwise distances are computed per partition with a Jukes-Cantor or Kimura-2-parameter correction chosen from that partition's selected model, then combined across partitions weighted by the number of comparable (non-gap) sites. Because the NJ tree carries no bootstrap support, it cannot be imported into the project directly.

The final phylogeny was estimated by maximum likelihood with RAxML-NG (v2.0+), executed on the `usegalaxy.eu` Galaxy platform through its REST API: the trimmed alignment (and, for partitioned analyses, a partition file) is uploaded to a dedicated Galaxy history, the analysis is launched, and the job is polled to completion. RAxML-NG was run in all-in-one mode (`--all`: an adaptive ML tree search followed by non-parametric bootstrapping with autoMRE bootstopping), with a fixed random seed for reproducibility and a user-specified maximum number of bootstrap replicates (default 1000). The Felsenstein bootstrap proportion (`bs_metric = fbp`) was requested explicitly, so that support values are computed and written onto the best-scoring ML tree as node labels; without this specification the tool defaults to a rapid-bootstrap metric whose output tree is not produced under the same name. For a multi-fragment or concatenated analysis, a RAxML-NG partition file (one `<model>, <partition> = <start>-<end>` line per fragment, the model taken from that fragment's ModelTest-NG selection) is submitted so that each partition is estimated under its own substitution model with proportionally linked ("scaled") branch lengths; a single-partition analysis uses the alignment's selected model string (default GTR+G). On completion the pipeline downloads every output dataset and selects the tree carrying bootstrap support as node labels (the RAxML-NG `.raxml.support` / bipartitions tree) in preference to any support-free best tree; a run that returns only a support-free tree (e.g. due to a Galaxy configuration issue) is flagged explicitly rather than silently accepted, since a tree without support defeats the purpose of the bootstrap analysis.

### 5. Tree Import and Rooting

The bootstrap-supported RAxML-NG tree is imported into the matrix view, where the user may specify one or more outgroup genera for re-rooting. Re-rooting is performed server-side by matching tip labels to the nominated outgroup genera and rooting at their most recent common ancestor — using `ape` (via Rscript) where an R installation is available, and falling back to an equivalent BioPython `root_with_outgroup` implementation otherwise. The updated tree — with bootstrap support values preserved — is saved to `Project.tree_newick`. Tip labels in the imported tree are automatically parsed and any new species names are added to the specimen list. Because only the bootstrap-supported tree can be imported, every tree used downstream in the pipeline (matrix view, taxonomic description context, exports) carries bootstrap support.

Sequences may be revised after an initial run — replaced with an alternative GenBank accession, reverse-complemented, removed, or added — via the Revise & Resubmit interface, which reruns quality/direction checking, alignment, trimming, and NJ preview on the updated sequence set before resubmission to Galaxy.

Phylogenetic job results are written to:
```
AI_morpho2/phylogeny/Results/job_<YYYYMMDD_HHMMSS>/
```
Each job folder is self-contained and can be deleted without affecting the database.

---

## Taxonomic Descriptions

Auto-generated taxonomic descriptions are produced from the character matrix for each specimen. The description enumerates confirmed character states for each structure type (hooks, anchors, bars, MCO) in standard morphological prose format. Descriptions are rendered in the browser and can be exported as formatted DOCX files (Microsoft Word) that include an illustration gallery (one image per structure type) followed by the description text. Both the web view and the DOCX strip the species name from the top of the description body since it is already displayed as the section heading.

---

## AI-Assisted Character Evaluation

An AI-advisory module was implemented to assist with character design and evaluation. The module transmits the full project context (specimen counts, character definitions, value statistics, and state distributions) to a large language model (Anthropic Claude Sonnet 4.6 or user-specified alternative) and receives structured suggestions for: (i) new characters that could be measured from the existing landmark data; (ii) improved state boundary definitions for existing characters; (iii) redundant or uninformative characters warranting removal; and (iv) general observations on the morphometric scheme. Users may also pose free-form scientific questions about their dataset; the advisor responds with the full project context available. AI suggestions are presented for expert review and are not applied automatically.

---

*End of Methods section — update after changes to character definitions, measurement algorithms, threshold values, or pipeline architecture.*
