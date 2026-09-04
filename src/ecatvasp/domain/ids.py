"""Stable and type-distinct identifiers for scientific domain entities."""

from __future__ import annotations

import secrets
import time
from typing import NewType
from uuid import UUID

ProjectId = NewType("ProjectId", UUID)
CatalystId = NewType("CatalystId", UUID)
StructureVariantId = NewType("StructureVariantId", UUID)
StructureSnapshotId = NewType("StructureSnapshotId", UUID)
ActiveSiteId = NewType("ActiveSiteId", UUID)
AdsorptionStateId = NewType("AdsorptionStateId", UUID)
StateConformerId = NewType("StateConformerId", UUID)
CalculationId = NewType("CalculationId", UUID)
ExecutionAttemptId = NewType("ExecutionAttemptId", UUID)
RemoteJobId = NewType("RemoteJobId", UUID)
ArtifactId = NewType("ArtifactId", UUID)
AnalysisId = NewType("AnalysisId", UUID)
MethodFingerprintId = NewType("MethodFingerprintId", UUID)
WorkflowPlanId = NewType("WorkflowPlanId", UUID)
WorkflowStepBindingId = NewType("WorkflowStepBindingId", UUID)
AtomUid = NewType("AtomUid", UUID)


def new_uuid7(*, timestamp_ms: int | None = None) -> UUID:
    """Return an RFC 9562 UUIDv7 without requiring a third-party dependency."""

    unix_ms = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    if not 0 <= unix_ms < 1 << 48:
        raise ValueError("timestamp_ms must fit in the 48-bit UUIDv7 timestamp field")

    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (
        (unix_ms << 80)
        | (0x7 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    return UUID(int=value)


def new_project_id() -> ProjectId:
    return ProjectId(new_uuid7())


def new_catalyst_id() -> CatalystId:
    return CatalystId(new_uuid7())


def new_structure_variant_id() -> StructureVariantId:
    return StructureVariantId(new_uuid7())


def new_structure_snapshot_id() -> StructureSnapshotId:
    return StructureSnapshotId(new_uuid7())


def new_active_site_id() -> ActiveSiteId:
    return ActiveSiteId(new_uuid7())


def new_adsorption_state_id() -> AdsorptionStateId:
    return AdsorptionStateId(new_uuid7())


def new_state_conformer_id() -> StateConformerId:
    return StateConformerId(new_uuid7())


def new_calculation_id() -> CalculationId:
    return CalculationId(new_uuid7())


def new_execution_attempt_id() -> ExecutionAttemptId:
    return ExecutionAttemptId(new_uuid7())


def new_remote_job_id() -> RemoteJobId:
    return RemoteJobId(new_uuid7())


def new_artifact_id() -> ArtifactId:
    return ArtifactId(new_uuid7())


def new_analysis_id() -> AnalysisId:
    return AnalysisId(new_uuid7())


def new_method_fingerprint_id() -> MethodFingerprintId:
    return MethodFingerprintId(new_uuid7())


def new_workflow_plan_id() -> WorkflowPlanId:
    return WorkflowPlanId(new_uuid7())


def new_workflow_step_binding_id() -> WorkflowStepBindingId:
    return WorkflowStepBindingId(new_uuid7())


def new_atom_uid() -> AtomUid:
    return AtomUid(new_uuid7())
