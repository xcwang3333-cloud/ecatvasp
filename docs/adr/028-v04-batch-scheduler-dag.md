# ADR-028: v0.4 Batch Dispatch and Scheduler DAG Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

Blocks 1-8 established exact `ExecutionPlan -> ExecutionAttempt -> RemoteJob` provenance, secure
SSH/Slurm execution, monitoring, retrieval, and fail-closed recovery classification. v0.4 now
needs to dispatch many already-prepared calculations without collapsing scheduler orchestration
into the scientific DAG or creating duplicate attempts/jobs after an orchestrator restart.

A batch layer is especially dangerous if it can infer scientific dependencies, silently retry a
failed VASP run, or issue scheduler side effects before durable attempt provenance exists.

The design was cross-checked against jobflow-remote's explicit job graph and batch-manager state
separation. ECatVASP intentionally keeps a smaller execution-only contract and does not adopt
jobflow-remote as a domain or runtime dependency.

## Decision

### Scheduler DAG is not the scientific DAG

`SchedulerDag` lives in `ecatvasp.execution`. It is an immutable execution-order value object,
not a scientific Domain entity and not a ProjectBundle schema object.

Every `SchedulerDagNode` must contain an already-existing, exact:

- `Calculation`; and
- portable immutable `ExecutionPlan` bound to that Calculation.

A node therefore cannot create a scientific dependency. `depends_on` means only "do not dispatch
this already-prepared node until the upstream execution-order node has exited." It never rewrites
the downstream structure, MethodFingerprint, input manifest, Recipe, or ExecutionPlan.

If a downstream scientific calculation actually consumes an upstream result such as CONTCAR,
that scientific handoff must first create the appropriate new `StructureSnapshot`, `Calculation`,
MethodFingerprint/input artifacts, and ExecutionPlan outside this scheduler DAG.

### Deterministic DAG contract

The scheduler DAG requires:

- portable unique node ids;
- exactly one node per Calculation;
- dependencies that reference existing nodes;
- no self-dependencies;
- an acyclic graph;
- deterministic lexical topological ordering; and
- a deterministic `dag_hash` derived from node id, Calculation id, exact ExecutionPlan hash, and
  scheduler-order dependencies.

No Slurm array representation is introduced in v0.4.

### Resume from persisted execution facts

`reconcile_batch_dispatch()` is side-effect free. It reconstructs one batch snapshot from
persisted `ExecutionAttempt` and `RemoteJob` facts.

For the node's current exact ExecutionPlan:

- no attempt -> `READY`;
- `CREATED` -> `RESERVED` and the exact attempt may be continued;
- `STAGING` -> `STAGING` and no duplicate stage/submission is created automatically;
- `QUEUED` -> `QUEUED`;
- `RUNNING` -> `RUNNING`;
- `EXITED`, `RETRIEVING`, or `PARSED` -> scheduler-order `COMPLETE`;
- `FAILED` or `CANCELLED` -> `RECOVERY_REQUIRED`.

A scheduler-order `COMPLETE` state explicitly does **not** mean scientific convergence. It is only
sufficient to release another already-independent/prepared scheduler node.

A latest attempt with no v0.4 plan hash or a different plan hash becomes `STALE_PLAN` unless an
explicit Block 8 recovery decision authorizes the new attempt/plan transition.

`QUEUED`/`RUNNING` attempts require persisted RemoteJob evidence. CREATED/STAGING attempts may not
already own a RemoteJob. Terminal/post-exit attempts may not conflict with an active or uncertain
RemoteJob. Inconsistent snapshots fail closed rather than guessing whether a scheduler submission
exists.

### Dependency failure propagation

A not-yet-dispatched node:

- becomes `READY` only when every scheduler-order dependency is `COMPLETE`;
- remains `WAITING_DEPENDENCIES` while upstream work is pending/active; and
- becomes `BLOCKED_DEPENDENCY` when an upstream node needs recovery or has stale plan provenance.

Batch orchestration never converts an upstream execution failure into scientific failure and never
automatically repairs descendants.

### Concurrency

`BatchConcurrencyPolicy.max_active` limits nodes in:

- `RESERVED`;
- `STAGING`;
- `QUEUED`; or
- `RUNNING`.

Existing active work is never cancelled merely because a later policy lowers the limit. New
attempts are selected deterministically in topological order only from remaining capacity.

### Two-phase batch dispatch

`prepare_batch_dispatch_wave()` does not stage files or call a scheduler. It produces
`BatchDispatchTicket` values.

For a brand-new node it creates a `CREATED` ExecutionAttempt pinned to the exact ExecutionPlan.
The caller **must persist that new attempt before any SSH staging or scheduler submission**. Only
then may existing Block 4/5 functions perform remote side effects.

This ordering makes restart behavior fail closed:

1. if the attempt was persisted before a crash, the next batch pass sees `RESERVED` and reuses the
   exact CREATED attempt rather than allocating another attempt;
2. if staging had already advanced to persisted STAGING/QUEUED/RUNNING state, the batch layer does
   not allocate or submit a duplicate;
3. ambiguous staging/recovery is delegated to the existing integrity/recovery boundaries rather
   than guessed by the batch layer.

### Recovery gate cannot be bypassed

FAILED/CANCELLED attempts and changed ExecutionPlans do not become automatically READY.

A new batch attempt after failure or execution tuning requires an explicit Block 8
`RecoveryDecision` whose action is `NEW_EXECUTION_ATTEMPT` and whose source/target hashes match the
persisted latest attempt and current node plan.

- same-plan retry requires source-plan equality;
- execution-only tuning requires the decision source plan to match the previous attempt and its
  target execution hash to match the new ExecutionPlan;
- the latest attempt must be terminal before a new attempt is created;
- any decision requiring a new Calculation or changing scientific identity is rejected by the
  scheduler DAG.

Same-attempt scheduler replacement (`RESUBMIT_SAME_ATTEMPT`) is not automated by Block 9 because
it requires positive no-VASP-launch evidence plus a verified reusable stage. It remains governed
by ADR-027 and lower-level execution contracts.

## Consequences

- scientific DAG identity remains independent from scheduler topology;
- batch restarts are idempotent with respect to attempt allocation and known RemoteJobs;
- concurrency limits never create scientific identity changes;
- failed calculations cannot be silently retried by batch orchestration;
- execution-only recovery can re-enter batch dispatch only through explicit Block 8 provenance;
- batch tickets compose with existing staging/submission/monitoring/retrieval functions rather
  than duplicating them.

## Explicit non-scope

Block 9 does not add Slurm arrays, scheduler-native dependency flags, concrete PBS/LSF adapters,
automatic VASP correction, automatic same-attempt resubmission, scientific result parsing,
CONTCAR continuation/import, a persistent daemon/service, distributed locks, multi-process queue
ownership, GUI, tag, GitHub Release, or PyPI publication.
