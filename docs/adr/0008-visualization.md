# ADR-008: Visualization Boundary

- Status: Accepted
- Date: 2026-09-02

## Context

ECatVASP needs high-quality structure, trajectory, and volumetric visualization without turning crystal rendering into a core scientific-maintenance burden.

## Decision

MatterViz is the preferred visualization dependency candidate for structure, trajectory, and volumetric rendering. Visualization is a presentation concern and must consume ECatVASP DTOs rather than own scientific source-of-truth data.

A Tauri + Svelte/TypeScript desktop frontend with a local Python backend is the preferred desktop architecture, subject to implementation benchmarking. The Python scientific core must remain independently usable by CLI, tests, and API clients.

## Consequences

Frontend technology can evolve without changing permanent scientific identifiers or project storage. Figures and visual settings do not become scientific provenance sources.
