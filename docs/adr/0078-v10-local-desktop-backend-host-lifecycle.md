# ADR-078: v1.0 Local Desktop Backend Host and Process Lifecycle

- Status: Accepted
- Date: 2026-09-07
- Scope: v1.0 Block 2

## Context

ADR-077 selected the Production Desktop Workspace route for v1.0 and established
`ecatvasp-desktop-ipc-v1` as a strict, stateless, read-only Python boundary. Block 1 deliberately did
not define a process transport. Every project-scoped request still carries an explicit project path,
and `DesktopBackend` resolves current `ProjectStore` state rather than retaining an active project or
scientific session.

Block 2 must turn that in-process contract into an executable local sidecar suitable for a later Tauri
shell. The host needs deterministic startup, readiness, request framing, malformed-message behavior,
shutdown, crash reporting, and restart semantics without introducing a web server, network listener,
new database, project session, or scientific authority.

The post-merge-CI-verified Block 1 baseline is
`main@e0a65c0c9dd40bb63cfb69bd7fe766af7542c0a4`, package version `1.0.0.dev0`, with
`SCHEMA_VERSION = 3`.

## Decision

### 1. Use local stdio NDJSON, not a network transport

The Python desktop backend is a child process controlled through stdin/stdout:

- one input line is one UTF-8 JSON request;
- one valid decoded request produces exactly one `DesktopResponse` line;
- every output frame is flushed immediately;
- CRLF and LF input line endings are accepted by stripping only line terminators;
- no HTTP server, TCP socket, Unix-domain socket, named pipe server, or database server is introduced
  in Block 2.

This keeps the control plane local, minimizes runtime dependencies, and leaves Tauri responsible only
for child-process ownership and typed IPC consumption in the next block.

### 2. Startup is silent; `health` is the readiness handshake

The sidecar emits no unsolicited startup banner or readiness event on stdout. A controlling process
proves readiness by sending the existing `health` request and receiving its normal versioned IPC
response.

This avoids a second startup message grammar and guarantees that every normal stdout line is either a
response to a valid request or a defined host-level framing error.

Health does not require a project to have been opened. There is no active-project prerequisite.

### 3. Preserve the Block 1 application IPC contract unchanged

`ecatvasp-desktop-ipc-v1` remains the application/request contract. Block 2 does not add a
`shutdown`, `startup`, `session`, or `current_project` operation to `DesktopOperation`.

The process host has a separate framing identity:

`ecatvasp-desktop-host-v1`

Host-level frames exist only when an input line cannot be decoded into a valid `DesktopRequest`. Such
a frame has:

- `host_protocol_version`;
- `frame_type = "host_error"`;
- `ok = false`;
- a stable non-scientific error code and message.

It deliberately has no fabricated request id or application operation because an undecodable line may
not contain trustworthy correlation fields.

### 4. Malformed input is isolated per line

A malformed JSON line, blank line, wrong IPC version, unsupported operation, unknown field, or invalid
request shape is treated as a framing/request error, not a backend crash.

The host:

1. writes one deterministic host-level `invalid_request` frame to stdout;
2. flushes it;
3. continues reading the next input line.

The malformed input value itself is not echoed.

This guarantees that one corrupt client frame cannot poison later valid requests.

### 5. EOF is the canonical graceful shutdown signal

Closing the controlling stdin pipe is the Block 2 graceful-shutdown mechanism. On EOF the host:

- performs no scientific or project mutation;
- emits no shutdown response or trailing banner;
- exits with code `0`.

An explicit `shutdown` IPC operation is intentionally not added. Process lifecycle belongs to the
parent desktop shell, not to the scientific/application request namespace. If a future shell requires
an explicit control message for a demonstrated platform reason, that change must be versioned in the
host-control contract rather than smuggled into scientific/application operations.

### 6. Recognized application failures remain normal responses; unexpected failures terminate

`DesktopBackend` already converts recognized project-read/storage/migration failures into structured
application responses. Those remain request-scoped and do not terminate the host.

If the already-decoded backend handler raises an unexpected exception, the host treats it as an
internal process failure:

- no fake `DesktopResponse` is written to stdout;
- stderr receives only a compact fatal diagnostic containing the exception class, not the request
  payload, project path, or exception message;
- the host exits nonzero with the Block 2 backend-failure exit code.

The parent process can therefore distinguish a recoverable request failure from a failed backend
process and may restart the sidecar without misrepresenting scientific state.

### 7. stdout is protocol-only; stderr is diagnostics-only

Normal startup, normal requests, malformed-line handling, and graceful EOF do not write human-readable
text to stdout. stdout is reserved for machine-readable protocol frames.

Unexpected backend process failures use stderr. Block 2 does not add logging files or persist runtime
diagnostics inside `ProjectBundle`.

This is required so a Tauri process client can parse stdout deterministically without filtering
warnings or banners.

### 8. Restart creates a clean runtime boundary, not a new scientific state

The host may reuse one `DesktopBackend` object during its process lifetime, but that object is
stateless with respect to projects. Every project-scoped request still carries its exact path and
reopens current `ProjectStore` state through existing authorities.

After process exit and restart:

