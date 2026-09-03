from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationType,
    ExecutionSettings,
    Project,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
)
from ecatvasp.domain.ids import new_artifact_id
from ecatvasp.execution import (
    ExecutionEvidence,
    RecoveryAction,
    RecoveryCause,
    RecoveryChangeLayer,
    RecoveryClassificationError,
    RecoveryRequest,
    classify_recovery,
    create_execution_attempt,
    create_recovery_execution_attempt,
    derive_execution_recovery_plan,
)
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)


def _calculation(project: Project) -> Calculation:
    return Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
    )


def _settings() -> ExecutionSettings:
    return ExecutionSettings(
        ncore=4,
        kpar=2,
        nodes=1,
        cores=8,
        memory_mb=16000,
        walltime_seconds=3600,
        partition="compute",
        mpi_ranks=8,
        omp_threads=1,
        executable="vasp_std",
    )


def _plan(calculation: Calculation, settings: ExecutionSettings | None = None) -> ExecutionPlan:
    return ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="a" * 64,
        preparation_hash="b" * 64,
        staging_inputs=(),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash="c" * 64,
            metadata_hash="d" * 64,
            entries=(PotcarResolutionEntry("Pb", "Pb_d", "e" * 64),),
        ),
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=settings or _settings(),
    )


def test_transport_retry_with_proven_no_side_effect_reuses_attempt() -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))

    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.TRANSPORT_FAILURE,
            evidence=ExecutionEvidence.NO_REMOTE_SIDE_EFFECT_CONFIRMED,
        ),
    )

    assert decision.action is RecoveryAction.RETRY_SAME_ATTEMPT
    assert decision.change_layer is RecoveryChangeLayer.NONE
    assert decision.scientific_identity_preserved
    assert not decision.requires_new_execution_attempt
    assert not decision.requires_new_execution_plan


def test_scheduler_replacement_requires_positive_no_launch_evidence() -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))

    no_launch = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.SCHEDULER_FAILURE,
            evidence=ExecutionEvidence.NO_VASP_LAUNCH_CONFIRMED,
        ),
    )
    uncertain = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.SCHEDULER_FAILURE,
            evidence=ExecutionEvidence.EXECUTION_UNCERTAIN,
        ),
    )

    assert no_launch.action is RecoveryAction.RESUBMIT_SAME_ATTEMPT
    assert not no_launch.requires_new_execution_attempt
    assert uncertain.action is RecoveryAction.NEW_EXECUTION_ATTEMPT
    assert uncertain.requires_new_execution_attempt


def test_confirmed_vasp_launch_requires_new_attempt_with_same_plan() -> None:
    project = Project(name="Recovery", slug="recovery")
    calculation = _calculation(project)
    plan = _plan(calculation)
    first = create_execution_attempt(plan=plan, calculation=calculation)
    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        ),
    )

    second = create_recovery_execution_attempt(
        plan=plan,
        calculation=calculation,
        existing_attempts=(first,),
        decision=decision,
    )

    assert decision.action is RecoveryAction.NEW_EXECUTION_ATTEMPT
    assert not decision.requires_new_execution_plan
    assert second.attempt_number == 2
    assert second.previous_attempt_id == first.id
    assert second.execution_plan_hash == plan.plan_hash


def test_execution_tuning_changes_plan_and_attempt_not_scientific_identity() -> None:
    project = Project(name="Recovery", slug="recovery")
    calculation = _calculation(project)
    plan = _plan(calculation)
    first = create_execution_attempt(plan=plan, calculation=calculation)
    proposed = replace(
        plan.execution_settings,
        ncore=2,
        kpar=1,
        memory_mb=24000,
        walltime_seconds=7200,
        partition="long",
        executable="vasp_gam",
    )
    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.EXECUTION_TUNING,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_execution_settings=proposed,
            proposed_incar_tags=("KPAR", "NCORE"),
        ),
    )
    recovered_plan = derive_execution_recovery_plan(
        plan=plan,
        execution_settings=proposed,
    )
    second = create_recovery_execution_attempt(
        plan=recovered_plan,
        calculation=calculation,
        existing_attempts=(first,),
        decision=decision,
    )

    assert decision.action is RecoveryAction.NEW_EXECUTION_ATTEMPT
    assert decision.change_layer is RecoveryChangeLayer.EXECUTION
    assert decision.scientific_identity_preserved
    assert decision.requires_new_execution_plan
    assert set(decision.changed_execution_fields) == {
        "ncore",
        "kpar",
        "memory_mb",
        "walltime_seconds",
        "partition",
        "executable",
    }
    assert recovered_plan.calculation_id == plan.calculation_id
    assert recovered_plan.input_manifest_sha256 == plan.input_manifest_sha256
    assert recovered_plan.preparation_hash == plan.preparation_hash
    assert recovered_plan.plan_hash != plan.plan_hash
    assert second.execution_plan_hash == recovered_plan.plan_hash


