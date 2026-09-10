# Real HPC/VASP Site-Specific Acceptance Protocol

- Scope: ECatVASP v1.1 real-environment acceptance boundary
- Protocol status: Active
- Baseline audited: `main@7d4115da198aa1598260857fddd3a76b5e316b0c`
- Related closure issue: #106, Audit E

## Purpose

Portable CI proves ECatVASP's deterministic execution, persistence, desktop packaging, and scientific-authority contracts. It does **not** prove that one institutional SSH/Slurm/VASP installation satisfies the same contracts. This protocol defines the site-specific evidence required to evaluate the real path:

`Desktop/backend -> SSH -> remote work directory -> Slurm -> VASP -> retrieve -> Result Center -> downstream analysis`

A site acceptance record must distinguish software capability from site evidence. A path that was not actually executed must never be reported as passed.

## Status vocabulary

Every protocol row or acceptance record uses one of these states:

- `EXECUTED_VERIFIED`: executed against the named real site and evidence satisfied the stated checks.
- `PROTOCOL_DEFINED`: acceptance procedure exists but was not executed for the named site.
- `UNAVAILABLE_AT_SITE`: the site lacks a required executable, licensed component, or supported configuration.
- `FAILED`: the path was executed and failed its acceptance criterion.

`PROTOCOL_DEFINED` and `UNAVAILABLE_AT_SITE` are not passes.

## Frozen authority boundaries

Audit E must preserve the existing v1.1 boundaries:

- `ProjectStore` is durable project authority.
- Python domain/application code is scientific authority.
- `Calculation != ExecutionAttempt != RemoteJob`.
- Slurm `COMPLETED` is an execution fact, not VASP scientific convergence.
- Scientific convergence is derived from retrieved managed VASP evidence by Python.
- Only a scientifically converged supported relaxation may be promoted to a new structure snapshot.
- Permanent atom identity, structure lineage, artifact provenance, and freshness remain ProjectStore/Python responsibilities.
- IPC v1 remains frozen and typed operation-specific IPC v2 remains the current desktop application boundary.
- Schema 3 remains unchanged.
- Site acceptance must not introduce a second convergence engine, second workflow state machine, or new scientific identity dimension.

## Current implementation audit

The following classification describes the audited v1.1 implementation, not a claim that a particular institution has passed this protocol.

| Path | Current implementation boundary | Site acceptance requirement |
| --- | --- | --- |
| Desktop/backend to execution service | Job Center composes persisted project facts and execution actions through Python application services | Exercise the installed desktop or the identical bundled backend against the named target profile |
| SSH transport | Real `OpenSshTransport` invokes system `ssh`/`scp` with batch mode and strict host-key checking | Verify the installed client, enrolled host key, agent/key configuration, and non-interactive connection |
| Remote staging | Real SSH staging creates an isolated attempt directory and verifies uploaded file size/SHA-256 | Verify creation and hashes on the real remote filesystem |
| POTCAR | Remote resolver assembles the licensed POTCAR on the execution host from configured site-local sources and verifies expected identity/hash | Verify mapping, source identity, ordering, remote assembly, and fail-closed mismatch behavior without copying POTCAR into Git |
| Slurm submit/status | Real Slurm adapter renders the job script, submits with `sbatch`, and observes scheduler state using Slurm queries | Verify accepted job ID, queue/accounting observation, terminal state, and failure behavior |
| Retrieval | Real transport retrieves configured result artifacts into managed project storage with integrity/provenance checks | Verify required outputs, digest integrity, retrieval state, and incomplete retrieval failure |
| Result Center | Python parses managed VASP outputs and separately assesses scientific convergence | Verify a real retrieved run; do not use scheduler state as the verdict |
| Structure promotion | Python promotion gate requires an exact persisted converged relaxation result and re-verifies current output evidence | Verify promotion only after scientific convergence and confirm atom/provenance lineage |
| DOS/PDOS | VASP `DOS_STATIC` output is parsed/materialized by Python; descriptors consume exact fresh DOS analysis | Execute when a low-cost representative DOS calculation is practical |
| Band center | Python descriptor analysis over accepted DOS/PDOS with explicit selector/window/freshness identity | Prefer as the first downstream descriptor after DOS/PDOS |
| Bader | External-tool result intake/materialization; ECatVASP records exact invocation/input provenance and parses `ACF.dat` | Run Bader externally at the site, then bind/import its exact outputs; ECatVASP does not create a second generic scheduler backend for Bader |
| Charge difference | Python analysis consumes the exact three charge-density dependencies and requires compatible grids | Execute only if the three real charge calculations are available and cost is justified |
| LOBSTER | VASP prerequisite calculation plus external LOBSTER result intake with exact invocation/input provenance | Run LOBSTER externally when installed/licensed/available; then bind/import outputs |
| Harmonic thermochemistry | Python consumes an accepted frequency result and explicit mode/condition policies | Execute when a representative real frequency calculation is practical |
| Reaction/CHE presentation | Python consumes fresh upstream thermochemistry/reference evidence and explicit electrochemical conditions | May be protocol-only for Audit E if the necessary real upstream evidence was not produced |

