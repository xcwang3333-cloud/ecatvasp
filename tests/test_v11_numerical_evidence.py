from __future__ import annotations

from pathlib import Path

import pytest

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.calculation_wizard import WizardNumericalEvidence
from ecatvasp.api.numerical_evidence import (
    persist_validated_numerical_evidence,
    resolve_validated_numerical_evidence,
)
from ecatvasp.domain import (
    Calculation,
    CalculationType,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.contracts import VaspSystemKind
from ecatvasp.vasp.kpoints import KPointValidationEvidence
from ecatvasp.vasp.potcar import EncCutValidationEvidence


def _case(tmp_path: Path) -> tuple[ProjectStore, Calculation, WizardNumericalEvidence]:
    project = Project(name="Numerical Evidence", slug="numerical-evidence")
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
        label="root",
    )
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "1" * 64),),
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.EXPLICIT_MESH, mesh=(3, 3, 1)),
            ediffg_ev_per_angstrom=-0.02,
        ),
        recipe=RecipeIdentity("ECatVASP.VASP.SlabRelax"),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    evidence = WizardNumericalEvidence(
        encut=EncCutValidationEvidence(
            core_method_hash=fingerprint.core_method_hash,
            potcar_spec_hash="2" * 64,
            tested_encuts_ev=(400.0, 450.0, 500.0),
            selected_encut_ev=450.0,
            analysis_hash="3" * 64,
        ),
        kpoints=KPointValidationEvidence(
            core_method_hash=fingerprint.core_method_hash,
            system_kind=VaspSystemKind.SLAB_2D,
            tested_plan_hashes=("4" * 64,),
            selected_plan_hash="4" * 64,
            analysis_hash="5" * 64,
        ),
    )
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(snapshot,),
            method_fingerprints=(fingerprint,),
            calculations=(calculation,),
        )
    )
    return store, calculation, evidence


def test_validated_numerical_evidence_survives_project_store_restart(tmp_path: Path) -> None:
    store, calculation, evidence = _case(tmp_path)

    artifact = persist_validated_numerical_evidence(
        store=store,
        calculation_id=calculation.id,
        evidence=evidence,
    )

    reopened_store = ProjectStore(tmp_path)
    bundle = reopened_store.open()
    resolved = resolve_validated_numerical_evidence(
        project_root=reopened_store.root,
        bundle=bundle,
        calculation_id=calculation.id,
    )

    assert resolved == evidence
    assert artifact in bundle.artifacts
    assert artifact.local_path is not None
    assert (reopened_store.root / artifact.local_path).is_file()


def test_validated_numerical_evidence_reuse_is_idempotent_and_rejects_drift(
    tmp_path: Path,
) -> None:
    store, calculation, evidence = _case(tmp_path)
    first = persist_validated_numerical_evidence(
        store=store,
        calculation_id=calculation.id,
        evidence=evidence,
    )
    second = persist_validated_numerical_evidence(
        store=ProjectStore(tmp_path),
        calculation_id=calculation.id,
        evidence=evidence,
    )
    assert second.id == first.id

    assert first.local_path is not None
    (store.root / first.local_path).write_text("{}\n", encoding="utf-8")
    with pytest.raises(ApplicationServiceError, match="drift detected"):
        resolve_validated_numerical_evidence(
            project_root=store.root,
            bundle=store.open(),
            calculation_id=calculation.id,
        )
