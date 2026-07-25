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
import json

records = []
for accession in ACCESSIONS:
    hit = host.science.search("uniprot", accession, limit=1)
    records.append(hit)

# Check before saving, not after: an artifact written with three quarters of
# its evidence missing is already wrong by the time anyone can query it.
for accession, record in zip(ACCESSIONS, records):
    envelope = record["provenance"]
    assert accession in envelope["request_url"], accession
    assert envelope["retrieved_at"] and envelope["response_sha256"]

host.write_file("raw_uniprot.json", json.dumps(records, indent=2))
raw = host.save_artifact(
    "raw_uniprot.json",
    # EVERY retrieval, not the first one. This file is the evidence for four
    # independent requests, and `records[0]["provenance"]` describes exactly
    # one of them — the other three accessions would then sit inside an
    # artifact that claims to preserve their evidence while carrying no
    # request URL, no retrieval time and no response hash for them.
    source={
        "kind": "aggregate",
        "database": "uniprot",
        "queries": ACCESSIONS,
        "sources": [record["provenance"] for record in records],
    },
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

One artifact, four retrievals, so **four envelopes**. The aggregate above is
the form to use whenever a file is assembled from more than one request: a
single envelope covers a single request, and attaching one of four is worse
than attaching none — the artifact then looks provenanced while three quarters
of it is unaccounted for. Read it back and confirm every accession is there:

```python
attached = json.loads(host.query(
    "SELECT source FROM artifact_versions WHERE version_id = ?",
    [raw["version_id"]],
)[0]["source"])
covered = {envelope["request_url"] for envelope in attached["sources"]}
missing = [a for a in ACCESSIONS if not any(a in url for url in covered)]
assert not missing, f"no retrieval provenance attached for {missing}"
```

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
