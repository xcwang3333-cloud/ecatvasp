"""End-to-end VASP preparation for v0.3 analysis prerequisite calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ecatvasp.domain.calculation import Calculation
from ecatvasp.domain.entities import StructureSnapshot
from ecatvasp.domain.ids import CalculationId
from ecatvasp.domain.method import (
    DftUSetting,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    ProtocolDefinition,
    SpinTreatment,
    canonical_sha256,
)
from ecatvasp.vasp.analysis_prerequisites import (
    prepare_analysis_prerequisite_incar,
    validate_analysis_prerequisite_recipe,
)
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.incar import PreparedIncar, UidMagmom
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
from ecatvasp.vasp.poscar import PreparedPoscar, prepare_poscar
from ecatvasp.vasp.potcar import (
    EncCutValidationEvidence,
    LocalPotcarLibrary,
    ResolvedPotcarSet,
    validate_project_lock_encut,
)
from ecatvasp.vasp.recipes import (
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_LOBSTER_PREREQUISITE,
    validate_calculation_recipe_contract,
)

_ANALYSIS_PIPELINE_RECIPES = frozenset(
    {
        RECIPE_DOS_PREREQUISITE,
        RECIPE_CHARGE_DENSITY_STATIC,
        RECIPE_LOBSTER_PREREQUISITE,
    }
)


class AnalysisPrerequisiteInputPipelineError(ValueError):
    """Raised when a Calculation cannot enter the Block 9 prerequisite pipeline."""


@dataclass(frozen=True, slots=True)
class AnalysisPrerequisiteInputResult:
    """Prepared and materialized inputs for one exact analysis prerequisite Calculation."""

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
            raise ValueError("analysis prerequisite preparations must target one StructureSnapshot")
        if self.prepared_kpoints.system_context != self.system_context:
            raise ValueError("analysis prerequisite k-point context does not match result context")
        if self.resolved_potcars.spec.species_order != self.prepared_poscar.species_order:
            raise ValueError("analysis prerequisite POTCAR order does not match POSCAR")
        if self.prepared_incar.recipe_id != self.recipe_id:
            raise ValueError("analysis prerequisite INCAR recipe does not match result recipe")
        if self.materialized.calculation_id != self.calculation_id:
            raise ValueError("analysis prerequisite materialization belongs to another Calculation")


@dataclass(frozen=True, slots=True)
class ChargeDifferenceTripletMember:
    """One frozen member of a combined/slab/adsorbate charge-density triplet."""

    calculation: Calculation
    snapshot: StructureSnapshot
    fingerprint: MethodFingerprint
    project_lock: ProjectNumericalLock
    encut_evidence: EncCutValidationEvidence
    kpoint_evidence: KPointValidationEvidence | None = None
    magmom: UidMagmom | None = None


@dataclass(frozen=True, slots=True)
class ChargeDifferenceTriplet:
    """Strict frozen-geometry prerequisite contract for charge-density subtraction."""

    combined: ChargeDifferenceTripletMember
    slab: ChargeDifferenceTripletMember
    adsorbate: ChargeDifferenceTripletMember
    system_context: VaspSystemContext
    contract_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_charge_difference_triplet(self)
        object.__setattr__(
            self,
            "contract_hash",
            canonical_sha256(
                {
                    "combined_calculation_id": self.combined.calculation.id,
                    "slab_calculation_id": self.slab.calculation.id,
                    "adsorbate_calculation_id": self.adsorbate.calculation.id,
                    "combined_snapshot_id": self.combined.snapshot.id,
                    "slab_snapshot_id": self.slab.snapshot.id,
                    "adsorbate_snapshot_id": self.adsorbate.snapshot.id,
                    "combined_fingerprint": self.combined.fingerprint.instance_hash,
                    "slab_fingerprint": self.slab.fingerprint.instance_hash,
                    "adsorbate_fingerprint": self.adsorbate.fingerprint.instance_hash,
                    "system_context": self.system_context,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class ChargeDifferenceTripletInputResult:
    """Materialized VASP inputs for all three strict charge-difference prerequisites."""

    contract_hash: str
    combined: AnalysisPrerequisiteInputResult
    slab: AnalysisPrerequisiteInputResult
    adsorbate: AnalysisPrerequisiteInputResult


@dataclass(frozen=True, slots=True)
class _PreparedAnalysisPrerequisite:
    calculation: Calculation
    snapshot: StructureSnapshot
    fingerprint: MethodFingerprint
    system_context: VaspSystemContext
    project_lock: ProjectNumericalLock
    prepared_poscar: PreparedPoscar
    resolved_potcars: ResolvedPotcarSet
    prepared_kpoints: PreparedKPoints
    prepared_incar: PreparedIncar


def prepare_analysis_prerequisite_inputs(
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
    magmom: UidMagmom | None = None,
) -> AnalysisPrerequisiteInputResult:
    """Prepare and materialize one DOS/charge/LOBSTER prerequisite Calculation."""

    prepared = _prepare_analysis_prerequisite(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        potcar_library=potcar_library,
        project_lock=project_lock,
        encut_evidence=encut_evidence,
        kpoint_evidence=kpoint_evidence,
        magmom=magmom,
    )
    return _materialize_prepared(project_root=project_root, prepared=prepared)


def prepare_charge_difference_triplet_inputs(
    *,
    project_root: Path | str,
    triplet: ChargeDifferenceTriplet,
    potcar_library: LocalPotcarLibrary,
) -> ChargeDifferenceTripletInputResult:
    """Preflight all three charge-density members before materializing any input set."""

    members = (triplet.combined, triplet.slab, triplet.adsorbate)
    prepared_members = tuple(
        _prepare_analysis_prerequisite(
            calculation=member.calculation,
            snapshot=member.snapshot,
            fingerprint=member.fingerprint,
            system_context=triplet.system_context,
            potcar_library=potcar_library,
            project_lock=member.project_lock,
            encut_evidence=member.encut_evidence,
            kpoint_evidence=member.kpoint_evidence,
            magmom=member.magmom,
        )
        for member in members
    )
    materialized = tuple(
        _materialize_prepared(project_root=project_root, prepared=item)
        for item in prepared_members
    )
    return ChargeDifferenceTripletInputResult(
        contract_hash=triplet.contract_hash,
        combined=materialized[0],
        slab=materialized[1],
        adsorbate=materialized[2],
    )


def _prepare_analysis_prerequisite(
    *,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    potcar_library: LocalPotcarLibrary,
    project_lock: ProjectNumericalLock,
    encut_evidence: EncCutValidationEvidence,
    kpoint_evidence: KPointValidationEvidence | None,
    magmom: UidMagmom | None,
) -> _PreparedAnalysisPrerequisite:
    _validate_analysis_contract(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        project_lock=project_lock,
    )
    centering = _protocol_kpoint_centering(fingerprint.protocol)
    prepared_poscar = prepare_poscar(snapshot)
    prepared_kpoints = prepare_kpoints(
        snapshot,
        policy=fingerprint.protocol.kpoints,
        system_context=system_context,
        centering=centering,
    )
    validate_protocol_kpoint_contract(protocol=fingerprint.protocol, prepared=prepared_kpoints)
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
    prepared_incar = prepare_analysis_prerequisite_incar(
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
    return _PreparedAnalysisPrerequisite(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        system_context=system_context,
        project_lock=project_lock,
        prepared_poscar=prepared_poscar,
        resolved_potcars=resolved_potcars,
        prepared_kpoints=prepared_kpoints,
        prepared_incar=prepared_incar,
    )


def _materialize_prepared(
    *,
    project_root: Path | str,
    prepared: _PreparedAnalysisPrerequisite,
) -> AnalysisPrerequisiteInputResult:
    materialized = materialize_calculation_inputs(
        project_root=project_root,
        calculation=prepared.calculation,
        snapshot=prepared.snapshot,
        fingerprint=prepared.fingerprint,
        recipe=prepared.fingerprint.recipe,
        system_context=prepared.system_context,
        prepared_poscar=prepared.prepared_poscar,
        prepared_incar=prepared.prepared_incar,
        prepared_kpoints=prepared.prepared_kpoints,
        potcar_spec=prepared.resolved_potcars.spec,
        project_lock=prepared.project_lock,
    )
    return AnalysisPrerequisiteInputResult(
        calculation_id=prepared.calculation.id,
        recipe_id=prepared.fingerprint.recipe.recipe_id,
        system_context=prepared.system_context,
        prepared_poscar=prepared.prepared_poscar,
        resolved_potcars=prepared.resolved_potcars,
        prepared_kpoints=prepared.prepared_kpoints,
        prepared_incar=prepared.prepared_incar,
        materialized=materialized,
    )


def _validate_analysis_contract(
    *,
    calculation: Calculation,
    snapshot: StructureSnapshot,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    project_lock: ProjectNumericalLock,
) -> None:
    if calculation.input_structure_snapshot_id != snapshot.id:
        raise AnalysisPrerequisiteInputPipelineError(
            "Calculation input snapshot does not match StructureSnapshot"
        )
    if calculation.method_fingerprint_id != fingerprint.id:
        raise AnalysisPrerequisiteInputPipelineError(
            "Calculation MethodFingerprint id does not match fingerprint"
        )
    if calculation.recipe_id != fingerprint.recipe.recipe_id:
        raise AnalysisPrerequisiteInputPipelineError(
            "Calculation recipe does not match MethodFingerprint recipe"
        )
    spec = validate_calculation_recipe_contract(
        calculation=calculation,
        system_context=system_context,
        project_lock=project_lock,
    )
    if spec.recipe_id not in _ANALYSIS_PIPELINE_RECIPES:
        raise AnalysisPrerequisiteInputPipelineError(
            f"recipe {spec.recipe_id} is outside the v0.3 Block 9 prerequisite pipeline"
        )
    if fingerprint.recipe.version != spec.version:
        raise AnalysisPrerequisiteInputPipelineError(
            "MethodFingerprint recipe version is not canonical"
        )
    validate_analysis_prerequisite_recipe(fingerprint.recipe)
    if project_lock.core_method_hash != fingerprint.core_method_hash:
        raise AnalysisPrerequisiteInputPipelineError(
            "Project numerical lock core method does not match fingerprint"
        )
    if project_lock.encut_ev != fingerprint.protocol.encut_ev:
        raise AnalysisPrerequisiteInputPipelineError(
            "Project numerical lock ENCUT does not match Protocol"
        )
    if project_lock.kpoints != fingerprint.protocol.kpoints:
        raise AnalysisPrerequisiteInputPipelineError(
            "Project numerical lock k-points do not match Protocol"
        )


def _validate_charge_difference_triplet(triplet: ChargeDifferenceTriplet) -> None:
    members = (triplet.combined, triplet.slab, triplet.adsorbate)
    calculations = tuple(member.calculation for member in members)
    if len({item.id for item in calculations}) != 3:
        raise AnalysisPrerequisiteInputPipelineError(
            "charge-difference triplet requires three distinct Calculations"
        )
    if len({member.snapshot.id for member in members}) != 3:
        raise AnalysisPrerequisiteInputPipelineError(
            "charge-difference triplet requires three distinct StructureSnapshots"
        )
    if len({item.project_id for item in calculations}) != 1:
        raise AnalysisPrerequisiteInputPipelineError(
            "charge-difference triplet Calculations must belong to one Project"
        )
    for member in members:
        if member.calculation.recipe_id != RECIPE_CHARGE_DENSITY_STATIC:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference triplet members must use ChargeDensityStatic"
            )
        if member.fingerprint.recipe.recipe_id != RECIPE_CHARGE_DENSITY_STATIC:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference fingerprints must use ChargeDensityStatic"
            )
        if member.fingerprint.recipe.parameters:
            raise AnalysisPrerequisiteInputPipelineError(
                "ChargeDensityStatic fingerprints must not carry Recipe parameters"
            )
        if member.fingerprint.method.charge_e != 0.0:
            raise AnalysisPrerequisiteInputPipelineError(
                "v0.3 charge-difference triplets require neutral member calculations"
            )
        if member.project_lock.project_id != member.calculation.project_id:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference project lock belongs to another Project"
            )
        if member.project_lock.system_kind is not triplet.system_context.kind:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference members must share one VASP system context"
            )

    combined_snapshot = triplet.combined.snapshot
    if triplet.slab.snapshot.lattice != combined_snapshot.lattice or (
        triplet.adsorbate.snapshot.lattice != combined_snapshot.lattice
    ):
        raise AnalysisPrerequisiteInputPipelineError(
            "charge-difference triplet requires exactly the same lattice"
        )
    if triplet.slab.snapshot.periodic != combined_snapshot.periodic or (
        triplet.adsorbate.snapshot.periodic != combined_snapshot.periodic
    ):
        raise AnalysisPrerequisiteInputPipelineError(
            "charge-difference triplet requires identical periodic semantics"
        )

    combined_sites = {site.atom_uid: site for site in combined_snapshot.sites}
    slab_sites = {site.atom_uid: site for site in triplet.slab.snapshot.sites}
    adsorbate_sites = {site.atom_uid: site for site in triplet.adsorbate.snapshot.sites}
    if set(slab_sites) & set(adsorbate_sites):
        raise AnalysisPrerequisiteInputPipelineError(
            "slab and adsorbate charge-difference fragments must have disjoint atom_uids"
        )
    if set(combined_sites) != set(slab_sites) | set(adsorbate_sites):
        raise AnalysisPrerequisiteInputPipelineError(
            "slab and adsorbate atom_uids must exactly partition the combined structure"
        )
    for atom_uid, site in {**slab_sites, **adsorbate_sites}.items():
        if combined_sites[atom_uid] != site:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference fragments must preserve frozen element and coordinates"
            )

    methods = tuple(member.fingerprint.method for member in members)
    if len({_global_method_hash(method) for method in methods}) != 1:
        raise AnalysisPrerequisiteInputPipelineError(
            "charge-difference members must share all non-element-specific Method settings"
        )
    _validate_fragment_element_methods(
        combined=triplet.combined.fingerprint.method,
        fragment=triplet.slab.fingerprint.method,
        fragment_snapshot=triplet.slab.snapshot,
    )
    _validate_fragment_element_methods(
        combined=triplet.combined.fingerprint.method,
        fragment=triplet.adsorbate.fingerprint.method,
        fragment_snapshot=triplet.adsorbate.snapshot,
    )
    if len({_protocol_numerical_hash(member.fingerprint.protocol) for member in members}) != 1:
        raise AnalysisPrerequisiteInputPipelineError(
            "charge-difference members must share the same numerical/electronic Protocol"
        )
    _validate_triplet_magmom(triplet)
    _validate_triplet_member_bindings(triplet)


def _validate_triplet_member_bindings(triplet: ChargeDifferenceTriplet) -> None:
    for member in (triplet.combined, triplet.slab, triplet.adsorbate):
        if member.calculation.input_structure_snapshot_id != member.snapshot.id:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference Calculation input snapshot does not match member snapshot"
            )
        if member.calculation.method_fingerprint_id != member.fingerprint.id:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference Calculation MethodFingerprint does not match member fingerprint"
            )
        if member.calculation.recipe_id != member.fingerprint.recipe.recipe_id:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference Calculation recipe does not match member fingerprint"
            )
        if member.project_lock.core_method_hash != member.fingerprint.core_method_hash:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference project lock core method does not match member fingerprint"
            )
        if member.project_lock.encut_ev != member.fingerprint.protocol.encut_ev:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference project lock ENCUT does not match member Protocol"
            )
        if member.project_lock.kpoints != member.fingerprint.protocol.kpoints:
            raise AnalysisPrerequisiteInputPipelineError(
                "charge-difference project lock k-points do not match member Protocol"
            )


def _global_method_hash(method: MethodDefinition) -> str:
    return canonical_sha256(
        {
            "xc_functional": method.xc_functional,
            "potcar_family": method.potcar_family,
            "engine": method.engine,
            "engine_version": method.engine_version,
            "dispersion_model": method.dispersion_model,
            "spin_treatment": method.spin_treatment,
            "soc": method.soc,
            "solvation_model": method.solvation_model,
            "charge_e": method.charge_e,
            "electric_field_ev_per_angstrom": method.electric_field_ev_per_angstrom,
            "extra_parameters": method.extra_parameters,
        }
    )


def _protocol_numerical_hash(protocol: ProtocolDefinition) -> str:
    return canonical_sha256(
        {
            "encut_ev": protocol.encut_ev,
            "kpoints": protocol.kpoints,
            "precision": protocol.precision,
            "ediff_ev": protocol.ediff_ev,
            "ediffg_ev_per_angstrom": protocol.ediffg_ev_per_angstrom,
            "ismear": protocol.ismear,
            "sigma_ev": protocol.sigma_ev,
            "dipole_policy": protocol.dipole_policy,
            "lreal": protocol.lreal,
            "isym": protocol.isym,
            "extra_parameters": protocol.extra_parameters,
        }
    )


def _validate_fragment_element_methods(
    *,
    combined: MethodDefinition,
    fragment: MethodDefinition,
    fragment_snapshot: StructureSnapshot,
) -> None:
    elements = {site.element for site in fragment_snapshot.sites}
    combined_potcars = _potcar_map(combined.potcars)
    fragment_potcars = _potcar_map(fragment.potcars)
    if set(fragment_potcars) != elements:
        raise AnalysisPrerequisiteInputPipelineError(
            "fragment POTCAR identities must exactly cover fragment species"
        )
    for element in elements:
        if combined_potcars.get(element) != fragment_potcars[element]:
            raise AnalysisPrerequisiteInputPipelineError(
                "shared elements must use identical POTCAR identities across the triplet"
            )
    combined_dft_u = _dft_u_map(combined.dft_u)
    fragment_dft_u = _dft_u_map(fragment.dft_u)
    if set(fragment_dft_u) - elements:
        raise AnalysisPrerequisiteInputPipelineError(
            "fragment DFT+U settings reference elements absent from the fragment"
        )
    for element in elements:
        if combined_dft_u.get(element) != fragment_dft_u.get(element):
            raise AnalysisPrerequisiteInputPipelineError(
                "shared elements must use identical DFT+U settings across the triplet"
            )


def _potcar_map(items: tuple[PotcarIdentity, ...]) -> dict[str, PotcarIdentity]:
    return {item.element: item for item in items}


def _dft_u_map(items: tuple[DftUSetting, ...]) -> dict[str, DftUSetting]:
    return {item.element: item for item in items}


def _validate_triplet_magmom(triplet: ChargeDifferenceTriplet) -> None:
    members = (triplet.combined, triplet.slab, triplet.adsorbate)
    treatment = triplet.combined.fingerprint.method.spin_treatment
    if treatment is SpinTreatment.UNPOLARIZED:
        if any(member.magmom is not None for member in members):
            raise AnalysisPrerequisiteInputPipelineError(
                "unpolarized charge-difference triplet must not carry MAGMOM mappings"
            )
        return
    if any(member.magmom is None for member in members):
        raise AnalysisPrerequisiteInputPipelineError(
            "spin-polarized charge-difference triplet requires MAGMOM for every member"
        )
    assert triplet.combined.magmom is not None
    combined_map = {item.atom_uid: item.components for item in triplet.combined.magmom.entries}
    if set(combined_map) != {site.atom_uid for site in triplet.combined.snapshot.sites}:
        raise AnalysisPrerequisiteInputPipelineError(
            "combined MAGMOM mapping must exactly cover combined atoms"
        )
    for member in (triplet.slab, triplet.adsorbate):
        assert member.magmom is not None
        member_map = {item.atom_uid: item.components for item in member.magmom.entries}
        member_uids = {site.atom_uid for site in member.snapshot.sites}
        if set(member_map) != member_uids:
            raise AnalysisPrerequisiteInputPipelineError(
                "fragment MAGMOM mapping must exactly cover fragment atoms"
            )
        if any(combined_map[uid] != components for uid, components in member_map.items()):
            raise AnalysisPrerequisiteInputPipelineError(
                "fragment MAGMOM values must be frozen subsets of the combined mapping"
            )


def _protocol_kpoint_centering(protocol: ProtocolDefinition) -> KPointCentering:
    matches = tuple(
        item for item in protocol.extra_parameters if item.name == ECATVASP_KPOINT_CENTERING
    )
    if len(matches) != 1 or not isinstance(matches[0].value, str):
        raise AnalysisPrerequisiteInputPipelineError(
            "Protocol requires exactly one string ECATVASP_KPOINT_CENTERING parameter"
        )
    try:
        return KPointCentering(matches[0].value)
    except ValueError as error:
        raise AnalysisPrerequisiteInputPipelineError(
            "Protocol has an invalid k-point centering value"
        ) from error
