# ADR-020: v0.4 ExecutionAttempt Provenance and RemoteJob Cardinality

- Status: Accepted
- Date: 2026-09-03

## Context

ADR-007 separates the scientific DAG from execution infrastructure: `Calculation` stores the
scientific task, `ExecutionAttempt` stores one actual run attempt, and `RemoteJob` stores scheduler
facts. ADR-019 completes v0.3 at an immutable `ExecutionPlan` that pins the exact Calculation,
scientific input manifest, preparation hash, safe staging inputs, license-safe POTCAR resolution
request, expected outputs, runtime constraints, and execution-only settings.

v0.4 must connect that portable handoff to persisted execution provenance without collapsing the
scientific and scheduler layers. The existing schema-1 `ExecutionAttempt` records the input manifest
hash but cannot identify which execution-only plan was consumed. Two executions may therefore share
one scientific input manifest and MethodFingerprint while legitimately differing in `NCORE`, `KPAR`,
MPI/thread intent, executable intent, or later scheduler resources.

## Decision

### ExecutionPlan-to-attempt identity

Every new v0.4 `ExecutionAttempt` created from an `ExecutionPlan` must pin:

- the exact `Calculation` id;
- `ExecutionPlan.plan_hash` as `execution_plan_hash`;
- the plan's immutable `input_manifest_sha256` as `input_manifest_hash`;
- a positive `attempt_number` unique within that Calculation;
- the immediately preceding attempt id when history exists.

`execution_plan_hash` remains optional on the frozen Domain entity so schema-1 projects and the
legacy OUTCAR importer can still be decoded conservatively. The v0.4 execution bridge, however,
fails closed if an attempt is missing the plan hash or if either hash disagrees with the supplied
plan.

An `ExecutionPlan` remains a preparation-layer immutable value object rather than a persisted Domain
entity. A later execution materialization block may emit `execution-plan.json` as an
ExecutionAttempt-produced Artifact; this ADR does not implement runtime staging or execution.

### Attempt numbering and history

Within one `Calculation`, `(calculation_id, attempt_number)` is unique. Numbers increase monotonically
but need not be gap-free, allowing imported or externally reconciled histories. A
`previous_attempt_id`, when present, must reference an attempt from the same Calculation with a lower
attempt number. Project-level storage validation enforces the same invariants before persistence.

Creating another actual run after VASP may have launched is a new `ExecutionAttempt`, even when the
scientific Calculation and MethodFingerprint are unchanged. Transport-operation retries that do not
create another run are not new attempts.

### RemoteJob cardinality

`ExecutionAttempt != RemoteJob` remains frozen. The relationship is one-to-many:

`ExecutionAttempt 1 -> N RemoteJob`

The common case is one scheduler job. Multiple RemoteJobs are permitted when infrastructure
reconciliation can prove that a later scheduler submission does not represent a second VASP run
(for example, an initial submission failed before launch). If launch cannot be disproven, a new
ExecutionAttempt is required.

Scheduler-native requeue with the same scheduler job identity remains one RemoteJob. Scheduler job
state never establishes scientific convergence.

### Scheduler state normalization

`SchedulerState.UNKNOWN` means the scheduler returned a state ECatVASP cannot normalize.
`SchedulerState.LOST` means a previously observed scheduler job can no longer be found after the
reconciliation policy's grace period. The exact polling/reconciliation policy is deferred to the
monitoring block.

### Execution artifacts

Schema v2 reserves execution Artifact types for exact provenance materialization, including execution
plans, job scripts, stdout/stderr, remote-stage manifests, retrieval manifests, and scheduler records.
Their production semantics are implemented by later v0.4 blocks; Block 1 only freezes the names and
producer boundary.

### Storage schema v2

The project schema advances from 1 to 2 because persisted execution entities gain additive provenance
semantics. The built-in v1 -> v2 migration updates the persisted `Project.schema_version`; legacy
ExecutionAttempt payloads remain valid because `execution_plan_hash` is optional and defaults to
`None`. Re-saving a migrated project canonicalizes all rows under schema v2.

This schema migration does not alter scientific identity, MethodFingerprint, permanent `atom_uid`,
or any v0.3 VASP input Artifact.

## Explicit non-scope

Block 1 does not implement:

- SSH or credential management;
- remote directory creation or staging;
- licensed POTCAR resolution on an execution host;
- LocalExecutor or VASP process launch;
- Slurm/PBS/LSF job scripts, submission, polling, or cancellation;
- scheduler resources or execution targets;
- retrieval;
- automatic INCAR correction or restart mutation;
- scheduler DAG or batch dispatch.

## Consequences

- every new v0.4 attempt can be traced to the exact immutable execution handoff it consumed;
- legacy execution records remain readable without pretending they had plan provenance;
- attempt-history corruption is rejected at the project persistence boundary;
- scheduler retries can evolve without changing Calculation or MethodFingerprint identity;
- later execution adapters inherit a strict provenance contract before any SSH or scheduler code is
  introduced.
