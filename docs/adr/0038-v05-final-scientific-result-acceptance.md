# ADR-038: v0.5 Final Scientific Result Acceptance and Hardening

- Status: Accepted
- Date: 2026-09-04

## Context

Blocks 1-8 established the complete v0.5 scientific-result vocabulary and boundaries: exact result
Artifact intake, explicit VASP energy semantics, recipe-aware convergence classification,
UID-addressed final forces/magnetization, deterministic CONTCAR reconstruction and explicit
promotion, frequency-mode extraction, durable result/convergence provenance, freshness propagation,
and normalized existing-folder import compatibility.

Those capabilities have intentionally been implemented as small composable functions rather than a
scientific workflow engine. The final v0.5 block therefore must prove that the independently frozen
boundaries compose correctly for real managed-result lifecycles and must close only provenance gaps
that become visible at end-to-end scale. It must not pre-implement v0.6 workflow orchestration,
v0.7 electronic-structure analysis, or v0.8 thermochemistry/electrocatalysis.

Two end-to-end provenance gaps are visible only after composing Blocks 5-8:

1. UID-bound force/magnetization and frequency datasets use the exact staged POSCAR and
   `atom-index-map.json`, but the generic Block 8 RESULT_PARSE graph initially records only raw
   result Artifacts from the result intake;
2. managed CONTCAR reconstruction carries exact Calculation/input/attempt/source identities in the
   Block 6 value object, while persistence of the reconstructed snapshot's provenance was explicitly
   left caller-controlled.

Without durable records for those exact inputs, reopened projects could retain scientifically correct
values but lose part of the audit/freshness chain that explains atom identity or relaxed geometry.

## Decision

### 1. v0.5 acceptance is composition, not orchestration

Block 9 adds no new automatic scientific workflow state machine. Acceptance tests invoke the frozen
public layers explicitly and in scientific order:

`ExecutionAttempt-produced Artifacts -> result intake -> normalized parse -> optional UID-bound enrichment -> convergence evidence/verdict -> durable result materialization -> optional CONTCAR reconstruction -> explicit promotion -> ProjectBundle persistence/reopen -> freshness evaluation`.

A caller remains responsible for deciding when to invoke each operation. No restart, correction,
continuation, job chaining, or next-step Calculation is created by v0.5.

### 2. Managed converged relaxation is accepted only through the full scientific gate

The converged managed-relax acceptance must prove all of the following in one vertical slice:

- the exact Calculation, ExecutionPlan, ExecutionAttempt, raw Artifact producers, sizes, paths, and
  SHA-256 digests agree;
- `ExecutionAttempt.EXITED` by itself leaves Calculation scientific status unchanged;
- explicit VASP energies are parsed without restoring a generic total-energy field;
- final forces and collinear magnetization are bound to permanent `atom_uid` values through the exact
  staged atom-index map;
- convergence is classified from VASP scientific evidence, not scheduler state;
- CONTCAR reconstruction preserves permanent identity through the exact staged POSCAR/atom map;
- only an overall `CONVERGED` assessment may explicitly promote the candidate snapshot;
- the durable Analysis/Artifact/provenance graph survives ProjectStore save/reopen;
- scientific input drift makes affected downstream results stale under the existing FreshnessEngine.

### 3. Managed unconverged relaxation remains inspectable but unpromoted

A second vertical slice must prove that an exited run with a valid final energy and CONTCAR can still
be scientifically `UNCONVERGED`. The Calculation reconciles to
`COMPLETED_UNCONVERGED`, the immutable CONTCAR-derived candidate remains available for inspection,
and explicit promotion fails closed. Scheduler/execution completion therefore never becomes a
scientific success shortcut.

### 4. UID-bound result provenance includes exact atom-identity inputs

When a normalized result contains forces, site magnetization, or frequency eigenvectors addressed by
permanent `atom_uid`, the RESULT_PARSE Analysis may be explicitly augmented with the exact staged
POSCAR and `atom-index-map.json` Artifact identities used by the parsing adapter.

