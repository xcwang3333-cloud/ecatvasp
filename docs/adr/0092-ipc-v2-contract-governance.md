# ADR-092: IPC v2 Contract Governance and Drift Prevention

## Status

Accepted

## Context

ECatVASP uses a typed desktop IPC v2 boundary between the Python application layer, desktop frontend contracts, and native transport validation.

The IPC surface contains operation-specific requests and must remain fail-closed. As the number of scientific and execution operations grows, independently maintained operation catalogs create drift risk.

## Decision

The IPC v2 operation catalog remains explicit and operation-specific.

Every new operation must be synchronized across:

1. Python operation catalog;
2. TypeScript frontend contract;
3. native validator boundary;
4. regression tests.

Unknown operations must be rejected.
Wildcard RPC forwarding is prohibited.

## Consequences

Positive:

- contract drift becomes detectable before release;
- frontend/backend/native boundaries remain auditable;
- scientific and execution authority boundaries remain unchanged.

Negative:

- adding a new IPC operation requires coordinated changes across layers.

## Non-goals

This ADR does not introduce:

- generic RPC;
- schema migration;
- ProjectStore changes;
- new execution entities;
- AI agent capabilities.
