# ADR-002: Scientific Core Architecture

- Status: Accepted
- Date: 2026-09-02

## Context

The scientific core must be reusable by a future desktop UI, CLI, API, and test suite without depending on any frontend framework or workflow engine.

## Decision

The Python package is organized into explicit boundaries: domain, schema, structures (added in a later block), vasp, workflow, execution, analysis, thermo, reactions, storage, provenance, and api.

The scientific core must not depend on Tauri or Svelte. UI DTOs and external-tool adapters will isolate frontend and third-party implementation details.

## Consequences

The repository can add Tauri/Svelte later without contaminating scientific logic. Replacing a workflow or visualization dependency will not require changing permanent scientific identifiers or project schemas.
