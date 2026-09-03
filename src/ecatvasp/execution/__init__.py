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
    "RuntimeFileRecord",
    "RuntimeInputManifest",
    "RuntimeMaterializationError",
    "SchedulerAdapter",
    "SchedulerObservation",
    "SchedulerSubmission",
    "SshSecurityPolicy",
    "TargetRelativePath",
    "TransportAdapter",
    "TransportKind",
    "create_execution_attempt",
    "materialize_local_runtime",
    "remote_absolute_path",
    "stage_remote_runtime",
    "validate_adapter_target",
    "validate_execution_attempt_plan",
]
