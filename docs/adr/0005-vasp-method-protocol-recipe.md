# ADR-005: VASP Method, Protocol, Recipe, and Execution Boundaries

- Status: Accepted
- Date: 2026-09-02

## Context

VASP input parameters mix physical-method choices, numerical convergence choices, task-specific requirements, and execution-only performance settings. Treating them as one undifferentiated INCAR configuration makes compatibility and provenance unreliable.

## Decision

ECatVASP separates Method, Protocol, Recipe, and Execution settings. Method includes choices such as XC, POTCAR identity, dispersion, DFT+U, spin treatment, SOC, and solvation. Protocol includes numerical choices such as ENCUT, k-point policy, PREC, EDIFF, EDIFFG, smearing, and dipole policy. Recipe defines the scientific task. Execution contains performance and scheduler settings such as NCORE, KPAR, nodes, cores, memory, and walltime.

`ECATVASP_ECAT_STANDARD` uses `EDIFFG = -0.02 eV/Å` as its candidate force criterion. ENCUT and k-point density are not universal constants and must support convergence validation and project-level locking.

## Consequences

Execution tuning does not invalidate scientific results, while method changes cannot silently reuse incompatible reference energies. Recipe APIs remain stable even if implementation backends change.
