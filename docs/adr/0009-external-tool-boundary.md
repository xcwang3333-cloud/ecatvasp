# ADR-009: External Scientific Tool Boundary

- Status: Accepted
- Date: 2026-09-02

## Context

ECatVASP depends on licensed or separately distributed scientific executables and on third-party parsers whose APIs may change.

## Decision

VASP, Henkelman Bader, LOBSTER, and optional VASPKIT access must pass through explicit adapters. External tool detection, version reporting, validation, preparation, execution, and parsing must not be scattered through domain code.

POTCAR files are never committed or redistributed. Projects may store POTCAR specifications and non-secret metadata/hashes, while licensed environments materialize the actual files. LOBSTER is never bundled.

## Consequences

Licensing boundaries stay explicit and external tooling can be upgraded or replaced without contaminating the permanent domain model.
