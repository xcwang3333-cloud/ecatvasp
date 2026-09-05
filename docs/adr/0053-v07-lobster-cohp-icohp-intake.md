# ADR-053: v0.7 LOBSTER / COHP / ICOHP Result-Intake Boundary

Status: Accepted

## Context

v0.3 already defines `LOBSTER_PREREQUISITE` as a VASP Calculation that produces a suitable static
wavefunction and deterministic VASP inputs. v0.7 must ingest external LOBSTER results without turning
LOBSTER into a second scheduler backend, without persisting licensed POTCAR bodies, and without
rewriting native COHP/ICOHP facts into plotting or chemical interpretations.

ADR-048 freezes three important rules:

1. LOBSTER execution remains external analysis;
2. COHP/ICOHP native sign is canonical and `-COHP` is only a downstream view;
3. pair identity binds to permanent `atom_uid` through the exact frozen atom-index map, never through
   geometric nearest-neighbour guessing.

## Decision

### 1. External Analysis over an exact VASP prerequisite

A durable `AnalysisType.COHP` requires one scientifically `CONVERGED`
`CalculationType.LOBSTER_PREREQUISITE` plus its exact immutable scientific context:

- `StructureSnapshot`;
- `MethodFingerprint`;
- exact execution-produced `WAVECAR`;
- exact calculation-produced `atom-index-map.json`;
- exact prerequisite `POSCAR`, `INCAR`, `KPOINTS`, and `POTCAR.spec` Artifacts.

The WAVECAR's `ExecutionAttempt` is validated only as source ownership. ExecutionAttempt identity is
not part of Analysis scientific identity and is not added to the SCIENTIFIC dependency DAG.

### 2. External invocation provenance is explicit

LOBSTER result intake requires an `ExternalToolInvocation` whose tool is `lobster` and whose logical
input digests include at least:

- `wavefunction`;
- `poscar`;
- `potcar`;
- `lobsterin`.

The managed WAVECAR and POSCAR digests must match the invocation receipt. The external POTCAR-body
digest is retained only as provenance; the licensed POTCAR body itself remains outside the project,
repository, package, and persisted Artifact set. `POTCAR.spec` remains the managed prerequisite
metadata Artifact.

No new scheduler, credential, remote-execution, or generic external-process state machine is added.

### 3. Canonical COHPCAR semantics

The parser accepts the LOBSTER COHPCAR layout in which the parameter line supplies interaction count,
spin count, energy-point count, and the source Fermi energy, followed by one `Average` header plus
interaction headers and a common numerical grid.

For each interaction and spin channel, canonical facts retain the native:

- COHP values;
- integrated COHP (ICOHP) curve;
- pair/orbital source label;
- bond length;
- optional periodic-cell translation;
- optional orbital labels.

Collinear spin is normalized to either `TOTAL` or ordered `UP/DOWN`. Noncollinear decomposition is
outside v0.7.

### 4. Energy reference is explicitly LOBSTER Fermi-relative

LOBSTER COHPCAR energies are already written relative to the Fermi level. They must not be mislabeled
as the native VASP energy frame used by the DOS contract.

Canonical COHP therefore uses the explicit reference
`CohpEnergyReference.LOBSTER_FERMI_RELATIVE`, requires the energy window to include 0 eV, and stores
the source/header Fermi-energy value separately for provenance.

No implicit conversion back to a VASP-native or vacuum-referenced axis is performed.

### 5. Native sign is immutable raw scientific fact

The canonical result preserves the sign written by LOBSTER. ECatVASP does not multiply by `-1`, label
negative/positive regions as bonding/antibonding, apply thresholds, or infer bond strength during
intake.

Any `-COHP` display convention, bonding interpretation, thresholding, or qualitative chemical label is
a downstream view/analysis and must not rewrite the canonical dataset.

### 6. Permanent pair identity uses only the frozen atom-index map

