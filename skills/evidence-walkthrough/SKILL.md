---
name: evidence-walkthrough
description: Run the reference end-to-end research pass — fixed database query, local analysis, versioned artifacts with lineage, then an exported evidence package that verifies in a clean environment. Use as the first-run demonstration, as a benchmark case, or when a result must be handed to someone who was not there when it ran.
origin: openai4s
category: research-workflow
---

# Evidence walkthrough

The reference pass a result has to survive: **query → analyse → artifacts with
lineage → an evidence package a stranger can verify.**

Its point is not the science, which is deliberately small. Its point is that
every step leaves evidence, and the package at the end can be checked by
someone who does not trust this machine — a reviewer, a colleague, or you on a
different laptop in six months.

## Fixed inputs

Use these exact accessions. They are fixed so two runs are comparable and so
this doubles as a benchmark case; changing them makes a run incomparable to
every previous one.

```python
ACCESSIONS = ["P69905", "P68871", "P02042", "P02100"]   # human haemoglobin subunits
```

## Workflow

### 1. Retrieve, and record what you retrieved

```python
records = []
for accession in ACCESSIONS:
    hit = host.science.search("uniprot", accession, limit=1)
    records.append(hit)

host.write_file("raw_uniprot.json", json.dumps(records, indent=2))
raw = host.save_artifact(
    "raw_uniprot.json",
    source=records[0]["provenance"],             # where it came from, and when
)                                                # -> {"version_id": ...}
```

Save the raw response **before** analysing it. The analysis is a claim; the raw
response is the evidence for it, and a claim whose evidence was never written
down cannot be rechecked later.

`source` is the other half. Every `host.science.search` result carries a
`provenance` envelope naming the database, the exact request, the moment it was
fetched and a hash of the bytes that came back. Pass it and the artifact can
answer *when was this true* and *was it the same data* — without it a saved
file records what you have but not what it is evidence of, and a rerun that
quietly returned something different is indistinguishable from one that did
not.

### 2. Analyse

Keep it to what the raw file supports. Length, mass, and sequence composition
are properties of the record; anything requiring a source you did not save is
a claim you cannot back.

```python
import json, collections

rows = []
for record in records:
    entry = (record.get("results") or [{}])[0]
    sequence = (entry.get("sequence") or {}).get("value", "")
    rows.append({
        "accession": entry.get("primaryAccession"),
        "name": entry.get("proteinDescription", {}).get(
            "recommendedName", {}).get("fullName", {}).get("value"),
        "length": len(sequence),
        "top_residues": collections.Counter(sequence).most_common(3),
    })
```

### 3. Produce artifacts, declaring their inputs

```python
host.write_file("summary.json", json.dumps(rows, indent=2))
summary = host.save_artifact("summary.json", input_version_ids=[raw["version_id"]])
```

`input_version_ids` is the lineage edge. Without it the summary is a file that
appeared from nowhere; with it, anyone reading the artifact can walk back to the
exact bytes it was derived from. Declare it on **every** derived artifact,
including figures.

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(6, 3.2))
ax.bar([r["accession"] for r in rows], [r["length"] for r in rows])
ax.set_ylabel("residues")
ax.set_title("Haemoglobin subunit lengths")
fig.tight_layout()
fig.savefig("lengths.png", dpi=150)
plt.close(fig)

host.save_artifact("lengths.png", input_version_ids=[raw["version_id"]])
```

### 4. Export and verify

Export the session package from the UI (or `GET
/api/v1/frames/<id>/session/export`), then verify it the way a recipient
would — with no daemon involved:

```
openai4s verify-package <session>.openai4s-session.zip
```

A pass means every listed file matches its recorded hash and the manifest
matches its own digest. It does **not** establish who produced the package;
that needs a signature, which this format does not carry. Say "verified
intact", not "verified authentic".

## What to check before calling it done

- Every derived artifact declares `input_version_ids`. A missing edge is the
  difference between a result and an anecdote.
- The raw retrieval is saved as its own artifact, not just parsed in memory.
- `verify-package` exits 0 on the exported package.
- Numbers in your summary can each be traced to a saved file.

## Offline and reproducibility

The retrieval step needs the network. Everything after it is deterministic
given the same raw file, so a benchmark run should fix the raw artifact and
replay from step 2 — that separates "the analysis changed" from "the upstream
database changed", which are different failures and only one of them is yours.
