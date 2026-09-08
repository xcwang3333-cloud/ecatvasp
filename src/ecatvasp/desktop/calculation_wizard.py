"""Desktop-facing typed actions for the v1.1 calculation/workflow wizard."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from ecatvasp.api.calculation_wizard import (
    CalculationWizardTask,
    PotcarSymbolSelection,
    ProjectCalculationWizardApplicationService,
    WizardMethodSettings,
    WizardNumericalEvidence,
    WizardProtocolSettings,
    WizardRecipeSettings,
)
from ecatvasp.domain import (
    KPointPolicyKind,
    MethodFingerprintId,
    SpinTreatment,
    StructureSnapshotId,
    WorkflowPlanId,
)
from ecatvasp.storage import ProjectStore
from ecatvasp.vasp import (
    EncCutValidationEvidence,
    KPointCentering,
    KPointValidationEvidence,
    LatticeAxis,
    VaspSystemKind,
)


def calculation_catalog_action(project_root: Path | str) -> dict[str, object]:
    service = ProjectCalculationWizardApplicationService(ProjectStore(project_root))
    return {"project_root": str(Path(project_root)), "catalog": service.catalog()}


def prepare_calculation_workflow_action(
    *,
    project_root: Path | str,
    task: str,
    root_structure_snapshot_id: str,
    method: dict[str, Any],
    protocol: dict[str, Any],
    recipe: dict[str, Any],
) -> dict[str, object]:
    service = ProjectCalculationWizardApplicationService(ProjectStore(project_root))
    receipt = service.prepare(
        task=CalculationWizardTask(task),
        root_structure_snapshot_id=StructureSnapshotId(UUID(root_structure_snapshot_id)),
        method_settings=_method_settings(method),
        protocol_settings=_protocol_settings(protocol),
        recipe_settings=_recipe_settings(recipe),
    )
    return {
        "project_root": str(Path(project_root)),
        "project_id": receipt.project_id,
        "workflow_plan_id": receipt.workflow_plan_id,
        "workflow_recipe_id": receipt.workflow_recipe_id,
        "root_structure_snapshot_id": receipt.root_structure_snapshot_id,
        "task": receipt.task.value,
        "system_kind": receipt.system_kind.value,
        "reused_plan": receipt.reused_plan,
        "steps": [
            {
                "step_key": item.step_key,
                "recipe_id": item.recipe_id,
                "calculation_type": item.calculation_type,
                "method_fingerprint_id": item.method_fingerprint_id,
                "fingerprint_hash": item.fingerprint_hash,
                "materialized": item.materialized,
                "blocker_codes": list(item.blocker_codes),
            }
            for item in receipt.steps
        ],
    }


def materialize_calculation_step_action(
    *,
    project_root: Path | str,
    workflow_plan_id: str,
    step_key: str,
    method_fingerprint_id: str,
    task: str,
    method: dict[str, Any],
    protocol: dict[str, Any],
    numerical_evidence: dict[str, Any],
) -> dict[str, object]:
    service = ProjectCalculationWizardApplicationService(ProjectStore(project_root))
    receipt = service.materialize_root_step(
        workflow_plan_id=WorkflowPlanId(UUID(workflow_plan_id)),
        step_key=step_key,
        method_fingerprint_id=MethodFingerprintId(UUID(method_fingerprint_id)),
        task=CalculationWizardTask(task),
        method_settings=_method_settings(method),
        protocol_settings=_protocol_settings(protocol),
        numerical_evidence=numerical_evidence_from_payload(numerical_evidence),
    )
    return {
        "project_root": str(Path(project_root)),
        "project_id": receipt.project_id,
        "workflow_plan_id": receipt.workflow_plan_id,
        "step_key": receipt.step_key,
        "calculation_id": receipt.calculation_id,
        "workflow_step_binding_id": receipt.workflow_step_binding_id,
        "reused": receipt.reused,
    }


def _method_settings(raw: dict[str, Any]) -> WizardMethodSettings:
    symbols_raw = raw["potcar_symbols"]
    if not isinstance(symbols_raw, list):
        raise ValueError("method.potcar_symbols must be a list")
    symbols = tuple(
        PotcarSymbolSelection(element=str(item["element"]), symbol=str(item["symbol"]))
        for item in symbols_raw
        if isinstance(item, dict)
    )
    if len(symbols) != len(symbols_raw):
        raise ValueError("method.potcar_symbols entries must be objects")
    return WizardMethodSettings(
        xc_functional=str(raw["xc_functional"]),
        potcar_family=str(raw["potcar_family"]),
        potcar_root=Path(str(raw["potcar_root"])),
        potcar_symbols=symbols,
        spin_treatment=SpinTreatment(str(raw.get("spin_treatment", "collinear"))),
        engine_version=_optional_text(raw.get("engine_version")),
        dispersion_model=_optional_text(raw.get("dispersion_model")),
    )


def _protocol_settings(raw: dict[str, Any]) -> WizardProtocolSettings:
    mesh_value = raw.get("kpoint_mesh")
    mesh: tuple[int, int, int] | None = None
    if mesh_value is not None:
        if (
            not isinstance(mesh_value, list)
            or len(mesh_value) != 3
            or any(isinstance(item, bool) or not isinstance(item, int) for item in mesh_value)
        ):
            raise ValueError("protocol.kpoint_mesh must contain three integers")
        mesh = (mesh_value[0], mesh_value[1], mesh_value[2])
    vacuum = raw.get("vacuum_axis")
    return WizardProtocolSettings(
        encut_ev=float(raw["encut_ev"]),
        kpoint_kind=KPointPolicyKind(str(raw["kpoint_kind"])),
        kpoint_mesh=mesh,
        kpoint_value=_optional_float(raw.get("kpoint_value")),
        kpoint_centering=KPointCentering(str(raw.get("kpoint_centering", "gamma"))),
        vacuum_axis=LatticeAxis(str(vacuum)) if vacuum is not None else None,
        precision=str(raw.get("precision", "Accurate")),
        ediff_ev=float(raw.get("ediff_ev", 1e-5)),
        ediffg_ev_per_angstrom=_optional_float(raw.get("ediffg_ev_per_angstrom", -0.02)),
        ismear=int(raw.get("ismear", 0)),
        sigma_ev=float(raw.get("sigma_ev", 0.05)),
        isym=_optional_int(raw.get("isym")),
    )


def _recipe_settings(raw: dict[str, Any]) -> WizardRecipeSettings:
    uids = raw.get("frequency_atom_uids", [])
    if not isinstance(uids, list) or any(not isinstance(item, str) for item in uids):
        raise ValueError("recipe.frequency_atom_uids must be a string list")
    return WizardRecipeSettings(
        frequency_potim_angstrom=float(raw.get("frequency_potim_angstrom", 0.015)),
        frequency_atom_uids=tuple(uids),
        dos_nedos=int(raw.get("dos_nedos", 2001)),
        lobster_nbands=_optional_int(raw.get("lobster_nbands")),
    )


def numerical_evidence_from_payload(raw: dict[str, Any]) -> WizardNumericalEvidence:
    """Decode one already-validated desktop numerical-evidence payload."""

    encut_raw = raw["encut"]
    if not isinstance(encut_raw, dict):
        raise ValueError("numerical_evidence.encut must be an object")
    tested = encut_raw["tested_encuts_ev"]
    if not isinstance(tested, list):
        raise ValueError("encut.tested_encuts_ev must be a list")
    encut = EncCutValidationEvidence(
        core_method_hash=str(encut_raw["core_method_hash"]),
        potcar_spec_hash=str(encut_raw["potcar_spec_hash"]),
        tested_encuts_ev=tuple(float(item) for item in tested),
        selected_encut_ev=float(encut_raw["selected_encut_ev"]),
        analysis_hash=str(encut_raw["analysis_hash"]),
    )
    kpoint_raw = raw.get("kpoints")
    kpoints = None
    if kpoint_raw is not None:
        if not isinstance(kpoint_raw, dict):
            raise ValueError("numerical_evidence.kpoints must be an object when supplied")
        tested_hashes = kpoint_raw["tested_plan_hashes"]
        if not isinstance(tested_hashes, list):
            raise ValueError("kpoints.tested_plan_hashes must be a list")
        kpoints = KPointValidationEvidence(
            core_method_hash=str(kpoint_raw["core_method_hash"]),
            system_kind=VaspSystemKind(str(kpoint_raw["system_kind"])),
            tested_plan_hashes=tuple(str(item) for item in tested_hashes),
            selected_plan_hash=str(kpoint_raw["selected_plan_hash"]),
            analysis_hash=str(kpoint_raw["analysis_hash"]),
        )
    return WizardNumericalEvidence(encut=encut, kpoints=kpoints)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("floating-point value must be numeric")
    return float(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("integer value must be an integer and not boolean")
    return value


__all__ = [
    "calculation_catalog_action",
    "materialize_calculation_step_action",
    "numerical_evidence_from_payload",
    "prepare_calculation_workflow_action",
]
