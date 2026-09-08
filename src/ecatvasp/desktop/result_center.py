"""Desktop-facing Result Center actions for v1.1 Block 5."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ecatvasp.api.result_center import ProjectResultCenterApplicationService
from ecatvasp.domain import CalculationId
from ecatvasp.storage import ProjectStore


def result_catalog_action(project_root: Path | str) -> dict[str, object]:
    service = ProjectResultCenterApplicationService(ProjectStore(project_root))
    return {"project_root": str(Path(project_root)), **service.catalog()}


def analyze_result_action(
    *,
    project_root: Path | str,
    calculation_id: str,
) -> dict[str, object]:
    service = ProjectResultCenterApplicationService(ProjectStore(project_root))
    receipt = service.analyze_result(
        calculation_id=CalculationId(UUID(calculation_id)),
    )
    energies = receipt.result.energies
    return {
        "project_root": str(Path(project_root)),
        "calculation_id": str(receipt.calculation_id),
        "attempt_id": str(receipt.attempt_id),
        "intake_hash": receipt.intake_hash,
        "scientific_verdict": receipt.assessment.overall.value,
        "electronic_verdict": receipt.assessment.electronic.value,
        "ionic_verdict": receipt.assessment.ionic.value,
        "free_energy_toten_ev": energies.free_energy_toten_ev,
        "energy_without_entropy_ev": energies.energy_without_entropy_ev,
        "energy_sigma0_ev": energies.energy_sigma0_ev,
        "fermi_energy_ev": energies.fermi_energy_ev,
        "ionic_steps": receipt.result.ionic_steps,
        "electronic_steps": receipt.result.electronic_steps,
        "termination_observed": receipt.result.termination_observed,
        "evidence_codes": list(receipt.assessment.evidence_codes),
        "force_count": (
            len(receipt.result.forces.atom_uids)
            if receipt.result.forces is not None
            else 0
        ),
        "frequency_mode_count": (
            len(receipt.result.frequencies.modes)
            if receipt.result.frequencies is not None
            else 0
        ),
    }


def promote_result_structure_action(
    *,
    project_root: Path | str,
    calculation_id: str,
    label: str | None = None,
) -> dict[str, object]:
    service = ProjectResultCenterApplicationService(ProjectStore(project_root))
    receipt = service.promote_result_structure(
        calculation_id=CalculationId(UUID(calculation_id)),
        label=label,
    )
    return {
        "project_root": str(Path(project_root)),
        "calculation_id": str(receipt.reconstruction.calculation_id),
        "structure_variant_id": str(receipt.promotion.updated_variant.id),
        "promoted_structure_snapshot_id": str(receipt.promotion.snapshot.id),
        "parent_structure_snapshot_id": str(receipt.reconstruction.input_snapshot_id),
        "scientific_verdict": receipt.promotion.convergence.overall.value,
        "source_artifact_id": str(receipt.reconstruction.source_artifact_id),
        "source_sha256": receipt.reconstruction.source_sha256,
    }


__all__ = [
    "analyze_result_action",
    "promote_result_structure_action",
    "result_catalog_action",
]
