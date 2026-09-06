"""Public, fail-closed exports for the canonical Block 8 reaction-diagram implementation."""

from ecatvasp.thermo.electrocatalysis import (
    HERDeltaGHStarResult,
    LimitingPotentialResult,
    OEROverpotentialResult,
    ReversiblePotentialResult,
)
from ecatvasp.thermo.reaction_diagram import (
    CANONICAL_REACTION_DIAGRAM_FORMAT,
    CANONICAL_REACTION_DIAGRAM_VERSION,
    REACTION_DIAGRAM_TOOL_NAME,
    REACTION_DIAGRAM_TOOL_VERSION,
    DurableReactionDiagram,
    ReactionDiagramDataset,
    ReactionDiagramDescriptorKind,
    ReactionDiagramDescriptorUnit,
    ReactionDiagramError,
    ReactionSourceArtifactBinding,
    materialize_reaction_diagram,
)
from ecatvasp.thermo.reaction_diagram import (
    ReactionDiagramDescriptorDefinition as _ReactionDiagramDescriptorDefinition,
)
from ecatvasp.thermo.reaction_diagram import (
    ReactionDiagramSourceReceipt as _ReactionDiagramSourceReceipt,
)


class ReactionDiagramDescriptorDefinition(_ReactionDiagramDescriptorDefinition):
    """Public descriptor definition with runtime enum validation."""

    __slots__ = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReactionDiagramDescriptorKind):
            raise ReactionDiagramError("descriptor kind must be ReactionDiagramDescriptorKind")
        if not isinstance(self.unit, ReactionDiagramDescriptorUnit):
            raise ReactionDiagramError("descriptor unit must be ReactionDiagramDescriptorUnit")
        super().__post_init__()


class ReactionDiagramSourceReceipt(_ReactionDiagramSourceReceipt):
    """Public source receipt that rejects forged source-kind strings."""

    __slots__ = ()

    def __post_init__(self) -> None:
        from ecatvasp.thermo.reaction import ReactionEnergySourceKind

        if not isinstance(self.source_kind, ReactionEnergySourceKind):
            raise ReactionDiagramError("source_kind must be ReactionEnergySourceKind")
        super().__post_init__()


def define_limiting_potential_descriptor(
    *, key: str, result: LimitingPotentialResult
) -> ReactionDiagramDescriptorDefinition:
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
    *, key: str, result: ReversiblePotentialResult
) -> ReactionDiagramDescriptorDefinition:
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
    *, key: str, result: OEROverpotentialResult
) -> ReactionDiagramDescriptorDefinition:
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
    *, key: str, result: HERDeltaGHStarResult
) -> ReactionDiagramDescriptorDefinition:
    return ReactionDiagramDescriptorDefinition(
        key=key,
        kind=ReactionDiagramDescriptorKind.HER_DELTA_G_H_STAR,
        value=result.delta_g_h_star_ev,
        unit=ReactionDiagramDescriptorUnit.ELECTRON_VOLT,
        source_result_hash=result.result_hash,
    )


__all__ = [
    "CANONICAL_REACTION_DIAGRAM_FORMAT",
    "CANONICAL_REACTION_DIAGRAM_VERSION",
    "REACTION_DIAGRAM_TOOL_NAME",
    "REACTION_DIAGRAM_TOOL_VERSION",
    "DurableReactionDiagram",
    "ReactionDiagramDataset",
    "ReactionDiagramDescriptorDefinition",
    "ReactionDiagramDescriptorKind",
    "ReactionDiagramDescriptorUnit",
    "ReactionDiagramError",
    "ReactionDiagramSourceReceipt",
    "ReactionSourceArtifactBinding",
    "define_her_delta_g_h_star_descriptor",
    "define_limiting_potential_descriptor",
    "define_oer_overpotential_descriptor",
    "define_reversible_potential_descriptor",
    "materialize_reaction_diagram",
]
