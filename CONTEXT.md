# GyroMorpho

Flask web app for morphometric analysis, automated taxonomic description, and
phylogenetic inference of Gyrodactylidae (Monogenoidea) sclerotized structures
(hooks, anchors, bars, MCO).

## Language

### MCO Illustration Reliability (Blind)

**Rating**:
One scientist's blind scoring of a species' MCO illustration against the
project's reliability criteria, producing a Composite Reliability Index (CRI).
_Avoid_: score, evaluation (as a noun for the row)

**Rating status**:
The state of one (rater, species) pair — exactly one of **Never seen**,
**Skipped**, or **Rated**. Never seen means no interaction yet. Rated means
scores and a CRI are recorded. There is no retained memory of a prior Skipped
state once a species is Rated — status reflects the rater's current
disposition only, not history.
_Avoid_: "pending" as a specific state — it conflates Never seen and Skipped

**Skip**:
A rater's deliberate decision to defer scoring a species' MCO illustration to
a later session. Skipping removes the species from that rater's default
Evaluate queue and surfaces it in that rater's separate Skipped queue instead.
Skipping does not record a reason.
_Avoid_: defer, flag, pass

**CRI (Composite Reliability Index)**:
The weighted-normalized mean, in [0,1], of a rating's per-criterion scores
over whichever criteria that rating actually scored. Undefined (not zero) for
a Skipped or Never-seen species.
