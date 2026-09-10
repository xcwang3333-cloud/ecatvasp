# Changelog

This file records durable ECatVASP development and release baselines. An entry here does not by itself declare a public release.

## v1.1 development baseline — 2026-09-10

Status: planned v1.1 implementation and portable acceptance are complete; formal release remains pending.

### Accepted implementation baseline

- v1.1 Blocks 1–9: complete.
- Closing implementation PR: #105.
- Accepted `main`: `9c1d76a88d71b7a38143430f44c6d25792144470`.
- Exact post-merge CI: CI #655 / Run `34422167542`, `completed/success`, on that exact `main` SHA.
- Installed-product acceptance: ADR-090, **v1.1 Installed Desktop Scientific E2E Acceptance**, accepted.

### Frozen compatibility and authority state

- Python development version: `1.1.0.dev0`.
- Desktop/Tauri development version: `1.1.0-dev.0`.
- Durable project schema: `SCHEMA_VERSION = 3`.
- Desktop IPC v1 remains frozen as the compatibility contract.
- Desktop IPC v2 is typed and operation-specific.
- `ProjectStore` remains the durable project authority.
- Python remains the scientific authority; scheduler completion does not imply scientific convergence, and the frontend does not independently infer scientific results.

### Distribution evidence

The Windows GitHub Actions pipeline has demonstrated the following on the accepted v1.1 development line:

- frozen Python backend build;
- Tauri desktop build;
- NSIS installer packaging;
- silent installation of the exact NSIS installer produced by the current commit;
- installed-path desktop/backend scientific end-to-end acceptance;
- upload of the NSIS installer as a GitHub Actions artifact.

A GitHub Actions artifact is CI/build evidence, not a formal software release.

### Publication state

As of this baseline, ECatVASP v1.1 has **not** been finalized as `1.1.0`, tagged as a formal v1.1 release, published as a GitHub Release, or published to PyPI. The repository remains on development versions.

Issue #106 tracks the release-readiness closure. Repository truth, durable history, version/release metadata, and distribution documentation are Audits A–D. Real VASP + institutional SSH/Slurm acceptance remains the separate site-specific Audit E, and the release/no-release choice remains Audit F; therefore completion of A–D does not close #106.
