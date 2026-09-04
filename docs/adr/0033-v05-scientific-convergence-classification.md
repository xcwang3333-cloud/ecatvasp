# ADR-033: v0.5 Scientific Convergence Classification Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

ADR-030 separates parser-normalized VASP facts from scientific convergence verdicts, and ADR-032
implements only the energy/metadata parser. Block 4 must now decide whether an exact parsed result
provides sufficient evidence for electronic and, where applicable, ionic convergence without
collapsing scheduler completion, file presence, parser success, or a single text marker into a
scientific success claim.

The legacy v0.1 importer treated the presence of `aborting loop because EDIFF is reached` and
`reached required accuracy` as direct booleans. That is too weak for the permanent result layer:
a relaxation OUTCAR can contain an EDIFF marker for an earlier ionic step while a later electronic
cycle exhausts NELM, and a normally terminated relaxation can finish only because NSW was exhausted.
Partial or truncated output also needs a durable `INDETERMINATE` path rather than being guessed into
`FAILED` or `UNCONVERGED`.

The design was cross-checked against aiida-vasp, which exposes electronic and ionic convergence as
separate run-status facts and considers run completeness plus electronic/ionic step limits. ECatVASP
does not import that parser or workflow model; it retains its own immutable Analysis/Artifact and
MethodFingerprint boundaries.

## Decision

### 1. Raw convergence evidence and verdict classification are separate operations

Block 4 introduces `VaspConvergenceEvidence` and two public functions:

- `collect_vasp_convergence_evidence()` reads only convergence-specific facts from the exact Block 2
  intake and cross-checks them against the Block 3 `VaspResultDocument`;
- `assess_vasp_convergence()` is a pure recipe-aware classifier that consumes a Calculation,
  MethodFingerprint, and exact evidence object and returns `VaspConvergenceAssessment`.

Evidence collection assigns no scientific verdict. Classification performs no filesystem access and
no lifecycle mutation.

### 2. Evidence remains bound to exact scientific identity

The evidence records the exact Calculation id, intake hash, CalculationType, and recipe id. The
classifier requires:

- `evidence.calculation_id == Calculation.id`;
- the Calculation to reference the exact supplied MethodFingerprint id;
- Calculation, MethodFingerprint, and evidence recipe ids to agree;
- the CalculationType to match both evidence and the canonical recipe registry;
- the fingerprinted recipe version to match the canonical recipe version.

Evidence from a scientifically similar but distinct Calculation cannot be reused implicitly.

### 3. Convergence-specific file reads are fail-closed and content-addressed

The collector re-resolves OUTCAR/OSZICAR beneath `project_root` and revalidates byte size and
SHA-256 before using them. This closes the parse-to-convergence TOCTOU gap in the same way Block 3
closes intake-to-parse drift.

OUTCAR supplies the observed NELM and NSW values. Multiple distinct values are treated as ambiguous
concatenated/mixed output and are rejected. OSZICAR supplies the observed ionic-step count, final
electronic iteration, and maximum electronic iteration encountered.

### 4. Affirmative electronic convergence requires complete evidence

Electronic convergence is classified in this order:

1. if normal OUTCAR termination is not observed, verdict is `INDETERMINATE`;
2. if final `free energy TOTEN` is absent, verdict is `INDETERMINATE`;
3. if NELM is known and any observed electronic cycle reaches or exceeds NELM, verdict is
   `UNCONVERGED`;
4. otherwise an explicit `EDIFF is reached` observation yields `CONVERGED`;
5. otherwise verdict is `INDETERMINATE`.

NELM exhaustion therefore outranks a global EDIFF marker and prevents an earlier ionic step from
creating a false-positive final electronic verdict. Missing evidence is not silently interpreted as
failure.

### 5. Ionic convergence is recipe-aware

For `RELAX` and `GAS_RELAX`:

- an explicit `reached required accuracy` marker yields `CONVERGED` when the run is complete and the
  observed NSW does not contradict the fingerprinted recipe;
- absence of that marker plus observed ionic steps reaching the exact recipe NSW yields
  `UNCONVERGED`;
- absence of both a convergence marker and NSW exhaustion yields `INDETERMINATE`;
- incomplete output yields `INDETERMINATE`.

For STATIC, DOS/charge/LOBSTER prerequisite, and finite-difference frequency calculations, ionic
convergence is `NOT_APPLICABLE`. Frequency-mode validity is deferred to Block 7 and is not hidden
inside ionic convergence.

The exact expected NSW is derived from the canonical fingerprinted recipe semantics: relax defaults
to 200, frequency to 1, static-like recipes to 0, with only valid RecipeIdentity overrides accepted.

### 6. Runtime NSW disagreement blocks the overall scientific verdict

If OUTCAR reports an NSW that differs from the exact fingerprinted recipe expectation, the overall
verdict is `INDETERMINATE` even when electronic convergence is otherwise affirmative. For non-relax
calculations the ionic component remains `NOT_APPLICABLE`, but a scientific-input identity conflict
cannot still produce overall `CONVERGED`.

### 7. Overall verdict combination is conservative

- any applicable `UNCONVERGED` component makes overall `UNCONVERGED`;
- electronic `CONVERGED` plus ionic `CONVERGED` or `NOT_APPLICABLE` makes overall `CONVERGED`;
- every other combination is `INDETERMINATE`;
- an observed recipe/runtime NSW mismatch forces overall `INDETERMINATE`.

The classifier never manufactures a `CalculationScientificStatus`.

### 8. No lifecycle reconciliation in Block 4

Block 4 does not mutate:

- `Calculation.status`;
- `ExecutionAttempt.status`;
- scheduler state;
- StructureVariant/current structure;
- result Artifact persistence.

The assessment remains a scientific value contract. Durable CONVERGENCE Analysis persistence and
Calculation scientific-status reconciliation are handled later with provenance/freshness work.

### 9. No schema migration or new dependency

Project schema remains version 2. No runtime parser dependency is added. The implementation uses the
existing result contracts, exact raw Artifacts, recipe registry, and MethodFingerprint identity.

## Non-scope

Block 4 does not add:

- Calculation scientific-status reconciliation;
- RESULT_PARSE or CONVERGENCE Analysis persistence;
- PARSED_RESULT persistence;
- force, stress, magnetization, eigenvalue, DOS, or frequency-mode parsing;
- CONTCAR atom-UID reconstruction or structure promotion;
- automatic restart, correction, or continuation;
- scientific workflow orchestration;
- thermochemistry, CHE, or free-energy diagrams;
- GUI work, tag, GitHub Release, or PyPI publication.

## Consequences

v0.5 now has an explicit boundary from raw execution artifacts through normalized parser facts to a
recipe-aware scientific convergence verdict, while `INDETERMINATE` remains a first-class outcome for
partial, ambiguous, or identity-conflicting evidence. Block 5 can add force and magnetization data
without changing convergence semantics, and later provenance work can persist/reconcile the verdict
without reinterpreting scheduler state.
