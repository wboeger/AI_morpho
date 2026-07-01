# GyroMorpho v2: Introduction and Materials and Methods (manuscript draft)

*American English. Drafted 2026-06-30 from the application source and `docs/methods_section.md`. Adjust author wording, citations, and dataset-specific numbers before submission.*

---

## 1. Introduction

The Gyrodactylidae (Platyhelminthes: Monogenoidea) are among the most diverse and taxonomically challenging groups of metazoan parasites. Species delimitation in the family rests almost entirely on the morphology of the sclerotized structures of the haptor—the marginal hooks (hamuli), the anchors and their associated superficial (dorsal) and deep (ventral) bars—and, for many genera, on the sclerotized male copulatory organ (MCO). These structures are small (typically tens of micrometers), geometrically intricate, and frequently differ between closely related species only in subtle proportions, curvatures, and angles. Consequently, the comparative description of gyrodactylid hard parts has historically depended on a small set of linear measurements (e.g., total hook length, anchor point length, shaft length) supplemented by qualitative, observer-dependent assessments of shape.

This traditional approach has two well-recognized limitations. First, linear measurements capture only a fraction of the morphological information present in a structure's outline and are sensitive to orientation, magnification, and the operator's choice of measurement endpoints, which compromises reproducibility across laboratories. Second, qualitative shape characters ("recurved point," "prominent heel," "sinuous root") are difficult to standardize, are rarely accompanied by explicit decision criteria, and are therefore problematic both for repeatable diagnosis and for coding into the discrete character matrices required by phylogenetic inference. Geometric morphometrics—the statistical analysis of landmark configurations after the removal of position, scale, and rotation—offers a rigorous alternative that preserves the full geometry of a structure and yields scale-invariant, reproducible shape descriptors. Despite its maturity, geometric morphometrics has been adopted only sporadically in gyrodactylid systematics, in part because no integrated tool exists to carry a worker from raw microscopy images through landmark acquisition, shape-based character coding, taxonomic description, and matrix export to molecular phylogenetic analysis within a single reproducible workflow.

Here we describe **GyroMorpho v2**, a web-based platform that closes this gap. GyroMorpho integrates (i) landmark and pseudolandmark acquisition from microscopy images of gyrodactylid sclerotized structures; (ii) automated computation of geometric shape characters (ratios, angles, curvatures, and sinuosities) from Procrustes-aligned contours; (iii) data-driven discretization of these continuous measurements into ordered character states, with explicit, recoverable thresholds; (iv) automated generation of standardized morphological descriptions and comparative diagnoses; (v) export of the resulting character matrix in standard phylogenetic formats; and (vi) an end-to-end molecular pipeline that retrieves sequences, aligns and trims them, infers a maximum-likelihood tree, and integrates the phylogeny with the morphological matrix. The platform additionally incorporates an optional large-language-model advisory module to assist with character design and evaluation. By making every measurement algorithm and every state boundary explicit and reproducible, GyroMorpho is intended to improve the objectivity, repeatability, and transparency of morphology-based systematics in the Gyrodactylidae and, more broadly, in monogenoid taxa whose diagnostic characters reside in sclerotized hard parts.

---

## 2. Materials and Methods

### 2.1 Software architecture and availability

GyroMorpho v2 is a client–server web application implemented in Python (≥3.10) using the Flask framework, with a SQLAlchemy object–relational layer over an SQLite database (Write-Ahead Logging enabled for concurrent access). Numerical computation uses NumPy; image handling uses Pillow and OpenCV. The application runs locally on a personal computer and is accessed through a standard web browser; no internet connection is required for the morphometric and character-coding steps. All project data—microscopy images, landmark coordinates, anatomical boundary assignments, character-state codes, and phylogenetic trees—are stored within a single project directory, with structured data held in the SQLite database (`data/db.sqlite`) and image and sequence files held on disk under `data/uploads/`. The system supports multiple users with role-based access (administrator and annotator) and maintains an activity log in which every character-state override records the responsible user, the time, and the justification.

### 2.2 Structures and anatomical regions

For each specimen, up to five categories of sclerotized structure were digitized, each subdivided into named anatomical regions ("parts") used as the basis for shape characters:

- **Marginal hooks (hamuli):** up to six parts—Point, Shaft, Base, Shelf, Heel, and Toe.
- **Anchors:** up to four parts—Point, Shaft, Superficial Root, and Deep Root.
- **Superficial (dorsal) bar:** the Bar Proper, with optional Shield, Shield Distal End, and Anterolateral Processes.
- **Deep (ventral) bar:** a single Bar Proper region.
- **Male copulatory organ (MCO):** Bulb, Principal Spine, and Spinelets.

### 2.3 Image acquisition and landmark placement

Microscopy images of individual sclerotized structures were imported into GyroMorpho, either individually per specimen or in batch from a local folder, with filenames matched to specimens by a normalized species-epithet key. Each structure outline was captured as an ordered sequence of two-dimensional landmark points tracing the outer contour.

Landmarks were acquired in one of two ways. (i) Using the platform's integrated browser-based image editor, which provides pan and zoom and semi-automated contour tracing; landmarks were placed manually as an ordered point sequence. (ii) Using two purpose-built ImageJ/Fiji macros (provided with the software) for hooks and anchors. The macro workflow comprises image review and quality control, cropping and optional contrast enhancement (Gaussian blur, CLAHE, unsharp masking, 3× upscaling), orientation standardization, optional threshold-based binarization, semi-automated outline extraction with the wand tool, contour smoothing by a configurable moving average, placement of three homologous anatomical landmarks, and equidistant resampling to a fixed number of pseudolandmarks (100 per structure) spaced by arc length along the contour. The three anatomical landmarks are: for hooks, L1 = point tip, L2 = toe tip, L3 = point–shaft junction (inner face); for anchors, L1 = point tip, L2 = external tip of the superficial root, L3 = distal-most base of the deep root. Each macro run produces one comma-separated coordinate file per specimen (X, Y; 100 rows) together with quality-control and rejection logs; these may be imported individually or as a ZIP archive, with automatic resampling to the target landmark count.

After landmarks were imported, the boundaries of the anatomical parts were defined within the browser by marking the index ranges of the landmark points belonging to each region, using click, range, or lasso selection; a "copy from similar" function transfers boundary assignments from the most morphologically similar previously confirmed specimen. All landmark placements and boundary assignments were verified by the annotator and explicitly confirmed before any character was computed.

### 2.4 Shape alignment

Prior to measurement, all specimens of a given structure type were superimposed by Generalized Procrustes Analysis (GPA): each configuration was translated to a common centroid, scaled to unit centroid size (removing isometric size), and iteratively rotated to minimize the summed squared distances to the evolving mean shape. Where a midline direction was required (e.g., for fork angles or point curvature), a central axis was computed from the part contour: for parts bounded by two edges (e.g., the shaft), the two edge sequences were resampled to equal arc-length density and averaged element-wise; for parts bounded by a single closed edge (e.g., the superficial root), the contour was split at the tip (the point of maximum distance from the part base) and the two resulting half-sequences were averaged. Best-fit axis directions were estimated by principal component analysis (the first principal component, via singular value decomposition), with the sign resolved by an anatomical convention (e.g., pointing from the junction toward the distal tip).

### 2.5 Geometric character measurement

Geometric characters were computed automatically from the confirmed, aligned landmark coordinates. Measurements are either dimensionless ratios or angles expressed in degrees, and are therefore scale-invariant. The principal contour descriptors are:

- **Arc length** of a part: the summed Euclidean distance between consecutive landmark points, *L*<sub>arc</sub> = Σ ‖*p*<sub>i+1</sub> − *p*<sub>i</sub>‖.
- **Chord length:** the straight-line distance between the first and last points of a part, *L*<sub>chord</sub> = ‖*p*<sub>n</sub> − *p*<sub>1</sub>‖.
- **Sinuosity:** the ratio *S* = *L*<sub>arc</sub> / *L*<sub>chord</sub> (≥ 1 by definition; 1 denotes a straight outline, higher values increasing undulation).
- **Signed sinuosity:** an extension used for the superficial-root profile, in which the magnitude *S* is multiplied by +1 if the contour bows toward the anchor point (medially) and −1 if it bows away, so that the sign encodes the direction of curvature.
- **Local (Menger) curvature** at an interior point *i*, computed from three consecutive points as κ<sub>i</sub> = 4*A* / (*d*<sub>i−1,i</sub> · *d*<sub>i,i+1</sub> · *d*<sub>i−1,i+1</sub>), where *A* is the signed triangle area and the *d* are pairwise distances; mean and maximum curvature are the mean and peak of |κ|.
- **Direction vectors and angles:** the local trajectory of a part end was estimated as the mean unit difference vector over its first or last five points; the angle between two vectors was obtained from their normalized dot product, θ = arccos(*v*<sub>1</sub>·*v*<sub>2</sub> / ‖*v*<sub>1</sub>‖‖*v*<sub>2</sub>‖) ∈ [0°, 180°]. Junction angles use the direction at the end of the proximal part and the start of the distal part; fork angles use the proximal halves of the two central axes; and the anchor point curvature is reported as the acute exterior angle at the point–shaft junction, computed from the point midline and the best-fit direction of the middle third of the shaft axis.
- **Relative vertical position:** the normalized vertical displacement between the distal tips of two parts, δ = (*Y*<sub>tip,A</sub> − *Y*<sub>tip,B</sub>) / (*Y*<sub>max</sub> − *Y*<sub>min</sub>).

The complete library comprises 36 default characters: 12 hook characters (C01–C12), 9 anchor characters (A01–A09, of which 8 are geometric and 1 is scored manually), 6 superficial-bar characters (B01–B06), 3 deep-bar characters (D01–D03), and 6 MCO characters (M01–M06). Bar and MCO characters are scored manually from explicit state definitions stored in the platform; users may also define, edit, reorder, and remove characters through an interactive character workshop.

### 2.6 Discretization of continuous characters

Continuous geometric measurements were converted to ordered discrete states using threshold intervals. For a character with *k* states defined by boundaries *t*<sub>1</sub> < … < *t*<sub>k−1</sub>, an observation was assigned to state 0 below *t*<sub>1</sub>, to the highest state at or above *t*<sub>k−1</sub>, and to the intermediate state whose interval contained it otherwise. For each assignment a confidence score was computed as the normalized distance from the nearest threshold, flagging borderline values for manual review without altering the state code. Characters with inapplicability dependencies (e.g., heel profile when the heel is absent) were coded as inapplicable ("−"), and unmeasurable or erroneous values were coded as missing ("?").

State boundaries were not set arbitrarily. For each character, candidate thresholds were derived from the empirical distribution of raw values using the Fisher–Jenks natural-breaks algorithm, a one-dimensional dynamic-programming partition that minimizes the within-class sum of squared deviations (the one-dimensional analogue of *k*-means, with a guaranteed global optimum). Solutions for *k* = 2, 3, and 4 classes were evaluated using the Goodness of Variance Fit, GVF = (σ²<sub>total</sub> − WCSS<sub>k</sub>) / σ²<sub>total</sub>, which ranges from 0 (no improvement over a single class) to 1 (perfect separation). Suggested boundaries were displayed graphically over a histogram of observed values and were validated—and, where biologically motivated, adjusted—by the taxonomist before being committed to the matrix. All thresholds are stored with the project and are therefore fully recoverable and reproducible.

### 2.7 Character matrix, descriptions, and export

Confirmed state codes for all specimens and characters were assembled into a standard morphological character matrix presented as an interactive grid (rows = species, columns = characters), with confidence coloring, cell-level override, and filtering by structure type. From this matrix the platform automatically generates standardized morphological descriptions in conventional taxonomic prose and comparative diagnoses for user-defined groups (e.g., genera or subfamilies); descriptions can be exported as formatted documents that pair an illustration gallery with the descriptive text. The matrix itself was exported in NEXUS, TNT, CSV, and JSON formats for downstream analysis, with inapplicable and missing data coded "−" and "?", respectively. Character states, definitions, and specimen data can also be transferred between projects by character code and normalized species key, with a dry-run preview before import.

### 2.8 Molecular phylogenetic pipeline

