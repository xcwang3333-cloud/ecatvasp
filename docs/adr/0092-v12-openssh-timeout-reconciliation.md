# ADR-092: v1.2 OpenSSH Timeout and Reconciliation Boundary

- Status: Accepted
- Date: 2026-09-12
- Scope: v1.2 real-site OpenSSH timeout and reconciliation semantics
- Roadmap authority: Issue #121

## Context

ECatVASP delegates remote transport to the user's system `ssh` and `scp` clients under the strict
credential, host-key, argument, and path boundaries accepted by ADR-021 and ADR-023. Those calls are
currently not bounded by an ECatVASP-owned subprocess timeout. A stalled connection, remote command,
or transfer can therefore stall preflight, staging, submission, monitoring, cancellation, retrieval,
or cleanup indefinitely.

Adding a subprocess timeout is not only a process-control change. When an observation times out, no
remote state has been learned. When a command with side effects times out, the remote side effect may
have completed even though the local client did not receive a result. Blindly retrying an ambiguous
`sbatch`, `scancel`, upload, or deletion could duplicate execution, corrupt provenance, or destroy
evidence.

ADR-025 already establishes that scheduler observation failure is an error rather than `UNKNOWN` or
`LOST`. ADR-026 requires complete observation and verification around retrieval and remote deletion.
ADR-091 keeps preflight and execution evidence operational rather than scientific. This ADR freezes
the timeout and reconciliation rules before Issue #121 changes the transport implementation.

## Decision

### Timeout ownership and meaning

Every local `ssh` or `scp` subprocess invocation made by the managed OpenSSH adapter must have an
explicit, finite, positive timeout selected by an ECatVASP-owned operation policy. A timeout bounds
one local client invocation only. It does not bound a submitted Slurm allocation or VASP calculation.

An OpenSSH client timeout is distinct from:

- Slurm's normalized `SchedulerState.TIMEOUT`, which is a successful scheduler observation;
- a scheduler observation failure, where no trustworthy scheduler state was returned;
- an application deadline spanning several transport calls;
- VASP scientific convergence or failure.

No transport timeout may directly change `CalculationScientificStatus` or fabricate a scheduler
state.

### Error taxonomy

The transport layer must translate `subprocess.TimeoutExpired` into a dedicated
`OpenSshTimeoutError`, which remains a subtype of `OpenSshTransportError`. The structured error must
carry a stable `SSH_TRANSPORT_TIMEOUT` code and a bounded operation category such as command,
upload, or download. Callers must not infer timeout semantics from free-form diagnostic text.

Diagnostics may identify the operation category and configured duration. They must not include
credentials, raw SSH configuration, absolute licensed POTCAR roots, unrestricted remote stderr, or
other host-sensitive values. Unrelated launch, validation, decoding, and nonzero-exit failures retain
their existing explicit error paths; implementation must not add a broad catch.

### Preflight and other side-effect-free operations

SSH reachability, executable and module probes, scheduler queries, remote existence/hash/size checks,
and bounded telemetry are observation operations. A timeout produces fail-closed operational evidence
and no scientific state. It must not create or mutate a `Calculation`, `ExecutionAttempt`, `RemoteJob`,
or convergence result.

Read-only and idempotent observations may be repeated according to the existing caller policy. A
timed-out scheduler query remains an observation error and is never normalized to `LOST`, `UNKNOWN`,
or scheduler `TIMEOUT`, preserving ADR-025.

Real Site Preflight must preserve a known `OpenSshTimeoutError` as typed diagnostic evidence, using a
dedicated reason code or an equivalently explicit typed mapping. It must not infer timeout from message
text or collapse the known cause into an indistinguishable generic transport failure or synthetic
exit-255 result. This preserves ADR-091's stable machine-readable diagnostic contract through the
application boundary.

### Indeterminate side effects

A timeout during a side-effecting operation does not prove that the operation failed. The outcome is
indeterminate until operation-specific reconciliation establishes remote truth. This applies to:

- directory or file creation and mutation;
- `scp` upload and download;
- `sbatch` submission;
- `scancel` cancellation;
- remote release, discard, deletion, and cleanup.

The timed-out call itself must not be recorded as a successful side effect. It also must not be
classified as `NO_REMOTE_SIDE_EFFECT_CONFIRMED` under ADR-027.

### Reconciliation before retry

Retries follow this matrix:

