"""Explicit electrocatalysis reaction-family preset compilers for v0.8 Block 7.

Presets only compile named electrocatalytic mechanisms into the generic Block 6
``ReactionPathwayDefinition`` contract. They never evaluate a second formula, infer species
from filenames, or reverse another pathway implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ecatvasp.domain import canonical_sha256
from ecatvasp.thermo.reaction import (
    ReactionPathwayDefinition,
    ReactionStepDefinition,
    StoichiometricTerm,
)


class ElectrocatalysisPresetError(ValueError):
    """Raised when a reaction-family preset would be scientifically ambiguous."""


class ElectrocatalysisReactionFamily(StrEnum):
    """Initial explicit electrocatalysis reaction-family vocabulary."""

    HER = "her"
    ORR = "orr"
    OER = "oer"
    CO2RR_TO_CO = "co2rr_to_co"


class ElectrocatalysisPresetKind(StrEnum):
    """Named directed mechanisms compiled by the v0.8 MVP preset layer."""

    HER_VOLMER_HEYROVSKY = "her_volmer_heyrovsky"
    ORR_ASSOCIATIVE_4E = "orr_associative_4e"
    OER_ASSOCIATIVE_4E = "oer_associative_4e"
    CO2RR_TO_CO_2E = "co2rr_to_co_2e"


_PRESET_FAMILY = {
    ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY: ElectrocatalysisReactionFamily.HER,
    ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E: ElectrocatalysisReactionFamily.ORR,
    ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E: ElectrocatalysisReactionFamily.OER,
    ElectrocatalysisPresetKind.CO2RR_TO_CO_2E: ElectrocatalysisReactionFamily.CO2RR_TO_CO,
}


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ElectrocatalysisPresetError(f"{field_name} must not be blank")


def _require_distinct_role_keys(**role_keys: str) -> None:
    for role, key in role_keys.items():
        _require_text(key, role)
    reverse: dict[str, list[str]] = {}
    for role, key in role_keys.items():
        reverse.setdefault(key, []).append(role)
    collisions = {key: roles for key, roles in reverse.items() if len(roles) > 1}
    if collisions:
        raise ElectrocatalysisPresetError(
            f"preset scientific role keys must be distinct; collisions={collisions}"
        )


def _term(species_key: str, coefficient: float) -> StoichiometricTerm:
    return StoichiometricTerm(species_key=species_key, coefficient=coefficient)


@dataclass(frozen=True, slots=True)
class ElectrocatalysisPathwayPreset:
    """Named preset plus its authoritative generic directed pathway definition."""

    family: ElectrocatalysisReactionFamily
    kind: ElectrocatalysisPresetKind
    definition: ReactionPathwayDefinition
    preset_hash: str = field(init=False)

    def __post_init__(self) -> None:
        expected_family = _PRESET_FAMILY.get(self.kind)
        if expected_family is None or self.family is not expected_family:
            raise ElectrocatalysisPresetError(
                "preset family does not match the declared mechanism kind"
            )
        object.__setattr__(
            self,
            "preset_hash",
            canonical_sha256(
                {
                    "family": self.family,
                    "kind": self.kind,
                    "definition_hash": self.definition.content_hash,
                }
            ),
        )

    @property
    def required_species_keys(self) -> tuple[str, ...]:
        """Return the exact scientific source registry required for evaluation."""

        return tuple(
            sorted(
                {
                    term.species_key
                    for step in self.definition.steps
                    for term in step.terms
                }
            )
        )


def compile_her_volmer_heyrovsky_preset(
    *,
    pathway_key: str,
    clean_surface_key: str,
    h_adsorbed_key: str,
    h2_reference_key: str,
    che_key: str,
) -> ElectrocatalysisPathwayPreset:
    """Compile acidic-CHE HER as explicit Volmer then Heyrovsky steps."""

    _require_text(pathway_key, "pathway_key")
    _require_distinct_role_keys(
        clean_surface_key=clean_surface_key,
        h_adsorbed_key=h_adsorbed_key,
        h2_reference_key=h2_reference_key,
        che_key=che_key,
    )
    steps = (
        ReactionStepDefinition(
            step_key="her_volmer",
            label="* + (H+ + e-) -> H*",
            initial_state_key=clean_surface_key,
            final_state_key=h_adsorbed_key,
            terms=(
                _term(clean_surface_key, -1.0),
                _term(che_key, -1.0),
                _term(h_adsorbed_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="her_heyrovsky",
            label="H* + (H+ + e-) -> * + H2",
            initial_state_key=h_adsorbed_key,
            final_state_key=clean_surface_key,
            terms=(
                _term(h_adsorbed_key, -1.0),
                _term(che_key, -1.0),
                _term(clean_surface_key, 1.0),
                _term(h2_reference_key, 1.0),
            ),
        ),
    )
    definition = ReactionPathwayDefinition(
        pathway_key=pathway_key,
        label="HER Volmer-Heyrovsky 2e pathway",
        state_keys=(clean_surface_key, h_adsorbed_key, clean_surface_key),
        steps=steps,
    )
    return ElectrocatalysisPathwayPreset(
        family=ElectrocatalysisReactionFamily.HER,
        kind=ElectrocatalysisPresetKind.HER_VOLMER_HEYROVSKY,
        definition=definition,
    )


def compile_orr_associative_4e_preset(
    *,
    pathway_key: str,
    clean_surface_key: str,
    ooh_adsorbed_key: str,
    o_adsorbed_key: str,
    oh_adsorbed_key: str,
    o2_reference_key: str,
    h2o_reference_key: str,
    che_key: str,
) -> ElectrocatalysisPathwayPreset:
    """Compile the explicit associative four-electron ORR pathway to water."""

    _require_text(pathway_key, "pathway_key")
    _require_distinct_role_keys(
        clean_surface_key=clean_surface_key,
        ooh_adsorbed_key=ooh_adsorbed_key,
        o_adsorbed_key=o_adsorbed_key,
        oh_adsorbed_key=oh_adsorbed_key,
        o2_reference_key=o2_reference_key,
        h2o_reference_key=h2o_reference_key,
        che_key=che_key,
    )
    steps = (
        ReactionStepDefinition(
            step_key="orr_ooh_formation",
            label="* + O2 + (H+ + e-) -> OOH*",
            initial_state_key=clean_surface_key,
            final_state_key=ooh_adsorbed_key,
            terms=(
                _term(clean_surface_key, -1.0),
                _term(o2_reference_key, -1.0),
                _term(che_key, -1.0),
                _term(ooh_adsorbed_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="orr_o_formation",
            label="OOH* + (H+ + e-) -> O* + H2O",
            initial_state_key=ooh_adsorbed_key,
            final_state_key=o_adsorbed_key,
            terms=(
                _term(ooh_adsorbed_key, -1.0),
                _term(che_key, -1.0),
                _term(o_adsorbed_key, 1.0),
                _term(h2o_reference_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="orr_oh_formation",
            label="O* + (H+ + e-) -> OH*",
            initial_state_key=o_adsorbed_key,
            final_state_key=oh_adsorbed_key,
            terms=(
                _term(o_adsorbed_key, -1.0),
                _term(che_key, -1.0),
                _term(oh_adsorbed_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="orr_water_release",
            label="OH* + (H+ + e-) -> * + H2O",
            initial_state_key=oh_adsorbed_key,
            final_state_key=clean_surface_key,
            terms=(
                _term(oh_adsorbed_key, -1.0),
                _term(che_key, -1.0),
                _term(clean_surface_key, 1.0),
                _term(h2o_reference_key, 1.0),
            ),
        ),
    )
    definition = ReactionPathwayDefinition(
        pathway_key=pathway_key,
        label="Associative ORR 4e pathway",
        state_keys=(
            clean_surface_key,
            ooh_adsorbed_key,
            o_adsorbed_key,
            oh_adsorbed_key,
            clean_surface_key,
        ),
        steps=steps,
    )
    return ElectrocatalysisPathwayPreset(
        family=ElectrocatalysisReactionFamily.ORR,
        kind=ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E,
        definition=definition,
    )


def compile_oer_associative_4e_preset(
    *,
    pathway_key: str,
    clean_surface_key: str,
    oh_adsorbed_key: str,
    o_adsorbed_key: str,
    ooh_adsorbed_key: str,
    h2o_reference_key: str,
    o2_reference_key: str,
    che_key: str,
) -> ElectrocatalysisPathwayPreset:
    """Compile OER explicitly in the oxidation direction; no ORR reversal is used."""

    _require_text(pathway_key, "pathway_key")
    _require_distinct_role_keys(
        clean_surface_key=clean_surface_key,
        oh_adsorbed_key=oh_adsorbed_key,
        o_adsorbed_key=o_adsorbed_key,
        ooh_adsorbed_key=ooh_adsorbed_key,
        h2o_reference_key=h2o_reference_key,
        o2_reference_key=o2_reference_key,
        che_key=che_key,
    )
    steps = (
        ReactionStepDefinition(
            step_key="oer_oh_formation",
            label="* + H2O -> OH* + (H+ + e-)",
            initial_state_key=clean_surface_key,
            final_state_key=oh_adsorbed_key,
            terms=(
                _term(clean_surface_key, -1.0),
                _term(h2o_reference_key, -1.0),
                _term(oh_adsorbed_key, 1.0),
                _term(che_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="oer_o_formation",
            label="OH* -> O* + (H+ + e-)",
            initial_state_key=oh_adsorbed_key,
            final_state_key=o_adsorbed_key,
            terms=(
                _term(oh_adsorbed_key, -1.0),
                _term(o_adsorbed_key, 1.0),
                _term(che_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="oer_ooh_formation",
            label="O* + H2O -> OOH* + (H+ + e-)",
            initial_state_key=o_adsorbed_key,
            final_state_key=ooh_adsorbed_key,
            terms=(
                _term(o_adsorbed_key, -1.0),
                _term(h2o_reference_key, -1.0),
                _term(ooh_adsorbed_key, 1.0),
                _term(che_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="oer_oxygen_release",
            label="OOH* -> * + O2 + (H+ + e-)",
            initial_state_key=ooh_adsorbed_key,
            final_state_key=clean_surface_key,
            terms=(
                _term(ooh_adsorbed_key, -1.0),
                _term(clean_surface_key, 1.0),
                _term(o2_reference_key, 1.0),
                _term(che_key, 1.0),
            ),
        ),
    )
    definition = ReactionPathwayDefinition(
        pathway_key=pathway_key,
        label="Associative OER 4e pathway",
        state_keys=(
            clean_surface_key,
            oh_adsorbed_key,
            o_adsorbed_key,
            ooh_adsorbed_key,
            clean_surface_key,
        ),
        steps=steps,
    )
    return ElectrocatalysisPathwayPreset(
        family=ElectrocatalysisReactionFamily.OER,
        kind=ElectrocatalysisPresetKind.OER_ASSOCIATIVE_4E,
        definition=definition,
    )


def compile_co2rr_to_co_2e_preset(
    *,
    pathway_key: str,
    clean_surface_key: str,
    cooh_adsorbed_key: str,
    co_adsorbed_key: str,
    co2_reference_key: str,
    h2o_reference_key: str,
    co_reference_key: str,
    che_key: str,
) -> ElectrocatalysisPathwayPreset:
    """Compile the two-electron CO2-to-CO pathway through COOH* and CO*."""

    _require_text(pathway_key, "pathway_key")
    _require_distinct_role_keys(
        clean_surface_key=clean_surface_key,
        cooh_adsorbed_key=cooh_adsorbed_key,
        co_adsorbed_key=co_adsorbed_key,
        co2_reference_key=co2_reference_key,
        h2o_reference_key=h2o_reference_key,
        co_reference_key=co_reference_key,
        che_key=che_key,
    )
    steps = (
        ReactionStepDefinition(
            step_key="co2rr_cooh_formation",
            label="* + CO2 + (H+ + e-) -> COOH*",
            initial_state_key=clean_surface_key,
            final_state_key=cooh_adsorbed_key,
            terms=(
                _term(clean_surface_key, -1.0),
                _term(co2_reference_key, -1.0),
                _term(che_key, -1.0),
                _term(cooh_adsorbed_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="co2rr_co_formation",
            label="COOH* + (H+ + e-) -> CO* + H2O",
            initial_state_key=cooh_adsorbed_key,
            final_state_key=co_adsorbed_key,
            terms=(
                _term(cooh_adsorbed_key, -1.0),
                _term(che_key, -1.0),
                _term(co_adsorbed_key, 1.0),
                _term(h2o_reference_key, 1.0),
            ),
        ),
        ReactionStepDefinition(
            step_key="co2rr_co_release",
            label="CO* -> * + CO",
            initial_state_key=co_adsorbed_key,
            final_state_key=clean_surface_key,
            terms=(
                _term(co_adsorbed_key, -1.0),
                _term(clean_surface_key, 1.0),
                _term(co_reference_key, 1.0),
            ),
        ),
    )
    definition = ReactionPathwayDefinition(
        pathway_key=pathway_key,
        label="CO2-to-CO 2e pathway",
        state_keys=(
            clean_surface_key,
            cooh_adsorbed_key,
            co_adsorbed_key,
            clean_surface_key,
        ),
        steps=steps,
    )
    return ElectrocatalysisPathwayPreset(
        family=ElectrocatalysisReactionFamily.CO2RR_TO_CO,
        kind=ElectrocatalysisPresetKind.CO2RR_TO_CO_2E,
        definition=definition,
    )
