"""Desktop actions for the v1.1 Electronic Analysis Workspace."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ecatvasp.analysis import (
    BandCenterEnergyReference,
    BandCenterKind,
    BandCenterSpinMode,
    ProjectionScope,
)
from ecatvasp.api.electronic_workspace import ProjectElectronicAnalysisApplicationService
from ecatvasp.domain import AnalysisId, AtomUid, CalculationId
from ecatvasp.storage import ProjectStore


def electronic_analysis_catalog_action(project_root: Path | str) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectElectronicAnalysisApplicationService(ProjectStore(root))
    return {"project_root": str(root), **service.catalog()}


def materialize_dos_analysis_action(
    *,
    project_root: Path | str,
    calculation_id: str,
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectElectronicAnalysisApplicationService(ProjectStore(root))
    return {
        "project_root": str(root),
        **service.materialize_dos(
            calculation_id=CalculationId(UUID(calculation_id)),
        ),
    }


def electronic_analysis_view_action(
    *,
    project_root: Path | str,
    analysis_id: str,
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectElectronicAnalysisApplicationService(ProjectStore(root))
    return {
        "project_root": str(root),
        **service.analysis_view(analysis_id=AnalysisId(UUID(analysis_id))),
    }


def materialize_band_center_action(
    *,
    project_root: Path | str,
    source_analysis_id: str,
    kind: str,
    scope: str,
    spin: str,
    atom_uid: str | None,
    element: str | None,
    energy_reference: str,
    window_lower_ev: float,
    window_upper_ev: float,
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectElectronicAnalysisApplicationService(ProjectStore(root))
    return {
        "project_root": str(root),
        **service.materialize_band_center(
            source_analysis_id=AnalysisId(UUID(source_analysis_id)),
            kind=BandCenterKind(kind),
            scope=ProjectionScope(scope),
            spin=BandCenterSpinMode(spin),
            atom_uid=AtomUid(UUID(atom_uid)) if atom_uid is not None else None,
            element=element,
            energy_reference=BandCenterEnergyReference(energy_reference),
            window_lower_ev=window_lower_ev,
            window_upper_ev=window_upper_ev,
        ),
    }


__all__ = [
    "electronic_analysis_catalog_action",
    "electronic_analysis_view_action",
    "materialize_band_center_action",
    "materialize_dos_analysis_action",
]