The v0.4 deterministic final acceptance intentionally used an in-memory simulated remote host. That test is contract integration evidence; it is not real SSH/HPC or licensed VASP evidence. Audit E must not relabel it.

## 1. Site environment identity

Before execution, create a **sanitized local acceptance record** with the following fields. Do not commit secrets or licensed file contents.

```text
acceptance_date_utc:
ecatvasp_main_sha:
ecatvasp_python_version:
ecatvasp_desktop_version:
site_label:                 # non-sensitive alias only
remote_os_distribution:
remote_kernel_or_platform:
scheduler: Slurm
slurm_version:
vasp_version:
vasp_executable_command:
mpi_launcher:
module_environment_summary:
pseudopotential_family:
pseudopotential_release_or_source_identity:
potcar_resolver_id:
potcar_source_identity:     # sanitized identity/hash/catalog label; never POTCAR body
lobster_available:
lobster_version:
bader_available:
bader_version:
```

Do **not** put any of the following in Git history, PR text, screenshots, or acceptance artifacts:

- passwords or one-time codes;
- private-key bodies or passphrases;
- institutional access tokens or credentials;
- secret host configuration that the institution forbids publishing;
- licensed POTCAR contents;
- copied licensed pseudopotential libraries.

The execution target is user-local configuration. `host_alias` and `remote_work_root` are operational site data, while the persisted execution-environment snapshot is sanitized and excludes those values and all credential material.

## 2. Local/desktop prerequisites

Record the exact installed product under test. Prefer the installed Windows artifact accepted by repository CI when testing the desktop distribution rather than a source-only development shell.

Verify locally:

1. ECatVASP opens the intended project and ProjectStore reopens without drift.
2. The target profile is SSH + Slurm and points to a non-root absolute remote work root.
3. System OpenSSH provides `ssh` and `scp`.
4. Host-key enrollment is already complete; do not bypass strict host-key checking.
5. Non-interactive authentication works through the user's system OpenSSH configuration/agent.
6. The target profile names safe command identifiers for the VASP executable, optional launcher, and modules; it does not contain shell fragments or credentials.

Any missing prerequisite is a fail-closed site result, not a reason to weaken SSH policy.

## 3. Connectivity gate

Exercise the same target that Job Center will use.

Acceptance sequence:

1. `Desktop/backend -> system OpenSSH`: a non-interactive command reaches the configured host alias.
2. `SSH -> remote work root`: the configured root exists or can be used according to site policy, and the isolated ECatVASP attempt directory can be created beneath it.
3. `SSH -> Slurm`: `sbatch` and scheduler query commands are available in the configured module/environment context.
4. Querying an intentionally nonexistent job must return an uncertain/not-found scheduler observation rather than being rewritten as successful execution.

Evidence to retain locally:

- sanitized target ID and target hash;
- command exit status and timestamps;
- Slurm version/query evidence;
- no credential material.

Acceptance: all four connectivity steps are `EXECUTED_VERIFIED` before submitting VASP.

## 4. Representative calculation path

Use an existing low-cost ECatVASP structure and existing VASP recipe. Do not create a new scientific model family for Audit E. Prefer the smallest already-supported electrocatalysis-compatible structure that can complete within the site's routine short-job policy.

A relaxation is preferred because it exercises both scientific convergence and structure-promotion semantics. If the site cannot economically run a relaxation during the audit, a low-cost supported static calculation may verify execution/retrieval, but structure promotion must remain `PROTOCOL_DEFINED` rather than passed.

Capture these identities before submission:

```text
StructureSnapshot id:
Calculation id:
MethodFingerprint / calculation scientific identity:
ExecutionPlan hash:
Input-manifest hash:
ExecutionAttempt id:
Sanitized target hash:
```

Required sequence:

