# Mineral Spectra Analysis Skill

Reading an unknown mixed-mineral Raman spectrum. Preprocess the spectrum once, then loop on the residual: detect peaks, match them against a reference library, refit every selected component with NNLS, subtract, go again. What comes out is a component list with fractions, the peaks that support each one, and a reliability verdict. The loop is blind by construction — synthetic cases with hidden truth can be generated and scored, but `truth.json` is never read during analysis, and that path stays off the inference route on purpose.

The sidecar holds the numerical pipeline. It needs numpy, scipy, pybaselines and matplotlib at runtime, and it may download and cache the RRUFF database when that is allowed. Both the bundled library and the pipeline are prototype-grade: scoring well on a synthetic case is not evidence that a real sample was correctly identified.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The pipeline order is the content, and it has to be preserved: a two-column spectrum in, aligned to the library grid, despiked and denoised and baseline-corrected and normalized exactly once — then the residual loop reads that cleaned spectrum back and iterates until the residual peaks vanish, the correlation drops too low, or another round would gain nothing. Around it: preparing the dependencies and the RRUFF data, the diagnostics and report to produce, the output layout, why synthetic evaluation is fenced off from the inference path, and how far a library-driven fit may honestly be read. |
| [`kernel.py`](kernel.py) | The optional sidecar, and where the numbers actually happen. It reports which optional dependencies are importable, hands out the fixed config, builds an RRUFF library aligned to one common grid (downloading and parsing the ZIP when needed), resamples the input spectrum onto that grid and preprocesses it once, then drives the residual loop (second-derivative peak detection, peak-driven match against ranked references, NNLS refit, subtract) and finishes by diagnosing the residual, drawing the figures and writing the report. Synthetic case generation and truth scoring live here too, kept apart from the loop. Scientific imports are deferred to call time, so the module still imports without numpy present. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | One committed synthetic case: the observable input, the hidden truth stored separately, the recorded blind-analysis output, the report derived from them, and the stdlib-only script that rebuilds it. |