This augmentation is pure: it changes only immutable Analysis/provenance/dependency values returned
to the caller. It performs no parsing, file movement, storage write, status mutation, or workflow
action. The supporting inputs are content-addressed and become `SCIENTIFIC` dependencies so drift in
either identity source propagates staleness through the normalized result chain.

Historical existing-folder imports do not receive these managed dependencies because they do not
possess a historical ECatVASP atom-index map. No identity is fabricated retroactively.

### 5. Managed reconstructed snapshots receive durable reconstruction provenance

A pure provenance builder records a managed CONTCAR-derived `StructureSnapshot` as produced by the
Block 6 reconstructor and binds it scientifically to:

- the Calculation context;
- the immutable input StructureSnapshot;
- the exact CONTCAR Artifact and digest;
- the exact staged POSCAR Artifact and digest;
- the exact staged `atom-index-map.json` Artifact and digest.

The builder neither reconstructs nor promotes the structure. Convergence is deliberately not an input
to reconstruction provenance because unconverged candidates are valid inspectable revisions.
Promotion remains the separate convergence-aware decision frozen in ADR-035.

### 6. Frequency acceptance stops at scientific frequencies

The managed frequency acceptance covers exact result intake, energy metadata, finite-difference mode
extraction, permanent atom identity, convergence, durable result provenance, persistence/reopen, and
freshness. It explicitly verifies that the normalized v0.5 result contains frequency facts and mode
semantics only.

No ZPE, entropy, heat-capacity, thermal correction, free energy, CHE, potential/pH correction, or
reaction thermodynamics is computed or persisted. Those remain v0.8 scope.

### 7. Existing freshness semantics remain unchanged

Block 9 does not modify `FreshnessEngine`, precedence, or dependency-kind behavior. It only completes
missing `SCIENTIFIC` edges for managed UID-bound results and reconstructed snapshots. Drift in raw
OUTCAR/OSZICAR/CONTCAR or exact atom-identity inputs can therefore be evaluated by the already-frozen
engine after reopen.

### 8. Existing-folder compatibility remains a distinct provenance boundary

ADR-037 remains authoritative for historical folders. They share normalized energy/convergence
semantics but do not pretend to have managed ExecutionPlan, input-manifest, atom-index-map, or
retrieval identities. Block 9 acceptance does not weaken that distinction.

### 9. No Project schema or release action

All hardening uses existing `Analysis`, `Artifact`, `ProvenanceRecord`, `DependencyRecord`,
`Calculation`, and `StructureSnapshot` entities. `SCHEMA_VERSION` remains 2.

Completion of Block 9 completes the v0.5 development scope only. It does not by itself authorize a
version tag, GitHub Release, PyPI publication, or promotion from the existing development-version
strategy.

## External implementation references

The final acceptance shape was cross-checked against established open-source VASP workflow projects
without adding a runtime dependency:

- atomate2 tests creation and downstream use of structured task documents from concrete VASP output
  directories, keeping parsed scientific documents distinct from job execution;
- aiida-vasp separately tests retrieved calculation outputs and ionic-convergence handling rather
  than equating process completion with scientific convergence.

ECatVASP keeps its smaller explicit Analysis/Artifact/provenance model and does not import either
project's workflow/database abstractions.

## Non-scope

Block 9 does not add:

- automatic workflow orchestration, continuation, retry, correction, or INCAR mutation;
- DOS/PDOS interpretation, Bader, charge-density difference, COHP, band-center analysis, or LOBSTER
  execution;
- ZPE, entropy, thermal corrections, reference-energy management, CHE, ORR/OER/HER/CO2RR free-energy
  logic, potential corrections, or pH corrections;
- geometric nearest-neighbour atom identity reconstruction;
- fabricated historical managed-execution identities;
- new scheduler backends, arrays, daemon services, or GUI features;
- Project schema migration;
- tag, GitHub Release, or PyPI publication.

## Consequences

After Block 9 acceptance, v0.5 provides an auditable scientific handoff from verified managed VASP
outputs to explicit normalized results, convergence verdicts, UID-addressed observables/frequencies,
and optional scientifically accepted relaxed structures. The persisted graph remains freshness-aware
across reopen, while workflow orchestration, electronic-structure interpretation, and
thermochemistry remain cleanly deferred to their later phases.