1. Prepare the existing `Calculation` from the selected model/recipe.
2. Materialize VASP inputs through the existing Python authority.
3. Confirm the immutable scientific input manifest and runtime overlay remain distinct.
4. Stage to the isolated remote directory for this `ExecutionAttempt`.
5. Resolve/assemble POTCAR on the remote host according to the exact planned POTCAR identity.
6. Submit the generated Slurm script.
7. Persist the returned Slurm job ID as the `RemoteJob` for the exact `ExecutionAttempt`.
8. Observe scheduler transitions without mutating scientific convergence.
9. After the scheduler reaches a terminal execution state, retrieve the configured outputs.
10. Run Result Center analysis over the managed retrieved artifacts.
11. Only if Python classifies a supported relaxation as scientifically `CONVERGED`, exercise structure promotion.

Acceptance evidence must demonstrate that `Calculation`, `ExecutionAttempt`, and `RemoteJob` retain separate IDs and responsibilities throughout the run.

## 5. POTCAR acceptance

POTCAR is a mandatory real-site gate because portable CI cannot provide licensed VASP pseudopotentials.

### Mapping and identity

Before staging:

- record the pseudopotential family/release/source identity in sanitized form;
- verify every species maps to the intended POTCAR title/variant in the exact structure order;
- verify the configured resolver/library identity matches the execution plan;
- verify expected source digest/size information where the resolver contract requires it.

### Remote materialization

During staging:

- non-POTCAR VASP inputs may be transferred normally;
- licensed POTCAR bodies must be resolved from the configured remote/site-local pseudopotential library;
- source pieces and the assembled remote POTCAR must satisfy the planned identity/integrity checks;
- the client must not create a managed local attempt artifact containing the POTCAR body.

### Failure checks

At least one safe pre-submit failure exercise should demonstrate fail-closed behavior without running VASP, for example a disposable target/resolver configuration that references a nonexistent POTCAR source. It must fail before a valid job is submitted.

A mismatched title/order/digest must also be treated as failure. Do not "accept" a nearby pseudopotential variant by element name alone.

### Repository/licensing check

The repository must continue excluding `POTCAR` from Git. Acceptance evidence may record titles, family/release labels, digests, sizes, and resolver identity, but never POTCAR content.

## 6. Slurm submission and lifecycle acceptance

For the representative calculation:

1. inspect the sanitized rendered resource request and module/executable configuration;
2. submit through the ECatVASP Slurm adapter;
3. require a parseable real Slurm job ID;
4. observe at least one scheduler query through the normal monitor path;
5. persist the `RemoteJob` independently from the `ExecutionAttempt`;
6. reconcile the terminal scheduler observation without declaring scientific success.

If `sbatch` rejects the script, the site test is `FAILED`; do not fabricate a job ID or advance the lifecycle as successful.

## 7. Retrieval and Result Center acceptance

After the representative run reaches a terminal scheduler state:

1. retrieve the configured required outputs into the project-managed artifact area;
2. verify exact file digest/size expectations recorded by retrieval;
3. confirm optional/on-demand/remote-only outputs keep their declared lifecycle semantics;
4. reopen ProjectStore and confirm the attempt/job/retrieval relationships survive restart;
5. analyze the exact managed VASP result with Result Center;
6. retain the Python convergence verdict separately from the Slurm terminal state.

For a relaxation, promotion acceptance requires all of the following:

- Result Center has an exact persisted result analysis for the calculation;
- Python scientific authority classifies the result as `CONVERGED`;
- current managed output evidence still proves that same convergence state;
- promoted structure lineage points to the exact source calculation/result;
- permanent atom identities are preserved according to the existing promotion contract.

An exited or Slurm-completed but unconverged VASP run must be accepted as **execution evidence** and rejected as **scientifically converged evidence**.

## 8. Representative downstream analysis

Audit E does not require every expensive downstream path. Execute the highest-priority feasible path and record all others truthfully.

Recommended order:

1. **DOS/PDOS** — create/run the existing DOS-static calculation from an accepted structure and parse the exact managed DOS output.
2. **Band center** — materialize a descriptor from the fresh accepted DOS/PDOS analysis with explicit selector and integration window.
3. **Bader** — if the site has Bader, run it externally against the exact accepted charge-density inputs and import/materialize the result with tool version, argv, and input hashes.
4. **Charge-density difference** — only when the exact combined/slab/adsorbate charge calculations and compatible grids are available.
5. **LOBSTER COHP/ICOHP** — if LOBSTER is available, run it externally from the exact prerequisite calculation and bind results to the exact wavefunction/input provenance.
6. **Harmonic thermochemistry** — use a real accepted frequency result and explicit mode/temperature policies.
7. **Reaction/CHE presentation** — use only fresh accepted thermochemistry/reference evidence.

