# ADR-007: HPC Execution Boundary

- Status: Accepted
- Date: 2026-09-02

## Context

Scientific workflows must remain valid regardless of whether calculations run locally or on a remote scheduler.

## Decision

The scientific DAG produces an ExecutionPlan consumed by execution adapters. Initial execution targets are local execution and SSH/Slurm; jobflow/jobflow-remote remain implementation candidates rather than domain dependencies. Recovery mechanisms may use custodian-style handlers, but every correction must be recorded and no scientific change may occur silently.

RemoteJob stores scheduler facts; ExecutionAttempt stores one actual run; Calculation stores the scientific task.

## Consequences

Scheduler replacements do not alter the scientific schema. Retry, resume, staging, and remote artifact handling can evolve independently from reaction and provenance logic.
