# Molecular phylogenetics — Materials and Methods (manuscript version)

*Concise, journal-ready prose. Replace bracketed placeholders (versions, replicate
counts, accession tables, software citations) with the values used for the final run
and add the corresponding references to the bibliography.*

---

## Taxon sampling and sequence data

DNA sequence data were obtained from GenBank for the ingroup and for outgroup
representatives. Ingroup sampling targeted all species [of Gyrodactylidae] included in
the morphological dataset; for each species, records of the target marker(s) — 18S
rRNA and/or the internal transcribed spacer region (ITS1–5.8S–ITS2) — were retrieved
through the NCBI Entrez E-utilities. Searches used a taxon-restricted organism query
combined with a marker query from which generic-exclusion terms were omitted, so that
sequences deposited only as combined ribosomal cassettes (18S–ITS1–5.8S–ITS2–28S) were
not excluded from the marker-specific datasets; the relevant region of each such record
was subsequently isolated by per-marker alignment and trimming (below). For every
species, the longest available record per marker was retained, excepting records
exceeding a length-outlier threshold [2× the mean length of the deduplicated set];
identical sequences of the same species were collapsed, whereas identical sequences
belonging to different species were kept. Species not recovered by the taxon-level
search were queried individually with no minimum-length restriction to recover short or
partial records. A species was scored as unavailable only when GenBank held no record
of the marker for it; unavailable species are listed in [Table Sx / Supplementary
Material]. Outgroup sequences were drawn from [families/genera], selecting the [n]
longest records per [genus/family]. All accession numbers are given in [Table Sx].

## Alignment and trimming

Each marker was aligned separately with MAFFT [v7.526] under the `--auto` strategy, with
sequence orientation corrected (`--adjustdirection`; corrected sequences verified by
shared *k*-mer content against the longest record of the set). Ambiguously aligned
positions were removed with trimAl [v1.4] using the `-gappyout` algorithm [or
`-automated1`; state which was used]. Where the concatenated 18S + ITS matrix was
analysed, the two single-marker alignments were trimmed independently and then
concatenated by species, with missing partitions coded as gaps; the number of terminals
represented by both markers, and by each marker alone, is reported in [Table Sx].

## Substitution-model selection and tree inference

The best-fit nucleotide substitution model was selected for each alignment (and
independently for each partition of the concatenated/partitioned matrices) with
ModelTest-NG [vX.X] under the Bayesian information criterion. Maximum-likelihood
phylogenetic inference was performed in RAxML-NG [v2.0] under the selected model(s)
[e.g. GTR+G], using the all-in-one analysis (`--all`): an adaptive maximum-likelihood
tree search followed by non-parametric bootstrapping. Bootstrapping used [up to] [1000]
replicates with autoMRE bootstopping and a fixed random seed; branch support was
computed as Felsenstein bootstrap proportions and mapped onto the best-scoring ML tree.
For partitioned analyses, each partition was assigned its own substitution model with
branch lengths linked proportionally across partitions. The resulting tree was rooted on
[outgroup taxon/taxa] and is presented with bootstrap support values at nodes; support
values below [50] are not shown.

## Reproducibility and data availability

Sequence retrieval, alignment, trimming, model selection, and tree inference were
executed through GyroMorpho v2, an open-source pipeline that automates and records each
step [cite software / repository DOI]. RAxML-NG analyses were run on the Galaxy Europe
platform (usegalaxy.eu). All GenBank accession numbers, the final alignments, and the
inferred tree(s) are available in [repository / Supplementary Material / TreeBASE
accession].

---

### Suggested software references to add

- **MAFFT** — Katoh & Standley (2013), *Mol. Biol. Evol.* 30:772–780.
- **trimAl** — Capella-Gutiérrez et al. (2009), *Bioinformatics* 25:1972–1973.
- **ModelTest-NG** — Darriba et al. (2020), *Mol. Biol. Evol.* 37:291–294.
- **RAxML-NG** — Kozlov et al. (2019), *Bioinformatics* 35:4453–4455.
- **Galaxy** — The Galaxy Community (2024), *Nucleic Acids Res.* 52:W83–W94.
