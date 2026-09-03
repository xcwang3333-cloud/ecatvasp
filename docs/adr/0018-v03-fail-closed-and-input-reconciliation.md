# ADR-018 — v0.3 Global Fail-Closed Matrix and Input Reconciliation

- Status: Accepted
- Date: 2026-09-03
- Scope: v0.3 Block 10

## Decision

ECatVASP v0.3 uses one explicit fail-closed rule registry for VASP input preparation and one manifest-aware reconciliation path for generated input folders.

The registry provides stable machine-readable error codes without replacing the existing domain-specific exception hierarchy. Existing preparation modules remain authoritative for scientific compilation; reconciliation verifies that persisted generated files still represent the exact Calculation, StructureSnapshot, MethodFingerprint, Recipe, SystemContext, ProjectNumericalLock, POTCAR metadata identity, k-point plan, and atom UID mapping that produced them.

## Generated input reconciliation

A generated input directory is considered reconciled only when all of the following hold:

1. `input-manifest.json` has the expected ECatVASP format/version/generator identity.
2. Calculation id/type/engine/scientific hash match the caller-supplied Calculation.
3. Structure snapshot id/scientific hash match the immutable StructureSnapshot.
4. MethodFingerprint id/core method/protocol/instance hashes match exactly.
5. Recipe id/version/hash match exactly.
6. System kind/vacuum axis and ProjectNumericalLock hash match exactly.
7. Every manifest file is present exactly once, has a safe recorded path, byte size, and SHA-256 digest.
8. KPOINTS presence agrees with the recorded k-point policy; KSPACING-backed inputs must not acquire a KPOINTS file.
9. `POTCAR.spec` remains metadata-only and its ordered symbols/species identity agrees with the fingerprint and POSCAR.
10. `atom-index-map.json` binds the exact POSCAR hash to the immutable snapshot and preserves permanent `atom_uid`, element, snapshot index, POSCAR index, VASP ordinal, and selective-dynamics flags.
11. POSCAR is deterministically regenerated from the snapshot plus UID-addressed selective dynamics and must be byte-identical.
12. INCAR is recompiled through the exact core/frequency/analysis-prerequisite compiler and must be byte-identical.
13. The materialization `preparation_hash` is recomputed from reconciled scientific identities and file records.

Hash validation is necessary but not sufficient: identity, compilation, and atom mapping are checked independently.

## Existing-folder importer boundary

The legacy result importer remains conservative and compatible with external VASP folders. It still requires caller-supplied scientific identity rather than guessing POTCAR, DFT+U, dispersion, spin, or recipe semantics from incomplete files.

Generated-input reconciliation is a separate pre-execution/post-staging operation. A future result-import extension may consume the reconciled identity, but Block 10 does not silently broaden v0.1 result parsing into every v0.3 recipe.

## Global fail-closed matrix

Stable codes cover:

- numerical lock and POTCAR identity;
- KPOINTS/KSPACING and vacuum-axis legality;
- spin, vdW, dipole, and MAGMOM resolution;
- finite-difference frequency UID selection and IBRION legality;
- Recipe/Protocol/Snapshot identity;
- charge-difference three-member compatibility;
- generated-input manifest/file/POTCAR/UID reconciliation.

The matrix is an audit surface. It does not bypass specialized compilers or redefine frozen Domain entities.

## Boundaries

Block 10 does not add scheduler/HPC behavior, execution retries, result-analysis parsing, Bader/LOBSTER execution, CHGCAR grid subtraction, DOS/PDOS/COHP result generation, or frozen Domain/storage schema changes.
