# ADR-0092 Implementation Notes: IPC v2 Contract Governance

## Status
Accepted

## Decision
IPC v2 operations remain explicitly enumerated. Python, TypeScript, and native validation layers must maintain operation parity.

## Enforcement
- Reject unknown operations.
- Detect contract drift in CI.
- Avoid wildcard dispatch.

## Scope Boundary
This ADR does not change ProjectStore, ExecutionAttempt, RemoteJob, Artifact, or schema versioning.
