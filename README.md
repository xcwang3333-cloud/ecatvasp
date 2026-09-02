# ECatVASP

**ECatVASP — An Electrocatalysis-oriented VASP Research Workbench**

ECatVASP is a VASP-first research workbench for electrocatalysis. The repository is currently in **v0.1 Scientific Core / Block 1: Repository Bootstrap & Architecture Skeleton**.

## Current scope

This bootstrap intentionally contains only the engineering skeleton:

- Python package and stable module boundaries (`domain`, `structures`, `vasp`, `workflow`, `execution`, `analysis`, `thermo`, `reactions`, `storage`, `provenance`, `api`)
- project schema skeleton
- architecture decision records (ADRs)
- test, typing, lint, and CI configuration
- BSD-3-Clause licensing

It does **not** yet implement Model Studio, HPC execution, VASP recipes, Bader/PDOS/COHP, thermochemistry, or reaction free-energy calculations.

## Architecture rule

The scientific core must remain independent of the desktop UI and of any specific third-party workflow engine. Future implementation will preserve the frozen boundaries established in Phase 0.

## Development

```bash
python -m pip install -e '.[dev]'
ruff check .
mypy src
pytest
```
