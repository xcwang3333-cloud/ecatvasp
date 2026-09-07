# ADR-083: v1.0 Windows Sidecar Packaging and Runtime Compatibility

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.0 Block 7

## Context

Blocks 1–6 established a production desktop boundary without changing ECatVASP scientific or
storage authority. The desktop shell is Tauri + Svelte/TypeScript, and the local Python backend is a
versioned stdio NDJSON process. The stable Block 6 baseline still requires a separately installed
`ecatvasp-desktop-backend` executable and `bundle.active` remains false.

v1.0 needs a Windows-first distributable that launches on a clean workstation without requiring a
system Python installation. Packaging must not turn the desktop runtime into a scientific authority,
add a network control plane, or change ProjectBundle/schema semantics.

## Decision

### 1. Windows x64 is the first packaged target

Block 7 freezes `x86_64-pc-windows-msvc` as the first production packaging target. Linux/macOS
packaging parity remains deferred. Existing Ubuntu desktop-shell CI remains mandatory because it
continues to validate the portable Rust/TypeScript contracts.

### 2. The Python backend is a PyInstaller one-file sidecar

The Windows backend is built on Windows with CPython 3.11 and PyInstaller 6.22.2. The matching
community hooks package is pinned to 2026.7. These are build/distribution dependencies only and do not
enter the Python scientific runtime dependency set.

The output name follows Tauri's external-binary contract exactly:

`ecatvasp-desktop-backend-x86_64-pc-windows-msvc.exe`

Tauri removes the target-triple suffix when placing the sidecar in the final application bundle. The
sidecar embeds its own Python interpreter and ECatVASP runtime dependencies, so the target workstation
does not need Python, NumPy, ASE, PyInstaller, or development tooling installed separately.

The build uses `--onefile`, `--clean`, `--noupx`, an explicit output directory, and a small packaging
entry point that delegates to `ecatvasp.desktop.host.main`. The sidecar protocol remains stdio NDJSON;
no HTTP server, TCP listener, database server, or browser-accessible local API is added.

### 3. Tauri Windows configuration is platform-specific

The base `tauri.conf.json` remains suitable for Linux development checks. Windows packaging is enabled
through `tauri.windows.conf.json`, which is merged by Tauri only on Windows.

The Windows overlay enables:

- Tauri bundling;
- NSIS as the first installer target;
- `binaries/ecatvasp-desktop-backend` as `bundle.externalBin`;
- current-user installation, avoiding an administrator requirement for the initial production target;
- the existing application icon.

The target-triple-specific sidecar binary is generated in CI/build output and is gitignored. Binary
build products are never committed to the repository.

### 4. Runtime sidecar discovery is explicit and non-scientific

`ECATVASP_DESKTOP_BACKEND` remains the highest-priority explicit development/diagnostic override.
Without the override, the desktop process first checks for the bundled backend next to the current
Tauri executable. If no bundled executable exists, it falls back to the development command name
`ecatvasp-desktop-backend` and normal OS command lookup.

Executable paths, process IDs, installer locations, target triples, and package artifacts are runtime
facts only. They are not scientific identity, provenance, freshness, workflow generation, or
ProjectBundle state.

### 5. Windows packaging CI is an acceptance gate

A dedicated `windows-packaging` job runs on `windows-latest` and must:

1. install CPython 3.11, Node 24, and the stable MSVC Rust toolchain;
2. install ECatVASP plus the pinned packaging requirements;
3. build the target-triple-named PyInstaller sidecar;
4. launch that frozen executable directly and verify the exact v1 desktop health handshake;
5. create a temporary schema-v3 ProjectStore and verify `open_project` through the frozen executable;
6. typecheck/test/build the Svelte desktop surface;
7. run Rust transport tests;
8. build the Tauri Windows application and NSIS installer with the external sidecar;
9. verify that the expected app executable, bundled sidecar, and NSIS installer exist;
10. upload the Windows installer as a CI artifact for inspection.

The smoke check verifies package major compatibility, IPC contract, schema version, and stateless
project requests. It does not manufacture scientific convergence or bypass application gates.

### 6. No release publication in Block 7

The produced installer is a CI artifact only. Block 7 does not create a Git tag, GitHub Release, PyPI
release, updater channel, or signing identity. Code signing and release publication require a separate
release decision after v1.0 final E2E acceptance.

## Rejected alternatives

### Require a system Python installation

Rejected because it makes desktop runtime compatibility depend on user PATH/environment state and
prevents a clean-workstation production acceptance test.

### Add a localhost HTTP service

Rejected because the existing stdio contract is sufficient and avoids network exposure, port
allocation, CORS/CSP behavior, and a second transport security boundary.

### Bundle Python as loose interpreter files managed manually

Rejected for Block 7 because it creates a larger custom launcher/resource-management surface. A frozen
PyInstaller sidecar is narrower and already matches Tauri's documented external-binary model.

### Build MSI and NSIS simultaneously

Rejected for the first Windows gate. NSIS is the narrower initial target and avoids making WiX/VBSCRIPT
availability an additional acceptance variable. MSI can be added after the Windows pipeline is stable.

## Consequences

Positive:

- clean Windows machines do not need Python;
- Python scientific/storage authority remains unchanged;
- packaging dependencies stay outside scientific runtime dependencies;
- platform-specific Tauri config does not destabilize Linux development CI;
- the actual frozen backend and actual installer become CI-tested artifacts.

Costs/risks:

- PyInstaller one-file startup is slower because it extracts its runtime before starting the long-lived
  backend process;
- unsigned executables may trigger antivirus/reputation warnings;
- installer and sidecar size increase because Python/NumPy/ASE dependencies are embedded;
- Tauri/PyInstaller/Windows native dependency updates require explicit compatibility retesting;
- signing, installer upgrade policy, and multi-architecture packaging remain future work.

## Invariants

Block 7 must preserve all previously frozen scientific boundaries. In particular:

- `SCHEMA_VERSION` remains 3;
- package version remains `1.0.0.dev0`;
- Calculation, ExecutionAttempt, and RemoteJob remain distinct;
- scheduler success never means scientific convergence;
- ProjectStore and existing Python application/scientific services remain authoritative;
- frontend/Rust/installer state never enters scientific hashes, provenance, or freshness;
- no generic mutation API is introduced;
- no network listener is introduced.

## Block 7 acceptance

Block 7 is stable only after Draft PR review, exact-head CI including the Windows packaging job,
anchored self-review, Ready/merge guard, squash merge, and exact-main post-merge CI all succeed.