For taxa with available sequence data, GyroMorpho includes an automated molecular pipeline. Sequences for a chosen marker (e.g., 18S rRNA, ITS, or COI) were either uploaded by the user or retrieved from GenBank via NCBI Entrez for each specimen with a recorded accession; after filtering (removal of excluded accessions, sequences below a minimum length, and exact duplicates, retaining the longest sequence per species) and optional inclusion of outgroup taxa, sequences were aligned with MAFFT (`--auto --adjustdirection`) and ambiguously aligned columns were removed with trimAl. Maximum-likelihood trees were inferred with RAxML-NG (GTR+G substitution model, with bootstrap support), executed either locally or remotely through the CIPRES Science Gateway; a neighbor-joining tree (via Biopython) is available as a rapid alternative. The resulting tree was rooted on a user-selected outgroup and imported into the matrix view, where the rows are reordered to match the ladderized leaf order and displayed alongside a synchronized cladogram with branch support. Each phylogenetic job is written to a self-contained, timestamped results directory.

### 2.9 Optional AI-assisted character evaluation

An optional advisory module transmits the project's non-image context—specimen counts, character definitions, and summary statistics of the value distributions—to a large language model (configurable; e.g., Anthropic Claude, OpenAI GPT-4o, or Google Gemini) and returns structured, non-binding suggestions for new characters, refined state boundaries, and potentially redundant or uninformative characters, as well as answers to free-form questions about the dataset. No images are transmitted, and all suggestions are presented for expert review and are never applied automatically.

### 2.10 Reproducibility

Because every measurement algorithm, character definition, and state threshold is stored explicitly within the project and exported with the data, analyses performed in GyroMorpho are fully recoverable and reproducible. The complete record of structured analytical results resides in the SQLite database; loss of the database removes all derived results even if the underlying images remain, and regular backup of `data/db.sqlite` is therefore recommended.

---

*End of Methods. Replace generic statements with dataset-specific counts (number of specimens, species, and characters actually scored), software version numbers (MAFFT, trimAl, RAxML-NG, Python), and the AI model/version actually used.*

---

## 3. Results (draft — replace bracketed values with your data)

### 3.1 Dataset assembled
The character matrix comprised [N] specimens representing [N] nominal species of [taxon], scored for [N] of the 36 available characters ([N] hook, [N] anchor, [N] bar, and [N] MCO characters); the remaining characters were inapplicable or invariant across the sampled taxa. Of the scored cells, [N]% were filled, [N]% coded as inapplicable ("−"), and [N]% as missing ("?"). [N] specimens were excluded from automated coding because raw sinuosity exceeded the quality threshold (2.0), indicating incomplete or erroneous contour tracing, and were coded as missing for the affected characters.

