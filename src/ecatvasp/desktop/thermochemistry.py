"""Desktop actions for the v1.1 Thermochemistry & Reaction Workspace."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ecatvasp.api.read_request_store import RequestScopedVerifiedReadStore
from ecatvasp.api.thermochemistry_workspace import ProjectThermochemistryApplicationService
from ecatvasp.desktop.protocol_v2_thermochemistry import (
    DesktopV2GasAtomicMass,
    DesktopV2ModeExclusion,
)
from ecatvasp.domain import AnalysisId, AtomUid, CalculationId
from ecatvasp.storage import ProjectStore
from ecatvasp.thermo import (
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasAtomicMass,
    GasGeometryKind,
    GasReferenceSpecies,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ModeExclusion,
    ModeExclusionReason,
    ThermochemicalStandardState,
    ThermochemistrySubjectKind,
)


def thermochemistry_catalog_action(project_root: Path | str) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectThermochemistryApplicationService(RequestScopedVerifiedReadStore(root))
    return {"project_root": str(root), **service.catalog()}


def materialize_harmonic_thermochemistry_action(
    *,
    project_root: Path | str,
    calculation_id: str,
    subject_kind: str,
    temperature_k: float,
    electronic_energy_kind: str,
    electronic_entropy_policy: str,
    frequency_cutoff_cm_inverse: float,
    imaginary_mode_policy: str,
    low_frequency_policy: str,
    exclusions: tuple[DesktopV2ModeExclusion, ...],
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectThermochemistryApplicationService(ProjectStore(root))
    return {
        "project_root": str(root),
        **service.materialize_harmonic(
            calculation_id=CalculationId(UUID(calculation_id)),
            subject_kind=ThermochemistrySubjectKind(subject_kind),
            temperature_k=temperature_k,
            electronic_energy_kind=ElectronicEnergyKind(electronic_energy_kind),
            electronic_entropy_policy=ElectronicEntropyPolicy(electronic_entropy_policy),
            frequency_cutoff_cm_inverse=frequency_cutoff_cm_inverse,
            imaginary_mode_policy=ImaginaryModePolicy(imaginary_mode_policy),
            low_frequency_policy=LowFrequencyPolicy(low_frequency_policy),
            exclusions=_mode_exclusions(exclusions),
        ),
    }


def materialize_gas_reference_action(
    *,
    project_root: Path | str,
    calculation_id: str,
    species: str,
    temperature_k: float,
    pressure_pa: float,
    standard_state: str,
    electronic_energy_kind: str,
    electronic_entropy_policy: str,
    geometry_kind: str,
    symmetry_number: int,
    spin_multiplicity: int,
    atomic_masses: tuple[DesktopV2GasAtomicMass, ...],
    frequency_cutoff_cm_inverse: float,
    imaginary_mode_policy: str,
    low_frequency_policy: str,
    exclusions: tuple[DesktopV2ModeExclusion, ...],
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectThermochemistryApplicationService(ProjectStore(root))
    return {
        "project_root": str(root),
        **service.materialize_gas_reference(
            calculation_id=CalculationId(UUID(calculation_id)),
            species=GasReferenceSpecies(species),
            temperature_k=temperature_k,
            pressure_pa=pressure_pa,
            standard_state=ThermochemicalStandardState(standard_state),
            electronic_energy_kind=ElectronicEnergyKind(electronic_energy_kind),
            electronic_entropy_policy=ElectronicEntropyPolicy(electronic_entropy_policy),
            geometry_kind=GasGeometryKind(geometry_kind),
            symmetry_number=symmetry_number,
            spin_multiplicity=spin_multiplicity,
            atomic_masses=tuple(
                GasAtomicMass(
                    atom_uid=AtomUid(UUID(item.atom_uid)),
                    mass_amu=item.mass_amu,
                    isotopologue_label=item.isotopologue_label,
                )
                for item in atomic_masses
            ),
            frequency_cutoff_cm_inverse=frequency_cutoff_cm_inverse,
            imaginary_mode_policy=ImaginaryModePolicy(imaginary_mode_policy),
            low_frequency_policy=LowFrequencyPolicy(low_frequency_policy),
            exclusions=_mode_exclusions(exclusions),
        ),
    }


def thermochemistry_view_action(
    *,
    project_root: Path | str,
    analysis_id: str,
) -> dict[str, object]:
    root = Path(project_root)
    service = ProjectThermochemistryApplicationService(RequestScopedVerifiedReadStore(root))
    return {
        "project_root": str(root),
        **service.analysis_view(analysis_id=AnalysisId(UUID(analysis_id))),
    }


def _mode_exclusions(
    values: tuple[DesktopV2ModeExclusion, ...],
) -> tuple[ModeExclusion, ...]:
    return tuple(
        ModeExclusion(
            mode_index=item.mode_index,
            reason=ModeExclusionReason(item.reason),
            note=item.note,
        )
        for item in values
    )


__all__ = [
    "materialize_gas_reference_action",
    "materialize_harmonic_thermochemistry_action",
    "thermochemistry_catalog_action",
    "thermochemistry_view_action",
]
