from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.calculation_wizard import (
    CalculationWizardTask,
    PotcarSymbolSelection,
    ProjectCalculationWizardApplicationService,
    WizardMethodSettings,
    WizardNumericalEvidence,
    WizardProtocolSettings,
    WizardRecipeSettings,
)
from ecatvasp.api.model_studio import ProjectModelStudioApplicationService, create_project_store
from ecatvasp.domain import KPointPolicyKind, StructureSnapshot, StructureSnapshotId
from ecatvasp.storage import ProjectStore
from ecatvasp.structures import GrapheneBuildSpec
from ecatvasp.vasp import (
    EncCutValidationEvidence,
    KPointCentering,
    KPointValidationEvidence,
    LatticeAxis,
    LocalPotcarLibrary,
    VaspSystemContext,
    VaspSystemKind,
    prepare_kpoints,
    prepare_poscar,
)


def _potcar_text(symbol: str, *, zval: float = 4.0, enmax_ev: float = 400.0) -> str:
    return (
        f"PAW_PBE {symbol} 01Jan2026\n"
        "parameters from PSCTR are:\n"
        f"   TITEL  = PAW_PBE {symbol} 01Jan2026\n"
        f"   POMASS = 1.000; ZVAL = {zval:.3f} mass and valenz\n"
        f"   ENMAX = {enmax_ev:.3f}; ENMIN = {enmax_ev * 0.75:.3f} eV\n"
        " End of Dataset\n"
    )


def _case(tmp_path: Path):
    root = tmp_path / "project"
    create_project_store(root, name="Block 3", slug="block-3")
    model_service = ProjectModelStudioApplicationService(ProjectStore(root))
    catalyst = model_service.create_catalyst(name="C", slug="c")
    model = model_service.build_graphene_model(
        catalyst_id=catalyst.id,
        variant_name="graphene",
        spec=GrapheneBuildSpec(nx=2, ny=2, vacuum_gap_angstrom=15.0),
    )
    potcar_root = tmp_path / "PBE_54"
    path = potcar_root / "C" / "POTCAR"
    path.parent.mkdir(parents=True)
    path.write_text(_potcar_text("C"), encoding="utf-8")
    method = WizardMethodSettings(
        xc_functional="PBE",
        potcar_family="PBE_54",
        potcar_root=potcar_root,
        potcar_symbols=(PotcarSymbolSelection("C", "C"),),
    )
    protocol = WizardProtocolSettings(
        encut_ev=450.0,
        kpoint_kind=KPointPolicyKind.EXPLICIT_MESH,
        kpoint_mesh=(3, 3, 1),
        kpoint_centering=KPointCentering.GAMMA,
        vacuum_axis=LatticeAxis.C,
    )
    return root, model, method, protocol


def test_catalog_exposes_task_roots_without_requiring_raw_identity_input(tmp_path: Path) -> None:
    root, model, _, _ = _case(tmp_path)
    service = ProjectCalculationWizardApplicationService(ProjectStore(root))

    catalog = service.catalog()

    assert catalog["scientific_profile"]["numerical_lock_policy"] == (
        "validated_convergence_evidence_required"
    )
    assert catalog["roots"] == [
        {
            "structure_snapshot_id": str(model.snapshot.id),
            "structure_variant_id": str(model.variant.id),
            "label": model.snapshot.label or model.variant.name,
            "atom_count": len(model.snapshot.sites),
            "elements": ["C"],
            "eligible_task": "slab",
            "is_conformer": False,
        }
    ]
    assert {item["task"] for item in catalog["tasks"]} == {
        "slab",
        "adsorbate",
        "gas_reference",
    }


def test_prepare_persists_plan_and_fingerprints_but_never_fabricates_lock_evidence(
    tmp_path: Path,
) -> None:
    root, model, method, protocol = _case(tmp_path)
    service = ProjectCalculationWizardApplicationService(ProjectStore(root))

    receipt = service.prepare(
        task=CalculationWizardTask.SLAB,
        root_structure_snapshot_id=model.snapshot.id,
        method_settings=method,
        protocol_settings=protocol,
        recipe_settings=WizardRecipeSettings(lobster_nbands=96),
    )

    reopened = ProjectStore(root).open()
    assert receipt.workflow_recipe_id == "ECatVASP.Workflow.SlabScientificPreparation"
    assert len(reopened.workflow_plans) == 1
    assert len(reopened.method_fingerprints) == len(receipt.steps)
    assert reopened.calculations == ()
    assert reopened.workflow_step_bindings == ()
    root_step = receipt.steps[0]
    assert root_step.step_key == "relax"
    assert root_step.blocker_codes == ("validated_numerical_evidence_required",)
    assert all(item.method_fingerprint_id is not None for item in receipt.steps)
    assert all(
        item.blocker_codes == ("accepted_structure_required",)
        for item in receipt.steps[1:]
    )

    replay = service.prepare(
        task=CalculationWizardTask.SLAB,
        root_structure_snapshot_id=model.snapshot.id,
        method_settings=method,
        protocol_settings=protocol,
        recipe_settings=WizardRecipeSettings(lobster_nbands=96),
    )
    assert replay.reused_plan is True
    assert len(ProjectStore(root).open().method_fingerprints) == len(receipt.steps)


def test_prepare_reports_recipe_specific_missing_inputs_instead_of_guessing(tmp_path: Path) -> None:
    root, model, method, protocol = _case(tmp_path)
    service = ProjectCalculationWizardApplicationService(ProjectStore(root))

    receipt = service.prepare(
        task=CalculationWizardTask.SLAB,
        root_structure_snapshot_id=model.snapshot.id,
        method_settings=method,
        protocol_settings=protocol,
    )

    lobster = next(item for item in receipt.steps if "Lobster" in item.recipe_id)
    assert lobster.method_fingerprint_id is None
    assert lobster.blocker_codes == ("lobster_nbands_required",)


