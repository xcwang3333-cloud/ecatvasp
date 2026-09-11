# ECatVASP Architecture Audit — 2026-09-11

## Live baseline

GitHub repository `xcwang3333-cloud/ecatvasp` is public, active, and defaults to `main`. Live `main` HEAD is `29615dc24378f19a2c8d8bbdee1ee5671cfbf375`; local checkout matches it and was clean before this branch. Open work is Issue #109 (v1.2 roadmap), #118 (IPC v2 governance), and PR #119 (IPC v2 governance). The latest main CI run for this HEAD succeeded; PR #119 has a successful IPC drift run and a CI run in progress.

## Architecture

The tree is a Python package under `src/ecatvasp`, a Svelte/Tauri desktop under `ui/desktop`, JSON schema, ADRs, and a large focused pytest suite. Python owns domain entities, ProjectStore, VASP input/result semantics, workflow DAG planning/gates/recovery, provenance/freshness, electronic analysis, thermochemistry, and reporting. `execution` separates local/SSH/Slurm staging, monitoring, retrieval, recovery, and transient site preflight. The CLI is a thin facade over ProjectStore. Tauri/Rust and TypeScript validate typed transport, route requests, and render presentation; the frontend has no scientific authority.

ProjectStore persists a schema-3 ProjectBundle in SQLite with an integrity manifest and explicit v1→v2→v3 migration. Scientific identity, immutable artifact bytes, dependency DAG and freshness are validated at the Python boundary. `Calculation`, `ExecutionAttempt`, and `RemoteJob` remain separate namespaces; scheduler completion is not convergence.

## IPC v2

Python defines `DESKTOP_IPC_V2_CONTRACT_VERSION` and an explicit `DesktopV2Operation` catalog. Decoders reject unknown fields and operations, enforce request IDs and typed operation payloads, and the stdio host emits correlated error frames while continuing after decode errors. Tauri/Rust mirrors the catalog and validates nested fields, hashes, UUIDs, scheduler and execution contracts. PR #119 adds a catalog-governance marker/workflow. The audit found one concrete gap: the Python site-preflight enum checker performed set membership on arbitrary JSON values, so arrays/objects raised an unhandled TypeError and terminated the sidecar. Issue #120 and this branch fix it with regression coverage. Explicit null handling remains a compatibility decision tracked in #120.

## Scientific workflow

The implemented flow is structure/model construction → deterministic workflow recipe planning → VASP INCAR/POSCAR/KPOINTS/POTCAR specification and immutable materialization → local or SSH/Slurm execution → scheduler/attempt monitoring and recovery → integrity-checked retrieval → Python result parsing/convergence and provenance → electronic/thermochemical/reaction analyses and reports. Real institutional credentials and licensed POTCAR bodies remain site-local. Real-site preflight is transient and does not mutate ProjectStore.

## Issues generated

- #120: reject malformed site-preflight enum values with recoverable IPC errors (P1; selected implementation).
- #121: define bounded OpenSSH preflight and execution timeout semantics (P2).
- #122: record reproducible dependency and CI installation policy (P3).
- #123: handle CLI project compatibility errors without tracebacks (P2).

## Recommended sequence

Merge the small #120 hardening first; then specify #121 timeout/reconciliation semantics before modifying transport; implement #123 with storage-exception regression fixtures; address #122 as governance work. Advanced Study/schema-4 work remains architecture research per ADR-091.
