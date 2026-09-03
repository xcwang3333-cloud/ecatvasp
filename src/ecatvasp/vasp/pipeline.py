"""End-to-end preparation for the v0.3 core production VASP recipes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ecatvasp.domain.calculation import Calculation
from ecatvasp.domain.entities import StructureSnapshot
from ecatvasp.domain.ids import CalculationId
from ecatvasp.domain.method import MethodFingerprint, ProtocolDefinition
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.incar import PreparedIncar, UidMagmom, prepare_incar
from ecatvasp.vasp.kpoints import (
    ECATVASP_KPOINT_CENTERING,
    KPointCentering,
    KPointValidationEvidence,
    PreparedKPoints,
    prepare_kpoints,
    validate_project_lock_kpoints,
    validate_protocol_kpoint_contract,
)
from ecatvasp.vasp.materialization import MaterializedInputSet
from ecatvasp.vasp.materialization_guard import materialize_calculation_inputs
from ecatvasp.vasp.poscar import PreparedPoscar, UidSelectiveDynamics, prepare_poscar
from ecatvasp.vasp.potcar import (
    EncCutValidationEvidence,
    LocalPotcarLibrary,
    ResolvedPotcarSet,
    validate_project_lock_encut,
)
from ecatvasp.vasp.recipes import (
    RECIPE_ADSORBATE_RELAX,
    RECIPE_GAS_RELAX,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_SLAB_RELAX,
    validate_calculation_recipe_contract,
)

_CORE_PIPELINE_RECIPES = frozenset(
    {
        RECIPE_SLAB_RELAX,
        RECIPE_ADSORBATE_RELAX,
        RECIPE_GAS_RELAX,
        RECIPE_GROUND_STATE_STATIC,
    }
)


class CoreInputPipelineError(ValueError):
    """Raised when a calculation cannot enter the Block 7 production pipeline."""


@dataclass(frozen=True, slots=True)
class CoreInputPipelineResult:
    """Prepared and materialized inputs for one exact core VASP Calculation."""

    calculation_id: CalculationId
    recipe_id: str
    system_context: VaspSystemContext
    prepared_poscar: PreparedPoscar
    resolved_potcars: ResolvedPotcarSet
    prepared_kpoints: PreparedKPoints
    prepared_incar: PreparedIncar
    materialized: MaterializedInputSet

    def __post_init__(self) -> None:
        snapshot_ids = {
            self.prepared_poscar.structure_snapshot_id,
            self.prepared_kpoints.structure_snapshot_id,
            self.prepared_incar.structure_snapshot_id,
        }
        if len(snapshot_ids) != 1:
            raise ValueError("core pipeline preparations must target one StructureSnapshot")
        if self.prepared_kpoints.system_context != self.system_context:
            raise ValueError("core pipeline k-point context does not match result context")
        if self.resolved_potcars.spec.species_order != self.prepared_poscar.species_order:
            raise ValueError("core pipeline POTCAR order does not match POSCAR species order")
        if self.prepared_incar.recipe_id != self.recipe_id:
            raise ValueError("core pipeline INCAR recipe does not match result recipe")
        if self.materialized.calculation_id != self.calculation_id:
            raise ValueError("core pipeline materialization belongs to another Calculation")


def prepare_core_calculation_inputs(
    *,
    project_root: Path | str,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    potcar_library: LocalPotcarLibrary,
    project_lock: ProjectNumericalLock,
    encut_evidence: EncCutValidationEvidence,
    kpoint_evidence: KPointValidationEvidence | None = None,
    selective_dynamics: UidSelectiveDynamics | None = None,
    magmom: UidMagmom | None = None,
) -> CoreInputPipelineResult:
    """Prepare and materialize one core production recipe without hidden inference.

    Gas-phase static calculations intentionally use ``GroundStateStatic`` with
    ``MOLECULE_0D`` rather than introducing a separate gas-static CalculationType.
    Licensed POTCAR files are resolved only to verified local paths and safe metadata;
    their bodies are never copied into the project by this pipeline.
    """

    _validate_core_contract(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        project_lock=project_lock,
    )
    centering = _protocol_kpoint_centering(fingerprint.protocol)

    prepared_poscar = prepare_poscar(snapshot, selective_dynamics=selective_dynamics)
    prepared_kpoints = prepare_kpoints(
        snapshot,
        policy=fingerprint.protocol.kpoints,
        system_context=system_context,
        centering=centering,
    )
    validate_protocol_kpoint_contract(
        protocol=fingerprint.protocol,
        prepared=prepared_kpoints,
    )
    validate_project_lock_kpoints(
        lock=project_lock,
        prepared=prepared_kpoints,
        evidence=kpoint_evidence,
    )

    resolved_potcars = potcar_library.resolve(
        prepared_poscar=prepared_poscar,
        method=fingerprint.method,
    )
    validate_project_lock_encut(
        lock=project_lock,
        spec=resolved_potcars.spec,
        evidence=encut_evidence,
    )

    prepared_incar = prepare_incar(
        snapshot=snapshot,
        method=fingerprint.method,
        protocol=fingerprint.protocol,
        recipe=fingerprint.recipe,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=resolved_potcars.spec,
        project_lock=project_lock,
        magmom=magmom,
    )
    materialized = materialize_calculation_inputs(
        project_root=project_root,
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        recipe=fingerprint.recipe,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_incar=prepared_incar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=resolved_potcars.spec,
        project_lock=project_lock,
    )
    return CoreInputPipelineResult(
        calculation_id=calculation.id,
        recipe_id=fingerprint.recipe.recipe_id,
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        resolved_potcars=resolved_potcars,
        prepared_kpoints=prepared_kpoints,
        prepared_incar=prepared_incar,
        materialized=materialized,
    )


def _validate_core_contract(
    *,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock,
) -> None:
    if calculation.input_structure_snapshot_id != snapshot.id:
        raise CoreInputPipelineError("Calculation input snapshot does not match StructureSnapshot")
    if calculation.method_fingerprint_id != fingerprint.id:
        raise CoreInputPipelineError("Calculation MethodFingerprint id does not match fingerprint")
    if calculation.recipe_id != fingerprint.recipe.recipe_id:
        raise CoreInputPipelineError("Calculation recipe does not match MethodFingerprint recipe")

    spec = validate_calculation_recipe_contract(
        calculation=calculation,
        system_context=system_context,
        project_lock=project_lock,
    )
    if spec.recipe_id not in _CORE_PIPELINE_RECIPES:
        raise CoreInputPipelineError(
            f"recipe {spec.recipe_id} is outside the v0.3 Block 7 core pipeline"
        )
    if fingerprint.recipe.version != spec.version:
        raise CoreInputPipelineError("MethodFingerprint recipe version is not canonical")
    if project_lock.core_method_hash != fingerprint.core_method_hash:
        raise CoreInputPipelineError(
            "Project numerical lock core method does not match fingerprint"
        )
    if project_lock.encut_ev != fingerprint.protocol.encut_ev:
        raise CoreInputPipelineError("Project numerical lock ENCUT does not match Protocol")
    if project_lock.kpoints != fingerprint.protocol.kpoints:
        raise CoreInputPipelineError("Project numerical lock k-points do not match Protocol")


def _protocol_kpoint_centering(protocol: ProtocolDefinition) -> KPointCentering:
    matches = tuple(
        item for item in protocol.extra_parameters if item.name == ECATVASP_KPOINT_CENTERING
    )
    if len(matches) != 1 or not isinstance(matches[0].value, str):
        raise CoreInputPipelineError(
            "Protocol requires exactly one string ECATVASP_KPOINT_CENTERING parameter"
        )
    try:
        return KPointCentering(matches[0].value)
    except ValueError as error:
        raise CoreInputPipelineError("Protocol has an invalid k-point centering value") from error
