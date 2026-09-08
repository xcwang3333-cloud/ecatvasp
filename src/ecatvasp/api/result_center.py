"""Task-oriented VASP Result Center application service for v1.1 Block 5.

This module composes the existing v0.5 result-intake, parser, convergence, result
materialization, and CONTCAR-promotion authorities. It does not infer scientific
success from scheduler state and never accepts user-supplied hashes, result paths,
or convergence verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ecatvasp.api.application import (
    ApplicationServiceError,
    ApplicationStructurePromotionResult,
    ProjectApplicationService,
)
from ecatvasp.api.execution_plan_resolver import resolve_execution_plan
from ecatvasp.domain import (
    AnalysisType,
    Artifact,
    Calculation,
    CalculationId,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptId,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    MethodFingerprint,
    StructureSnapshot,
    StructureVariant,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp import (
    ConvergenceVerdict,
    ExecutionPlan,
    VaspConvergenceAssessment,
    VaspConvergenceEvidence,
    VaspResultArtifactIntake,
    VaspResultDocument,
    assess_vasp_convergence,
    build_vasp_result_artifact_intake,
    collect_vasp_convergence_evidence,
    parse_vasp_energy_metadata,
    parse_vasp_forces_magnetization,
    parse_vasp_frequency_results,
)

_PARSEABLE_ATTEMPT_STATES = frozenset(
    {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.PARSED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }
)
_FREQUENCY_TYPES = frozenset(
    {CalculationType.FREQUENCY, CalculationType.GAS_FREQUENCY}
)
_PROMOTABLE_TYPES = frozenset({CalculationType.RELAX})


@dataclass(frozen=True, slots=True)
class ResultCenterAnalysisReceipt:
    calculation_id: CalculationId
    attempt_id: ExecutionAttemptId
    intake_hash: str
    result: VaspResultDocument
    evidence: VaspConvergenceEvidence
    assessment: VaspConvergenceAssessment


@dataclass(frozen=True, slots=True)
class _ResolvedResult:
    bundle: ProjectBundle
    calculation: Calculation
    attempt: ExecutionAttempt
    fingerprint: MethodFingerprint
    input_snapshot: StructureSnapshot
    plan: ExecutionPlan
    intake: VaspResultArtifactIntake


class ProjectResultCenterApplicationService(ProjectApplicationService):
    """Project-scoped Result Center over existing scientific-result authorities."""

    def catalog(self) -> dict[str, object]:
        bundle = self.store.open()
        rows: list[dict[str, object]] = []
        for calculation in bundle.calculations:
            latest = _latest_attempt(bundle, calculation.id)
            analyzed = False
            analysis_ready = False
            promotion_ready = False
            promotion_variant_id: str | None = None
            source_roles: list[str] = []
            readiness_reason = "no execution attempt"
            if latest is not None:
                source_artifacts = _attempt_result_artifacts(bundle, latest)
                source_roles = sorted(item.artifact_type.value for item in source_artifacts)
                if latest.status in _PARSEABLE_ATTEMPT_STATES:
                    try:
                        plan = resolve_execution_plan(self.store.root, bundle, latest.id)
                        intake = build_vasp_result_artifact_intake(
                            project_root=self.store.root,
                            calculation=calculation,
                            plan=plan,
                            attempt=latest,
                            artifacts=source_artifacts,
                        )
                    except ValueError as error:
                        readiness_reason = str(error)
                    else:
                        analyzed = _has_exact_result_analysis(bundle, intake)
                        analysis_ready = not analyzed
                        readiness_reason = (
                            "already analyzed"
                            if analyzed
                            else "exact managed outputs are parse-ready"
                        )
                        if (
                            analyzed
                            and calculation.status is CalculationScientificStatus.CONVERGED
                        ):
                            variant = _promotion_variant(bundle, calculation)
                            if variant is not None:
                                promotion_ready = (
                                    calculation.calculation_type in _PROMOTABLE_TYPES
                                )
                                promotion_variant_id = (
                                    str(variant.id) if promotion_ready else None
                                )
                else:
                    readiness_reason = (
                        f"attempt status {latest.status.value} is not parse-ready"
                    )
            rows.append(
                {
                    "calculation_id": str(calculation.id),
                    "calculation_type": calculation.calculation_type.value,
                    "recipe_id": calculation.recipe_id,
                    "scientific_status": calculation.status.value,
                    "latest_attempt_id": str(latest.id) if latest is not None else None,
                    "attempt_status": latest.status.value if latest is not None else None,
                    "retrieved_artifact_types": source_roles,
                    "analyzed": analyzed,
                    "analysis_ready": analysis_ready,
                    "analysis_readiness_reason": readiness_reason,
                    "promotion_ready": promotion_ready,
                    "promotion_variant_id": promotion_variant_id,
                }
            )
        return {
            "project_id": str(bundle.project.id),
            "project_name": bundle.project.name,
            "calculations": rows,
        }

    def analyze_result(
        self,
        *,
        calculation_id: CalculationId,
    ) -> ResultCenterAnalysisReceipt:
        resolved = self._resolve_parseable_result(calculation_id)
        if _has_exact_result_analysis(resolved.bundle, resolved.intake):
            raise ApplicationServiceError("exact latest VASP result is already analyzed")

        result = parse_vasp_energy_metadata(
            project_root=self.store.root,
            intake=resolved.intake,
        )
        if resolved.calculation.calculation_type in _FREQUENCY_TYPES:
            result = parse_vasp_frequency_results(
                project_root=self.store.root,
                calculation=resolved.calculation,
                fingerprint=resolved.fingerprint,
                plan=resolved.plan,
                intake=resolved.intake,
                input_snapshot=resolved.input_snapshot,
                result=result,
            )
        else:
            result = parse_vasp_forces_magnetization(
                project_root=self.store.root,
                calculation=resolved.calculation,
                fingerprint=resolved.fingerprint,
                plan=resolved.plan,
                intake=resolved.intake,
                result=result,
            )
        evidence = collect_vasp_convergence_evidence(
            project_root=self.store.root,
            intake=resolved.intake,
            result=result,
        )
        assessment = assess_vasp_convergence(
            calculation=resolved.calculation,
            fingerprint=resolved.fingerprint,
            evidence=evidence,
        )
        self.analyze_vasp_result(
            calculation_id=resolved.calculation.id,
            intake=resolved.intake,
            result=result,
            assessment=assessment,
            execution_plan=resolved.plan,
        )
        _mark_attempt_parsed(self.store, resolved.attempt.id)
        return ResultCenterAnalysisReceipt(
            calculation_id=resolved.calculation.id,
            attempt_id=resolved.attempt.id,
            intake_hash=resolved.intake.intake_hash,
            result=result,
            evidence=evidence,
            assessment=assessment,
        )

    def promote_result_structure(
        self,
        *,
        calculation_id: CalculationId,
        label: str | None = None,
    ) -> ApplicationStructurePromotionResult:
        resolved = self._resolve_parseable_result(calculation_id)
        if resolved.calculation.calculation_type not in _PROMOTABLE_TYPES:
            raise ApplicationServiceError(
                "Result Center promotion is available only for catalyst relaxation Calculations"
            )
        if not _has_exact_result_analysis(resolved.bundle, resolved.intake):
            raise ApplicationServiceError(
                "Result Center requires persisted scientific analysis before promotion"
            )
        persisted_calculation = _require_calculation(self.store.open(), calculation_id)
        if persisted_calculation.status is not CalculationScientificStatus.CONVERGED:
            raise ApplicationServiceError(
                "only a scientifically converged analyzed result may be promoted"
            )
        variant = _promotion_variant(self.store.open(), persisted_calculation)
        if variant is None:
            raise ApplicationServiceError(
                "no unique current StructureVariant owns this Calculation input snapshot"
            )

        result = parse_vasp_energy_metadata(
            project_root=self.store.root,
            intake=resolved.intake,
        )
        result = parse_vasp_forces_magnetization(
            project_root=self.store.root,
            calculation=resolved.calculation,
            fingerprint=resolved.fingerprint,
            plan=resolved.plan,
            intake=resolved.intake,
            result=result,
        )
        evidence = collect_vasp_convergence_evidence(
            project_root=self.store.root,
            intake=resolved.intake,
            result=result,
        )
        assessment = assess_vasp_convergence(
            calculation=resolved.calculation,
            fingerprint=resolved.fingerprint,
            evidence=evidence,
        )
        if assessment.overall is not ConvergenceVerdict.CONVERGED:
            raise ApplicationServiceError(
                "current exact output no longer proves scientific convergence"
            )
        return self.promote_vasp_structure(
            structure_variant_id=variant.id,
            calculation_id=resolved.calculation.id,
            method_fingerprint_id=resolved.fingerprint.id,
            execution_plan=resolved.plan,
            intake=resolved.intake,
            evidence=evidence,
            label=label,
        )

    def _resolve_parseable_result(self, calculation_id: CalculationId) -> _ResolvedResult:
        bundle = self.store.open()
        calculation = _require_calculation(bundle, calculation_id)
        attempt = _latest_attempt(bundle, calculation.id)
        if attempt is None:
            raise ApplicationServiceError("Calculation has no ExecutionAttempt")
        if attempt.status not in _PARSEABLE_ATTEMPT_STATES:
            raise ApplicationServiceError(
                f"latest ExecutionAttempt status {attempt.status.value!r} is not parse-ready"
            )
        fingerprint = _require_fingerprint(bundle, calculation)
        input_snapshot = _require_snapshot(bundle, calculation)
        plan = resolve_execution_plan(self.store.root, bundle, attempt.id)
        intake = build_vasp_result_artifact_intake(
            project_root=self.store.root,
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            artifacts=_attempt_result_artifacts(bundle, attempt),
        )
        return _ResolvedResult(
            bundle=bundle,
            calculation=calculation,
            attempt=attempt,
            fingerprint=fingerprint,
            input_snapshot=input_snapshot,
            plan=plan,
            intake=intake,
        )


def _mark_attempt_parsed(
    store: ProjectStore,
    attempt_id: ExecutionAttemptId,
) -> ExecutionAttempt:
    """Persist parse completion without erasing failed/cancelled execution truth."""

    bundle = store.open()
    attempt = _require_attempt(bundle, attempt_id)
    if attempt.status in {
        ExecutionAttemptStatus.PARSED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }:
        return attempt
    if attempt.status not in {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
    }:
        raise ApplicationServiceError(
            "scientific parsing completed from an unexpected ExecutionAttempt state"
        )
    updated = replace(attempt, status=ExecutionAttemptStatus.PARSED)
    store.save(
        replace(
            bundle,
            execution_attempts=tuple(
                updated if item.id == attempt_id else item
                for item in bundle.execution_attempts
            ),
        )
    )
    persisted = _require_attempt(store.open(), attempt_id)
    if persisted != updated:
        raise ApplicationServiceError(
            "parsed ExecutionAttempt failed post-save verification"
        )
    return persisted


def _latest_attempt(
    bundle: ProjectBundle,
    calculation_id: CalculationId,
) -> ExecutionAttempt | None:
    attempts = sorted(
        (
            item
            for item in bundle.execution_attempts
            if item.calculation_id == calculation_id
        ),
        key=lambda item: item.attempt_number,
    )
    return attempts[-1] if attempts else None


def _attempt_result_artifacts(
    bundle: ProjectBundle,
    attempt: ExecutionAttempt,
) -> tuple[Artifact, ...]:
    producer = ExecutionAttemptProducerRef(attempt.id)
    return tuple(item for item in bundle.artifacts if item.producer == producer)


def _has_exact_result_analysis(
    bundle: ProjectBundle,
    intake: VaspResultArtifactIntake,
) -> bool:
    expected = set(intake.input_artifact_ids)
    matches = tuple(
        item
        for item in bundle.analyses
        if item.analysis_type is AnalysisType.RESULT_PARSE
        and set(item.input_artifact_ids) == expected
    )
    if len(matches) > 1:
        raise ApplicationServiceError(
            "exact VASP result has duplicate RESULT_PARSE analyses"
        )
    return len(matches) == 1


def _promotion_variant(
    bundle: ProjectBundle,
    calculation: Calculation,
) -> StructureVariant | None:
    matches = tuple(
        item
        for item in bundle.structure_variants
        if item.current_structure_snapshot_id
        == calculation.input_structure_snapshot_id
    )
    if len(matches) > 1:
        raise ApplicationServiceError(
            "multiple StructureVariants currently own the Calculation input snapshot"
        )
    return matches[0] if matches else None


def _require_calculation(
    bundle: ProjectBundle,
    calculation_id: CalculationId,
) -> Calculation:
    matches = tuple(item for item in bundle.calculations if item.id == calculation_id)
    if len(matches) != 1:
        raise ApplicationServiceError("Calculation is absent or duplicated")
    return matches[0]


def _require_attempt(
    bundle: ProjectBundle,
    attempt_id: ExecutionAttemptId,
) -> ExecutionAttempt:
    matches = tuple(item for item in bundle.execution_attempts if item.id == attempt_id)
    if len(matches) != 1:
        raise ApplicationServiceError("ExecutionAttempt is absent or duplicated")
    return matches[0]


def _require_fingerprint(
    bundle: ProjectBundle,
    calculation: Calculation,
) -> MethodFingerprint:
    matches = tuple(
        item
        for item in bundle.method_fingerprints
        if item.id == calculation.method_fingerprint_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError(
            "Calculation MethodFingerprint is absent or duplicated"
        )
    return matches[0]


def _require_snapshot(
    bundle: ProjectBundle,
    calculation: Calculation,
) -> StructureSnapshot:
    matches = tuple(
        item
        for item in bundle.structure_snapshots
        if item.id == calculation.input_structure_snapshot_id
    )
    if len(matches) != 1:
        raise ApplicationServiceError(
            "Calculation input StructureSnapshot is absent or duplicated"
        )
    return matches[0]


__all__ = [
    "ProjectResultCenterApplicationService",
    "ResultCenterAnalysisReceipt",
]
