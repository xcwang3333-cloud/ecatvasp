"""Generic signed reaction/pathway free-energy evaluation for v0.8 Block 6.

Reaction free energy is always evaluated as ``Delta G = sum(nu_i G_i)`` with
products positive and reactants negative. Scientific sources are explicit thermochemistry,
molecular-reference, or CHE objects; filenames and reaction names carry no thermodynamic meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from ecatvasp.domain import canonical_sha256
from ecatvasp.thermo.che import CHEProtonElectronChemicalPotential
from ecatvasp.thermo.contracts import ThermochemistryResult, ThermochemistrySubjectKind
from ecatvasp.thermo.reference_binding import BoundGasReferenceThermochemistry
from ecatvasp.thermo.references import ReferenceThermochemistryResult


class ReactionFreeEnergyError(ValueError):
    """Raised when reaction free energy cannot be evaluated without ambiguity."""


class ReactionEnergySourceKind(StrEnum):
    """Scientific source family used by one explicit stoichiometric participant."""

    THERMOCHEMISTRY = "thermochemistry"
    MOLECULAR_REFERENCE = "molecular_reference"
    CHE_RESERVOIR = "che_reservoir"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ReactionFreeEnergyError(f"{field_name} must not be blank")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ReactionFreeEnergyError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class ThermochemistryReactionSource:
    """Surface/adsorbate thermochemistry bound to an explicit reaction species key."""

    species_key: str
    result: ThermochemistryResult

    def __post_init__(self) -> None:
        _require_text(self.species_key, "species_key")
        if self.result.identity.subject_kind is ThermochemistrySubjectKind.GAS:
            raise ReactionFreeEnergyError(
                "gas reaction species require explicit MolecularReferenceReactionSource binding"
            )

    @property
    def kind(self) -> ReactionEnergySourceKind:
        return ReactionEnergySourceKind.THERMOCHEMISTRY

    @property
    def gibbs_free_energy_ev(self) -> float:
        return self.result.gibbs_free_energy_ev

    @property
    def source_hash(self) -> str:
        return self.result.result_hash

    @property
    def temperature_k(self) -> float:
        return self.result.identity.conditions.temperature_k

    @property
    def electrochemical_condition_hash(self) -> str | None:
        return None


@dataclass(frozen=True, slots=True)
class MolecularReferenceReactionSource:
    """Explicit molecular reference bound to its exact raw and optional corrected lineage."""

    species_key: str
    raw: BoundGasReferenceThermochemistry
    corrected: ReferenceThermochemistryResult | None = None

    def __post_init__(self) -> None:
        _require_text(self.species_key, "species_key")
        corrected = self.corrected
        if corrected is None:
            return
        if corrected.adjustment.reference != self.raw.reference:
            raise ReactionFreeEnergyError(
                "corrected molecular reaction reference differs from the bound raw reference"
            )
        if corrected.source_result_hash != self.raw.result.result_hash:
            raise ReactionFreeEnergyError(
                "corrected molecular reaction reference does not derive from the exact raw result"
            )
        if corrected.source_gibbs_free_energy_ev != self.raw.result.gibbs_free_energy_ev:
            raise ReactionFreeEnergyError(
                "corrected molecular reaction source Gibbs energy differs from the raw result"
            )

    @property
    def kind(self) -> ReactionEnergySourceKind:
        return ReactionEnergySourceKind.MOLECULAR_REFERENCE

    @property
    def gibbs_free_energy_ev(self) -> float:
        if self.corrected is not None:
            return self.corrected.corrected_gibbs_free_energy_ev
        return self.raw.result.gibbs_free_energy_ev

    @property
    def source_hash(self) -> str:
        return canonical_sha256(
            {
                "bound_raw_reference_hash": self.raw.content_hash,
                "raw_result_hash": self.raw.result.result_hash,
                "corrected_result_hash": (
                    None if self.corrected is None else self.corrected.result_hash
                ),
            }
        )

    @property
    def temperature_k(self) -> float:
        return self.raw.result.identity.conditions.temperature_k

    @property
    def electrochemical_condition_hash(self) -> str | None:
        return None


@dataclass(frozen=True, slots=True)
class CHEReactionSource:
    """One explicit H+ + e- CHE reservoir event bound to a reaction species key."""

    species_key: str
    result: CHEProtonElectronChemicalPotential

    def __post_init__(self) -> None:
        _require_text(self.species_key, "species_key")

    @property
    def kind(self) -> ReactionEnergySourceKind:
        return ReactionEnergySourceKind.CHE_RESERVOIR

    @property
    def gibbs_free_energy_ev(self) -> float:
        return self.result.chemical_potential_ev

    @property
    def source_hash(self) -> str:
        return self.result.result_hash

    @property
    def temperature_k(self) -> float:
        return self.result.conditions.temperature_k

    @property
    def electrochemical_condition_hash(self) -> str | None:
        return self.result.conditions.parameters_hash


ReactionEnergySource = (
    ThermochemistryReactionSource
    | MolecularReferenceReactionSource
    | CHEReactionSource
)


@dataclass(frozen=True, slots=True)
class StoichiometricTerm:
    """One signed coefficient; products are positive and reactants are negative."""

    species_key: str
    coefficient: float

    def __post_init__(self) -> None:
        _require_text(self.species_key, "species_key")
        if not isfinite(self.coefficient) or self.coefficient == 0.0:
            raise ReactionFreeEnergyError(
                "stoichiometric coefficient must be finite and non-zero"
            )


@dataclass(frozen=True, slots=True)
class ReactionStepDefinition:
    """One explicit directed reaction step with complete signed stoichiometry."""

    step_key: str
    label: str
    initial_state_key: str
    final_state_key: str
    terms: tuple[StoichiometricTerm, ...]

    def __post_init__(self) -> None:
        _require_text(self.step_key, "step_key")
        _require_text(self.label, "label")
        _require_text(self.initial_state_key, "initial_state_key")
        _require_text(self.final_state_key, "final_state_key")
        if self.initial_state_key == self.final_state_key:
            raise ReactionFreeEnergyError("reaction step must connect two distinct states")
        if not self.terms:
            raise ReactionFreeEnergyError("reaction step requires explicit stoichiometric terms")
        ordered = tuple(sorted(self.terms, key=lambda item: item.species_key))
        keys = tuple(item.species_key for item in ordered)
        if len(keys) != len(set(keys)):
            raise ReactionFreeEnergyError(
                "reaction step requires one aggregated coefficient per species_key"
            )
        if not any(item.coefficient > 0.0 for item in ordered):
            raise ReactionFreeEnergyError("reaction step requires at least one product term")
        if not any(item.coefficient < 0.0 for item in ordered):
            raise ReactionFreeEnergyError("reaction step requires at least one reactant term")
        object.__setattr__(self, "terms", ordered)

    @property
    def content_hash(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ReactionPathwayDefinition:
    """Ordered directed pathway; reverse chemistry requires another explicit definition."""

    pathway_key: str
    label: str
    state_keys: tuple[str, ...]
    steps: tuple[ReactionStepDefinition, ...]

    def __post_init__(self) -> None:
        _require_text(self.pathway_key, "pathway_key")
        _require_text(self.label, "label")
        if len(self.state_keys) < 2:
            raise ReactionFreeEnergyError("reaction pathway requires at least two states")
        for state_key in self.state_keys:
            _require_text(state_key, "state_key")
        if len(self.state_keys) != len(set(self.state_keys)):
            raise ReactionFreeEnergyError("reaction pathway state_keys must be unique")
        if len(self.steps) != len(self.state_keys) - 1:
            raise ReactionFreeEnergyError(
                "reaction pathway requires exactly one directed step between adjacent states"
            )
        step_keys = tuple(step.step_key for step in self.steps)
        if len(step_keys) != len(set(step_keys)):
            raise ReactionFreeEnergyError("reaction pathway step_keys must be unique")
        for index, step in enumerate(self.steps):
            if step.initial_state_key != self.state_keys[index]:
                raise ReactionFreeEnergyError(
                    "reaction pathway step initial_state_key does not match ordered states"
                )
            if step.final_state_key != self.state_keys[index + 1]:
                raise ReactionFreeEnergyError(
                    "reaction pathway step final_state_key does not match ordered states"
                )

    @property
    def content_hash(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ReactionTermContribution:
    """Auditable contribution from one signed stoichiometric participant."""

    species_key: str
    coefficient: float
    source_kind: ReactionEnergySourceKind
    source_hash: str
    gibbs_free_energy_ev: float
    contribution_ev: float

    def __post_init__(self) -> None:
        _require_text(self.species_key, "species_key")
        object.__setattr__(
            self,
            "source_hash",
            _normalized_sha256(self.source_hash, "source_hash"),
        )
        values = (
            self.coefficient,
            self.gibbs_free_energy_ev,
            self.contribution_ev,
        )
        if not all(isfinite(value) for value in values):
            raise ReactionFreeEnergyError("reaction contribution values must be finite")
        if self.coefficient == 0.0:
            raise ReactionFreeEnergyError("reaction contribution coefficient must be non-zero")
        if self.contribution_ev != self.coefficient * self.gibbs_free_energy_ev:
            raise ReactionFreeEnergyError(
                "reaction contribution does not equal coefficient times Gibbs energy"
            )


@dataclass(frozen=True, slots=True)
class ReactionStepResult:
    """Component-resolved free energy for one explicit reaction step."""

    definition_hash: str
    step_key: str
    initial_state_key: str
    final_state_key: str
    temperature_k: float
    electrochemical_condition_hash: str | None
    contributions: tuple[ReactionTermContribution, ...]
    delta_g_ev: float
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "definition_hash",
            _normalized_sha256(self.definition_hash, "definition_hash"),
        )
        if self.electrochemical_condition_hash is not None:
            object.__setattr__(
                self,
                "electrochemical_condition_hash",
                _normalized_sha256(
                    self.electrochemical_condition_hash,
                    "electrochemical_condition_hash",
                ),
            )
        _require_text(self.step_key, "step_key")
        _require_text(self.initial_state_key, "initial_state_key")
        _require_text(self.final_state_key, "final_state_key")
        if not isfinite(self.temperature_k) or self.temperature_k <= 0.0:
            raise ReactionFreeEnergyError("reaction temperature_k must be finite and positive")
        if not self.contributions:
            raise ReactionFreeEnergyError("reaction result requires explicit contributions")
        keys = tuple(item.species_key for item in self.contributions)
        if len(keys) != len(set(keys)):
            raise ReactionFreeEnergyError(
                "reaction result contribution species_keys must be unique"
            )
        expected = sum(item.contribution_ev for item in self.contributions)
        if not isfinite(self.delta_g_ev) or self.delta_g_ev != expected:
            raise ReactionFreeEnergyError(
                "reaction delta_g_ev does not equal the explicit contribution sum"
            )
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "definition_hash": self.definition_hash,
                    "step_key": self.step_key,
                    "initial_state_key": self.initial_state_key,
                    "final_state_key": self.final_state_key,
                    "temperature_k": self.temperature_k,
                    "electrochemical_condition_hash": self.electrochemical_condition_hash,
                    "contributions": self.contributions,
                    "delta_g_ev": self.delta_g_ev,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class ReactionPathwayResult:
    """Pure ordered pathway result; durable diagram materialization remains Block 8."""

    pathway_hash: str
    state_keys: tuple[str, ...]
    step_results: tuple[ReactionStepResult, ...]
    cumulative_state_free_energies_ev: tuple[float, ...]
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pathway_hash",
            _normalized_sha256(self.pathway_hash, "pathway_hash"),
        )
        if len(self.state_keys) < 2:
            raise ReactionFreeEnergyError("pathway result requires at least two states")
        if len(self.step_results) != len(self.state_keys) - 1:
            raise ReactionFreeEnergyError("pathway result step count does not match states")
        if len(self.cumulative_state_free_energies_ev) != len(self.state_keys):
            raise ReactionFreeEnergyError(
                "pathway cumulative free energies must match the ordered state count"
            )
        if not all(isfinite(value) for value in self.cumulative_state_free_energies_ev):
            raise ReactionFreeEnergyError("pathway cumulative free energies must be finite")
        if self.cumulative_state_free_energies_ev[0] != 0.0:
            raise ReactionFreeEnergyError("pathway cumulative free energy must start at zero")
        cumulative = 0.0
        for index, step_result in enumerate(self.step_results):
            if step_result.initial_state_key != self.state_keys[index]:
                raise ReactionFreeEnergyError(
                    "pathway result step initial state does not match ordered state keys"
                )
            if step_result.final_state_key != self.state_keys[index + 1]:
                raise ReactionFreeEnergyError(
                    "pathway result step final state does not match ordered state keys"
                )
            cumulative += step_result.delta_g_ev
            if self.cumulative_state_free_energies_ev[index + 1] != cumulative:
                raise ReactionFreeEnergyError(
                    "pathway cumulative free energy differs from the ordered step sum"
                )
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "pathway_hash": self.pathway_hash,
                    "state_keys": self.state_keys,
                    "step_result_hashes": tuple(
                        item.result_hash for item in self.step_results
                    ),
                    "cumulative_state_free_energies_ev": (
                        self.cumulative_state_free_energies_ev
                    ),
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class AdsorptionReferenceTerm:
    """One explicit reactant-side reference consumed by an adsorption step."""

    source: ReactionEnergySource
    coefficient: float

    def __post_init__(self) -> None:
        if not isfinite(self.coefficient) or self.coefficient >= 0.0:
            raise ReactionFreeEnergyError(
                "adsorption reference coefficient must be finite and negative"
            )


def evaluate_reaction_step(
    *,
    definition: ReactionStepDefinition,
    sources: tuple[ReactionEnergySource, ...],
) -> ReactionStepResult:
    """Evaluate one step using exactly the source keys declared by its stoichiometry."""

    source_map = _validated_source_map(
        sources=sources,
        expected_keys={term.species_key for term in definition.terms},
    )
    return _evaluate_step_from_map(definition=definition, source_map=source_map)


def evaluate_reaction_pathway(
    *,
    definition: ReactionPathwayDefinition,
    sources: tuple[ReactionEnergySource, ...],
) -> ReactionPathwayResult:
    """Evaluate an ordered directed pathway from one exact scientific source registry."""

    expected_keys = {
        term.species_key
        for step in definition.steps
        for term in step.terms
    }
    source_map = _validated_source_map(sources=sources, expected_keys=expected_keys)
    _validated_common_conditions(tuple(source_map.values()))
    step_results = tuple(
        _evaluate_step_from_map(
            definition=step,
            source_map={
                term.species_key: source_map[term.species_key]
                for term in step.terms
            },
        )
        for step in definition.steps
    )
    cumulative_values = [0.0]
    cumulative = 0.0
    for result in step_results:
        cumulative += result.delta_g_ev
        cumulative_values.append(cumulative)
    return ReactionPathwayResult(
        pathway_hash=definition.content_hash,
        state_keys=definition.state_keys,
        step_results=step_results,
        cumulative_state_free_energies_ev=tuple(cumulative_values),
    )


def evaluate_adsorption_free_energy(
    *,
    step_key: str,
    label: str,
    initial_state_key: str,
    final_state_key: str,
    adsorbed_source: ThermochemistryReactionSource,
    clean_surface_source: ThermochemistryReactionSource,
    reference_terms: tuple[AdsorptionReferenceTerm, ...],
) -> ReactionStepResult:
    """Evaluate adsorption as one ordinary signed stoichiometric reaction step."""

    if not reference_terms:
        raise ReactionFreeEnergyError(
            "adsorption free energy requires at least one explicit reference term"
        )
    terms = [
        StoichiometricTerm(
            species_key=adsorbed_source.species_key,
            coefficient=1.0,
        ),
        StoichiometricTerm(
            species_key=clean_surface_source.species_key,
            coefficient=-1.0,
        ),
    ]
    sources: list[ReactionEnergySource] = [adsorbed_source, clean_surface_source]
    for reference_term in reference_terms:
        terms.append(
            StoichiometricTerm(
                species_key=reference_term.source.species_key,
                coefficient=reference_term.coefficient,
            )
        )
        sources.append(reference_term.source)
    definition = ReactionStepDefinition(
        step_key=step_key,
        label=label,
        initial_state_key=initial_state_key,
        final_state_key=final_state_key,
        terms=tuple(terms),
    )
    return evaluate_reaction_step(definition=definition, sources=tuple(sources))


def _evaluate_step_from_map(
    *,
    definition: ReactionStepDefinition,
    source_map: dict[str, ReactionEnergySource],
) -> ReactionStepResult:
    temperature_k, condition_hash = _validated_common_conditions(
        tuple(source_map.values())
    )
    contributions = tuple(
        ReactionTermContribution(
            species_key=term.species_key,
            coefficient=term.coefficient,
            source_kind=source_map[term.species_key].kind,
            source_hash=source_map[term.species_key].source_hash,
            gibbs_free_energy_ev=(
                source_map[term.species_key].gibbs_free_energy_ev
            ),
            contribution_ev=(
                term.coefficient * source_map[term.species_key].gibbs_free_energy_ev
            ),
        )
        for term in definition.terms
    )
    delta_g_ev = sum(item.contribution_ev for item in contributions)
    return ReactionStepResult(
        definition_hash=definition.content_hash,
        step_key=definition.step_key,
        initial_state_key=definition.initial_state_key,
        final_state_key=definition.final_state_key,
        temperature_k=temperature_k,
        electrochemical_condition_hash=condition_hash,
        contributions=contributions,
        delta_g_ev=delta_g_ev,
    )


def _validated_source_map(
    *,
    sources: tuple[ReactionEnergySource, ...],
    expected_keys: set[str],
) -> dict[str, ReactionEnergySource]:
    if not sources:
        raise ReactionFreeEnergyError("reaction evaluation requires explicit scientific sources")
    source_map: dict[str, ReactionEnergySource] = {}
    for source in sources:
        if source.species_key in source_map:
            raise ReactionFreeEnergyError("reaction source species_keys must be unique")
        source_map[source.species_key] = source
    actual_keys = set(source_map)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ReactionFreeEnergyError(
            f"reaction sources must exactly match stoichiometry; missing={missing}, extra={extra}"
        )
    return source_map


def _validated_common_conditions(
    sources: tuple[ReactionEnergySource, ...],
) -> tuple[float, str | None]:
    temperatures = {source.temperature_k for source in sources}
    if len(temperatures) != 1:
        raise ReactionFreeEnergyError(
            "all reaction free-energy sources must use the exact same temperature"
        )
    temperature_k = next(iter(temperatures))
    if not isfinite(temperature_k) or temperature_k <= 0.0:
        raise ReactionFreeEnergyError("reaction source temperature must be finite and positive")
    condition_hashes = {
        source.electrochemical_condition_hash
        for source in sources
        if source.electrochemical_condition_hash is not None
    }
    if len(condition_hashes) > 1:
        raise ReactionFreeEnergyError(
            "all CHE reservoir sources in one reaction evaluation must share exact conditions"
        )
    condition_hash = next(iter(condition_hashes)) if condition_hashes else None
    return temperature_k, condition_hash