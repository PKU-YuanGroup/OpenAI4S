# evidence-walkthrough

The reference end-to-end pass: fixed database query → local analysis →
versioned artifacts carrying lineage → an evidence package that verifies in a
clean environment.

Use it as the first-run demonstration, as a benchmark case (the inputs are
fixed so two runs are comparable), or whenever a result has to be handed to
someone who was not there when it ran.

Verify an exported package the way a recipient would, with no daemon:

```
openai4s verify-package <session>.openai4s-session.zip
```

A pass means the package is **intact**, not that it is authentic — see
[`openai4s/evidence.py`](../../openai4s/evidence.py) for exactly what
verification does and does not establish.
