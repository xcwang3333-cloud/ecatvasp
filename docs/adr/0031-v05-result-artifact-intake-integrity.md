# ADR-031: v0.5 Result Artifact Intake and Integrity Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

ADR-030 defines result parsing as a scientific `Analysis` that consumes exact raw VASP
Artifacts. v0.4 already defines output roles, Artifact types, relative paths, retrieval policies,
and required/optional semantics in `ExecutionPlan.expected_outputs`, and ADR-026 explicitly
requires retrieval to preserve that contract without inventing a second output model.

The missing boundary is deciding when execution-produced output metadata is sufficiently exact and
locally trustworthy to become input to a scientific parser. File presence alone is insufficient:
Artifacts may be remote-only, missing, stale on disk, associated with another ExecutionAttempt, or
carry metadata that no longer matches the underlying bytes.

## Decision

### 1. ExecutionPlan remains the authoritative output contract

v0.5 result intake reuses the exact `ExecutionPlan.expected_outputs` attached to the supplied
ExecutionAttempt. It does not define a parallel table of output paths or retrieval rules.

The gate requires:

- one exact `Calculation`;
- one exact `ExecutionPlan` for that Calculation;
- one exact `ExecutionAttempt` pinned to the plan hash and input-manifest hash;
- attempt-produced output Artifacts whose Artifact type and retrieval policy match the exact plan;
- verified local bytes for every source admitted to parsing.

### 2. OUTCAR is the canonical minimum source

The plan must declare `outcar` as required `ArtifactType.OUTCAR`, and a locally available,
content-addressed OUTCAR is mandatory for every managed scientific-result intake.

`oszicar` is optional when declared. A missing or remote-only optional OSZICAR does not block the
intake and is not guessed into the source set.

For `RELAX` and `GAS_RELAX` Calculations, the exact plan must declare required `contcar`, and the
CONTCAR must already be locally available and integrity-verified. This establishes a safe future
input for Block 6 structure reconstruction without promoting the structure here.

`vasprun_xml` is supported as an optional source role only when the exact ExecutionPlan explicitly
declares it. Block 2 deliberately does not mutate the frozen v0.3 plan generator to add a new
expected output retroactively. A later plan/parser extension may opt in to the source without
changing the Block 2 gate.

### 3. Parse-ready means local and byte-verified

A source is admitted only when its Artifact availability is `LOCAL` or `BOTH` and it carries:

- a normalized project-relative local path;
- SHA-256;
- byte size.

The gate resolves the path under the supplied project root, rejects path traversal or symlink
escape, re-reads the file, recomputes byte size and SHA-256, and fails closed on any mismatch.

`REMOTE`, `MISSING`, or `ARCHIVED` optional sources remain outside the intake. Required sources in
those states block the intake. The gate performs no download, remote deletion, file copy, or
retention decision; ADR-026 remains authoritative for those operations.

### 4. Attempt state is evidence availability, not scientific success

Result intake is permitted for `EXITED`, `RETRIEVING`, `PARSED`, `FAILED`, and `CANCELLED`
ExecutionAttempts when the required raw sources are actually available. This allows later parser
and convergence logic to inspect partial or failed runs without relabelling them successful.

`CREATED`, `STAGING`, `QUEUED`, and `RUNNING` attempts are not parse-ready.

The intake object deliberately contains no `CalculationScientificStatus`, convergence verdict, or
scheduler-success claim.

### 5. Intake identity is deterministic and location-independent

`VaspResultArtifactIntake` records Calculation identity/type, recipe, attempt identity/number,
plan hash, input-manifest hash, exact source Artifact ids/types/digests/sizes, expected output paths,
and retrieval policies.

Its `intake_hash` excludes the local project storage path, so moving a verified project tree does
not change the semantic intake identity. The local path remains available transiently to the parser
adapter.

### 6. No automatic directory scanning or local-output invention

Block 2 consumes persisted `Artifact` metadata. It does not scan arbitrary execution directories or
synthesize output Artifacts from untracked files. Remote retrieval already materializes managed
Artifacts under ADR-026; any future local-output materialization must feed the same Artifact gate
rather than bypass it.

### 7. No storage-schema migration

The intake package is a transient VASP-layer value object, not a new ProjectBundle entity. Project
schema remains version 2. Durable scientific outputs continue to use `Analysis`, `Artifact`,
`ProvenanceRecord`, and `DependencyRecord`.

## Non-scope

Block 2 does not add:

- concrete OUTCAR, OSZICAR, vasprun.xml, or CONTCAR parsing;
- energy extraction;
- force or magnetization extraction;
- convergence classification;
- Calculation scientific-status mutation;
- CONTCAR atom-UID reconstruction or structure promotion;
- retrieval, remote retention, or local-output collection;
- DOS/PDOS, Bader, charge-density difference, COHP, or LOBSTER analysis;
- thermochemistry or scientific workflow orchestration;
- automatic correction, restart, or continuation;
- schema migration, GUI work, tag, GitHub Release, or PyPI publication.

## Consequences

Block 3 can now implement concrete parser adapters against a small, deterministic,
content-addressed source bundle instead of filesystem guesses. Failed or partial executions can be
scientifically inspected without conflating evidence availability with convergence or execution
success, and all future parser sources remain subordinate to the existing ExecutionPlan/Artifact
lifecycle.
