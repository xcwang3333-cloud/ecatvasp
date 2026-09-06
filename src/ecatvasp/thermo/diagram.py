"""Durable, plotting-neutral electrocatalytic reaction-diagram datasets for v0.8 Block 8.

The materializer re-evaluates the generic Block 6 pathway from exact scientific sources, verifies
each non-CHE source against its durable THERMOCHEMISTRY Analysis/Artifact bytes, applies the Block 7
potential view, and only then writes an Analysis-produced DERIVED_DATASET.  It does not infer
species from filenames, persist a second workflow state machine, or become a plotting API.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path, PurePosixPath

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    RetrievalPolicy,
    canonical_json,
    canonical_sha256,
)
from ecatvasp.domain.ids import AnalysisId, ArtifactId, ProjectId
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.thermo.che import CHEConditions
from ecatvasp.thermo.electrocatalysis import (
    HERDeltaGHStarResult,
    LimitingPotentialResult,
    OEROverpotentialResult,
    PotentialDependentPathwayView,
    ReversiblePotentialResult,
    evaluate_potential_dependent_pathway_view,
)
from ecatvasp.thermo.gas import (
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
)
from ecatvasp.thermo.harmonic import (
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
)
from ecatvasp.thermo.reaction import (
    CHEReactionSource,
    MolecularReferenceReactionSource,
    ReactionEnergySource,
    ReactionEnergySourceKind,
    ReactionPathwayDefinition,
    ReactionPathwayResult,
    ThermochemistryReactionSource,
    evaluate_reaction_pathway,
)
from ecatvasp.thermo.references import (
    REFERENCE_CORRECTION_TOOL_NAME,
    REFERENCE_CORRECTION_TOOL_VERSION,
)

REACTION_DIAGRAM_TOOL_NAME = "ecatvasp.thermo.reaction-diagram"
REACTION_DIAGRAM_TOOL_VERSION = "1"
CANONICAL_REACTION_DIAGRAM_FORMAT = "ecatvasp-canonical-reaction-diagram"
CANONICAL_REACTION_DIAGRAM_VERSION = 1


class ReactionDiagramError(ValueError):
    """Raised when a durable reaction diagram cannot be formed without guessing."""


class ReactionDiagramDescriptorKind(StrEnum):
    """Initial scalar descriptor vocabulary that may accompany a canonical diagram."""

    LIMITING_POTENTIAL = "limiting_potential"
    REVERSIBLE_POTENTIAL = "reversible_potential"
    OER_THEORETICAL_OVERPOTENTIAL = "oer_theoretical_overpotential"
    HER_DELTA_G_H_STAR = "her_delta_g_h_star"


class ReactionDiagramDescriptorUnit(StrEnum):
    """Units supported by Block 8 scalar descriptor definitions."""

    VOLT = "V"
    ELECTRON_VOLT = "eV"


_PATHWAY_DESCRIPTOR_KINDS = frozenset(
    {
        ReactionDiagramDescriptorKind.LIMITING_POTENTIAL,
        ReactionDiagramDescriptorKind.REVERSIBLE_POTENTIAL,
        ReactionDiagramDescriptorKind.OER_THEORETICAL_OVERPOTENTIAL,
    }
)


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ReactionDiagramError(f"{field_name} must not be blank")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64:
        raise ReactionDiagramError(
            f"{field_name} must be a 64-character SHA-256 digest"
        )
    try:
        int(normalized, 16)
    except ValueError as error:
        raise ReactionDiagramError(
            f"{field_name} must contain only hexadecimal characters"
        ) from error
    return normalized


@dataclass(frozen=True, slots=True)
class ReactionDiagramDescriptorDefinition:
    """Content-addressed scalar descriptor definition; never an unbound display label."""

    key: str
    kind: ReactionDiagramDescriptorKind
    value: float
    unit: ReactionDiagramDescriptorUnit
    source_result_hash: str
    pathway_hash: str | None = None
    baseline_result_hash: str | None = None
    definition_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _require_text(self.key, "descriptor key")
        if not isfinite(self.value):
            raise ReactionDiagramError("descriptor value must be finite")
        object.__setattr__(
            self,
            "source_result_hash",
            _normalized_sha256(self.source_result_hash, "source_result_hash"),
        )
        expected_unit = (
            ReactionDiagramDescriptorUnit.ELECTRON_VOLT
            if self.kind is ReactionDiagramDescriptorKind.HER_DELTA_G_H_STAR
            else ReactionDiagramDescriptorUnit.VOLT
        )
        if self.unit is not expected_unit:
            raise ReactionDiagramError(
                "descriptor unit does not match the declared descriptor kind"
            )
        pathway_bound = self.kind in _PATHWAY_DESCRIPTOR_KINDS
        if pathway_bound:
            if self.pathway_hash is None or self.baseline_result_hash is None:
                raise ReactionDiagramError(
                    "pathway descriptor requires pathway_hash and baseline_result_hash"
                )
            object.__setattr__(
                self,
                "pathway_hash",
                _normalized_sha256(self.pathway_hash, "pathway_hash"),
            )
            object.__setattr__(
                self,
                "baseline_result_hash",
                _normalized_sha256(
                    self.baseline_result_hash,
                    "baseline_result_hash",
                ),
            )
        elif self.pathway_hash is not None or self.baseline_result_hash is not None:
            raise ReactionDiagramError(
                "HER Delta G_H* descriptor is adsorption-bound rather than pathway-bound"
            )
        object.__setattr__(self, "definition_hash", canonical_sha256(self))


def define_limiting_potential_descriptor(
    *,
    key: str,
    result: LimitingPotentialResult,
) -> ReactionDiagramDescriptorDefinition:
    """Bind a Block 7 limiting-potential result to a durable scalar definition."""

    return ReactionDiagramDescriptorDefinition(
        key=key,
        kind=ReactionDiagramDescriptorKind.LIMITING_POTENTIAL,
        value=result.limiting_potential_v,
        unit=ReactionDiagramDescriptorUnit.VOLT,
        source_result_hash=result.result_hash,
        pathway_hash=result.pathway_hash,
        baseline_result_hash=result.baseline_result_hash,
    )


def define_reversible_potential_descriptor(
    *,
    key: str,
    result: ReversiblePotentialResult,
) -> ReactionDiagramDescriptorDefinition:
    """Bind a Block 7 reversible-potential result to a durable scalar definition."""

    return ReactionDiagramDescriptorDefinition(
        key=key,
        kind=ReactionDiagramDescriptorKind.REVERSIBLE_POTENTIAL,
        value=result.reversible_potential_v,
        unit=ReactionDiagramDescriptorUnit.VOLT,
        source_result_hash=result.result_hash,
        pathway_hash=result.pathway_hash,
        baseline_result_hash=result.baseline_result_hash,
    )


def define_oer_overpotential_descriptor(
    *,
    key: str,
    result: OEROverpotentialResult,
) -> ReactionDiagramDescriptorDefinition:
    """Bind a Block 7 OER theoretical-overpotential result to a durable definition."""

    return ReactionDiagramDescriptorDefinition(
        key=key,
        kind=ReactionDiagramDescriptorKind.OER_THEORETICAL_OVERPOTENTIAL,
        value=result.overpotential_v,
        unit=ReactionDiagramDescriptorUnit.VOLT,
        source_result_hash=result.result_hash,
        pathway_hash=result.limiting.pathway_hash,
        baseline_result_hash=result.limiting.baseline_result_hash,
    )


def define_her_delta_g_h_star_descriptor(
    *,
    key: str,
    result: HERDeltaGHStarResult,
) -> ReactionDiagramDescriptorDefinition:
    """Bind a Block 7 HER adsorption descriptor without pretending it is pathway-derived."""

    return ReactionDiagramDescriptorDefinition(
        key=key,
        kind=ReactionDiagramDescriptorKind.HER_DELTA_G_H_STAR,
        value=result.delta_g_h_star_ev,
        unit=ReactionDiagramDescriptorUnit.ELECTRON_VOLT,
        source_result_hash=result.result_hash,
    )


@dataclass(frozen=True, slots=True)
class ReactionSourceArtifactBinding:
    """Exact durable THERMOCHEMISTRY Analysis/Artifact for one non-CHE reaction source."""

    species_key: str
    analysis: Analysis
    artifact: Artifact

    def __post_init__(self) -> None:
        _require_text(self.species_key, "species_key")
        if self.analysis.analysis_type is not AnalysisType.THERMOCHEMISTRY:
            raise ReactionDiagramError(
                "reaction source binding Analysis must be THERMOCHEMISTRY"
            )
        if self.analysis.status is not AnalysisStatus.COMPLETED:
            raise ReactionDiagramError(
                "reaction source binding Analysis must be completed"
            )
        if self.artifact.artifact_type is not ArtifactType.DERIVED_DATASET:
            raise ReactionDiagramError(
                "reaction source binding Artifact must be DERIVED_DATASET"
            )
        if (
            not isinstance(self.artifact.producer, AnalysisProducerRef)
            or self.artifact.producer.id != self.analysis.id
        ):
            raise ReactionDiagramError(
                "reaction source Artifact producer must be the bound Analysis"
            )
        if self.artifact.availability not in {
            ArtifactAvailability.LOCAL,
            ArtifactAvailability.BOTH,
        }:
            raise ReactionDiagramError(
                "reaction source Artifact must be locally available for verification"
            )
        if self.artifact.local_path is None:
            raise ReactionDiagramError("reaction source Artifact requires local_path")
        if self.artifact.sha256 is None or self.artifact.size_bytes is None:
            raise ReactionDiagramError(
                "reaction source Artifact requires SHA-256 and byte size"
            )


@dataclass(frozen=True, slots=True)
class ReactionDiagramSourceReceipt:
    """Minimal immutable source identity retained inside a canonical diagram dataset."""

    species_key: str
    source_kind: ReactionEnergySourceKind
    source_hash: str
    analysis_id: AnalysisId
    artifact_id: ArtifactId
    artifact_sha256: str
    artifact_result_hash: str

    def __post_init__(self) -> None:
        _require_text(self.species_key, "species_key")
        for field_name in (
            "source_hash",
            "artifact_sha256",
            "artifact_result_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalized_sha256(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class ReactionDiagramDataset:
    """Canonical plotting-neutral pathway data at one explicit requested CHE condition."""

    project_id: ProjectId
    pathway_definition_hash: str
    baseline_pathway_result_hash: str
    baseline_conditions: CHEConditions
    requested_conditions: CHEConditions
    potential_view: PotentialDependentPathwayView
    source_receipts: tuple[ReactionDiagramSourceReceipt, ...]
    descriptor_definitions: tuple[ReactionDiagramDescriptorDefinition, ...] = ()
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pathway_definition_hash",
            _normalized_sha256(
                self.pathway_definition_hash,
                "pathway_definition_hash",
            ),
        )
        object.__setattr__(
            self,
            "baseline_pathway_result_hash",
            _normalized_sha256(
                self.baseline_pathway_result_hash,
                "baseline_pathway_result_hash",
            ),
        )
        if self.potential_view.pathway_hash != self.pathway_definition_hash:
            raise ReactionDiagramError(
                "potential view pathway differs from durable diagram definition"
            )
        if self.potential_view.baseline_result_hash != self.baseline_pathway_result_hash:
            raise ReactionDiagramError(
                "potential view baseline result differs from durable diagram"
            )
        if self.potential_view.baseline_conditions != self.baseline_conditions:
            raise ReactionDiagramError(
                "potential view baseline CHE conditions differ from durable diagram"
            )
        if self.potential_view.target_conditions != self.requested_conditions:
            raise ReactionDiagramError(
                "potential view requested CHE conditions differ from durable diagram"
            )
        if not self.source_receipts:
            raise ReactionDiagramError(
                "reaction diagram requires at least one durable non-CHE scientific source"
            )
        receipts = tuple(sorted(self.source_receipts, key=lambda item: item.species_key))
        receipt_keys = tuple(item.species_key for item in receipts)
        if len(receipt_keys) != len(set(receipt_keys)):
            raise ReactionDiagramError("diagram source receipt species keys must be unique")
        object.__setattr__(self, "source_receipts", receipts)
        descriptors = tuple(
            sorted(self.descriptor_definitions, key=lambda item: item.key)
        )
        descriptor_keys = tuple(item.key for item in descriptors)
        if len(descriptor_keys) != len(set(descriptor_keys)):
            raise ReactionDiagramError("diagram descriptor keys must be unique")
        for descriptor in descriptors:
            if descriptor.kind not in _PATHWAY_DESCRIPTOR_KINDS:
                continue
            if descriptor.pathway_hash != self.pathway_definition_hash:
                raise ReactionDiagramError(
                    "pathway descriptor belongs to another reaction pathway"
                )
            if descriptor.baseline_result_hash != self.baseline_pathway_result_hash:
                raise ReactionDiagramError(
                    "pathway descriptor belongs to another baseline pathway result"
                )
        object.__setattr__(self, "descriptor_definitions", descriptors)
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "project_id": self.project_id,
                    "pathway_definition_hash": self.pathway_definition_hash,
                    "baseline_pathway_result_hash": self.baseline_pathway_result_hash,
                    "baseline_conditions": self.baseline_conditions,
                    "requested_conditions": self.requested_conditions,
                    "potential_view_hash": self.potential_view.result_hash,
                    "source_receipts": self.source_receipts,
                    "descriptor_definitions": self.descriptor_definitions,
                }
            ),
        )

    @property
    def state_keys(self) -> tuple[str, ...]:
        """Expose ordered pathway states without adding display semantics."""

        return self.potential_view.state_keys

    @property
    def cumulative_state_free_energies_ev(self) -> tuple[float, ...]:
        """Expose canonical requested-condition state free energies."""

        return self.potential_view.cumulative_state_free_energies_ev

    @property
    def step_delta_g_ev(self) -> tuple[float, ...]:
        """Expose canonical requested-condition step free energies."""

        return tuple(item.target_delta_g_ev for item in self.potential_view.step_views)


@dataclass(frozen=True, slots=True)
class DurableReactionDiagram:
    """Durable REACTION_DIAGRAM Analysis, Artifact, dataset, and scientific provenance."""

    analysis: Analysis
    artifact: Artifact
    dataset: ReactionDiagramDataset
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


def materialize_reaction_diagram(
    *,
    project_root: Path | str,
    definition: ReactionPathwayDefinition,
    sources: tuple[ReactionEnergySource, ...],
    source_bindings: tuple[ReactionSourceArtifactBinding, ...],
    baseline_conditions: CHEConditions,
    requested_conditions: CHEConditions,
    descriptor_definitions: tuple[ReactionDiagramDescriptorDefinition, ...] = (),
) -> DurableReactionDiagram:
    """Recompute, verify, and materialize one exact requested-condition reaction diagram."""

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ReactionDiagramError("project_root must be an existing directory")
    source_map = _source_map(sources)
    expected_keys = {
        term.species_key
        for step in definition.steps
        for term in step.terms
    }
    if set(source_map) != expected_keys:
        missing = sorted(expected_keys - set(source_map))
        extra = sorted(set(source_map) - expected_keys)
        raise ReactionDiagramError(
            f"reaction sources must exactly match pathway stoichiometry; missing={missing}, extra={extra}"
        )
    binding_map = _binding_map(source_bindings)
    expected_bound_keys = {
        key
        for key, source in source_map.items()
        if not isinstance(source, CHEReactionSource)
    }
    if set(binding_map) != expected_bound_keys:
        missing = sorted(expected_bound_keys - set(binding_map))
        extra = sorted(set(binding_map) - expected_bound_keys)
        raise ReactionDiagramError(
            "durable source bindings must exactly cover non-CHE reaction sources; "
            f"missing={missing}, extra={extra}"
        )
    if not binding_map:
        raise ReactionDiagramError(
            "reaction diagram requires at least one durable non-CHE source binding"
        )

    project_ids = {binding.analysis.project_id for binding in source_bindings}
    if len(project_ids) != 1:
        raise ReactionDiagramError(
            "all reaction source bindings must belong to the same Project"
        )
    project_id = next(iter(project_ids))

    receipts = tuple(
        _verify_bound_source_artifact(
            root=root,
            source=source_map[key],
            binding=binding_map[key],
        )
        for key in sorted(binding_map)
    )
    baseline = evaluate_reaction_pathway(definition=definition, sources=sources)
    view = evaluate_potential_dependent_pathway_view(
        baseline_pathway=baseline,
        baseline_conditions=baseline_conditions,
        target_conditions=requested_conditions,
    )
    dataset = ReactionDiagramDataset(
        project_id=project_id,
        pathway_definition_hash=definition.content_hash,
        baseline_pathway_result_hash=baseline.result_hash,
        baseline_conditions=baseline_conditions,
        requested_conditions=requested_conditions,
        potential_view=view,
        source_receipts=receipts,
        descriptor_definitions=descriptor_definitions,
    )
    input_artifact_ids = tuple(
        sorted({receipt.artifact_id for receipt in receipts}, key=str)
    )
    analysis = Analysis(
        project_id=project_id,
        analysis_type=AnalysisType.REACTION_DIAGRAM,
        input_artifact_ids=input_artifact_ids,
        status=AnalysisStatus.COMPLETED,
        tool=REACTION_DIAGRAM_TOOL_NAME,
        tool_version=REACTION_DIAGRAM_TOOL_VERSION,
        parameters_hash=dataset.result_hash,
    )
    payload = {
        "format": CANONICAL_REACTION_DIAGRAM_FORMAT,
        "version": CANONICAL_REACTION_DIAGRAM_VERSION,
        "analysis_id": analysis.id,
        "dataset_hash": dataset.result_hash,
        "dataset": dataset,
    }
    artifact = _write_result_artifact(root=root, analysis=analysis, payload=payload)
    provenance_records = (
        ProvenanceRecord(
            subject_id=analysis.id,
            tool=REACTION_DIAGRAM_TOOL_NAME,
            tool_version=REACTION_DIAGRAM_TOOL_VERSION,
            parameters_hash=analysis.parameters_hash,
        ),
        ProvenanceRecord(
            subject_id=artifact.id,
            tool=REACTION_DIAGRAM_TOOL_NAME,
            tool_version=REACTION_DIAGRAM_TOOL_VERSION,
            parameters_hash=artifact.sha256,
        ),
    )
    dependency_records = _dependency_records(
        bindings=tuple(binding_map[key] for key in sorted(binding_map)),
        analysis=analysis,
        artifact=artifact,
    )
    return DurableReactionDiagram(
        analysis=analysis,
        artifact=artifact,
        dataset=dataset,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def _source_map(sources: tuple[ReactionEnergySource, ...]) -> dict[str, ReactionEnergySource]:
    if not sources:
        raise ReactionDiagramError("reaction diagram requires an explicit source registry")
    result: dict[str, ReactionEnergySource] = {}
    for source in sources:
        if source.species_key in result:
            raise ReactionDiagramError("reaction source species keys must be unique")
        result[source.species_key] = source
    return result


def _binding_map(
    bindings: tuple[ReactionSourceArtifactBinding, ...],
) -> dict[str, ReactionSourceArtifactBinding]:
    result: dict[str, ReactionSourceArtifactBinding] = {}
    for binding in bindings:
        if binding.species_key in result:
            raise ReactionDiagramError("reaction source binding species keys must be unique")
        result[binding.species_key] = binding
    return result


def _verify_bound_source_artifact(
    *,
    root: Path,
    source: ReactionEnergySource,
    binding: ReactionSourceArtifactBinding,
) -> ReactionDiagramSourceReceipt:
    if source.species_key != binding.species_key:
        raise ReactionDiagramError("reaction source binding species key differs from source")
    if isinstance(source, CHEReactionSource):
        raise ReactionDiagramError("CHE sources must not be bound to durable Artifact inputs")

    expected_tool: tuple[str, str]
    expected_result_hash: str
    expected_result: object
    if isinstance(source, ThermochemistryReactionSource):
        expected_tool = (
            HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
            HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
        )
        expected_result_hash = source.result.result_hash
        expected_result = source.result
    elif isinstance(source, MolecularReferenceReactionSource):
        if source.corrected is None:
            expected_tool = (
                IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
                IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
            )
            expected_result_hash = source.raw.result.result_hash
            expected_result = source.raw.result
        else:
            expected_tool = (
                REFERENCE_CORRECTION_TOOL_NAME,
                REFERENCE_CORRECTION_TOOL_VERSION,
            )
            expected_result_hash = source.corrected.result_hash
            expected_result = source.corrected
    else:
        raise ReactionDiagramError("unsupported non-CHE reaction source kind")

    if (binding.analysis.tool, binding.analysis.tool_version) != expected_tool:
        raise ReactionDiagramError(
            "reaction source Analysis tool/version differs from source scientific semantics"
        )
    payload = _verified_artifact_payload(root=root, binding=binding)
    if payload.get("analysis_id") != str(binding.analysis.id):
        raise ReactionDiagramError("reaction source Artifact belongs to another Analysis")
    if payload.get("result_hash") != expected_result_hash:
        raise ReactionDiagramError(
            "reaction source durable result hash differs from in-memory scientific source"
        )
    if canonical_sha256(payload.get("result")) != canonical_sha256(expected_result):
        raise ReactionDiagramError(
            "reaction source durable result payload differs from in-memory scientific source"
        )
    artifact_sha256 = binding.artifact.sha256
    if artifact_sha256 is None:
        raise ReactionDiagramError("verified reaction source Artifact requires SHA-256")
    return ReactionDiagramSourceReceipt(
        species_key=source.species_key,
        source_kind=source.kind,
        source_hash=source.source_hash,
        analysis_id=binding.analysis.id,
        artifact_id=binding.artifact.id,
        artifact_sha256=artifact_sha256,
        artifact_result_hash=expected_result_hash,
    )


def _verified_artifact_payload(
    *,
    root: Path,
    binding: ReactionSourceArtifactBinding,
) -> dict[str, object]:
    artifact = binding.artifact
    if artifact.local_path is None:
        raise ReactionDiagramError("reaction source Artifact requires local_path")
    relative = PurePosixPath(artifact.local_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReactionDiagramError("reaction source Artifact path must be project-relative")
    absolute = (root / Path(*relative.parts)).resolve()
    if not absolute.is_relative_to(root) or not absolute.is_file():
        raise ReactionDiagramError("reaction source Artifact file is unavailable")
    body = absolute.read_bytes()
    if artifact.size_bytes != len(body):
        raise ReactionDiagramError("reaction source Artifact byte size differs")
    if artifact.sha256 != hashlib.sha256(body).hexdigest():
        raise ReactionDiagramError("reaction source Artifact SHA-256 differs")
    try:
        raw_payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReactionDiagramError(
            "reaction source Artifact is not valid UTF-8 JSON"
        ) from error
    if not isinstance(raw_payload, dict) or any(
        not isinstance(key, str) for key in raw_payload
    ):
        raise ReactionDiagramError("reaction source Artifact payload must be a JSON object")
    return raw_payload


def _dependency_records(
    *,
    bindings: tuple[ReactionSourceArtifactBinding, ...],
    analysis: Analysis,
    artifact: Artifact,
) -> tuple[DependencyRecord, ...]:
    records: list[DependencyRecord] = []
    for binding in bindings:
        records.extend(
            (
                DependencyRecord(
                    upstream_id=binding.analysis.id,
                    downstream_id=analysis.id,
                    kind=DependencyKind.SCIENTIFIC,
                    role=f"reaction_source_analysis:{binding.species_key}",
                    recorded_hash=scientific_hash(binding.analysis),
                ),
                DependencyRecord(
                    upstream_id=binding.artifact.id,
                    downstream_id=analysis.id,
                    kind=DependencyKind.SCIENTIFIC,
                    role=f"reaction_source_artifact:{binding.species_key}",
                    recorded_hash=scientific_hash(binding.artifact),
                ),
            )
        )
    records.append(
        DependencyRecord(
            upstream_id=analysis.id,
            downstream_id=artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="canonical_reaction_diagram",
            recorded_hash=scientific_hash(analysis),
        )
    )
    return tuple(records)


def _write_result_artifact(
    *,
    root: Path,
    analysis: Analysis,
    payload: object,
) -> Artifact:
    relative = Path("analyses") / str(analysis.id) / "canonical-reaction-diagram.json"
    absolute = (root / relative).resolve()
    if not absolute.is_relative_to(root):
        raise ReactionDiagramError("reaction diagram output resolves outside project_root")
    text = canonical_json(payload) + "\n"
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.exists():
        if not absolute.is_file():
            raise ReactionDiagramError("reaction diagram output is not a regular file")
        if absolute.read_text(encoding="utf-8") != text:
            raise ReactionDiagramError(
                "reaction diagram output already has different content"
            )
    else:
        temporary = absolute.with_name(f".{absolute.name}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, absolute)
        finally:
            if temporary.exists():
                temporary.unlink()
    body = text.encode("utf-8")
    return Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )
