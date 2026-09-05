"""Potential-dependent electrocatalysis views and descriptors for v0.8 Block 7.

All potential dependence is derived from explicit CHE stoichiometric coefficients already
present in Block 6 reaction results. Reaction names never determine a sign convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite, log

from ecatvasp.domain import canonical_sha256
from ecatvasp.thermo.che import CHEConditions, ElectrodePotentialReference
from ecatvasp.thermo.gas import GasReferenceSpecies
from ecatvasp.thermo.harmonic import BOLTZMANN_EV_PER_K
from ecatvasp.thermo.reaction import (
    AdsorptionReferenceTerm,
    MolecularReferenceReactionSource,
    ReactionEnergySourceKind,
    ReactionPathwayResult,
    ReactionStepResult,
    ThermochemistryReactionSource,
    evaluate_adsorption_free_energy,
)

LN10 = log(10.0)


class ElectrocatalysisDescriptorError(ValueError):
    """Raised when a descriptor cannot be derived without hidden thermodynamic assumptions."""


class PotentialConstraintKind(StrEnum):
    """Potential inequality imposed by one affine reaction step."""

    LOWER_BOUND = "lower_bound"
    UPPER_BOUND = "upper_bound"
    NONE = "none"


class LimitingPotentialSelection(StrEnum):
    """Which finite edge of the thermodynamically feasible potential interval is reported."""

    MAXIMUM_FEASIBLE = "maximum_feasible"
    MINIMUM_FEASIBLE = "minimum_feasible"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ElectrocatalysisDescriptorError(f"{field_name} must not be blank")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ElectrocatalysisDescriptorError(
            f"{field_name} must be a 64-character hexadecimal SHA-256 digest"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class PotentialStepView:
    """One pathway step transformed from baseline to a target CHE condition."""

    step_key: str
    che_coefficient: float
    potential_slope_ev_per_v: float
    baseline_delta_g_ev: float
    target_delta_g_ev: float

    def __post_init__(self) -> None:
        _require_text(self.step_key, "step_key")
        values = (
            self.che_coefficient,
            self.potential_slope_ev_per_v,
            self.baseline_delta_g_ev,
            self.target_delta_g_ev,
        )
        if not all(isfinite(value) for value in values):
            raise ElectrocatalysisDescriptorError("potential step-view values must be finite")
        if self.potential_slope_ev_per_v != -self.che_coefficient:
            raise ElectrocatalysisDescriptorError(
                "potential slope must equal minus the explicit net CHE coefficient"
            )


@dataclass(frozen=True, slots=True)
class PotentialDependentPathwayView:
    """Deterministic affine U/pH view of one canonical Block 6 pathway result."""

    pathway_hash: str
    baseline_result_hash: str
    baseline_conditions: CHEConditions
    target_conditions: CHEConditions
    delta_che_condition_ev: float
    state_keys: tuple[str, ...]
    step_views: tuple[PotentialStepView, ...]
    cumulative_state_free_energies_ev: tuple[float, ...]
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pathway_hash",
            _normalized_sha256(self.pathway_hash, "pathway_hash"),
        )
        object.__setattr__(
            self,
            "baseline_result_hash",
            _normalized_sha256(self.baseline_result_hash, "baseline_result_hash"),
        )
        if self.baseline_conditions.temperature_k != self.target_conditions.temperature_k:
            raise ElectrocatalysisDescriptorError(
                "potential-dependent view cannot change thermochemistry temperature"
            )
        if not isfinite(self.delta_che_condition_ev):
            raise ElectrocatalysisDescriptorError("delta_che_condition_ev must be finite")
        if len(self.state_keys) < 2:
            raise ElectrocatalysisDescriptorError("pathway view requires at least two states")
        if len(self.step_views) != len(self.state_keys) - 1:
            raise ElectrocatalysisDescriptorError(
                "pathway view requires exactly one step view between adjacent states"
            )
        if len(self.cumulative_state_free_energies_ev) != len(self.state_keys):
            raise ElectrocatalysisDescriptorError(
                "pathway view cumulative values must match ordered states"
            )
        if not all(isfinite(value) for value in self.cumulative_state_free_energies_ev):
            raise ElectrocatalysisDescriptorError(
                "pathway view cumulative free energies must be finite"
            )
        if self.cumulative_state_free_energies_ev[0] != 0.0:
            raise ElectrocatalysisDescriptorError(
                "pathway view cumulative free energy must start at zero"
            )
        cumulative = 0.0
        for index, step_view in enumerate(self.step_views):
            expected_target = (
                step_view.baseline_delta_g_ev
                + step_view.che_coefficient * self.delta_che_condition_ev
            )
            if step_view.target_delta_g_ev != expected_target:
                raise ElectrocatalysisDescriptorError(
                    "target step free energy does not match the CHE affine transformation"
                )
            cumulative += step_view.target_delta_g_ev
            if self.cumulative_state_free_energies_ev[index + 1] != cumulative:
                raise ElectrocatalysisDescriptorError(
                    "pathway view cumulative free energy differs from transformed step sum"
                )
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "pathway_hash": self.pathway_hash,
                    "baseline_result_hash": self.baseline_result_hash,
                    "baseline_conditions": self.baseline_conditions,
                    "target_conditions": self.target_conditions,
                    "delta_che_condition_ev": self.delta_che_condition_ev,
                    "state_keys": self.state_keys,
                    "step_views": self.step_views,
                    "cumulative_state_free_energies_ev": (
                        self.cumulative_state_free_energies_ev
                    ),
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class PotentialStepConstraint:
    """One affine step constraint for the condition Delta G(U) <= 0."""

    step_key: str
    che_coefficient: float
    potential_slope_ev_per_v: float
    baseline_delta_g_ev: float
    kind: PotentialConstraintKind
    zero_crossing_potential_v: float | None

    def __post_init__(self) -> None:
        _require_text(self.step_key, "step_key")
        values = (
            self.che_coefficient,
            self.potential_slope_ev_per_v,
            self.baseline_delta_g_ev,
        )
        if not all(isfinite(value) for value in values):
            raise ElectrocatalysisDescriptorError("potential constraint values must be finite")
        if self.potential_slope_ev_per_v != -self.che_coefficient:
            raise ElectrocatalysisDescriptorError(
                "constraint slope must equal minus the explicit net CHE coefficient"
            )
        if self.potential_slope_ev_per_v > 0.0:
            expected_kind = PotentialConstraintKind.UPPER_BOUND
        elif self.potential_slope_ev_per_v < 0.0:
            expected_kind = PotentialConstraintKind.LOWER_BOUND
        else:
            expected_kind = PotentialConstraintKind.NONE
        if self.kind is not expected_kind:
            raise ElectrocatalysisDescriptorError(
                "potential constraint kind does not match the affine step slope"
            )
        if self.kind is PotentialConstraintKind.NONE:
            if self.zero_crossing_potential_v is not None:
                raise ElectrocatalysisDescriptorError(
                    "potential-independent step must not carry a zero-crossing potential"
                )
        else:
            if self.zero_crossing_potential_v is None or not isfinite(
                self.zero_crossing_potential_v
            ):
                raise ElectrocatalysisDescriptorError(
                    "potential-dependent step requires a finite zero-crossing potential"
                )


@dataclass(frozen=True, slots=True)
class LimitingPotentialResult:
    """Finite thermodynamic limiting potential derived from all explicit step constraints."""

    pathway_hash: str
    baseline_result_hash: str
    conditions: CHEConditions
    net_che_coefficient: float
    selection: LimitingPotentialSelection
    constraints: tuple[PotentialStepConstraint, ...]
    feasible_lower_bound_v: float | None
    feasible_upper_bound_v: float | None
    limiting_potential_v: float
    determining_step_keys: tuple[str, ...]
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pathway_hash",
            _normalized_sha256(self.pathway_hash, "pathway_hash"),
        )
        object.__setattr__(
            self,
            "baseline_result_hash",
            _normalized_sha256(self.baseline_result_hash, "baseline_result_hash"),
        )
        if not isfinite(self.net_che_coefficient) or self.net_che_coefficient == 0.0:
            raise ElectrocatalysisDescriptorError(
                "limiting potential requires a finite non-zero net CHE coefficient"
            )
        expected_selection = (
            LimitingPotentialSelection.MAXIMUM_FEASIBLE
            if self.net_che_coefficient < 0.0
            else LimitingPotentialSelection.MINIMUM_FEASIBLE
        )
        if self.selection is not expected_selection:
            raise ElectrocatalysisDescriptorError(
                "limiting-potential selection conflicts with net CHE stoichiometry"
            )
        if not self.constraints:
            raise ElectrocatalysisDescriptorError(
                "limiting potential requires explicit step constraints"
            )
        if self.feasible_lower_bound_v is not None and not isfinite(
            self.feasible_lower_bound_v
        ):
            raise ElectrocatalysisDescriptorError("feasible lower bound must be finite")
        if self.feasible_upper_bound_v is not None and not isfinite(
            self.feasible_upper_bound_v
        ):
            raise ElectrocatalysisDescriptorError("feasible upper bound must be finite")
        if (
            self.feasible_lower_bound_v is not None
            and self.feasible_upper_bound_v is not None
            and self.feasible_lower_bound_v > self.feasible_upper_bound_v
        ):
            raise ElectrocatalysisDescriptorError("limiting-potential interval is infeasible")
        if not isfinite(self.limiting_potential_v):
            raise ElectrocatalysisDescriptorError("limiting_potential_v must be finite")
        expected_limit = (
            self.feasible_upper_bound_v
            if self.selection is LimitingPotentialSelection.MAXIMUM_FEASIBLE
            else self.feasible_lower_bound_v
        )
        if expected_limit is None or self.limiting_potential_v != expected_limit:
            raise ElectrocatalysisDescriptorError(
                "limiting potential does not equal the selected feasible interval edge"
            )
        if not self.determining_step_keys:
            raise ElectrocatalysisDescriptorError(
                "limiting potential requires at least one determining step"
            )
        for step_key in self.determining_step_keys:
            _require_text(step_key, "determining_step_key")
        if len(self.determining_step_keys) != len(set(self.determining_step_keys)):
            raise ElectrocatalysisDescriptorError(
                "determining_step_keys must be unique"
            )
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "pathway_hash": self.pathway_hash,
                    "baseline_result_hash": self.baseline_result_hash,
                    "conditions": self.conditions,
                    "net_che_coefficient": self.net_che_coefficient,
                    "selection": self.selection,
                    "constraints": self.constraints,
                    "feasible_lower_bound_v": self.feasible_lower_bound_v,
                    "feasible_upper_bound_v": self.feasible_upper_bound_v,
                    "limiting_potential_v": self.limiting_potential_v,
                    "determining_step_keys": self.determining_step_keys,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class ReversiblePotentialResult:
    """Pathway equilibrium potential derived from total free energy and net CHE slope."""

    pathway_hash: str
    baseline_result_hash: str
    conditions: CHEConditions
    net_che_coefficient: float
    total_baseline_delta_g_ev: float
    total_potential_slope_ev_per_v: float
    reversible_potential_v: float
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pathway_hash",
            _normalized_sha256(self.pathway_hash, "pathway_hash"),
        )
        object.__setattr__(
            self,
            "baseline_result_hash",
            _normalized_sha256(self.baseline_result_hash, "baseline_result_hash"),
        )
        values = (
            self.net_che_coefficient,
            self.total_baseline_delta_g_ev,
            self.total_potential_slope_ev_per_v,
            self.reversible_potential_v,
        )
        if not all(isfinite(value) for value in values):
            raise ElectrocatalysisDescriptorError(
                "reversible-potential components must be finite"
            )
        if self.net_che_coefficient == 0.0:
            raise ElectrocatalysisDescriptorError(
                "reversible potential requires non-zero net CHE stoichiometry"
            )
        if self.total_potential_slope_ev_per_v != -self.net_che_coefficient:
            raise ElectrocatalysisDescriptorError(
                "total potential slope must equal minus net CHE stoichiometry"
            )
        expected = self.conditions.potential_v - (
            self.total_baseline_delta_g_ev / self.total_potential_slope_ev_per_v
        )
        if self.reversible_potential_v != expected:
            raise ElectrocatalysisDescriptorError(
                "reversible potential does not solve the total pathway equilibrium condition"
            )
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "pathway_hash": self.pathway_hash,
                    "baseline_result_hash": self.baseline_result_hash,
                    "conditions": self.conditions,
                    "net_che_coefficient": self.net_che_coefficient,
                    "total_baseline_delta_g_ev": self.total_baseline_delta_g_ev,
                    "total_potential_slope_ev_per_v": (
                        self.total_potential_slope_ev_per_v
                    ),
                    "reversible_potential_v": self.reversible_potential_v,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class OEROverpotentialResult:
    """OER theoretical overpotential bound to limiting and reversible potential results."""

    limiting: LimitingPotentialResult
    reversible: ReversiblePotentialResult
    overpotential_v: float
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.limiting.pathway_hash != self.reversible.pathway_hash:
            raise ElectrocatalysisDescriptorError(
                "OER limiting and reversible potentials must use the same pathway"
            )
        if self.limiting.baseline_result_hash != self.reversible.baseline_result_hash:
            raise ElectrocatalysisDescriptorError(
                "OER limiting and reversible potentials must use the same baseline result"
            )
        if self.limiting.conditions != self.reversible.conditions:
            raise ElectrocatalysisDescriptorError(
                "OER limiting and reversible potentials must use the same conditions"
            )
        expected = (
            self.limiting.limiting_potential_v
            - self.reversible.reversible_potential_v
        )
        if not isfinite(self.overpotential_v) or self.overpotential_v != expected:
            raise ElectrocatalysisDescriptorError(
                "OER overpotential must equal limiting minus reversible potential"
            )
        if self.overpotential_v < 0.0:
            raise ElectrocatalysisDescriptorError(
                "OER theoretical overpotential must not be negative"
            )
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "limiting_result_hash": self.limiting.result_hash,
                    "reversible_result_hash": self.reversible.result_hash,
                    "overpotential_v": self.overpotential_v,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class HERDeltaGHStarResult:
    """HER Delta G_H* descriptor retaining its complete adsorption reaction result."""

    adsorption_result: ReactionStepResult
    h2_reference_hash: str
    delta_g_h_star_ev: float
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "h2_reference_hash",
            _normalized_sha256(self.h2_reference_hash, "h2_reference_hash"),
        )
        if not isfinite(self.delta_g_h_star_ev):
            raise ElectrocatalysisDescriptorError("delta_g_h_star_ev must be finite")
        if self.delta_g_h_star_ev != self.adsorption_result.delta_g_ev:
            raise ElectrocatalysisDescriptorError(
                "HER Delta G_H* must equal its explicit H adsorption free energy"
            )
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "adsorption_result_hash": self.adsorption_result.result_hash,
                    "h2_reference_hash": self.h2_reference_hash,
                    "delta_g_h_star_ev": self.delta_g_h_star_ev,
                }
            ),
        )


def evaluate_potential_dependent_pathway_view(
    *,
    baseline_pathway: ReactionPathwayResult,
    baseline_conditions: CHEConditions,
    target_conditions: CHEConditions,
) -> PotentialDependentPathwayView:
    """Transform one canonical pathway using only explicit CHE condition dependence."""

    _validate_baseline_conditions(
        baseline_pathway=baseline_pathway,
        baseline_conditions=baseline_conditions,
    )
    if target_conditions.temperature_k != baseline_conditions.temperature_k:
        raise ElectrocatalysisDescriptorError(
            "target CHE condition must retain the baseline thermochemistry temperature"
        )
    delta_che = _che_condition_term_ev(target_conditions) - _che_condition_term_ev(
        baseline_conditions
    )
    step_views = tuple(
        PotentialStepView(
            step_key=step.step_key,
            che_coefficient=_che_coefficient(step),
            potential_slope_ev_per_v=-_che_coefficient(step),
            baseline_delta_g_ev=step.delta_g_ev,
            target_delta_g_ev=(
                step.delta_g_ev + _che_coefficient(step) * delta_che
            ),
        )
        for step in baseline_pathway.step_results
    )
    cumulative_values = [0.0]
    cumulative = 0.0
    for step_view in step_views:
        cumulative += step_view.target_delta_g_ev
        cumulative_values.append(cumulative)
    return PotentialDependentPathwayView(
        pathway_hash=baseline_pathway.pathway_hash,
        baseline_result_hash=baseline_pathway.result_hash,
        baseline_conditions=baseline_conditions,
        target_conditions=target_conditions,
        delta_che_condition_ev=delta_che,
        state_keys=baseline_pathway.state_keys,
        step_views=step_views,
        cumulative_state_free_energies_ev=tuple(cumulative_values),
    )


def solve_limiting_potential(
    *,
    baseline_pathway: ReactionPathwayResult,
    baseline_conditions: CHEConditions,
) -> LimitingPotentialResult:
    """Solve all explicit affine step constraints and report the stoichiometric limit."""

    _validate_baseline_conditions(
        baseline_pathway=baseline_pathway,
        baseline_conditions=baseline_conditions,
    )
    constraints: list[PotentialStepConstraint] = []
    lower_candidates: list[tuple[float, str]] = []
    upper_candidates: list[tuple[float, str]] = []
    net_che_coefficient = 0.0
    for step in baseline_pathway.step_results:
        che_coefficient = _che_coefficient(step)
        net_che_coefficient += che_coefficient
        slope = -che_coefficient
        if slope > 0.0:
            zero_crossing = baseline_conditions.potential_v - step.delta_g_ev / slope
            kind = PotentialConstraintKind.UPPER_BOUND
            upper_candidates.append((zero_crossing, step.step_key))
        elif slope < 0.0:
            zero_crossing = baseline_conditions.potential_v - step.delta_g_ev / slope
            kind = PotentialConstraintKind.LOWER_BOUND
            lower_candidates.append((zero_crossing, step.step_key))
        else:
            zero_crossing = None
            kind = PotentialConstraintKind.NONE
            if step.delta_g_ev > 0.0:
                raise ElectrocatalysisDescriptorError(
                    f"potential-independent uphill step cannot be made downhill: {step.step_key}"
                )
        constraints.append(
            PotentialStepConstraint(
                step_key=step.step_key,
                che_coefficient=che_coefficient,
                potential_slope_ev_per_v=slope,
                baseline_delta_g_ev=step.delta_g_ev,
                kind=kind,
                zero_crossing_potential_v=zero_crossing,
            )
        )
    if net_che_coefficient == 0.0:
        raise ElectrocatalysisDescriptorError(
            "limiting potential requires non-zero net CHE stoichiometry"
        )
    lower_bound = max((value for value, _ in lower_candidates), default=None)
    upper_bound = min((value for value, _ in upper_candidates), default=None)
    if lower_bound is not None and upper_bound is not None and lower_bound > upper_bound:
        raise ElectrocatalysisDescriptorError(
            "no electrode potential makes every declared pathway step downhill"
        )
    if net_che_coefficient < 0.0:
        selection = LimitingPotentialSelection.MAXIMUM_FEASIBLE
        if upper_bound is None:
            raise ElectrocatalysisDescriptorError(
                "reduction-like pathway has no finite maximum feasible potential"
            )
        limiting_potential = upper_bound
        determining = tuple(
            sorted(key for value, key in upper_candidates if value == upper_bound)
        )
    else:
        selection = LimitingPotentialSelection.MINIMUM_FEASIBLE
        if lower_bound is None:
            raise ElectrocatalysisDescriptorError(
                "oxidation-like pathway has no finite minimum feasible potential"
            )
        limiting_potential = lower_bound
        determining = tuple(
            sorted(key for value, key in lower_candidates if value == lower_bound)
        )
    return LimitingPotentialResult(
        pathway_hash=baseline_pathway.pathway_hash,
        baseline_result_hash=baseline_pathway.result_hash,
        conditions=baseline_conditions,
        net_che_coefficient=net_che_coefficient,
        selection=selection,
        constraints=tuple(constraints),
        feasible_lower_bound_v=lower_bound,
        feasible_upper_bound_v=upper_bound,
        limiting_potential_v=limiting_potential,
        determining_step_keys=determining,
    )


def derive_reversible_potential(
    *,
    baseline_pathway: ReactionPathwayResult,
    baseline_conditions: CHEConditions,
) -> ReversiblePotentialResult:
    """Derive the pathway equilibrium potential from the same explicit CHE reference state."""

    _validate_baseline_conditions(
        baseline_pathway=baseline_pathway,
        baseline_conditions=baseline_conditions,
    )
    net_che_coefficient = sum(
        _che_coefficient(step) for step in baseline_pathway.step_results
    )
    if net_che_coefficient == 0.0:
        raise ElectrocatalysisDescriptorError(
            "reversible potential requires non-zero net CHE stoichiometry"
        )
    total_delta_g = sum(step.delta_g_ev for step in baseline_pathway.step_results)
    total_slope = -net_che_coefficient
    reversible_potential = baseline_conditions.potential_v - total_delta_g / total_slope
    return ReversiblePotentialResult(
        pathway_hash=baseline_pathway.pathway_hash,
        baseline_result_hash=baseline_pathway.result_hash,
        conditions=baseline_conditions,
        net_che_coefficient=net_che_coefficient,
        total_baseline_delta_g_ev=total_delta_g,
        total_potential_slope_ev_per_v=total_slope,
        reversible_potential_v=reversible_potential,
    )


def evaluate_oer_theoretical_overpotential(
    *,
    baseline_pathway: ReactionPathwayResult,
    baseline_conditions: CHEConditions,
) -> OEROverpotentialResult:
    """Evaluate OER eta only for an explicitly oxidation-like CHE pathway."""

    limiting = solve_limiting_potential(
        baseline_pathway=baseline_pathway,
        baseline_conditions=baseline_conditions,
    )
    if limiting.net_che_coefficient <= 0.0:
        raise ElectrocatalysisDescriptorError(
            "OER overpotential requires net production of CHE proton-electron pairs"
        )
    if any(
        constraint.potential_slope_ev_per_v > 0.0
        for constraint in limiting.constraints
    ):
        raise ElectrocatalysisDescriptorError(
            "OER overpotential requires every CHE-dependent step to be oxidation-like"
        )
    reversible = derive_reversible_potential(
        baseline_pathway=baseline_pathway,
        baseline_conditions=baseline_conditions,
    )
    overpotential = limiting.limiting_potential_v - reversible.reversible_potential_v
    return OEROverpotentialResult(
        limiting=limiting,
        reversible=reversible,
        overpotential_v=overpotential,
    )


def evaluate_her_delta_g_h_star(
    *,
    step_key: str,
    label: str,
    initial_state_key: str,
    final_state_key: str,
    hydrogen_adsorbed_source: ThermochemistryReactionSource,
    clean_surface_source: ThermochemistryReactionSource,
    h2_reference: MolecularReferenceReactionSource,
) -> HERDeltaGHStarResult:
    """Evaluate HER Delta G_H* as explicit H adsorption from one-half H2."""

    if h2_reference.raw.reference.species is not GasReferenceSpecies.H2:
        raise ElectrocatalysisDescriptorError(
            "HER Delta G_H* requires an explicitly species-bound H2 reference"
        )
    adsorption_result = evaluate_adsorption_free_energy(
        step_key=step_key,
        label=label,
        initial_state_key=initial_state_key,
        final_state_key=final_state_key,
        adsorbed_source=hydrogen_adsorbed_source,
        clean_surface_source=clean_surface_source,
        reference_terms=(AdsorptionReferenceTerm(h2_reference, -0.5),),
    )
    return HERDeltaGHStarResult(
        adsorption_result=adsorption_result,
        h2_reference_hash=h2_reference.source_hash,
        delta_g_h_star_ev=adsorption_result.delta_g_ev,
    )


def _validate_baseline_conditions(
    *,
    baseline_pathway: ReactionPathwayResult,
    baseline_conditions: CHEConditions,
) -> None:
    if not baseline_pathway.step_results:
        raise ElectrocatalysisDescriptorError("electrocatalytic pathway has no steps")
    temperatures = {step.temperature_k for step in baseline_pathway.step_results}
    if temperatures != {baseline_conditions.temperature_k}:
        raise ElectrocatalysisDescriptorError(
            "baseline CHE temperature differs from pathway thermochemistry"
        )
    expected_hash = baseline_conditions.parameters_hash
    che_found = False
    for step in baseline_pathway.step_results:
        has_che = any(
            item.source_kind is ReactionEnergySourceKind.CHE_RESERVOIR
            for item in step.contributions
        )
        if has_che:
            che_found = True
            if step.electrochemical_condition_hash != expected_hash:
                raise ElectrocatalysisDescriptorError(
                    "baseline CHE conditions do not match the pathway result"
                )
        elif step.electrochemical_condition_hash is not None:
            raise ElectrocatalysisDescriptorError(
                "non-CHE step unexpectedly carries an electrochemical condition hash"
            )
    if not che_found:
        raise ElectrocatalysisDescriptorError(
            "potential-dependent descriptor requires at least one explicit CHE contribution"
        )


def _che_coefficient(step: ReactionStepResult) -> float:
    return sum(
        item.coefficient
        for item in step.contributions
        if item.source_kind is ReactionEnergySourceKind.CHE_RESERVOIR
    )


def _che_condition_term_ev(conditions: CHEConditions) -> float:
    potential_term = -conditions.potential_v
    if conditions.potential_reference is ElectrodePotentialReference.SHE:
        return (
            potential_term
            - BOLTZMANN_EV_PER_K * conditions.temperature_k * LN10 * conditions.ph
        )
    if conditions.potential_reference is ElectrodePotentialReference.RHE:
        return potential_term
    raise ElectrocatalysisDescriptorError("unsupported electrode potential reference")
