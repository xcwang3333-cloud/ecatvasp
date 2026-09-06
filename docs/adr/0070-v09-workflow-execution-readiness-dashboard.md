# ADR-070: v0.9 Block 3 Workflow and Execution Readiness Dashboard

- Status: Accepted
- Date: 2026-09-07

## Context

ADR-067 defines v0.9 as the Research Workspace and Scientific Presentation phase. ADR-068 adds a
validated project-level workspace projection, and ADR-069 exposes exact scientific inventory,
provenance, dependency, and freshness information.

The next usability gap is operational readiness. A researcher needs to see which workflow generation
is current, whether each scientific step is ready/waiting/blocked/satisfied, why a gate has that
verdict, which execution attempts and remote jobs belong to the current Calculation, and what the
existing orchestration layer identifies as the next action.

ECatVASP already owns those decisions in v0.6 contracts. `resolve_workflow_binding_generations()`
selects current append-only generations without historical fallback. `WorkflowScientificGateEvaluation`
contains authoritative step and edge scientific states, readiness values, freshness state, and reason
codes. `WorkflowOrchestrationEvaluation` contains the exact next side-effect-free handoff and reason
codes. `ExecutionAttempt` and `RemoteJob` remain persisted execution observations separate from
Calculation scientific status.

Block 3 must present those authorities without creating another workflow evaluator or collapsing
scheduler state into scientific success.

## Decision

### 1. The readiness dashboard is a pure projection

`build_workflow_readiness_dashboard()` consumes:

- one validated `ProjectBundle`;
- one exact persisted `ScientificWorkflowPlan` id;
- one existing `WorkflowScientificGateEvaluation`; and
- optionally one existing `WorkflowOrchestrationEvaluation`.

It returns immutable `WorkflowReadinessDashboard` views. The dashboard is not persisted, is not a
provenance subject, and does not become workflow history.

### 2. Scientific gate verdicts are consumed, not recomputed

The dashboard copies the existing gate projection exactly:

- `WorkflowStepScientificState`;
- `WorkflowStepReadiness`;
- freshness state;
- step reason codes;
- `WorkflowEdgeGateVerdict`;
- edge reason codes;
- accepted-structure/source binding ids where already present.

It does not infer readiness from Calculation status, scheduler state, filenames, artifact presence, or
UI heuristics.

### 3. Current workflow generation is revalidated against persisted history

A gate projection can become stale even if its selected objects still exist in `ProjectBundle`.
Therefore Block 3 calls the existing `resolve_workflow_binding_generations()` authority and requires
the gate's `binding_selections` and superseded Calculation summary to match the current persisted
append-only generation history exactly.

This is validation of an existing projection, not a second gate evaluation. Historical generations
remain visible through exact superseded binding/Calculation ids but can never silently become the
current dashboard generation.

### 4. ExecutionAttempt and RemoteJob remain separate observations

For each current workflow Calculation, the dashboard exposes its complete persisted
`ExecutionAttempt` history in attempt-number order and the exact `RemoteJob` records attached to each
attempt.

The dashboard does not select a scientific winner from scheduler observations and does not translate
scheduler `COMPLETED` into Calculation convergence or a passed workflow gate. An attempt may be
`EXITED` and a Slurm job may be `COMPLETED` while the scientific gate remains `IN_PROGRESS`,
`BLOCKED`, `STALE`, or `INVALID`.

Execution attempts/jobs belonging only to superseded Calculations are not mixed into the current
step's execution observations. Their superseded Calculation ids remain available as history metadata.

### 5. Orchestration action is optional and authoritative when supplied

When an existing `WorkflowOrchestrationEvaluation` is supplied, the dashboard exposes its exact
`WorkflowOrchestrationAction` and reason codes. The orchestration projection must belong to the same
plan and must reference the same current binding/Calculation ids as the scientific gate.

The dashboard does not independently decide whether to materialize, submit, retry, resubmit, create a
new attempt, require a new workflow plan, or request manual review.

### 6. Fail closed on stale or mismatched projections

Dashboard construction rejects:

- a missing workflow plan;
- gate projections for another plan;
- step/edge sets that do not match the canonical persisted plan;
- gate binding selections that do not match the current persisted generation chain;
- gate current ids inconsistent with their selected generation; and
- orchestration handoffs that disagree with the gate's exact current binding/Calculation.

The presentation layer therefore cannot continue showing an obsolete workflow generation as current.

### 7. No persistence, schema, scientific-identity, or runtime-dependency expansion

Readiness dashboards and their step/edge/attempt/job views are transient application data only. They
do not enter `ProjectBundle`, scientific hashes, dependency graphs, workflow identity, or provenance.

`SCHEMA_VERSION` remains 3, package version remains `0.9.0.dev0`, and no runtime dependency is added.

## Acceptance criteria

Block 3 is accepted when:

- the dashboard preserves authoritative workflow step scientific state, readiness, freshness, and
  exact gate reason codes;
- exact edge gate verdicts and reason codes are projected without reinterpretation;
- the current binding generation is verified through the existing generation resolver;
- superseded binding/Calculation ids remain visible but historical fallback is rejected;
- only execution attempts belonging to the exact current Calculation appear in its current step view;
- exact RemoteJob scheduler/state/id/directory observations remain separate from scientific status;
- a scheduler `COMPLETED` job cannot cause a scientific gate to appear passed;
- optional orchestration action/reason codes are copied exactly and rejected if they reference a
  different current generation;
- unmaterialized steps expose no invented Calculation, attempt, or job state;
- invalid/mismatched project or projection state fails closed;
- no dashboard state is persisted and `SCHEMA_VERSION` remains 3; and
- Ruff, mypy strict, pytest on Python 3.11/3.12/3.13, and MatterViz contract remain green.

## Consequences

Block 4 can build scientific visualization presentation datasets against a workspace that now has
three stable read boundaries: project summary, scientific provenance/freshness inventory, and exact
workflow/execution readiness. Frontend and reporting layers can display operational state without
traversing raw workflow history or inventing their own gate/scheduler semantics.
