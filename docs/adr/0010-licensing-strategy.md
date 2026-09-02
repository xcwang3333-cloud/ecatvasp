# ADR-010: Licensing Strategy

- Status: Accepted
- Date: 2026-09-02

## Context

The project should remain broadly reusable while avoiding accidental inheritance of incompatible copyleft obligations from benchmark projects.

## Decision

ECatVASP is licensed under BSD-3-Clause. CatGo and other projects may be studied as architecture or UX references, but source code with incompatible licensing is not copied into ECatVASP. Direct dependency licenses must be reviewed before they are introduced.

Licensed VASP resources, POTCAR content, LOBSTER binaries, credentials, and other non-redistributable assets are excluded from repository content.

## Consequences

The project retains a permissive license and clear provenance for external dependencies. Dependency additions become an explicit review point.