@pytest.mark.parametrize(
    ("tags", "layer"),
    [
        (("ISTART",), RecoveryChangeLayer.SCIENTIFIC_INITIALIZATION),
        (("ICHARG", "ISTART"), RecoveryChangeLayer.SCIENTIFIC_INITIALIZATION),
        (("ENCUT",), RecoveryChangeLayer.SCIENTIFIC_INPUT),
        (("EDIFF", "ALGO"), RecoveryChangeLayer.SCIENTIFIC_INPUT),
        (("SOME_FUTURE_TAG",), RecoveryChangeLayer.SCIENTIFIC_INPUT),
    ],
)
def test_scientific_incar_changes_require_new_calculation(
    tags: tuple[str, ...],
    layer: RecoveryChangeLayer,
) -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))

    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.SCIENTIFIC_INPUT_CHANGE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_incar_tags=tags,
        ),
    )

    assert decision.action is RecoveryAction.NEW_CALCULATION
    assert decision.change_layer is layer
    assert decision.requires_new_calculation
    assert not decision.scientific_identity_preserved
    assert not decision.requires_new_execution_attempt


def test_mixed_execution_and_scientific_incar_change_uses_scientific_boundary() -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))

    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.SCIENTIFIC_INPUT_CHANGE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_incar_tags=("NCORE", "EDIFF"),
        ),
    )

    assert decision.action is RecoveryAction.NEW_CALCULATION
    assert decision.change_layer is RecoveryChangeLayer.SCIENTIFIC_INPUT


def test_contcar_continuation_requires_new_structure_and_calculation() -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))

    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.CONTCAR_CONTINUATION,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            continue_from_contcar=True,
        ),
    )

    assert decision.action is RecoveryAction.NEW_STRUCTURE_AND_CALCULATION
    assert decision.change_layer is RecoveryChangeLayer.STRUCTURE
    assert decision.requires_new_structure_snapshot
    assert decision.requires_new_calculation
    assert not decision.scientific_identity_preserved


def test_automatic_scientific_correction_is_not_applied() -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))

    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.VASP_FAILURE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_incar_tags=("ALGO",),
            automatic=True,
        ),
    )

    assert decision.action is RecoveryAction.MANUAL_REVIEW_REQUIRED
    assert decision.requires_new_calculation
    assert not decision.scientific_identity_preserved


def test_automatic_execution_tuning_is_classified_but_not_applied() -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))
    proposed = replace(plan.execution_settings, walltime_seconds=7200)

    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.EXECUTION_TUNING,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_execution_settings=proposed,
            automatic=True,
        ),
    )

    assert decision.action is RecoveryAction.MANUAL_REVIEW_REQUIRED
    assert decision.change_layer is RecoveryChangeLayer.EXECUTION
    assert decision.scientific_identity_preserved
    assert not decision.requires_new_calculation


def test_execution_tuning_requires_a_real_change() -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))

    with pytest.raises(RecoveryClassificationError, match="requires an explicit proposed change"):
        classify_recovery(
            plan=plan,
            request=RecoveryRequest(
                cause=RecoveryCause.EXECUTION_TUNING,
                evidence=ExecutionEvidence.EXECUTION_UNCERTAIN,
            ),
        )

    with pytest.raises(RecoveryClassificationError, match="do not change the plan"):
        derive_execution_recovery_plan(
            plan=plan,
            execution_settings=plan.execution_settings,
        )


def test_scientific_decision_cannot_create_recovery_attempt() -> None:
    project = Project(name="Recovery", slug="recovery")
    calculation = _calculation(project)
    plan = _plan(calculation)
    first = create_execution_attempt(plan=plan, calculation=calculation)
    decision = classify_recovery(
        plan=plan,
        request=RecoveryRequest(
            cause=RecoveryCause.SCIENTIFIC_INPUT_CHANGE,
            evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
            proposed_incar_tags=("ENCUT",),
        ),
    )

    with pytest.raises(RecoveryClassificationError, match="does not authorize a new attempt"):
        create_recovery_execution_attempt(
            plan=plan,
            calculation=calculation,
            existing_attempts=(first,),
            decision=decision,
        )


def test_recovery_decision_hash_is_deterministic_and_normalizes_tags() -> None:
    project = Project(name="Recovery", slug="recovery")
    plan = _plan(_calculation(project))
    request = RecoveryRequest(
        cause=RecoveryCause.SCIENTIFIC_INPUT_CHANGE,
        evidence=ExecutionEvidence.VASP_LAUNCH_CONFIRMED,
        proposed_incar_tags=("algo", "EDIFF", "ALGO"),
    )

    first = classify_recovery(plan=plan, request=request)
    second = classify_recovery(plan=plan, request=request)

    assert request.proposed_incar_tags == ("ALGO", "EDIFF")
    assert first.decision_hash == second.decision_hash