| Operation | Timeout outcome | Required reconciliation before retry |
| --- | --- | --- |
| Reachability, command/module/tool probe | Observation failed | May repeat as a read-only probe; no durable execution mutation |
| Scheduler query or telemetry | Observation failed | Repeat the normal query; do not synthesize scheduler state |
| Shared parent-directory creation | Indeterminate idempotent infrastructure mutation | Re-observe or safely re-establish only the shared parent such as `execution/` |
| Isolated `execution/<attempt_id>` creation | Indeterminate non-idempotent mutation | Existence alone is insufficient; fail closed, preserve partial state for forensic handling, and require positive reconciliation compatible with ADR-023/027 before any reuse |
| Upload | Indeterminate remote bytes | Re-observe exact size and SHA-256; accept only a complete exact match, otherwise use the existing isolated-stage failure policy |
| Download | Indeterminate local bytes | Validate temporary local size and SHA-256 before atomic promotion; never accept a partial file |
| `sbatch` | Indeterminate scheduler acceptance | Do not resubmit; require positive durable submission reconciliation, or stop for manual/operator reconciliation when no scheduler identity is available |
| `scancel` | Indeterminate cancellation request | Query the scheduler; only an observed state may update the attempt, as required by ADR-025 |
| Remote deletion or cleanup | Indeterminate destructive mutation | Re-observe existence and expected digest/size where applicable; preserve ADR-026 verification and retention rules |

If the available evidence cannot distinguish whether VASP launched or whether a remote side effect
occurred, recovery is `EXECUTION_UNCERTAIN`. ADR-027's stricter identity boundary applies; the system
must not reuse an attempt or create a second `RemoteJob` on assumption alone.

The shared `execution/` parent is idempotent infrastructure, but the isolated
`execution/<attempt_id>` directory is deliberately non-idempotent under ADR-023. After its creation
times out, observing that the directory exists cannot prove which actor created it or whether it is a
safe fresh stage. The current system must not continue or restage into it based on existence alone.

In particular, a timed-out `sbatch` must never trigger automatic resubmission. A future implementation
may make submission reconcilable only by adding a deterministic, persisted submission identity that
can be queried without ambiguity. Until such positive evidence exists, the outcome remains uncertain.
A new `ExecutionAttempt` is the minimum identity boundary if another execution is eventually
authorized, but it is not evidence that the first `sbatch` had no effect and is not, by itself,
permission to submit again. While the first submission remains unreconciled, no automatic second
submission is permitted; if no durable scheduler identity can resolve it, execution stops for
manual/operator reconciliation.

### Integrity and deletion remain authoritative

Timeout handling does not weaken ADR-023 or ADR-026. A timed-out upload becomes usable only after the
same exact remote SHA-256 and size gates as an ordinary upload. A timed-out download becomes usable
only after exact local verification and atomic promotion. Remote release or deletion still requires
pre-deletion re-observation and post-deletion verification; timeout is never proof of deletion.

Licensed POTCAR bodies and paths remain site-local. Timeout evidence may record only sanitized logical
operation context, never licensed contents or absolute licensed roots.

### Provenance boundary

Timeout observations are operational or execution evidence. They may be included in sanitized
preflight diagnostics or attempt-level execution records, but they are not scientific artifacts,
scientific freshness sources, or MethodFingerprint inputs. A preflight timeout must not create an
attempt merely to persist the diagnostic.

## Implementation sequence

This ADR is the specification-only first slice of Issue #121. A later isolated implementation slice
may add the timeout subtype/code, operation policy, subprocess bounds, and focused regression tests.
That slice must audit every `ssh` and `scp` call site and preserve the reconciliation responsibilities
of staging, submission, monitoring, cancellation, retrieval, and cleanup callers.

Timeout duration defaults and any user-facing configuration require a bounded typed contract. They
must not become arbitrary shell options, frontend-authored command strings, or scientific project
identity. Changing a duration may change operational behavior but not scientific identity.

## Explicit non-scope

This decision does not:

- implement Python, Rust, TypeScript, desktop, or CI changes;
- impose a wall-clock limit on Slurm jobs or VASP execution;
- alter scheduler-state normalization;
- add automatic submission, cancellation, staging, or deletion retries;
- change schema version, ProjectStore, scientific identity, or convergence semantics;
- introduce credential storage, host-key bypasses, arbitrary SSH options, or arbitrary shell text;
- resolve dependency reproducibility work tracked separately by Issue #122.

## Consequences

- real-site probes and execution transports can become bounded without conflating transport and
  scheduler timeouts;
- observation failures remain errors instead of invented remote states;
- ambiguous side effects stop for reconciliation rather than being repeated blindly;
- existing integrity, cancellation, retrieval, recovery-identity, credential, and scientific-authority
  boundaries remain intact;
- Issue #121 can proceed in small implementation increments with a stable error and retry contract.

## Acceptance criteria

This specification slice is accepted when:

1. OpenSSH subprocess timeout, scheduler `TIMEOUT`, and scheduler observation failure are explicitly
   distinct;
2. preflight timeout cannot create or mutate scientific or execution-domain identities;
3. read-only and side-effecting operations have separate retry rules;
4. `sbatch`, `scancel`, transfer, and deletion timeouts require operation-specific reconciliation;
5. uncertain launch or side-effect history preserves ADR-027's stricter identity boundary;
6. timeout diagnostics and provenance remain sanitized and operational;
7. no transport implementation or dependency-policy change is included in this increment.
