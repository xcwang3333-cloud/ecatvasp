# ADR-080: v1.0 Desktop Project Lifecycle and Local Preferences

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.0 Block 4

## Context

ADR-077 selected a production desktop workspace while freezing schema v3 and all scientific identity,
provenance, freshness, workflow, and ProjectStore authorities. ADR-079 then added the first Tauri +
Svelte/TypeScript shell and a typed read-only client for `ecatvasp-desktop-ipc-v1`.

The desktop now needs a real project-open lifecycle. That lifecycle must remember useful local UX state
without creating a second project session authority or writing desktop state into the scientific
project bundle.

The existing Python contract already provides the required validation seam: every project-scoped
request carries an explicit `project_root`, and `open_project` reopens and validates the current
ProjectStore. No Python storage/schema extension is required for Block 4.

## Decision

### 1. The Python backend remains the project authority

A project becomes selected in the desktop only after the Python `open_project` operation succeeds.
The desktop does not infer validity from path existence, filenames, SQLite contents, previous local
preferences, or a previously successful session.

Every switch or restart revalidates the exact project path through the backend. The Tauri process and
Svelte application do not retain a ProjectBundle or authoritative current-project session.

### 2. Desktop preferences use a separate local contract

Block 4 adds the local-only contract:

`ecatvasp-desktop-preferences-v1`

It contains only:

- `current_project_root: string | null`;
- `recent_project_roots: string[]`;
- the preferences contract version.

The recent list is an ordered MRU list with a maximum of eight exact paths and no duplicates.
Preferences contain no project metadata, scientific hashes, analysis state, workflow generations,
readiness verdicts, scheduler state, provenance, or freshness facts.

### 3. Preferences live in the operating-system app-config directory

Tauri/Rust owns the preferences file and resolves its location through Tauri's `app_config_dir`.
The file is named `desktop-preferences-v1.json`.

This file is outside ProjectStore and ProjectBundle. Updating it must not change project files,
scientific identity, provenance, dependency hashes, projection hashes, or schema version.

Rust validates the preference JSON strictly before load/save. Unknown fields, version drift, blank
paths, duplicate recent paths, and oversized MRU lists fail closed.

### 4. Project path strings are data, not Rust scientific/storage input

Rust does not canonicalize or inspect project roots when storing preferences. TypeScript also does not
rewrite Windows/POSIX path semantics.

Only the path returned by a successful Python `open_project` response is promoted to current/recent
local state. This keeps path validation and project integrity in the existing Python authority and
avoids creating a second filesystem interpretation layer in the desktop shell.

### 5. Restore failure cannot create a ghost project

On application restart:

1. backend health compatibility is established;
2. local preferences are loaded;
3. if a current path is stored, `open_project` is called again;
4. on success, the desktop selects the returned project and refreshes MRU order;
5. on failure, the stored current path is cleared while the recent list is retained for user action.

No stale cached project metadata is displayed as authoritative after a restore failure.

### 6. Invalid open attempts do not mutate local selection

If a new path fails backend validation, the current selected project and recent-project list remain
unchanged. Only successful validation may mutate project lifecycle preferences.

### 7. Block 4 UX is intentionally narrow

The Svelte shell provides:

- project-root entry;
- successful open/select;
- A -> B -> A switching;
- current validated project metadata returned by `open_project`;
- close-local-selection;
- recent-project reopen and forget actions;
- explicit invalid/restore error feedback;
- runtime compatibility details kept separate from project state.

Native directory dialogs, workspace scientific views, MatterViz presentation, typed scientific
mutations, packaging, and resilience/diagnostics remain owned by later Blocks 5-8 unless needed for a
specific acceptance repair.

## Persistence, schema, provenance, and identity

- `SCHEMA_VERSION` remains 3.
- Python package version remains `1.0.0.dev0`.
- No ProjectBundle entity is added.
- No scientific hash or provenance contract changes.
- No dependency/freshness semantics change.
- Current/recent paths are local UX metadata only.
- Closing or forgetting a project in the desktop does not delete or mutate the ProjectStore.

## Acceptance

Block 4 is accepted only if all of the following hold:

1. a valid project can be opened through the existing Python `open_project` contract;
2. invalid project open fails without replacing the previous selected project or adding a recent path;
3. A -> B -> A switching invokes backend validation on every transition and preserves no cross-project
   authoritative state;
4. recent projects are deduplicated, MRU ordered, and bounded to eight paths;
5. Windows-style paths with spaces/backslashes round-trip through local preferences without rewrite;
6. a saved current project is revalidated after desktop/backend restart;
7. a missing saved current project is cleared while its recent entry remains available;
8. preference codec rejects unknown fields, incompatible versions, duplicate/blank paths, and invalid
   shapes;
9. preferences are written only to the OS app-config location and preference writes do not modify
   project files;
10. Svelte typecheck, Vitest contract/lifecycle tests, Vite build, Rust tests, and `cargo check` pass;
11. the existing Ruff/mypy/pytest, Python 3.11/3.12/3.13, and MatterViz gates remain green;
12. exact-head CI, self-review, Ready state, final merge guard, squash merge, and exact-main post-merge
   push CI complete successfully.

## Consequences

Block 4 gives the desktop a usable restart-safe project lifecycle while preserving the strongest v0.9
and v1.0 boundary: local UX memory may help the user return to a project, but only the current Python
ProjectStore can establish what that project is and whether it is valid.