### 3.2 Reproducibility of geometric measurements
Because all geometric characters are computed from Procrustes-aligned contours as scale-invariant ratios and angles, repeated coding of the same landmark configuration returned identical state assignments. [If measured: re-digitization of [N] specimens by [N] independent operators yielded a mean inter-operator agreement of [N]% across geometric characters (Cohen's κ = [value]), compared with [N]% for the qualitative characters scored by the same operators.] This contrasts with the operator dependence reported for linear and qualitative coding of gyrodactylid hard parts.

### 3.3 Data-driven state boundaries
For the [N] geometric characters, Fisher–Jenks natural-breaks partitioning recovered well-separated classes (mean Goodness of Variance Fit = [value], range [value]–[value] at the selected number of states). [N] characters showed clear multimodal distributions for which the algorithm-suggested boundaries were accepted without modification; [N] were adjusted by the taxonomist for biological reasons (e.g., to align a break with a recognized morphological discontinuity). [N] characters with GVF below [threshold] were flagged as poorly partitioned and either redefined or deactivated.

### 3.4 Character matrix and phylogenetic integration
The assembled matrix was exported in NEXUS, TNT, CSV, and JSON formats without manual reformatting. The molecular pipeline produced an alignment of [N] sequences and [N] aligned positions ([N] after trimAl trimming) for the [marker] marker; the maximum-likelihood tree (RAxML-NG, GTR+G) was rooted on [outgroup] and integrated with the morphological matrix, reordering matrix rows to the ladderized leaf order. [State the broad congruence/incongruence between the morphometric character distribution and the molecular topology here.]

### 3.5 Automated descriptions and AI advisory output
Standardized morphological descriptions and group diagnoses were generated automatically from the matrix for all [N] species. The optional AI advisory module proposed [N] candidate new characters and [N] state-boundary refinements; of these, [N] were accepted by the taxonomist and [N] rejected as redundant or biologically unmotivated, illustrating the module's intended role as a non-binding aid subject to expert review.

---

## 4. Discussion (draft skeleton)

GyroMorpho v2 addresses a persistent obstacle in gyrodactylid systematics: the gap between the rich shape information present in sclerotized hard parts and the impoverished, operator-dependent way that information has traditionally been reduced to characters. By coupling pseudolandmark acquisition with explicit, scale-invariant geometric measurement and data-driven state discretization, the platform converts continuous shape variation into discrete, reproducible characters whose every threshold is recorded and recoverable. The principal advance is therefore methodological transparency: a third party can, in principle, reconstruct exactly how each cell of the matrix was obtained.

[Expand on the following points:]
- **Reproducibility vs. tradition.** Compare objectivity and inter-operator agreement against linear/qualitative coding; discuss implications for cross-laboratory comparability and for revisiting historical descriptions.
- **Integration as the key benefit.** Most morphometric studies stop at shape ordination; GyroMorpho's contribution is the unbroken path from image to coded matrix to phylogeny to formatted description, reducing transcription error and enabling rapid re-analysis when characters or thresholds change.
- **Limitations.** Two-dimensional contours discard depth; landmark homology for pseudolandmarks is positional rather than strictly biological; automated thresholds depend on adequate taxon sampling and can shift as new specimens are added; bar and MCO characters remain manual.
- **The role of AI assistance.** Frame the advisory module as hypothesis-generating, not decision-making; note that no images are transmitted and all suggestions require expert validation.
- **Outlook.** Extension to other monogenoid groups with sclerotized diagnostic structures; potential incorporation of automated segmentation (U-Net) to reduce manual tracing; community sharing of standardized character libraries.

---

## 5. References (fill in journal style; verify latest editions/DOIs)

- Capella-Gutiérrez, S., Silla-Martínez, J.M., Gabaldón, T. (2009). trimAl: a tool for automated alignment trimming in large-scale phylogenetic analyses. *Bioinformatics* 25(15), 1972–1973.
- Gower, J.C. (1975). Generalized Procrustes analysis. *Psychometrika* 40, 33–51.
- Jenks, G.F. (1967). The data model concept in statistical mapping. *International Yearbook of Cartography* 7, 186–190.
- Katoh, K., Standley, D.M. (2013). MAFFT multiple sequence alignment software version 7: improvements in performance and usability. *Molecular Biology and Evolution* 30(4), 772–780.
- Kozlov, A.M., Darriba, D., Flouri, T., Morel, B., Stamatakis, A. (2019). RAxML-NG: a fast, scalable and user-friendly tool for maximum likelihood phylogenetic inference. *Bioinformatics* 35(21), 4453–4455.
- Miller, M.A., Pfeiffer, W., Schwartz, T. (2010). Creating the CIPRES Science Gateway for inference of large phylogenetic trees. In *Proceedings of the Gateway Computing Environments Workshop (GCE)*, New Orleans, LA, 1–8.
- Rohlf, F.J., Slice, D. (1990). Extensions of the Procrustes method for the optimal superimposition of landmarks. *Systematic Zoology* 39(1), 40–59.
- Zelditch, M.L., Swiderski, D.L., Sheets, H.D. (2012). *Geometric Morphometrics for Biologists: A Primer*, 2nd ed. Academic Press, San Diego.
- Bookstein, F.L. (1991). *Morphometric Tools for Landmark Data: Geometry and Biology*. Cambridge University Press, Cambridge.
- Schindelin, J., et al. (2012). Fiji: an open-source platform for biological-image analysis. *Nature Methods* 9(7), 676–682.

*Gyrodactylid systematics references to add (use your own canonical set):*
- Boeger, W.A.P., Kritsky, D.C. — phylogenetic systematics / character homology of Monogenoidea.
- Harris, P.D., Shinn, A.P., Cable, J., Bakke, T.A. — *Gyrodactylus* species diversity and identification.
- Pugachev, O.N., Gerasev, P.I., Gussev, A.V., et al. — guides to *Gyrodactylus* of the Northern Hemisphere.
- Relevant references on hard-part homology and the use of hamuli/bar/MCO characters in gyrodactylid diagnosis.