For every row record one of `EXECUTED_VERIFIED`, `PROTOCOL_DEFINED`, `UNAVAILABLE_AT_SITE`, or `FAILED`, plus the exact upstream IDs/hashes. A deterministic fixture, copied historical output, or synthetic file is not real-site evidence.

## 9. Failure-path acceptance matrix

Failure testing should prefer inexpensive preflight/transport checks and deliberately small disposable jobs. Do not alter scientific semantics merely to force a test outcome.

| Failure path | Required fail-closed behavior | Minimum evidence |
| --- | --- | --- |
| SSH unavailable | OpenSSH command failure propagates; no staged/submitted success is recorded | nonzero transport result/error; unchanged job lifecycle |
| Bad remote path | validation/staging fails; no traversal/root fallback or implicit unrelated directory | rejected target or remote staging error |
| Slurm reject | `sbatch` error/unsupported job ID fails submission | submission error; no fabricated successful `RemoteJob` |
| Missing VASP executable | scheduler may terminate the process, but execution terminal state is not scientific convergence | scheduler/stdio evidence plus Result Center not-converged/indeterminate outcome |
| Missing POTCAR | remote materialization fails before valid VASP submission | POTCAR resolution/staging error |
| Mismatched POTCAR | title/order/digest identity failure aborts staging | mismatch evidence without POTCAR body |
| Scheduler completed, VASP unconverged | Python convergence authority returns unconverged/indeterminate; promotion denied | exact retrieved OUTCAR/OSZICAR evidence and verdict |
| Retrieval incomplete | required-output/integrity checks fail; partial data is not promoted as complete | retrieval error/manifest status |
| Source artifact drift | exact content/provenance/freshness checks reject or mark downstream stale | changed source digest and resulting rejection/staleness |
| Stale downstream analysis | workspace freshness/readiness refuses reuse/materialization as fresh | stale projection/rejection from Python authority |

If a real test exposes a transport, path, quoting, Slurm parsing, lifecycle, recovery, or existing-contract defect, fix it narrowly with a regression test. Stop and escalate instead if the required fix needs schema 4, a new permanent scientific entity, Study, a new workflow state machine, a new scientific identity dimension, NEB, constant-potential DFT, microkinetics, ML screening, or another new scientific capability.

## 10. Sanitized acceptance record template

Keep detailed institutional evidence locally according to site policy. A public/project closure record should contain only sanitized facts such as:

```text
Baseline SHA:
Protocol revision/commit:
Site label:
OS/platform summary:
Slurm version:
VASP version:
MPI launcher summary:
module/environment summary:
pseudopotential family/source identity:
Bader availability/version:
LOBSTER availability/version:

Connectivity: EXECUTED_VERIFIED | PROTOCOL_DEFINED | UNAVAILABLE_AT_SITE | FAILED
SSH:          ...
Remote stage: ...
POTCAR:       ...
Slurm submit: ...
Slurm status: ...
VASP run:     ...
Retrieval:    ...
Result parse: ...
Convergence:  ...
Promotion:    ...
DOS/PDOS:     ...
Band center:  ...
Bader:        ...
Charge diff:  ...
LOBSTER:      ...
Thermochem:   ...
Reaction/CHE: ...

Calculation id:
ExecutionAttempt id:
RemoteJob id / sanitized scheduler evidence:
Relevant artifact/analysis hashes:
Defects found and fix PR/commit:
Remaining site-specific limitations:
```

Do not publish an institutional hostname, username, private remote path, credential identifier, private key, or licensed file body unless the site explicitly permits that information to be public and it is actually needed. Prefer sanitized aliases and cryptographic identities.

## 11. Audit E closure semantics

Audit E closes the **real-environment acceptance boundary** when this protocol and the implementation classification are durably recorded and the closure issue truthfully states what was actually executed for the available site. It does not imply that every institution, every downstream executable, or every expensive scientific path has been validated.

A real-site run may be recorded later using this same protocol without changing the scientific domain model. If no site connection is available during a closure run, the affected rows remain `PROTOCOL_DEFINED`; they must not be called passed.

Audit F is a separate release decision. It must evaluate any remaining `PROTOCOL_DEFINED`, `UNAVAILABLE_AT_SITE`, or `FAILED` rows as explicit release risk rather than silently converting Audit E documentation into a real-site pass.
