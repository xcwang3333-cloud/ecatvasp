from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from ecatvasp.api.reaction_workspace import (
    HERPresetAnalysisBindings,
    ProjectReactionWorkspaceApplicationService,
)
from ecatvasp.api.thermochemistry_workspace import ProjectThermochemistryApplicationService
from ecatvasp.domain import AnalysisId, AtomUid
from ecatvasp.storage import ProjectStore
from ecatvasp.thermo import (
    CHEConditions,
    CHEPhSemantics,
    ElectrocatalysisPresetKind,
    ElectrodePotentialReference,
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
from ui.desktop.packaging.installed_acceptance_fixture import (
    build_installed_acceptance_fixture,
)


def _analysis_id(value: str) -> AnalysisId:
    return AnalysisId(UUID(value))


def _conditions(*, potential_v: float, ph: float) -> CHEConditions:
    return CHEConditions(
        temperature_k=298.15,
        potential_v=potential_v,
        ph=ph,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )


def _materialize_sources(root: Path) -> tuple[str, str, str]:
    fixture = build_installed_acceptance_fixture(root)
    service = ProjectThermochemistryApplicationService(ProjectStore(root))
    common = {
        "temperature_k": 298.15,
        "electronic_energy_kind": ElectronicEnergyKind.SIGMA_ZERO,
        "electronic_entropy_policy": ElectronicEntropyPolicy.NEGLECTED,
        "frequency_cutoff_cm_inverse": 50.0,
        "imaginary_mode_policy": ImaginaryModePolicy.REJECT_ANY,
    }
    clean = service.materialize_harmonic(
        calculation_id=UUID(fixture.clean_frequency_calculation_id),
        subject_kind=ThermochemistrySubjectKind.SURFACE,
        low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
        exclusions=(),
        **common,
    )
    ads = service.materialize_harmonic(
        calculation_id=UUID(fixture.ads_frequency_calculation_id),
        subject_kind=ThermochemistrySubjectKind.ADSORBATE,
        low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
        exclusions=(),
        **common,
    )
    h2 = service.materialize_gas_reference(
        calculation_id=UUID(fixture.h2_frequency_calculation_id),
        species=GasReferenceSpecies.H2,
        pressure_pa=100000.0,
        standard_state=ThermochemicalStandardState.IDEAL_GAS_1_BAR,
        geometry_kind=GasGeometryKind.LINEAR,
        symmetry_number=2,
        spin_multiplicity=1,
        atomic_masses=tuple(
            GasAtomicMass(atom_uid=AtomUid(UUID(atom_uid)), mass_amu=1.00784)
            for atom_uid in fixture.h2_atom_uids
        ),
        low_frequency_policy=LowFrequencyPolicy.EXCLUDE_EXPLICIT,
        exclusions=(
            ModeExclusion(mode_index=1, reason=ModeExclusionReason.TRANSLATIONAL),
            ModeExclusion(mode_index=2, reason=ModeExclusionReason.TRANSLATIONAL),
            ModeExclusion(mode_index=3, reason=ModeExclusionReason.TRANSLATIONAL),
            ModeExclusion(mode_index=4, reason=ModeExclusionReason.ROTATIONAL),
            ModeExclusion(mode_index=5, reason=ModeExclusionReason.ROTATIONAL),
        ),
        **common,
    )
    return (
        str(clean["analysis_id"]),
        str(ads["analysis_id"]),
        str(h2["analysis_id"]),
    )


def _materialize_reaction(
    root: Path,
    clean_id: str,
    ads_id: str,
    h2_id: str,
) -> dict[str, object]:
    service = ProjectReactionWorkspaceApplicationService(ProjectStore(root))
    return service.materialize_preset_diagram(
        preset_kind=ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY,
        bindings=HERPresetAnalysisBindings(
            clean_surface_analysis_id=_analysis_id(clean_id),
            h_adsorbed_analysis_id=_analysis_id(ads_id),
            h2_reference_analysis_id=_analysis_id(h2_id),
        ),
        baseline_conditions=_conditions(potential_v=0.0, ph=0.0),
        requested_conditions=_conditions(potential_v=-0.25, ph=7.0),
    )


def test_reaction_diagram_reuses_exact_identity_across_process_restart(
    tmp_path: Path,
) -> None:
    clean_id, ads_id, h2_id = _materialize_sources(tmp_path)
    first = _materialize_reaction(tmp_path, clean_id, ads_id, h2_id)
    assert first["reused"] is False

    script = r'''
import json
import sys
from pathlib import Path
from uuid import UUID

from ecatvasp.api.reaction_workspace import (
    HERPresetAnalysisBindings,
    ProjectReactionWorkspaceApplicationService,
)
from ecatvasp.domain import AnalysisId
from ecatvasp.storage import ProjectStore
from ecatvasp.thermo import (
    CHEConditions,
    CHEPhSemantics,
    ElectrocatalysisPresetKind,
    ElectrodePotentialReference,
)

root = Path(sys.argv[1])
clean_id = AnalysisId(UUID(sys.argv[2]))
ads_id = AnalysisId(UUID(sys.argv[3]))
h2_id = AnalysisId(UUID(sys.argv[4]))
service = ProjectReactionWorkspaceApplicationService(ProjectStore(root))
receipt = service.materialize_preset_diagram(
    preset_kind=ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY,
    bindings=HERPresetAnalysisBindings(
        clean_surface_analysis_id=clean_id,
        h_adsorbed_analysis_id=ads_id,
        h2_reference_analysis_id=h2_id,
    ),
    baseline_conditions=CHEConditions(
        temperature_k=298.15,
        potential_v=0.0,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    ),
    requested_conditions=CHEConditions(
        temperature_k=298.15,
        potential_v=-0.25,
        ph=7.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    ),
)
print(json.dumps(receipt, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), clean_id, ads_id, h2_id],
        check=True,
        capture_output=True,
        text=True,
    )
    restarted = json.loads(completed.stdout.strip().splitlines()[-1])

    assert restarted["reused"] is True, {
        "first": first,
        "restarted": restarted,
        "stderr": completed.stderr,
    }
    assert restarted["analysis_id"] == first["analysis_id"]
    assert restarted["artifact_id"] == first["artifact_id"]