LOBSTER atom labels are interpreted as one-based VASP ordinals plus element symbols. Each ordinal is
resolved only through the exact `ecatvasp-v03-atom-index-map` belonging to the prerequisite
StructureSnapshot.

The parser rejects:

- ordinals outside the frozen map;
- element labels inconsistent with the mapped site;
- malformed/non-contiguous atom maps;
- species metadata inconsistent with the exact serialized VASP order.

Coordinates and bond lengths are never used to guess atom identity.

Periodic translation vectors and orbital labels are preserved when the source provides them; they are
scientific pair metadata, not identity-recovery fallbacks.

### 7. ICOHP(E_F) is cross-checked against ICOHPLIST

`COHPCAR.lobster` remains the source of the full COHP and integrated-COHP curves.
`ICOHPLIST.lobster` supplies explicit pair-integrated values at the Fermi level for total pair
interactions.

For a total pair, intake requires a matching ICOHPLIST interaction and validates pair ordinal,
translation when present, and bond length. If COHPCAR contains an exact 0-eV grid point, the integrated
curve at that point must agree with ICOHPLIST within the parser tolerance.

Orbital-resolved COHPCAR interactions remain canonical facts even when no corresponding orbital-wise
ICOHPLIST row is available; their Fermi-level value may be read directly from an exact 0-eV COHPCAR
point, otherwise it remains unset rather than interpolated.

No interpolation to the Fermi level is performed in v1.

### 8. Raw external result Artifacts are typed and durable

Block 6 adds the additive Artifact enum value:

`ICOHPLIST_LOBSTER = "icohplist_lobster"`

This corrects an existing asymmetry: ADR-048 already treats both `COHPCAR.lobster` and
`ICOHPLIST.lobster` as raw Artifacts, while only COHPCAR previously had a dedicated ArtifactType.
The persisted record shape does not change, so `SCHEMA_VERSION` remains 3.

A completed COHP Analysis writes three `AnalysisProducerRef` Artifacts:

- exact `COHPCAR.lobster` as `COHPCAR_LOBSTER`;
- exact `ICOHPLIST.lobster` as `ICOHPLIST_LOBSTER`;
- deterministic `canonical-cohp.json` as `DERIVED_DATASET`.

The normalized JSON records the exact source receipt, invocation provenance, raw hashes, energy
reference, permanent atom identities, native COHP/ICOHP values, and a deterministic scientific content
hash.

### 9. Freshness reuses the existing SCIENTIFIC DAG

SCIENTIFIC dependencies are recorded from the prerequisite Calculation, StructureSnapshot,
MethodFingerprint, WAVECAR, atom-index map, POSCAR, INCAR, KPOINTS, and POTCAR.spec to the COHP
Analysis. The Analysis then owns the two raw external-result Artifacts and canonical result Artifact.

Changes to any exact managed scientific input therefore propagate stale state through the existing
`FreshnessEngine`. No LOBSTER-specific freshness engine is introduced.

## Consequences

- LOBSTER remains an external tool rather than a new execution backend.
- The exact VASP wavefunction and prerequisite inputs remain auditable scientific parents.
- Licensed POTCAR content remains outside persisted/project data while its external-run digest can be
  recorded.
- COHP and ICOHP values are reproducible native facts rather than sign-transformed plots.
- Pair identity is permanent-UID based and cannot silently drift through geometry matching.
- Schema v3 remains sufficient.

## Deferred

This Block does not add:

- LOBSTER execution/submission or `lobsterin` generation policy;
- automatic basis selection or POTCAR-body persistence;
- COOP, COBI, LCFO, fatband, charge spilling interpretation, or bonding classification;
- `-COHP` canonicalization, threshold labels, or qualitative bond-strength conclusions;
- noncollinear/SOC COHP decomposition;
- electronic descriptors such as d-/p-band centers (Block 7);
- workflow reconciliation/readiness projection (Block 8);
- thermochemistry, CHE, reaction free-energy diagrams, tag, Release, or PyPI publication.
