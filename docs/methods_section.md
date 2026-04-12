# Materials and Methods — Geometric Morphometrics of Gyrodactylid Sclerotized Structures

*This document is auto-maintained. Update after any major change to character definitions, measurement algorithms, or pipeline architecture.*

*Last revised: 2026-04-12*

---

## Pipeline Overview

Morphometric data were acquired and processed using **GyroMorpho v2**, a custom web-based platform developed for the collaborative morphometric analysis of gyrodactylid sclerotized structures (hooks, anchors, bars, and MCO). The system integrates image-based landmark annotation, automated geometric measurement, discrete character coding, phylogenetic matrix export, and AI-assisted character evaluation into a single reproducible workflow.

---

## Structures Examined

For each specimen, up to five categories of sclerotized structures were digitized:

- **Hooks** (*Hamuli*): The marginal hooks were annotated with up to 12 part boundaries (Point, Shaft, Base, Shelf, Heel, Toe) defining the major morphological regions.
- **Anchors**: Haptor anchors were annotated with up to nine regions (Point, Shaft, SuperficialRoot, DeepRoot).
- **Superficial bars**: The superficial (dorsal) haptor bar was annotated as BarProper with optional shield structures.
- **Deep bars**: The deep (ventral) haptor bar, annotated as a single region.
- **Male copulatory organ (MCO)**: The sclerotized MCO was annotated with bulb and armature regions.

---

## Image Acquisition and Landmark Placement

Microscopy images of individual sclerotized structures were uploaded to GyroMorpho v2. Landmark contours were placed manually using the integrated image editor, which provides pan/zoom, brightness/contrast adjustment, and semi-automated contour tracing (deep-learning-assisted boundary suggestion using a U-Net architecture trained on gyrodactylid hook outlines). Landmarks were placed as ordered sequences of 2D coordinate points tracing the outer contour of each structure. Part boundaries were then defined by marking the index ranges of landmark points corresponding to each morphological region (e.g., Point, Shaft, Heel). All landmark placements and boundary assignments were confirmed by the annotator before characters were computed.

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
A value of 1.0 indicates a perfectly straight outline; higher values indicate increasing waviness.

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
| A06 | Superficial root profile | signed sinuosity(SuperficialRoot) | 0: −1.03 to 1.03 (straight); 1: <−1.03 (inward); 2: >1.03 (outward) |
| A07 | Deep root profile | sinuosity(DeepRoot) | 0: <1.08; 1: ≥1.08 [inapplicable if A04=0] |
| A08 | Sclerite at superficial root tip | manual | 0: absent; 1: present |
| A09 | Shaft–superficial root angle | fork angle Shaft, SuperficialRoot | 0: <25° (nearly aligned); 1: 25–60° (moderately divergent); 2: >60° (widely divergent) |

### Bar and MCO Characters (B01–B06, D01–D03, M01–M06)

All bar (superficial and deep) and MCO characters were scored manually based on morphological criteria described in the character state definitions within the platform. Manual characters are not subject to threshold-based discretization; state assignment is made directly by the annotator.

---

## Character Matrix and Export

The discrete state codes for all specimens and characters were assembled into a standard morphological character matrix. The matrix was exported in NEXUS format for phylogenetic analysis. Inapplicable characters were coded as "−" and missing data as "?". The platform also supports direct export to TNT and raw CSV formats.

---

## AI-Assisted Character Evaluation

An AI-advisory module was implemented to assist with character design and evaluation. The module transmits the full project context (specimen counts, character definitions, value statistics, and state distributions) to a large language model (Anthropic Claude Opus 4.6 or user-specified alternative) and receives structured suggestions for: (i) new characters that could be measured from the existing landmark data; (ii) improved state boundary definitions for existing characters; (iii) redundant or uninformative characters warranting removal; and (iv) general observations on the morphometric scheme. Users may also pose free-form scientific questions about their dataset; the advisor responds with the full project context available. AI suggestions are presented for expert review and are not applied automatically.

---

*End of Methods section — update after changes to character definitions, measurement algorithms, threshold values, or pipeline architecture.*
