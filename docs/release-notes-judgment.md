# Experimental — semantic judgment layer (TypeSafe Jev)

Copy this block into the next GitHub Release notes. This tree has no unreleased
`docs/v0x-*.md` slot and `scripts/release_pipeline.py` does not read a
judgment-specific notes file. The live user-facing changelog remains the root
README News section.

---

### **Experimental** — semantic judgment layer (TypeSafe Jev)

Default off. Bring your own TypeSafe API key. The service is hosted in the
United States. Inputs are not used to train models; retention is unspecified;
ZDR is enterprise-only. Early access. Input $0.042 / million tokens.

Optional Skill suggestions, literature claim checks, text-feature engineering,
and recording-only safety / task-mode shadows, behind
`OPENAI4S_EXPERIMENTAL_JUDGMENT` and per-capability flags. Kill switch:
`OPENAI4S_EXPERIMENTAL_JUDGMENT=0`. Operator guide:
[docs/experimental-judgment.md](experimental-judgment.md).

`OPENAI4S_JUDGMENT_PROVIDER=llm` answers the same typed questions with your
configured main model instead of TypeSafe. Those results are marked
`calibrated: false`, and the default stays `typesafe`: a TypeSafe failure is
reported as unavailable and never falls back to the LLM silently.
