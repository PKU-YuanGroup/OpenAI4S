# Mineral Spectra Case 1

This directory separates observable synthetic input from its evaluation-only answer key.

## Direct files

| File | Responsibility |
| --- | --- |
| [`spectrum.csv`](spectrum.csv) | Observable 626-row two-column `raman_shift,intensity` dirty mixed-mineral Raman spectrum consumed by the blind pipeline. |
| [`input.png`](input.png) | 1200×360 RGBA plot of the same dirty input spectrum for visual inspection; it is presentation, not an additional measurement. |
| [`truth.json`](truth.json) | Hidden synthetic answer key naming Clinoptilolite-Ca, Bertrandite, and Diopside with their true fractions and generation noise/spike/baseline metadata; evaluation code may read it only after blind inference. |

## Direct subdirectories

None.
