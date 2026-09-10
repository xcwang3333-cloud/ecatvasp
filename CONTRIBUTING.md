# Contributing

ECatVASP is currently on the v1.1 development line. The planned v1.1 implementation and portable acceptance work through Blocks 1–9 is complete, but the project has not yet made a formal `v1.1.0` release.

Before opening a pull request, run the Python quality gates:

```bash
ruff check .
mypy src
pytest
```

If the desktop application is affected, also run the source-level desktop checks that are practical on your platform:

```bash
cd ui/desktop
npm install --no-audit --no-fund
npm run check
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml --lib
cargo check --manifest-path src-tauri/Cargo.toml
```

The repository CI is authoritative for the Windows frozen-backend, Tauri/NSIS packaging, exact silent-install, and installed-product acceptance path.

Changes must preserve the current authority boundaries unless an explicit architecture decision intentionally revises them:

- `ProjectStore` is the durable project authority; the current durable schema is 3.
- Python domain/application code is the scientific authority; frontend code must not independently infer scientific results, convergence, freshness, scientific identity, or provenance.
- Scheduler completion is not scientific convergence.
- Desktop IPC v1 is a frozen compatibility contract; IPC v2 remains typed and operation-specific.
- Scientific defaults, schema semantics, domain boundaries, provenance, freshness, and durable identity behavior must not change silently.
- Institutional SSH/Slurm credentials, POTCAR deployment, and cluster-specific policy belong to site-specific acceptance, not portable CI.

Material architecture or scientific-semantics changes require an ADR and focused tests. Repository-truth, governance, packaging, and release-metadata changes should remain narrowly scoped and must not imply a public release unless a separate release decision has been made.
