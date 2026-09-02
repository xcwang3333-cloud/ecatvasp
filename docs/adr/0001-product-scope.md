# ADR-001: Product Scope

- Status: Accepted
- Date: 2026-09-02

## Context

ECatVASP needs a narrow scientific identity to avoid becoming a general computational-chemistry desktop application.

## Decision

ECatVASP is a VASP-first, electrocatalysis-oriented research workbench. The initial product domain covers catalyst structures and adsorption states, VASP calculation provenance, electrocatalysis workflow semantics, and later Bader/charge-density/PDOS/COHP/thermochemistry/CHE workflows.

The project does not initially target ORCA, CP2K, Quantum ESPRESSO, LAMMPS, mobile clients, or a general AI-agent platform.

## Consequences

Domain semantics take priority over generic simulation-engine abstraction. Third-party packages may be wrapped, but they do not define ECatVASP's scientific domain model.
