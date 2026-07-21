# Mineral Spectra Case 1

The observable input of the synthetic case, with its answer key kept in a separate file that only the evaluation step may open.

## Files

| File | Responsibility |
| --- | --- |
| [`spectrum.csv`](spectrum.csv) | The dirty mixed-mineral Raman spectrum the blind pipeline consumes: 626 rows of two-column `raman_shift,intensity` data. |
| [`input.png`](input.png) | A 1200×360 RGBA plot of that same dirty spectrum, for eyeballing the input. It is a picture of the data, not a second measurement. |
| [`truth.json`](truth.json) | The hidden answer key: Clinoptilolite-Ca, Bertrandite and Diopside with their true fractions, plus the noise, spike and baseline settings the case was generated with. Evaluation code may read it only once blind inference has finished. |