def test_prepare_rejects_noncurrent_unbound_snapshot_as_stale_root(tmp_path: Path) -> None:
    root, model, method, protocol = _case(tmp_path)
    store = ProjectStore(root)
    bundle = store.open()
    orphan = StructureSnapshot(
        lattice=model.snapshot.lattice,
        sites=model.snapshot.sites,
        label="not-current",
        parent_snapshot_id=model.snapshot.id,
        periodic=model.snapshot.periodic,
    )
    store.save(replace(bundle, structure_snapshots=(*bundle.structure_snapshots, orphan)))
    service = ProjectCalculationWizardApplicationService(store)

    with pytest.raises(ApplicationServiceError, match="not a current model snapshot"):
        service.prepare(
            task=CalculationWizardTask.SLAB,
            root_structure_snapshot_id=orphan.id,
            method_settings=method,
            protocol_settings=protocol,
            recipe_settings=WizardRecipeSettings(lobster_nbands=96),
        )


def test_root_step_materializes_only_with_matching_real_numerical_evidence(tmp_path: Path) -> None:
    root, model, method_settings, protocol = _case(tmp_path)
    store = ProjectStore(root)
    service = ProjectCalculationWizardApplicationService(store)
    prepared = service.prepare(
        task=CalculationWizardTask.SLAB,
        root_structure_snapshot_id=model.snapshot.id,
        method_settings=method_settings,
        protocol_settings=protocol,
        recipe_settings=WizardRecipeSettings(lobster_nbands=96),
    )
    relax = prepared.steps[0]
    assert relax.method_fingerprint_id is not None
    fingerprint_id = UUID(relax.method_fingerprint_id)
    bundle = store.open()
    fingerprint = next(item for item in bundle.method_fingerprints if item.id == fingerprint_id)
    resolved = LocalPotcarLibrary(
        family=method_settings.potcar_family,
        root=method_settings.potcar_root,
    ).resolve(prepared_poscar=prepare_poscar(model.snapshot), method=fingerprint.method)
    encut = EncCutValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        potcar_spec_hash=resolved.spec.metadata_hash,
        tested_encuts_ev=(400.0, 450.0, 500.0),
        selected_encut_ev=450.0,
        analysis_hash="a" * 64,
    )
    context = VaspSystemContext(VaspSystemKind.SLAB_2D, vacuum_axis=LatticeAxis.C)
    kpoints = prepare_kpoints(
        model.snapshot,
        policy=fingerprint.protocol.kpoints,
        system_context=context,
        centering=KPointCentering.GAMMA,
    )
    kpoint_evidence = KPointValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        system_kind=VaspSystemKind.SLAB_2D,
        tested_plan_hashes=(kpoints.identity_hash,),
        selected_plan_hash=kpoints.identity_hash,
        analysis_hash="b" * 64,
    )

    materialized = service.materialize_root_step(
        workflow_plan_id=UUID(prepared.workflow_plan_id),
        step_key="relax",
        method_fingerprint_id=fingerprint.id,
        task=CalculationWizardTask.SLAB,
        protocol_settings=protocol,
        method_settings=method_settings,
        numerical_evidence=WizardNumericalEvidence(encut=encut, kpoints=kpoint_evidence),
    )
    reopened = store.open()
    assert materialized.reused is False
    assert len(reopened.calculations) == 1
    assert len(reopened.workflow_step_bindings) == 1

    replay = service.materialize_root_step(
        workflow_plan_id=UUID(prepared.workflow_plan_id),
        step_key="relax",
        method_fingerprint_id=fingerprint.id,
        task=CalculationWizardTask.SLAB,
        protocol_settings=protocol,
        method_settings=method_settings,
        numerical_evidence=WizardNumericalEvidence(encut=encut, kpoints=kpoint_evidence),
    )
    assert replay.reused is True
    assert len(store.open().calculations) == 1


def test_materialization_rejects_missing_solid_kpoint_evidence(tmp_path: Path) -> None:
    root, model, method_settings, protocol = _case(tmp_path)
    store = ProjectStore(root)
    service = ProjectCalculationWizardApplicationService(store)
    prepared = service.prepare(
        task=CalculationWizardTask.SLAB,
        root_structure_snapshot_id=model.snapshot.id,
        method_settings=method_settings,
        protocol_settings=protocol,
        recipe_settings=WizardRecipeSettings(lobster_nbands=96),
    )
    fingerprint_id = UUID(prepared.steps[0].method_fingerprint_id or "")
    fingerprint = next(item for item in store.open().method_fingerprints if item.id == fingerprint_id)
    resolved = LocalPotcarLibrary(
        family=method_settings.potcar_family,
        root=method_settings.potcar_root,
    ).resolve(prepared_poscar=prepare_poscar(model.snapshot), method=fingerprint.method)
    encut = EncCutValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        potcar_spec_hash=resolved.spec.metadata_hash,
        tested_encuts_ev=(450.0,),
        selected_encut_ev=450.0,
        analysis_hash="c" * 64,
    )

    with pytest.raises(ApplicationServiceError, match="requires validated k-point evidence"):
        service.materialize_root_step(
            workflow_plan_id=UUID(prepared.workflow_plan_id),
            step_key="relax",
            method_fingerprint_id=fingerprint.id,
            task=CalculationWizardTask.SLAB,
            protocol_settings=protocol,
            method_settings=method_settings,
            numerical_evidence=WizardNumericalEvidence(encut=encut),
        )
