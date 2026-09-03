"""Execution-layer provenance, target, runtime, and adapter contracts for ECatVASP."""

from ecatvasp.execution.adapters import (
    CommandResult,
    CommandSpec,
    SchedulerAdapter,
    SchedulerObservation,
    SchedulerSubmission,
    TargetRelativePath,
    TransportAdapter,
    validate_adapter_target,
)
from ecatvasp.execution.local import LocalExecutionError, LocalExecutionResult, LocalExecutor
from ecatvasp.execution.provenance import (
    ExecutionProvenanceError,
    create_execution_attempt,
    validate_execution_attempt_plan,
)
from ecatvasp.execution.remote import (
    RemotePotcarLibrary,
    RemoteStageFileRecord,
    RemoteStageManifest,
    RemoteStagePackage,
    RemoteStagingError,
    stage_remote_runtime,
)
from ecatvasp.execution.runtime import (
    LocalPotcarResolution,
    LocalRuntimePackage,
    RuntimeFileRecord,
    RuntimeInputManifest,
    RuntimeMaterializationError,
    materialize_local_runtime,
)
from ecatvasp.execution.slurm import (
    ResolvedSchedulerResources,
    SlurmAdapter,
    SlurmJobScript,
    SlurmSubmissionError,
    SlurmSubmissionPackage,
    render_slurm_job_script,
    resolve_scheduler_resources,
    submit_remote_slurm,
)
from ecatvasp.execution.ssh import (
    OpenSshTransport,
    OpenSshTransportError,
    remote_absolute_path,
)
from ecatvasp.execution.targets import (
    ExecutionEnvironmentSnapshot,
    ExecutionTargetProfile,
    SshSecurityPolicy,
    TransportKind,
)

__all__ = [
    "CommandResult",
    "CommandSpec",
    "ExecutionEnvironmentSnapshot",
    "ExecutionProvenanceError",
    "ExecutionTargetProfile",
    "LocalExecutionError",
    "LocalExecutionResult",
    "LocalExecutor",
    "LocalPotcarResolution",
    "LocalRuntimePackage",
    "OpenSshTransport",
    "OpenSshTransportError",
    "RemotePotcarLibrary",
    "RemoteStageFileRecord",
    "RemoteStageManifest",
    "RemoteStagePackage",
    "RemoteStagingError",
    "ResolvedSchedulerResources",
    "RuntimeFileRecord",
    "RuntimeInputManifest",
    "RuntimeMaterializationError",
    "SchedulerAdapter",
    "SchedulerObservation",
    "SchedulerSubmission",
    "SlurmAdapter",
    "SlurmJobScript",
    "SlurmSubmissionError",
    "SlurmSubmissionPackage",
    "SshSecurityPolicy",
    "TargetRelativePath",
    "TransportAdapter",
    "TransportKind",
    "create_execution_attempt",
    "materialize_local_runtime",
    "remote_absolute_path",
    "render_slurm_job_script",
    "resolve_scheduler_resources",
    "stage_remote_runtime",
    "submit_remote_slurm",
    "validate_adapter_target",
    "validate_execution_attempt_plan",
]
