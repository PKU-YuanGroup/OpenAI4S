# Nominal agreement and its limits

Adapted from AlterLab's `references/icr_and_reporting.md` and `scripts/icr.py`;
see `UPSTREAM.json`. The helper is for aligned, fixed units and single labels.

## What is calculated

`nominal_agreement(by_unit, coder_names=...)` takes a unit-by-coder matrix.
Every row must contain one slot per distinct coder name. Labels are strings;
`None` and the empty string are missing. Other types and whitespace-only labels
are rejected. Convert category identifiers deliberately; numeric distances do
not enter nominal agreement. The helper does not strip or combine labels.

Units with fewer than two observed codes contribute no coincidences. For a unit
with `m` observed codes, each ordered pair contributes weight `1/(m-1)`. If
`n_c` are the resulting category marginals and `n = sum(n_c)`, nominal alpha is
`1 - (n-1) * off_diagonal / (n*n - sum(n_c*n_c))`. This is the coincidence
formulation described in [Krippendorff's method paper](https://repository.upenn.edu/bitstreams/0421f871-f005-4322-b06a-a66bec328e3b/download).

Pairwise agreement weights each observed coder pair equally. Alpha weights
pairable units' coincidences differently when the number of observed codes
varies; these quantities are not interchangeable. With exactly two coders,
Cohen's kappa uses only complete pairs and their separate coder marginals.
Constant coding with zero expected disagreement yields `None`, not 1, for
both chance-corrected coefficients. No pairable units also yields `None` with a
different reason. Empty input preserves the audit and reports no estimate.

Missing values do not become a category, and singly coded units do not change
the estimated category distribution. The audit counts missing cells for every
coder, pairable/excluded units, and all observed pairs. Investigate selective
missingness rather than treating the software's ability to omit it as proof
that it is ignorable.

## What the report cannot establish

The helper deliberately returns `uncertainty.status = "not_estimated"`. A
sampling interval requires a justified independent sampling unit and a method
appropriate to the coding design. Segments from the same interview or case
are often dependent: a naive bootstrap over segments can understate uncertainty.
If resampling is appropriate, preserve the case/interview clusters and disclose
the method, seed, resample count, and undefined resamples. Do not silently
discard degenerate replicates and present the remainder as an ordinary CI.

Report point estimates as descriptive when uncertainty has not been computed.
Choose any decision threshold for the specific purpose before examining the
result; common alpha rules of thumb do not establish validity or universal
quality. Neither Pearson correlation, Cronbach alpha, nor a chi-square test
substitutes for a nominal coding-agreement coefficient.

For ordinal/interval labels, free segmentation, multilabel coding, weighted
kappa, or an inferential comparison of coders, use a method that explicitly
supports that design in an optional analysis environment. Do not claim this
helper performed it. A binary code-presence analysis per predeclared code may
be appropriate for some multilabel designs, but it needs an explicit absent
category and its own multiplicity/reporting decisions.

## Human and model coding

Keep human-human reliability separate from human-model agreement. Record how
human reference labels were created and adjudicated, their limits, model
version, exact prompt, coding unit, generation settings, and evaluation split.
Assess on held-out material when the model prompt was refined using examples.

A coefficient and its CI describe agreement; overlapping CIs do not demonstrate
equivalence or noninferiority to human coders. Such a claim needs a predeclared
margin and an appropriate comparison design. Model agreement is not independent
human coding, and a model-generated explanation cannot substitute for a
researcher's interpretation of an interview.

The final report states the analytic approach, codebook/units, coder roles,
training, subset-selection method, corpus and assessed-unit denominators,
pre-adjudication status, missingness, coefficient with uncertainty or its
explicit absence, disagreement resolution, and negative-case review.
