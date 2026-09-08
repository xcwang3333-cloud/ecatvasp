"""Task-oriented v1.1 calculation/workflow wizard over existing scientific authorities.

This module is an application composition layer. It does not introduce a new
scientific entity, a second workflow state machine, or a persisted numerical-lock
authority. Existing Method/Protocol/Recipe, workflow, VASP and ProjectStore
contracts remain authoritative.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError, ProjectApplicationService
from ecatvasp.domain import (
    AtomUid,
    KPointPolicy,
    KPointPolicyKind,
    MethodDefinition,
    MethodFingerprint,
    MethodFingerprintId,
    ParameterEntry,
    PotcarIdentity,
    ProtocolDefinition,
    RecipeIdentity,
    ScientificInputDigest,
    ScientificWorkflowPlan,
    SpinTreatment,
    StructureSnapshot,
    StructureSnapshotId,
    WorkflowPlanId,
    WorkflowRecipeIdentity,
)
from ecatvasp.provenance import scientific_hash
from ecatvasp.storage import ProjectBundle
from ecatvasp.vasp.analysis_prerequisites import (
    dos_recipe_parameters,
    lobster_recipe_parameters,
)
from ecatvasp.vasp.contracts import (
    ECATVASP_ECAT_STANDARD,
    LatticeAxis,
    ProjectNumericalLock,
    VaspSystemContext,
    VaspSystemKind,
)
from ecatvasp.vasp.frequency import FrequencySelection, frequency_recipe_parameters
from ecatvasp.vasp.incar import ecat_standard_protocol_parameters
from ecatvasp.vasp.kpoints import (
    ECATVASP_KPOINT_CENTERING,
    KPointCentering,
    KPointValidationEvidence,
    prepare_kpoints,
    validate_project_lock_kpoints,
)
from ecatvasp.vasp.poscar import prepare_poscar
from ecatvasp.vasp.potcar import (
    EncCutValidationEvidence,
    LocalPotcarLibrary,
    PotcarSpec,
    validate_encut_evidence,
    validate_project_lock_encut,
)
from ecatvasp.vasp.recipes import (
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    get_vasp_recipe_spec,
)
from ecatvasp.workflow import (
    WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION,
    WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION,
    WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    WorkflowRecipeSpec,
    evaluate_workflow_freshness,
    evaluate_workflow_recovery_policy,
    evaluate_workflow_scientific_gates,
    list_workflow_recipe_specs,
    reconcile_workflow_orchestration,
)
from ecatvasp.workflow.orchestration import (
    WorkflowOrchestrationAction,
    WorkflowOrchestrationEvaluation,
)


class CalculationWizardTask(StrEnum):
    """Researcher-facing task mapped to one canonical workflow recipe."""

    SLAB = "slab"
    ADSORBATE = "adsorbate"
    GAS_REFERENCE = "gas_reference"


@dataclass(frozen=True, slots=True)
class PotcarSymbolSelection:
    element: str
    symbol: str

    def __post_init__(self) -> None:
        if not self.element.strip() or not self.symbol.strip():
            raise ValueError("POTCAR element/symbol must not be blank")


@dataclass(frozen=True, slots=True)
class WizardMethodSettings:
    """Explicit method choices; POTCAR hashes are resolved from local licensed files."""

    xc_functional: str
    potcar_family: str
    potcar_root: Path
    potcar_symbols: tuple[PotcarSymbolSelection, ...]
    spin_treatment: SpinTreatment = SpinTreatment.COLLINEAR
    engine_version: str | None = None
    dispersion_model: str | None = None

    def __post_init__(self) -> None:
        if not self.xc_functional.strip() or not self.potcar_family.strip():
            raise ValueError("xc_functional and potcar_family must not be blank")
        if not self.potcar_symbols:
            raise ValueError("at least one explicit POTCAR symbol is required")
        elements = tuple(item.element for item in self.potcar_symbols)
        if len(elements) != len(set(elements)):
            raise ValueError("POTCAR selections must be unique by element")


@dataclass(frozen=True, slots=True)
class WizardProtocolSettings:
    """Visible ECAT_STANDARD numerical controls."""

    encut_ev: float
    kpoint_kind: KPointPolicyKind
    kpoint_mesh: tuple[int, int, int] | None = None
    kpoint_value: float | None = None
    kpoint_centering: KPointCentering = KPointCentering.GAMMA
    vacuum_axis: LatticeAxis | None = LatticeAxis.C
    precision: str = "Accurate"
    ediff_ev: float = 1e-5
    ediffg_ev_per_angstrom: float | None = -0.02
    ismear: int = 0
    sigma_ev: float = 0.05
    isym: int | None = None

    @property
    def kpoints(self) -> KPointPolicy:
        return KPointPolicy(
            kind=self.kpoint_kind,
            mesh=self.kpoint_mesh,
            value=self.kpoint_value,
        )


@dataclass(frozen=True, slots=True)
class WizardRecipeSettings:
    frequency_potim_angstrom: float = 0.015
    frequency_atom_uids: tuple[str, ...] = ()
    dos_nedos: int = 2001
    lobster_nbands: int | None = None


@dataclass(frozen=True, slots=True)
class WizardNumericalEvidence:
    """Typed real convergence evidence used to construct a transient project lock."""

    encut: EncCutValidationEvidence
    kpoints: KPointValidationEvidence | None = None


@dataclass(frozen=True, slots=True)
class WizardStepSummary:
    step_key: str
    recipe_id: str
    calculation_type: str
    method_fingerprint_id: str | None
    fingerprint_hash: str | None
    materialized: bool
    blocker_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CalculationWizardPreparationResult:
    project_id: str
    workflow_plan_id: str
    workflow_recipe_id: str
    root_structure_snapshot_id: str
    task: CalculationWizardTask
    system_kind: VaspSystemKind
    steps: tuple[WizardStepSummary, ...]
    reused_plan: bool


@dataclass(frozen=True, slots=True)
class CalculationWizardMaterializationResult:
    project_id: str
    workflow_plan_id: str
    step_key: str
    calculation_id: str
    workflow_step_binding_id: str
    reused: bool


_TASK_WORKFLOW_RECIPES = {
    CalculationWizardTask.SLAB: WORKFLOW_RECIPE_SLAB_SCIENTIFIC_PREPARATION,
    CalculationWizardTask.ADSORBATE: WORKFLOW_RECIPE_ADSORBATE_SCIENTIFIC_PREPARATION,
    CalculationWizardTask.GAS_REFERENCE: WORKFLOW_RECIPE_GAS_REFERENCE_PREPARATION,
}


class ProjectCalculationWizardApplicationService(ProjectApplicationService):
    """Typed calculation/workflow preparation over schema-v3 ProjectStore."""

    def catalog(self) -> dict[str, object]:
        bundle = self.store.open()
        conformer_ids = {item.structure_snapshot_id for item in bundle.state_conformers}
        current_variants = {
            item.current_structure_snapshot_id: item
            for item in bundle.structure_variants
            if item.current_structure_snapshot_id is not None
        }
        roots: list[dict[str, object]] = []
        for snapshot in bundle.structure_snapshots:
            variant = current_variants.get(snapshot.id)
            is_conformer = snapshot.id in conformer_ids
            if variant is None and not is_conformer:
                continue
            if is_conformer:
                task = CalculationWizardTask.ADSORBATE
                label = snapshot.label or "Adsorbate conformer"
            elif snapshot.periodic == (False, False, False):
                task = CalculationWizardTask.GAS_REFERENCE
                assert variant is not None
                label = snapshot.label or variant.name
            else:
                task = CalculationWizardTask.SLAB
                assert variant is not None
                label = snapshot.label or variant.name
            roots.append(
                {
                    "structure_snapshot_id": str(snapshot.id),
                    "structure_variant_id": str(variant.id) if variant is not None else None,
                    "label": label,
                    "atom_count": len(snapshot.sites),
                    "elements": sorted({site.element for site in snapshot.sites}),
                    "eligible_task": task.value,
                    "is_conformer": is_conformer,
                }
            )
        return {
            "project_id": str(bundle.project.id),
            "project_name": bundle.project.name,
            "roots": roots,
            "existing_workflows": [
                {
                    "workflow_plan_id": str(plan.id),
                    "workflow_recipe_id": plan.workflow_recipe.recipe_id,
                    "workflow_recipe_version": plan.workflow_recipe.version,
                    "root_structure_snapshot_id": str(plan.root_structure_snapshot_id),
                    "step_count": len(plan.steps),
                }
                for plan in bundle.workflow_plans
            ],
            "tasks": [
                {
                    "task": task.value,
                    "workflow_recipe_id": spec.recipe_id,
                    "workflow_recipe_version": spec.version,
                }
                for task in CalculationWizardTask
                for spec in (_workflow_spec(task),)
            ],
            "scientific_profile": {
                "standard_name": ECATVASP_ECAT_STANDARD,
                "default_ediffg_ev_per_angstrom": -0.02,
                "default_precision": "Accurate",
                "default_frequency_potim_angstrom": 0.015,
                "default_dos_nedos": 2001,
                "numerical_lock_policy": "validated_convergence_evidence_required",
            },
        }

    def prepare(
        self,
        *,
        task: CalculationWizardTask,
        root_structure_snapshot_id: StructureSnapshotId,
        method_settings: WizardMethodSettings,
        protocol_settings: WizardProtocolSettings,
        recipe_settings: WizardRecipeSettings | None = None,
    ) -> CalculationWizardPreparationResult:
        """Persist/reuse workflow intent and exact per-step MethodFingerprints."""

        resolved_recipe_settings = (
            WizardRecipeSettings() if recipe_settings is None else recipe_settings
        )
        bundle = self.store.open()
        snapshot = _require_selectable_root(bundle, root_structure_snapshot_id)
        _validate_task_root(bundle, task, snapshot)
        context = _system_context(task, protocol_settings)
        method, potcar_spec = _resolve_method(snapshot=snapshot, settings=method_settings)
        prepared_kpoints = prepare_kpoints(
            snapshot,
            policy=protocol_settings.kpoints,
            system_context=context,
            centering=protocol_settings.kpoint_centering,
        )
        if potcar_spec.species_order != prepare_poscar(snapshot).species_order:
            raise ApplicationServiceError("POTCAR species order does not match selected structure")

        workflow_spec = _workflow_spec(task)
        planned = self.prepare_workflow(
            workflow_recipe=WorkflowRecipeIdentity(
                recipe_id=workflow_spec.recipe_id,
                version=workflow_spec.version,
            ),
            root_structure_snapshot_id=snapshot.id,
        )
        plan = planned.plan
        existing_by_hash = {item.instance_hash: item for item in bundle.method_fingerprints}
        new_fingerprints: list[MethodFingerprint] = []
        summaries: list[WizardStepSummary] = []

        for step in plan.steps:
            blockers: list[str] = []
            fingerprint: MethodFingerprint | None = None
            try:
                recipe_spec = get_vasp_recipe_spec(step.recipe_id)
                if context.kind not in recipe_spec.allowed_system_kinds:
                    raise ApplicationServiceError(
                        "workflow step recipe is incompatible with selected physical system"
                    )
                recipe, digests = _step_recipe_identity(
                    step.recipe_id,
                    recipe_settings=resolved_recipe_settings,
                    snapshot=snapshot,
                )
                protocol = _step_protocol(
                    settings=protocol_settings,
                    centering=prepared_kpoints.centering,
                    recipe_id=step.recipe_id,
                    task=task,
                )
                fingerprint = MethodFingerprint(
                    method=method,
                    protocol=protocol,
                    recipe=recipe,
                    input_digests=digests,
                )
            except (ValueError, ApplicationServiceError) as error:
                blockers.append(_recipe_blocker_code(step.recipe_id, str(error)))
            if fingerprint is not None:
                existing = existing_by_hash.get(fingerprint.instance_hash)
                if existing is None:
                    existing_by_hash[fingerprint.instance_hash] = fingerprint
                    new_fingerprints.append(fingerprint)
                else:
                    fingerprint = existing
            summaries.append(
                WizardStepSummary(
                    step_key=step.key,
                    recipe_id=step.recipe_id,
                    calculation_type=step.calculation_type.value,
                    method_fingerprint_id=str(fingerprint.id) if fingerprint else None,
                    fingerprint_hash=fingerprint.instance_hash if fingerprint else None,
                    materialized=False,
                    blocker_codes=tuple(blockers),
                )
            )

        if new_fingerprints:
            current = self.store.open()
            hashes = {item.instance_hash for item in current.method_fingerprints}
            append = tuple(item for item in new_fingerprints if item.instance_hash not in hashes)
            if append:
                self.store.save(
                    replace(
                        current,
                        method_fingerprints=(*current.method_fingerprints, *append),
                    )
                )

        reopened = self.store.open()
        _verify_fingerprints(reopened, summaries)
        current_steps: list[WizardStepSummary] = []
        for summary in summaries:
            materialized = any(
                item.workflow_plan_id == plan.id and item.step_key == summary.step_key
                for item in reopened.workflow_step_bindings
            )
            blockers = list(summary.blocker_codes)
            if not materialized and not blockers:
                incoming = tuple(
                    edge
                    for edge in plan.edges
                    if edge.downstream_step_key == summary.step_key
                )
                blockers.append(
                    "accepted_structure_required"
                    if incoming
                    else "validated_numerical_evidence_required"
                )
            current_steps.append(
                replace(summary, materialized=materialized, blocker_codes=tuple(blockers))
            )
        return CalculationWizardPreparationResult(
            project_id=str(reopened.project.id),
            workflow_plan_id=str(plan.id),
            workflow_recipe_id=plan.workflow_recipe.recipe_id,
            root_structure_snapshot_id=str(plan.root_structure_snapshot_id),
            task=task,
            system_kind=context.kind,
            steps=tuple(current_steps),
            reused_plan=planned.reused,
        )

    def materialize_root_step(
        self,
        *,
        workflow_plan_id: WorkflowPlanId,
        step_key: str,
        method_fingerprint_id: MethodFingerprintId,
        task: CalculationWizardTask,
        protocol_settings: WizardProtocolSettings,
        method_settings: WizardMethodSettings,
        numerical_evidence: WizardNumericalEvidence,
    ) -> CalculationWizardMaterializationResult:
        """Materialize/reuse a root step only after validating real numerical evidence."""

        bundle = self.store.open()
        plan = _require_plan(bundle, workflow_plan_id)
        if any(edge.downstream_step_key == step_key for edge in plan.edges):
            raise ApplicationServiceError(
                "downstream workflow step requires accepted promoted-structure evidence; "
                "the preparation wizard cannot infer it"
            )
        step = plan.step(step_key)
        fingerprint = _require_fingerprint(bundle, method_fingerprint_id)
        if fingerprint.recipe.recipe_id != step.recipe_id:
            raise ApplicationServiceError("MethodFingerprint does not match workflow step")
        snapshot = _require_snapshot(bundle, plan.root_structure_snapshot_id)
        _validate_task_root(bundle, task, snapshot)
        context = _system_context(task, protocol_settings)
        method, potcar_spec = _resolve_method(snapshot=snapshot, settings=method_settings)
        if method != fingerprint.method:
            raise ApplicationServiceError(
                "current method settings do not match selected MethodFingerprint"
            )
        if protocol_settings.kpoints != fingerprint.protocol.kpoints:
            raise ApplicationServiceError(
                "current k-point settings do not match selected MethodFingerprint"
            )
        prepared_kpoints = prepare_kpoints(
            snapshot,
            policy=fingerprint.protocol.kpoints,
            system_context=context,
            centering=_fingerprint_centering(fingerprint),
        )
        validate_encut_evidence(spec=potcar_spec, evidence=numerical_evidence.encut)
        if numerical_evidence.encut.core_method_hash != fingerprint.core_method_hash:
            raise ApplicationServiceError("ENCUT evidence does not match fingerprint core method")
        if numerical_evidence.encut.selected_encut_ev != fingerprint.protocol.encut_ev:
            raise ApplicationServiceError("ENCUT evidence does not match fingerprinted ENCUT")
        kpoint_evidence = numerical_evidence.kpoints
        if context.kind is not VaspSystemKind.MOLECULE_0D and kpoint_evidence is None:
            raise ApplicationServiceError(
                "solid calculation materialization requires validated k-point evidence"
            )
        lock = ProjectNumericalLock(
            project_id=bundle.project.id,
            system_kind=context.kind,
            core_method_hash=fingerprint.core_method_hash,
            encut_ev=fingerprint.protocol.encut_ev,
            encut_validation_hash=numerical_evidence.encut.analysis_hash,
            kpoints=fingerprint.protocol.kpoints,
            kpoints_validation_hash=kpoint_evidence.analysis_hash if kpoint_evidence else None,
        )
        validate_project_lock_encut(
            lock=lock,
            spec=potcar_spec,
            evidence=numerical_evidence.encut,
        )
        validate_project_lock_kpoints(
            lock=lock,
            prepared=prepared_kpoints,
            evidence=kpoint_evidence,
        )

        existing = tuple(
            item
            for item in bundle.workflow_step_bindings
            if item.workflow_plan_id == plan.id and item.step_key == step_key
        )
        if existing:
            current = max(existing, key=lambda item: item.generation)
            calculation = next(
                item
                for item in bundle.calculations
                if item.id == current.calculation_id
            )
            if calculation.method_fingerprint_id != fingerprint.id:
                raise ApplicationServiceError(
                    "current workflow generation uses a different MethodFingerprint"
                )
            return CalculationWizardMaterializationResult(
                project_id=str(bundle.project.id),
                workflow_plan_id=str(plan.id),
                step_key=step_key,
                calculation_id=str(calculation.id),
                workflow_step_binding_id=str(current.id),
                reused=True,
            )

        orchestration = _orchestration(bundle, plan)
        if orchestration.step(step_key).action is not WorkflowOrchestrationAction.MATERIALIZE_STEP:
            raise ApplicationServiceError(
                f"workflow step is not ready for materialization: "
                f"{orchestration.step(step_key).action.value}"
            )
        result = self.prepare_workflow_step(
            workflow_plan_id=plan.id,
            orchestration=orchestration,
            step_key=step_key,
            method_fingerprint_id=fingerprint.id,
            system_context=context,
            project_lock=lock,
        )
        return CalculationWizardMaterializationResult(
            project_id=str(bundle.project.id),
            workflow_plan_id=str(plan.id),
            step_key=step_key,
            calculation_id=str(result.materialization.calculation.id),
            workflow_step_binding_id=str(result.materialization.binding.id),
            reused=result.reused,
        )


def _workflow_spec(task: CalculationWizardTask) -> WorkflowRecipeSpec:
    recipe_id = _TASK_WORKFLOW_RECIPES[task]
    for spec in list_workflow_recipe_specs():
        if spec.recipe_id == recipe_id:
            return spec
    raise ApplicationServiceError(f"canonical workflow recipe is unavailable: {recipe_id}")


def _resolve_method(
    *,
    snapshot: StructureSnapshot,
    settings: WizardMethodSettings,
) -> tuple[MethodDefinition, PotcarSpec]:
    elements = tuple(sorted({site.element for site in snapshot.sites}))
    selected = {item.element: item.symbol for item in settings.potcar_symbols}
    if set(selected) != set(elements):
        raise ApplicationServiceError(
            "explicit POTCAR selections must exactly cover structure elements"
        )
    identities: list[PotcarIdentity] = []
    for element in elements:
        symbol = selected[element]
        path = settings.potcar_root / symbol / "POTCAR"
        if not path.is_file():
            raise ApplicationServiceError(
                f"licensed POTCAR is missing for {element}/{symbol}"
            )
        identities.append(
            PotcarIdentity(
                element=element,
                symbol=symbol,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    method = MethodDefinition(
        xc_functional=settings.xc_functional,
        potcar_family=settings.potcar_family,
        potcars=tuple(identities),
        spin_treatment=settings.spin_treatment,
        engine_version=settings.engine_version,
        dispersion_model=settings.dispersion_model,
    )
    resolved = LocalPotcarLibrary(
        family=settings.potcar_family,
        root=settings.potcar_root,
    ).resolve(prepared_poscar=prepare_poscar(snapshot), method=method)
    return method, resolved.spec


def _step_recipe_identity(
    recipe_id: str,
    *,
    recipe_settings: WizardRecipeSettings,
    snapshot: StructureSnapshot,
) -> tuple[RecipeIdentity, tuple[ScientificInputDigest, ...]]:
    spec = get_vasp_recipe_spec(recipe_id)
    parameters: tuple[ParameterEntry, ...] = ()
    digests: tuple[ScientificInputDigest, ...] = ()
    if recipe_id in {
        RECIPE_SELECTED_ATOM_FREQUENCY,
        RECIPE_FULL_FREQUENCY,
        RECIPE_GAS_FREQUENCY,
    }:
        parameters = frequency_recipe_parameters(
            potim_angstrom=recipe_settings.frequency_potim_angstrom,
            nfree=2,
        )
        if recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY:
            if not recipe_settings.frequency_atom_uids:
                raise ApplicationServiceError(
                    "selected-atom frequency requires explicit atom selection"
                )
            available = {str(site.atom_uid) for site in snapshot.sites}
            if any(
                value not in available for value in recipe_settings.frequency_atom_uids
            ):
                raise ApplicationServiceError(
                    "frequency atom selection contains an atom absent from selected structure"
                )
            selection = FrequencySelection(
                tuple(
                    AtomUid(UUID(value))
                    for value in recipe_settings.frequency_atom_uids
                )
            )
            digests = (selection.input_digest,)
    elif recipe_id == RECIPE_DOS_PREREQUISITE:
        parameters = dos_recipe_parameters(nedos=recipe_settings.dos_nedos)
    elif recipe_id == RECIPE_CHARGE_DENSITY_STATIC:
        parameters = ()
    elif recipe_id == RECIPE_LOBSTER_PREREQUISITE:
        if recipe_settings.lobster_nbands is None:
            raise ApplicationServiceError("LOBSTER prerequisite requires explicit NBANDS")
        parameters = lobster_recipe_parameters(nbands=recipe_settings.lobster_nbands)
    return RecipeIdentity(
        spec.recipe_id,
        version=spec.version,
        parameters=parameters,
    ), digests


def _step_protocol(
    *,
    settings: WizardProtocolSettings,
    centering: KPointCentering,
    recipe_id: str,
    task: CalculationWizardTask,
) -> ProtocolDefinition:
    vacuum_axis = (
        settings.vacuum_axis.value
        if task is not CalculationWizardTask.GAS_REFERENCE
        and settings.vacuum_axis is not None
        else None
    )
    extras = (
        *ecat_standard_protocol_parameters(vacuum_axis=vacuum_axis),
        ParameterEntry(ECATVASP_KPOINT_CENTERING, centering.value),
    )
    ediff = settings.ediff_ev
    if recipe_id in {
        RECIPE_SELECTED_ATOM_FREQUENCY,
        RECIPE_FULL_FREQUENCY,
        RECIPE_GAS_FREQUENCY,
    }:
        ediff = min(ediff, 1e-8)
    return ProtocolDefinition(
        encut_ev=settings.encut_ev,
        kpoints=settings.kpoints,
        precision=settings.precision,
        ediff_ev=ediff,
        ediffg_ev_per_angstrom=settings.ediffg_ev_per_angstrom,
        ismear=settings.ismear,
        sigma_ev=settings.sigma_ev,
        isym=0 if recipe_id == RECIPE_LOBSTER_PREREQUISITE else settings.isym,
        extra_parameters=tuple(sorted(extras, key=lambda item: item.name)),
    )


def _system_context(
    task: CalculationWizardTask,
    settings: WizardProtocolSettings,
) -> VaspSystemContext:
    if task is CalculationWizardTask.GAS_REFERENCE:
        return VaspSystemContext(VaspSystemKind.MOLECULE_0D)
    if settings.vacuum_axis is None:
        raise ApplicationServiceError(
            "slab/adsorbate calculations require explicit vacuum axis"
        )
    return VaspSystemContext(
        VaspSystemKind.SLAB_2D,
        vacuum_axis=settings.vacuum_axis,
    )


def _validate_task_root(
    bundle: ProjectBundle,
    task: CalculationWizardTask,
    snapshot: StructureSnapshot,
) -> None:
    conformers = {item.structure_snapshot_id for item in bundle.state_conformers}
    if task is CalculationWizardTask.ADSORBATE:
        if snapshot.id not in conformers:
            raise ApplicationServiceError(
                "adsorbate workflow requires a persisted StateConformer structure"
            )
        return
    if snapshot.id in conformers:
        raise ApplicationServiceError(
            "adsorbate conformer structures must use adsorbate preparation task"
        )
    molecule = snapshot.periodic == (False, False, False)
    if task is CalculationWizardTask.GAS_REFERENCE and not molecule:
        raise ApplicationServiceError(
            "gas-reference workflow requires non-periodic structure"
        )
    if task is CalculationWizardTask.SLAB and molecule:
        raise ApplicationServiceError("slab workflow requires periodic structure")


def _require_selectable_root(
    bundle: ProjectBundle,
    snapshot_id: StructureSnapshotId,
) -> StructureSnapshot:
    snapshot = _require_snapshot(bundle, snapshot_id)
    if any(
        item.structure_snapshot_id == snapshot_id for item in bundle.state_conformers
    ):
        return snapshot
    if any(
        item.current_structure_snapshot_id == snapshot_id
        for item in bundle.structure_variants
    ):
        return snapshot
    raise ApplicationServiceError(
        "selected root is not a current model snapshot or persisted conformer"
    )


def _require_snapshot(
    bundle: ProjectBundle,
    snapshot_id: StructureSnapshotId,
) -> StructureSnapshot:
    for item in bundle.structure_snapshots:
        if item.id == snapshot_id:
            return item
    raise ApplicationServiceError("StructureSnapshot is absent from current ProjectStore")


def _require_plan(
    bundle: ProjectBundle,
    plan_id: WorkflowPlanId,
) -> ScientificWorkflowPlan:
    for item in bundle.workflow_plans:
        if item.id == plan_id:
            return item
    raise ApplicationServiceError("workflow plan is absent from current ProjectStore")


def _require_fingerprint(
    bundle: ProjectBundle,
    fingerprint_id: MethodFingerprintId,
) -> MethodFingerprint:
    for item in bundle.method_fingerprints:
        if item.id == fingerprint_id:
            return item
    raise ApplicationServiceError("MethodFingerprint is absent from current ProjectStore")


def _fingerprint_centering(fingerprint: MethodFingerprint) -> KPointCentering:
    values = [
        item.value
        for item in fingerprint.protocol.extra_parameters
        if item.name == ECATVASP_KPOINT_CENTERING
    ]
    if len(values) != 1 or not isinstance(values[0], str):
        raise ApplicationServiceError(
            "MethodFingerprint does not contain one explicit k-point centering"
        )
    return KPointCentering(values[0])


def _orchestration(
    bundle: ProjectBundle,
    plan: ScientificWorkflowPlan,
) -> WorkflowOrchestrationEvaluation:
    hashes: dict[UUID, str] = {}
    for variant in bundle.structure_variants:
        hashes[variant.id] = scientific_hash(variant)
    for snapshot in bundle.structure_snapshots:
        hashes[snapshot.id] = scientific_hash(snapshot)
    for active_site in bundle.active_sites:
        hashes[active_site.id] = scientific_hash(active_site)
    for adsorption_state in bundle.adsorption_states:
        hashes[adsorption_state.id] = scientific_hash(adsorption_state)
    for conformer in bundle.state_conformers:
        hashes[conformer.id] = scientific_hash(conformer)
    for fingerprint in bundle.method_fingerprints:
        hashes[fingerprint.id] = scientific_hash(fingerprint)
    for calculation in bundle.calculations:
        hashes[calculation.id] = scientific_hash(calculation)
    for artifact in bundle.artifacts:
        hashes[artifact.id] = scientific_hash(artifact)
    for analysis in bundle.analyses:
        hashes[analysis.id] = scientific_hash(analysis)
    freshness = evaluate_workflow_freshness(
        plan=plan,
        bindings=bundle.workflow_step_bindings,
        calculations=bundle.calculations,
        dependencies=bundle.dependency_records,
        current_hashes=hashes,
    )
    gates = evaluate_workflow_scientific_gates(
        plan=plan,
        bindings=bundle.workflow_step_bindings,
        calculations=bundle.calculations,
        freshness=freshness,
    )
    recovery = evaluate_workflow_recovery_policy(plan=plan, gates=gates)
    return reconcile_workflow_orchestration(
        plan=plan,
        gates=gates,
        recovery=recovery,
    )


def _verify_fingerprints(
    bundle: ProjectBundle,
    summaries: list[WizardStepSummary],
) -> None:
    ids = {str(item.id) for item in bundle.method_fingerprints}
    if any(
        item.method_fingerprint_id is not None and item.method_fingerprint_id not in ids
        for item in summaries
    ):
        raise ApplicationServiceError(
            "calculation wizard MethodFingerprint failed post-save verification"
        )


def _recipe_blocker_code(recipe_id: str, message: str) -> str:
    if recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY and "selection" in message:
        return "frequency_atom_selection_required"
    if recipe_id == RECIPE_LOBSTER_PREREQUISITE and "NBANDS" in message:
        return "lobster_nbands_required"
    return "scientific_settings_invalid"