- no active project is restored by Python process state;
- no in-memory `ProjectBundle` is authoritative;
- the next request observes the current persisted project contents;
- permanent entity ids and scientific hashes remain governed by existing storage/domain/provenance
  rules.

Desktop-local recent-project or active-project UX state remains deferred to Block 4 and must live
outside `ProjectBundle`.

### 9. Provide both module and installed-script entry points

The sidecar can be launched as:

`python -m ecatvasp.desktop`

and the package exposes:

`ecatvasp-desktop-backend`

Both enter the same stdio host function. There is no alternate server implementation or duplicated
request dispatcher.

### 10. Freeze Block 2 exit semantics

Block 2 defines:

- `HOST_EXIT_OK = 0` for graceful EOF;
- `HOST_EXIT_BACKEND_FAILURE = 1` for an unexpected backend-handler failure.

Malformed client frames do not change the eventual graceful exit code because they are isolated and
reported through host error frames.

## Architectural boundaries

The host is process orchestration only. It must not become:

- a persisted desktop/session entity;
- a cache of `ProjectBundle` or scientific facts;
- a second workflow state machine;
- a scheduler-status authority;
- a convergence classifier;
- a provenance or freshness authority;
- a filename/geometry identity inference layer;
- a network service;
- a generic mutation API.

All frozen boundaries from v0.1-v0.9 and ADR-077 remain intact, including:

- `Calculation != ExecutionAttempt != RemoteJob`;
- scheduler success != scientific convergence;
- parser facts != convergence verdict;
- scientific and execution dependencies remain separate;
- downstream scientific consumers use accepted/promoted `StructureSnapshot` identities;
- freshness remains governed by `DependencyKind.SCIENTIFIC`;
- presentation/report/handoff/host hashes or versions are not scientific identity.

## Persistence, schema, provenance, and dependencies

Block 2 adds no persisted entity and no project metadata.

- `SCHEMA_VERSION` remains `3`;
- package version remains `1.0.0.dev0`;
- scientific identity is unchanged;
- provenance is unchanged;
- freshness propagation is unchanged;
- Python runtime dependencies remain the existing package dependencies plus the standard library;
- no web framework or IPC package is added.

The console-script entry point is packaging metadata, not a new runtime dependency.

## Failure model

| Condition | stdout | stderr | Process behavior |
| --- | --- | --- | --- |
| Startup | nothing | nothing | wait for input |
| Valid request | one `DesktopResponse` NDJSON frame | nothing | continue |
| Recognized project/application failure | one failed `DesktopResponse` | nothing | continue |
| Malformed/invalid input line | one `ecatvasp-desktop-host-v1` error frame | nothing | continue |
| EOF on stdin | nothing extra | nothing | exit `0` |
| Unexpected backend exception | no fake response | exception class only | exit `1` |

## Alternatives rejected

### HTTP localhost server

Rejected for Block 2 because it adds a network listener, port lifecycle, server dependency, additional
security surface, and no current product requirement that stdio cannot satisfy.

### Generic RPC framework

Rejected because the application contract is already explicit and small. A framework would increase
runtime/packaging complexity before the Tauri client exists.

### Unsolicited startup-ready frame

Rejected because the existing `health` operation already provides an exact compatibility/readiness
handshake and an unsolicited frame would complicate correlation and parser state.

### Add `shutdown` to `DesktopOperation`

Rejected because shutdown controls the child process, not a project/application operation. EOF gives
the parent an unambiguous lifecycle signal without changing `ecatvasp-desktop-ipc-v1`.

### Continue after arbitrary backend exceptions

Rejected because an unknown failure may leave process-local libraries or resources in an uncertain
state. Fail closed at the process boundary and let the owning shell decide whether to restart.

### Persist active/recent project state in Python

Rejected because it would create session authority and violate ADR-077. Project lifecycle UX state is
non-scientific desktop-local state and belongs to Block 4.

## Tests and acceptance

Block 2 is accepted only if tests prove:

1. empty stdin/EOF exits cleanly with no output;
2. `health` can be the first request and returns the existing exact IPC contract;
3. output is one flushed NDJSON frame per valid request;
4. malformed input produces one host error and does not prevent the next valid request;
5. a project can be opened without creating retained project authority;
6. a fresh host after restart observes later persisted `ProjectStore` changes;
7. `python -m ecatvasp.desktop` starts the same host and keeps normal stderr empty;
8. unexpected backend exceptions produce no fake response and exit nonzero;
9. fatal stderr does not expose the exception message/request payload;
10. package script metadata exposes `ecatvasp-desktop-backend`;
11. `SCHEMA_VERSION = 3` and package version remains `1.0.0.dev0`;
12. full Ruff, strict mypy, pytest on Python 3.11/3.12/3.13, and MatterViz contract CI remain green.

## Consequences

Block 3 can now treat the Python backend as a deterministic child process with a minimal control
surface: launch, send health, exchange NDJSON requests, close stdin for graceful shutdown, and observe
process exit for failure/restart handling.

Tauri/Rust/Node/Svelte/TypeScript remain outside Block 2. Their dependency and typed-client decisions
are made in Block 3 on top of this frozen local-process seam.
