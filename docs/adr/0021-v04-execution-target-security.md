# ADR-021: v0.4 Execution Target, Adapter, and Credential Boundary

- Status: Accepted
- Date: 2026-09-03

## Context

ADR-007 separates scientific workflows from execution infrastructure, ADR-019 hands an immutable
`ExecutionPlan` to future adapters, and ADR-020 binds each new v0.4 `ExecutionAttempt` to the exact
plan it consumes. Block 2 must now define where a calculation may execute and how later local/remote
backends interact with transports and schedulers without introducing credentials, scheduler facts,
or host-specific configuration into scientific identity.

The first concrete execution targets remain local execution and SSH/Slurm. PBS and LSF remain future
scheduler-adapter implementations. This block defines contracts only; it does not connect to an SSH
host or submit any scheduler job.

## Decision

### ExecutionTargetProfile is execution-local configuration

`ExecutionTargetProfile` is an execution-layer value object, not a scientific Domain entity and not a
`ProjectBundle` member. It may contain host-specific execution configuration such as:

- a stable `target_id`;
- `LOCAL` or `SSH` transport family;
- scheduler family for SSH targets;
- an OpenSSH `host_alias`;
- an explicit remote work root;
- logical VASP executable and launcher command names;
- ordered module identifiers;
- a logical POTCAR resolver profile id.

The target profile has a deterministic `target_hash`, but that hash never participates in
`MethodFingerprint`, Calculation identity, or immutable scientific input hashes.

A sanitized `ExecutionEnvironmentSnapshot` may later be persisted with attempt provenance. The
snapshot records the target id/hash, transport, scheduler, executable/launcher, modules, and logical
POTCAR resolver id. It deliberately excludes host aliases, remote directories, and credential
material.

### Credential boundary

ECatVASP delegates SSH authentication to the user's system OpenSSH configuration and agent. The
execution target schema has no password, token, private-key body, passphrase, or credential-file
field.

Every SSH target requires `SshSecurityPolicy` with these non-negotiable properties:

- system OpenSSH credential resolution;
- strict host-key verification;
- non-interactive batch mode;
- no password prompting.

Permissive policies such as automatic unknown-host acceptance or `StrictHostKeyChecking=no` are not
representable through the supported contract. User/host routing, ProxyJump, MFA integration, key
selection, and agent behavior remain in system OpenSSH configuration rather than project data.

An SSH `host_alias` is a portable alias only. Inline `user@host`, shell options, or path syntax are
rejected so target configuration cannot become an alternate credential or command channel.

### Remote path boundary

SSH targets require an explicit non-root absolute POSIX `remote_work_root`. Later transport
operations operate on `TargetRelativePath` values confined beneath that root. Absolute target paths
and `..` traversal are rejected.

This establishes the staging containment rule before any remote filesystem implementation exists.

### TransportAdapter

`TransportAdapter` is a runtime-checkable protocol. Later Local/SSH implementations must provide:

- target-root directory creation;
- upload;
- download;
- argument-vector command execution.

Commands are represented by `CommandSpec(argv=...)`, not shell command strings. Adapters must treat
`argv` as an argument vector and must not silently reinterpret it as shell text. This is the base
command-injection boundary for later SSH staging and checksum operations.

### SchedulerAdapter

`SchedulerAdapter` is a runtime-checkable protocol independent from transport. Later scheduler
implementations must provide:

- submit;
- query;
- cancel.

Submission returns only scheduler identity/raw output. Query/cancel return normalized
`SchedulerObservation` plus raw scheduler state. Script rendering, resource resolution, retry policy,
and reconciliation timing are deferred to later v0.4 blocks.

`validate_adapter_target()` fails closed when a transport or scheduler adapter family does not match
its `ExecutionTargetProfile`.

Local targets are scheduler-free at this boundary. SSH targets require an explicit scheduler family;
the current Domain scheduler families remain Slurm, PBS, LSF, and OTHER. Block 2 does not implement
any of them.

### POTCAR target identity

Every target carries only a logical `potcar_resolver_id`. It does not carry a licensed POTCAR body or
licensed filesystem path. Later local/remote resolution maps that logical id to host-local licensed
configuration outside the project and must verify the exact hashes requested by `ExecutionPlan`.

This preserves the v0.3 licensed-POTCAR boundary.

## Explicit non-scope

Block 2 does not implement:

- SSH subprocess execution, connection pooling, upload, or download;
- OpenSSH configuration parsing;
- credential storage or prompting;
- local VASP process execution;
- Slurm/PBS/LSF concrete adapters;
- scheduler script rendering;
- scheduler resource resolution;
- remote POTCAR lookup or concatenation;
- staging manifests;
- job submission, polling, cancellation, or reconciliation;
- retrieval;
- retry/restart mutation;
- scheduler DAG or batch dispatch.

## Consequences

- execution targets can vary without changing scientific identity;
- credentials and host-routing policy remain outside ECatVASP projects;
- later SSH implementations inherit strict host verification and root-relative staging contracts;
- later scheduler implementations share one stable submit/query/cancel interface;
- local and SSH execution can evolve behind adapters without changing the scientific DAG;
- no project schema migration is required for Block 2 because target profiles are not persisted as
  Domain entities.
