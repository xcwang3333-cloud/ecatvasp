# ECatVASP

**ECatVASP — An Electrocatalysis-oriented VASP Research Workbench**

ECatVASP is a VASP-first research workbench for electrocatalysis. It connects structure preparation, VASP calculations and workflows, execution, result intake, electronic-structure analysis, thermochemistry, and electrocatalytic reaction pathways while keeping scientific identity, provenance, convergence, and freshness explicit.

The current development line is Python `1.1.0.dev0` and Windows desktop/Tauri `1.1.0-dev.0`. The planned v1.1 implementation and portable acceptance work through Blocks 1–9 is complete, but **v1.1.0 has not been formally tagged or released, and ECatVASP has not been published to PyPI**. See [CHANGELOG.md](CHANGELOG.md) for the accepted development baseline and release status.

## Research workflow

`model → calculation → workflow → HPC → retrieval → result → structure promotion → electronic analysis → thermochemistry → reaction pathway`

ECatVASP currently provides:

- **Model Studio and structures** — structure import/export and electrocatalyst-oriented construction tools for building durable scientific models.
- **VASP preparation and workflows** — calculation recipes, preflight validation, VASP input materialization, and deterministic multi-step workflow planning and continuation.
- **Execution and Job Center** — local and SSH/Slurm execution boundaries, submission/monitoring/retrieval models, retry/recovery semantics, and project-facing job status.
- **Result Center** — provenance-aware result intake, parser-backed scientific convergence classification, forces/magnetization and frequency result handling, freshness checks, and explicit promotion of accepted output structures.
- **Electronic analysis** — DOS/PDOS, Bader analysis, charge-density difference analysis, LOBSTER COHP/ICOHP intake, and band-center descriptors.
- **Thermochemistry** — harmonic surface/adsorbate thermochemistry, ideal-gas reference thermochemistry, and explicit reference-correction policies.
- **Electrocatalysis free energies** — computational hydrogen electrode (CHE) semantics and durable reaction-pathway/free-energy presentation for HER, associative ORR, associative OER, and CO2-to-CO workflows.
- **Windows desktop** — a Tauri/Svelte desktop application with a bundled frozen Python backend. Portable CI builds the backend and Tauri application, creates an NSIS installer, installs that exact installer silently, and exercises installed desktop/backend scientific acceptance.
- **Durable project state** — schema-3 `ProjectStore` persistence with scientific provenance, identity, freshness, reopen/restart, and tamper/drift boundaries.

## Scientific authority and boundaries

ECatVASP deliberately separates execution state from scientific interpretation:

- `ProjectStore` is the durable authority for project state. The current durable schema is **3**.
- Python domain/application code is the scientific authority. The desktop frontend consumes typed projections and actions; it does not independently infer scientific results, convergence, freshness, scientific identity, or provenance.
- Scheduler completion is an execution fact, **not** proof of VASP scientific convergence. Scientific convergence is classified from the retrieved scientific result by the Python authority.
- SSH/Slurm support is implemented, but acceptance against a real institutional VASP cluster is site-specific. Portable CI does not claim to validate institutional credentials, scheduler configuration, POTCAR deployment, or cluster policy.
- Desktop IPC v1 remains a frozen compatibility contract; the current desktop uses typed, operation-specific IPC v2 for supported application actions.
- The Windows installer uploaded by GitHub Actions is a CI artifact and acceptance record. It is **not** a GitHub Release or a formal `v1.1.0` distribution.

## Development

Python development requires Python 3.11 or newer:

```bash
python -m pip install -e '.[dev]'
ruff check .
mypy src
pytest
```

For the desktop shell, Node.js 24 or newer and Rust are required:

```bash
cd ui/desktop
npm install --no-audit --no-fund
npm run check
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml --lib
cargo check --manifest-path src-tauri/Cargo.toml
```

Windows frozen-backend, NSIS packaging, exact-install, and installed-product acceptance are enforced by the repository CI rather than implied by a source-only desktop build.

## Project documentation

- [CHANGELOG.md](CHANGELOG.md) records durable development/release baselines and distribution status.
- [CONTRIBUTING.md](CONTRIBUTING.md) describes contribution checks and frozen authority boundaries.
- [docs/adr](docs/adr/README.md) contains the architecture decision record index and the detailed contracts behind the workbench.

ECatVASP is licensed under the BSD 3-Clause License.
