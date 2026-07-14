# LigandMPNN Skill

Inverse folding when the design surface carries a ligand, a nucleic acid or a metal. LigandMPNN puts those atoms into the graph, so the network sees what vanilla ProteinMPNN ignores. Its `run.py` also threads the designed sequence back onto the input coordinates and writes the PDBs next to the FASTA, which is why it is the more convenient runner even for protein-only jobs — it accepts the ProteinMPNN and soluble weights too. The recipe points at the external LigandMPNN repository and its checkpoints; no executable and no weights are bundled here.

LigandMPNN designs around the ligand coordinates it was handed, and it treats that pose as fact. Hand it a docked or modelled placement that is off and you get a pocket shaped to a ligand position that may not be the real one — and the run looks exactly as healthy as a correct one. The `ligand_confidence` in each header is the model scoring the sequence it just wrote, not a measurement of binding. Chain, fixed-residue, context-atom and model-type choices all have to be checked against the actual input structure, and the sequences and threaded structures that come back are design candidates until the bench says otherwise.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Covers repository setup, the model types and their checkpoints, how the PDB and its ligand context are parsed, which chains and residues get designed or held fixed, sampling, the batch outputs, and the threaded PDBs. It also collects the ligand-aware traps, including the one where a stripped HETATM record leaves the model pocket-blind without complaining, and the validation that comes after. |
