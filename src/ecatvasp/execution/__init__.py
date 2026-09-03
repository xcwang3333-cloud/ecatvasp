"""Execution-layer provenance, target, and adapter contracts for ECatVASP."""

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
from ecatvasp.execution.provenance import (
    ExecutionProvenanceError,
    create_execution_attempt,
    validate_execution_attempt_plan,
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
    "SchedulerAdapter",
    "SchedulerObservation",
    "SchedulerSubmission",
    "SshSecurityPolicy",
    "TargetRelativePath",
    "TransportAdapter",
    "TransportKind",
    "create_execution_attempt",
    "validate_adapter_target",
    "validate_execution_attempt_plan",
]
