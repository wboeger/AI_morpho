# Persisted per-rater Skip state for MCO Illustration Reliability

Skipping a species in the blind reliability evaluator was purely client-side
(`skipItem()` just advanced an in-memory index) — it never survived reload
and gave raters no way to deliberately return to what they'd skipped. We
decided Skip needed to be a real, durable third state, and made these choices
about its shape:

- **Same table, not a new one.** `MCOReliabilityRating` already has a unique
  `(project_id, rater_id, species_norm)` row; we reuse it with a nullable
  `skipped_at` timestamp rather than adding a parallel table, since the row
  already means "this rater's current disposition toward this species" and
  every CRI/kappa aggregator already treats `cri is None` (empty `scores`) as
  "exclude this row" — a Skipped row costs those consumers nothing.
- **No skip reason.** Considered an optional free-text reason (mirroring the
  existing rating `notes` field) for admin oversight of *why* items get
  skipped, but rejected it to keep Skip a plain routing signal rather than a
  second annotation channel.
- **Skip is not retained once rated.** Rating a previously-skipped species
  clears `skipped_at` — the row becomes an ordinary rating with no trace it
  was ever skipped. We considered keeping the first-skipped timestamp as a
  permanent "took two passes" audit fact, but nothing in the app would ever
  read it, given the no-reason decision above.
- **Two separate queues, not one reordered queue.** The default Evaluate
  queue only ever contains Never-seen species; a distinct Skipped queue is
  how a rater intentionally revisits what they deferred. We rejected quietly
  sinking skipped items to the back of the single queue, since that gives no
  explicit "go work through my skipped backlog" action — for a queue long
  enough that a rater needs to skip at all, they might never loop back to it.
