# ADR-004: Scientific Workflow DAG

- Status: Accepted
- Date: 2026-09-02

## Context

A scheduler job graph is not the same as the scientific dependency graph required for electrocatalysis research.

## Decision

Scientific workflows use four conceptual node families: StructureNode, CalculationNode, AnalysisNode, and AggregateNode. Scientific dependencies are independent of scheduler and resource settings. Calculation scientific state, ExecutionAttempt state, and scheduler state are separate.

The lifecycle must distinguish READY, BLOCKED, CONVERGED, COMPLETED_UNCONVERGED, FAILED, STALE, and INVALID conditions and must preserve retry history. Scientific gates such as convergence, structure integrity, method compatibility, artifact completeness, charge-grid compatibility, and frequency-mode checks are first-class workflow logic.

## Consequences

Independent branches can complete partially, failed jobs can be retried without overwriting history, and stale propagation can be limited to affected scientific descendants.
