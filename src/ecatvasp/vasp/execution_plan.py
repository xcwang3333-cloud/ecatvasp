"""Portable v0.3 handoff from validated VASP inputs to future execution adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationEngine,
    CalculationProducerRef,
    ExecutionSettings,
    MethodFingerprint,
    RetrievalPolicy,
    canonical_sha256,
)
from ecatvasp.domain.ids import ArtifactId, CalculationId
from ecatvasp.vasp.contracts import VaspSystemContext
from ecatvasp.vasp.materialization import MaterializedInputSet
from ecatvasp.vasp.potcar import ResolvedPotcarSet
from ecatvasp.vasp.recipes import (
    RECIPE_ADSORBATE_RELAX,
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_ENCUT_CONVERGENCE_POINT,
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_GAS_RELAX,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_KPOINT_CONVERGENCE_POINT,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    RECIPE_SLAB_RELAX,
    get_vasp_recipe_spec,
)


class ExecutionPlanError(ValueError):
    """Raised when immutable scientific inputs cannot form a portable execution handoff."""


class StagingInputKind(StrEnum):
    """Whether a staged file is consumed by VASP or retained as provenance metadata."""

    VASP_INPUT = "vasp_input"
    METADATA = "metadata"


class VaspRuntimeCapability(StrEnum):
    """Semantic VASP runtime capabilities required by the exact scientific request."""

    NONCOLLINEAR = "noncollinear"
    SOC = "soc"
    SOLVATION = "solvation"
    CHARGED_CELL = "charged_cell"
    ELECTRIC_FIELD = "electric_field"
    FINITE_DIFFERENCE_FREQUENCY = "finite_difference_frequency"
    DOS_OUTPUT = "dos_output"
    CHARGE_DENSITY_OUTPUT = "charge_density_output"
    WAVECAR_OUTPUT = "wavecar_output"


def _validate_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _validate_relative_path(value: str, field_name: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or ".." in path.parts:
        raise ValueError(f"{field_name} must be a normalized relative POSIX path")
    if value in {"", "."}:
        raise ValueError(f"{field_name} must not be empty")
    return value


@dataclass(frozen=True, slots=True)
class StagingInput:
    """One immutable redistribution-safe artifact copied into a future run directory."""

    role: str
    kind: StagingInputKind
    artifact_id: ArtifactId
    artifact_type: ArtifactType
    source_relative_path: str
    target_relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_text(self.role, "role"))
        object.__setattr__(
            self,
            "source_relative_path",
            _validate_relative_path(self.source_relative_path, "source_relative_path"),
        )
        object.__setattr__(
            self,
            "target_relative_path",
            _validate_relative_path(self.target_relative_path, "target_relative_path"),
        )
        object.__setattr__(self, "sha256", _validate_sha256(self.sha256, "sha256"))
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class PotcarResolutionEntry:
    """One licensed POTCAR identity requested by a future execution host."""

    element: str
    symbol: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "element", _require_text(self.element, "element"))
        object.__setattr__(self, "symbol", _require_text(self.symbol, "symbol"))
        object.__setattr__(self, "sha256", _validate_sha256(self.sha256, "sha256"))


@dataclass(frozen=True, slots=True)
class PotcarResolutionRequest:
    """Portable license-safe request for ordered POTCAR concatenation."""

    family: str
    core_method_hash: str
    metadata_hash: str
    entries: tuple[PotcarResolutionEntry, ...]
    target_relative_path: str = "POTCAR"

    def __post_init__(self) -> None:
        object.__setattr__(self, "family", _require_text(self.family, "family"))
        object.__setattr__(
            self,
            "core_method_hash",
            _validate_sha256(self.core_method_hash, "core_method_hash"),
        )
        object.__setattr__(
            self,
            "metadata_hash",
            _validate_sha256(self.metadata_hash, "metadata_hash"),
        )
        object.__setattr__(
            self,
            "target_relative_path",
            _validate_relative_path(self.target_relative_path, "target_relative_path"),
        )
        if not self.entries:
            raise ValueError("POTCAR resolution request requires at least one entry")
        if len({item.element for item in self.entries}) != len(self.entries):
            raise ValueError("POTCAR resolution request elements must be unique")


@dataclass(frozen=True, slots=True)
class ExpectedOutput:
    """One output contract consumed by retrieval/parsing after execution."""

    role: str
    artifact_type: ArtifactType
    relative_path: str
    retrieval_policy: RetrievalPolicy
    required: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_text(self.role, "role"))
        object.__setattr__(
            self,
            "relative_path",
            _validate_relative_path(self.relative_path, "relative_path"),
        )


@dataclass(frozen=True, slots=True)
class VaspRuntimeConstraints:
    """Runtime compatibility predicates without selecting any scheduler or remote host."""

    required_version: str | None = None
    required_capabilities: tuple[VaspRuntimeCapability, ...] = ()

    def __post_init__(self) -> None:
        if self.required_version is not None:
            object.__setattr__(
                self,
                "required_version",
                _require_text(self.required_version, "required_version"),
            )
        capabilities = tuple(
            sorted(set(self.required_capabilities), key=lambda item: item.value)
        )
        object.__setattr__(self, "required_capabilities", capabilities)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable v0.3 handoff consumed by v0.4 execution adapters.

    The plan pins the scientific input manifest and safe staging artifacts, but never stores
    licensed POTCAR bodies/paths, scheduler job identifiers, remote directories, or retries.
    Execution-only settings are recorded separately from MethodFingerprint identity.
    """

    calculation_id: CalculationId
    recipe_id: str
    system_context: VaspSystemContext
    input_manifest_artifact_id: ArtifactId
    input_manifest_sha256: str
    preparation_hash: str
    staging_inputs: tuple[StagingInput, ...]
    potcar_resolution: PotcarResolutionRequest
    expected_outputs: tuple[ExpectedOutput, ...]
    runtime_constraints: VaspRuntimeConstraints
    execution_settings: ExecutionSettings
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipe_id", _require_text(self.recipe_id, "recipe_id"))
        object.__setattr__(
            self,
            "input_manifest_sha256",
            _validate_sha256(self.input_manifest_sha256, "input_manifest_sha256"),
        )
        object.__setattr__(
            self,
            "preparation_hash",
            _validate_sha256(self.preparation_hash, "preparation_hash"),
        )
        roles = tuple(item.role for item in self.staging_inputs)
        if roles != tuple(sorted(roles)) or len(roles) != len(set(roles)):
            raise ValueError("staging input roles must be unique and sorted")
        targets = tuple(item.target_relative_path for item in self.staging_inputs)
        if len(targets) != len(set(targets)):
            raise ValueError("staging input target paths must be unique")
        output_roles = tuple(item.role for item in self.expected_outputs)
        if (
            output_roles != tuple(sorted(output_roles))
            or len(output_roles) != len(set(output_roles))
        ):
            raise ValueError("expected output roles must be unique and sorted")
        object.__setattr__(
            self,
            "plan_hash",
            canonical_sha256(
                {
                    "calculation_id": self.calculation_id,
                    "recipe_id": self.recipe_id,
                    "system_context": self.system_context,
                    "input_manifest_artifact_id": self.input_manifest_artifact_id,
                    "input_manifest_sha256": self.input_manifest_sha256,
                    "preparation_hash": self.preparation_hash,
                    "staging_inputs": self.staging_inputs,
                    "potcar_resolution": self.potcar_resolution,
                    "expected_outputs": self.expected_outputs,
                    "runtime_constraints": self.runtime_constraints,
                    "execution_settings_hash": self.execution_settings.execution_hash,
                }
            ),
        )

    @property
    def execution_settings_hash(self) -> str:
        return self.execution_settings.execution_hash


def build_execution_plan(
    *,
    project_root: Path | str,
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    materialized: MaterializedInputSet,
    resolved_potcars: ResolvedPotcarSet,
    execution_settings: ExecutionSettings | None = None,
) -> ExecutionPlan:
    """Build a portable execution handoff from one exact materialized VASP input set."""

    settings = execution_settings if execution_settings is not None else ExecutionSettings()
    _validate_v03_execution_settings(settings)
    _validate_plan_identity(
        calculation=calculation,
        fingerprint=fingerprint,
        system_context=system_context,
        materialized=materialized,
        resolved_potcars=resolved_potcars,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ExecutionPlanError("project_root must be an existing project directory")
    staging_inputs, manifest_artifact_id = _staging_inputs(
        project_root=root,
        materialized=materialized,
        calculation=calculation,
    )
    potcar_request = _potcar_request(resolved_potcars, fingerprint, materialized)
    return ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=system_context,
        input_manifest_artifact_id=manifest_artifact_id,
        input_manifest_sha256=materialized.manifest.sha256,
        preparation_hash=materialized.manifest.preparation_hash,
        staging_inputs=staging_inputs,
        potcar_resolution=potcar_request,
        expected_outputs=_expected_outputs(calculation.recipe_id),
        runtime_constraints=_runtime_constraints(fingerprint),
        execution_settings=settings,
    )


def _validate_v03_execution_settings(settings: ExecutionSettings) -> None:
    deferred = {
        "nodes": settings.nodes,
        "cores": settings.cores,
        "memory_mb": settings.memory_mb,
        "walltime_seconds": settings.walltime_seconds,
        "partition": settings.partition,
    }
    configured = tuple(name for name, value in deferred.items() if value is not None)
    if configured:
        raise ExecutionPlanError(
            "v0.3 ExecutionPlan defers scheduler resource fields to v0.4 adapters: "
            + ", ".join(configured)
        )
    if "/" in settings.executable or "\\" in settings.executable:
        raise ExecutionPlanError(
            "v0.3 ExecutionPlan executable must be a portable command name, not a host path"
        )


def _validate_plan_identity(
    *,
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    system_context: VaspSystemContext,
    materialized: MaterializedInputSet,
    resolved_potcars: ResolvedPotcarSet,
) -> None:
    if calculation.engine is not CalculationEngine.VASP:
        raise ExecutionPlanError("ExecutionPlan only supports VASP Calculations")
    if calculation.id != materialized.calculation_id:
        raise ExecutionPlanError("materialized input set belongs to another Calculation")
    if calculation.id != materialized.manifest.calculation_id:
        raise ExecutionPlanError("input manifest belongs to another Calculation")
    if calculation.method_fingerprint_id != fingerprint.id:
        raise ExecutionPlanError("Calculation MethodFingerprint id does not match fingerprint")
    if calculation.recipe_id != fingerprint.recipe.recipe_id:
        raise ExecutionPlanError("Calculation recipe does not match MethodFingerprint")
    spec = get_vasp_recipe_spec(calculation.recipe_id)
    if calculation.calculation_type is not spec.calculation_type:
        raise ExecutionPlanError("CalculationType does not match canonical VASP recipe")
    if system_context.kind not in spec.allowed_system_kinds:
        raise ExecutionPlanError("VASP system context is incompatible with canonical recipe")
    if fingerprint.recipe.version != spec.version:
        raise ExecutionPlanError("MethodFingerprint recipe version is not canonical")
    if fingerprint.method.engine.casefold() != "vasp":
        raise ExecutionPlanError("MethodFingerprint engine must be VASP")
    if resolved_potcars.spec.core_method_hash != fingerprint.core_method_hash:
        raise ExecutionPlanError("resolved POTCAR set does not match core Method identity")

    payload = materialized.manifest.payload
    calculation_payload = _mapping(payload, "calculation")
    method_payload = _mapping(payload, "method_fingerprint")
    recipe_payload = _mapping(payload, "recipe")
    context_payload = _mapping(payload, "system_context")
    preparations_payload = _mapping(payload, "preparations")
    if _string(calculation_payload, "id") != str(calculation.id):
        raise ExecutionPlanError("manifest Calculation id does not match")
    if _string(method_payload, "id") != str(fingerprint.id):
        raise ExecutionPlanError("manifest MethodFingerprint id does not match")
    if _string(method_payload, "instance_hash") != fingerprint.instance_hash:
        raise ExecutionPlanError("manifest MethodFingerprint instance hash does not match")
    if _string(recipe_payload, "id") != fingerprint.recipe.recipe_id:
        raise ExecutionPlanError("manifest Recipe id does not match")
    if _string(recipe_payload, "version") != fingerprint.recipe.version:
        raise ExecutionPlanError("manifest Recipe version does not match")
    if _string(context_payload, "kind") != system_context.kind.value:
        raise ExecutionPlanError("manifest VASP system kind does not match")
    expected_axis = (
        system_context.vacuum_axis.value if system_context.vacuum_axis is not None else None
    )
    if context_payload.get("vacuum_axis") != expected_axis:
        raise ExecutionPlanError("manifest VASP vacuum axis does not match")
    if (
        _string(preparations_payload, "potcar_metadata_hash")
        != resolved_potcars.spec.metadata_hash
    ):
        raise ExecutionPlanError("manifest POTCAR metadata hash does not match resolved set")


def _staging_inputs(
    *,
    project_root: Path,
    materialized: MaterializedInputSet,
    calculation: Calculation,
) -> tuple[tuple[StagingInput, ...], ArtifactId]:
    artifacts_by_path = {
        artifact.local_path: artifact
        for artifact in materialized.artifacts
        if artifact.local_path is not None
    }
    manifest_artifact = next(
        (item for item in materialized.artifacts if item.id == materialized.manifest_artifact_id),
        None,
    )
    if manifest_artifact is None or manifest_artifact.local_path is None:
        raise ExecutionPlanError("input-manifest Artifact is missing")

    expected_paths = {record.relative_path for record in materialized.manifest.files}
    expected_paths.add(manifest_artifact.local_path)
    if set(artifacts_by_path) != expected_paths:
        raise ExecutionPlanError("materialized Artifact paths do not exactly match input manifest")

    items: list[StagingInput] = []
    vasp_roles = frozenset({"incar", "poscar", "kpoints"})
    for record in materialized.manifest.files:
        artifact = artifacts_by_path.get(record.relative_path)
        if artifact is None:
            raise ExecutionPlanError(f"manifest Artifact is missing: {record.relative_path}")
        _validate_materialized_artifact(
            project_root=project_root,
            artifact=artifact,
            calculation=calculation,
            artifact_type=record.artifact_type,
            sha256=record.sha256,
            size_bytes=record.size_bytes,
        )
        items.append(
            StagingInput(
                role=record.role,
                kind=(
                    StagingInputKind.VASP_INPUT
                    if record.role in vasp_roles
                    else StagingInputKind.METADATA
                ),
                artifact_id=artifact.id,
                artifact_type=artifact.artifact_type,
                source_relative_path=record.relative_path,
                target_relative_path=PurePosixPath(record.relative_path).name,
                sha256=record.sha256,
                size_bytes=record.size_bytes,
            )
        )

    manifest_size = len(materialized.manifest.text.encode("utf-8"))
    _validate_materialized_artifact(
        project_root=project_root,
        artifact=manifest_artifact,
        calculation=calculation,
        artifact_type=ArtifactType.DERIVED_DATASET,
        sha256=materialized.manifest.sha256,
        size_bytes=manifest_size,
    )
    items.append(
        StagingInput(
            role="input_manifest",
            kind=StagingInputKind.METADATA,
            artifact_id=manifest_artifact.id,
            artifact_type=manifest_artifact.artifact_type,
            source_relative_path=manifest_artifact.local_path,
            target_relative_path="input-manifest.json",
            sha256=materialized.manifest.sha256,
            size_bytes=manifest_size,
        )
    )
    return tuple(sorted(items, key=lambda item: item.role)), manifest_artifact.id


def _validate_materialized_artifact(
    *,
    project_root: Path,
    artifact: Artifact,
    calculation: Calculation,
    artifact_type: ArtifactType,
    sha256: str,
    size_bytes: int,
) -> None:
    if artifact.artifact_type is not artifact_type:
        raise ExecutionPlanError("materialized Artifact type does not match manifest")
    if artifact.availability not in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}:
        raise ExecutionPlanError("staging Artifact must be locally available")
    if not isinstance(artifact.producer, CalculationProducerRef):
        raise ExecutionPlanError("staging Artifact must be Calculation-produced")
    if artifact.producer.id != calculation.id:
        raise ExecutionPlanError("staging Artifact producer must be the exact Calculation")
    if artifact.sha256 != sha256 or artifact.size_bytes != size_bytes:
        raise ExecutionPlanError("staging Artifact digest/size does not match manifest")
    if artifact.local_path is None:
        raise ExecutionPlanError("staging Artifact must have a local path")

    relative = _validate_relative_path(artifact.local_path, "Artifact.local_path")
    source = (project_root / Path(*PurePosixPath(relative).parts)).resolve()
    if not source.is_relative_to(project_root):
        raise ExecutionPlanError("staging Artifact resolves outside project_root")
    if not source.is_file():
        raise ExecutionPlanError(f"staging Artifact file is missing: {relative}")
    actual_size = source.stat().st_size
    if actual_size != size_bytes:
        raise ExecutionPlanError(f"staging Artifact file size changed: {relative}")
    actual_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_sha != sha256:
        raise ExecutionPlanError(f"staging Artifact file hash changed: {relative}")


def _potcar_request(
    resolved: ResolvedPotcarSet,
    fingerprint: MethodFingerprint,
    materialized: MaterializedInputSet,
) -> PotcarResolutionRequest:
    if resolved.spec.core_method_hash != fingerprint.core_method_hash:
        raise ExecutionPlanError("POTCAR spec core method does not match fingerprint")
    if resolved.spec.species_order != tuple(item.element for item in resolved.spec.entries):
        raise ExecutionPlanError("POTCAR spec order is internally inconsistent")
    for item in resolved.files:
        if not item.path.is_file():
            raise ExecutionPlanError(
                f"licensed POTCAR disappeared before handoff: {item.entry.symbol}"
            )
        actual_sha = hashlib.sha256(item.path.read_bytes()).hexdigest()
        if actual_sha != item.entry.sha256:
            identity = f"{item.entry.element}/{item.entry.symbol}"
            raise ExecutionPlanError(
                f"licensed POTCAR changed after resolution: {identity}"
            )
    preparations = _mapping(materialized.manifest.payload, "preparations")
    if _string(preparations, "potcar_metadata_hash") != resolved.spec.metadata_hash:
        raise ExecutionPlanError("POTCAR metadata changed after input materialization")
    families = {item.family for item in resolved.spec.entries}
    if len(families) != 1 or next(iter(families)) != fingerprint.method.potcar_family:
        raise ExecutionPlanError("POTCAR family does not match MethodFingerprint")
    return PotcarResolutionRequest(
        family=fingerprint.method.potcar_family,
        core_method_hash=fingerprint.core_method_hash,
        metadata_hash=resolved.spec.metadata_hash,
        entries=tuple(
            PotcarResolutionEntry(item.element, item.symbol, item.sha256)
            for item in resolved.spec.entries
        ),
    )


def _expected_outputs(recipe_id: str) -> tuple[ExpectedOutput, ...]:
    outputs: list[ExpectedOutput] = [
        ExpectedOutput(
            "outcar",
            ArtifactType.OUTCAR,
            "OUTCAR",
            RetrievalPolicy.ALWAYS,
            True,
        ),
        ExpectedOutput(
            "oszicar",
            ArtifactType.OSZICAR,
            "OSZICAR",
            RetrievalPolicy.ALWAYS,
            False,
        ),
    ]
    if recipe_id in {RECIPE_SLAB_RELAX, RECIPE_ADSORBATE_RELAX, RECIPE_GAS_RELAX}:
        outputs.append(
            ExpectedOutput(
                "contcar",
                ArtifactType.CONTCAR,
                "CONTCAR",
                RetrievalPolicy.ALWAYS,
                True,
            )
        )
    elif recipe_id == RECIPE_DOS_PREREQUISITE:
        outputs.append(
            ExpectedOutput(
                "doscar",
                ArtifactType.DOSCAR,
                "DOSCAR",
                RetrievalPolicy.ALWAYS,
                True,
            )
        )
    elif recipe_id == RECIPE_CHARGE_DENSITY_STATIC:
        outputs.append(
            ExpectedOutput(
                "chgcar",
                ArtifactType.CHGCAR,
                "CHGCAR",
                RetrievalPolicy.ALWAYS,
                True,
            )
        )
    elif recipe_id == RECIPE_LOBSTER_PREREQUISITE:
        outputs.append(
            ExpectedOutput(
                "wavecar",
                ArtifactType.WAVECAR,
                "WAVECAR",
                RetrievalPolicy.ON_DEMAND,
                True,
            )
        )
    elif recipe_id not in {
        RECIPE_GROUND_STATE_STATIC,
        RECIPE_SELECTED_ATOM_FREQUENCY,
        RECIPE_FULL_FREQUENCY,
        RECIPE_GAS_FREQUENCY,
        RECIPE_ENCUT_CONVERGENCE_POINT,
        RECIPE_KPOINT_CONVERGENCE_POINT,
    }:
        raise ExecutionPlanError(f"unsupported VASP recipe output contract: {recipe_id}")
    return tuple(sorted(outputs, key=lambda item: item.role))


def _runtime_constraints(fingerprint: MethodFingerprint) -> VaspRuntimeConstraints:
    capabilities: list[VaspRuntimeCapability] = []
    method = fingerprint.method
    if method.spin_treatment.value == "noncollinear":
        capabilities.append(VaspRuntimeCapability.NONCOLLINEAR)
    if method.soc:
        capabilities.append(VaspRuntimeCapability.SOC)
    if method.solvation_model is not None:
        capabilities.append(VaspRuntimeCapability.SOLVATION)
    if method.charge_e != 0.0:
        capabilities.append(VaspRuntimeCapability.CHARGED_CELL)
    if method.electric_field_ev_per_angstrom is not None:
        capabilities.append(VaspRuntimeCapability.ELECTRIC_FIELD)
    if fingerprint.recipe.recipe_id in {
        RECIPE_SELECTED_ATOM_FREQUENCY,
        RECIPE_FULL_FREQUENCY,
        RECIPE_GAS_FREQUENCY,
    }:
        capabilities.append(VaspRuntimeCapability.FINITE_DIFFERENCE_FREQUENCY)
    if fingerprint.recipe.recipe_id == RECIPE_DOS_PREREQUISITE:
        capabilities.append(VaspRuntimeCapability.DOS_OUTPUT)
    if fingerprint.recipe.recipe_id == RECIPE_CHARGE_DENSITY_STATIC:
        capabilities.append(VaspRuntimeCapability.CHARGE_DENSITY_OUTPUT)
    if fingerprint.recipe.recipe_id == RECIPE_LOBSTER_PREREQUISITE:
        capabilities.append(VaspRuntimeCapability.WAVECAR_OUTPUT)
    return VaspRuntimeConstraints(
        required_version=method.engine_version,
        required_capabilities=tuple(capabilities),
    )


def _mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ExecutionPlanError(f"manifest {key} must be an object")
    normalized: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise ExecutionPlanError(f"manifest {key} must have string keys")
        normalized[raw_key] = raw_value
    return normalized


def _string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ExecutionPlanError(f"manifest {key} must be a string")
    return value
