"""Computational hydrogen electrode conditions for v0.8 Block 5.

This module evaluates the chemical potential of one proton-electron pair from an
explicit H2 thermochemistry reference. It keeps SHE/RHE potential conventions and pH
semantics parameter-complete and does not evaluate reaction/pathway stoichiometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite, log

from ecatvasp.domain import canonical_sha256
from ecatvasp.thermo.gas import GasReferenceSpecies
from ecatvasp.thermo.harmonic import BOLTZMANN_EV_PER_K
from ecatvasp.thermo.reference_binding import BoundGasReferenceThermochemistry
from ecatvasp.thermo.references import (
    ReferencePhase,
    ReferenceThermochemistryResult,
)

LN10 = log(10.0)


class CHEError(ValueError):
    """Raised when a CHE condition/reference would require ambiguous semantics."""


class ElectrodePotentialReference(StrEnum):
    """Reference electrode convention used for the supplied potential."""

    SHE = "she"
    RHE = "rhe"


class CHEPhSemantics(StrEnum):
    """Whether proton activity is explicit or already absorbed by the RHE potential."""

    EXPLICIT_ACTIVITY = "explicit_activity"
    INCLUDED_IN_RHE = "included_in_rhe"


@dataclass(frozen=True, slots=True)
class CHEConditions:
    """Parameter-complete CHE electrochemical conditions.

    SHE requires an explicit proton-activity pH term. RHE requires that the same pH
    dependence is already included in the electrode-potential convention. A mismatched
    pair is rejected so that an RHE condition cannot receive a second pH correction.
    """

    temperature_k: float
    potential_v: float
    ph: float
    potential_reference: ElectrodePotentialReference
    ph_semantics: CHEPhSemantics

    def __post_init__(self) -> None:
        if not isfinite(self.temperature_k) or self.temperature_k <= 0.0:
            raise CHEError("CHE temperature_k must be finite and positive")
        if not isfinite(self.potential_v):
            raise CHEError("CHE potential_v must be finite")
        if not isfinite(self.ph):
            raise CHEError("CHE pH must be finite")
        if self.potential_reference is ElectrodePotentialReference.SHE:
            if self.ph_semantics is not CHEPhSemantics.EXPLICIT_ACTIVITY:
                raise CHEError(
                    "SHE potential requires explicit proton-activity pH semantics"
                )
        elif self.potential_reference is ElectrodePotentialReference.RHE:
            if self.ph_semantics is not CHEPhSemantics.INCLUDED_IN_RHE:
                raise CHEError(
                    "RHE potential already includes pH and must not receive a second pH correction"
                )
        else:
            raise CHEError("unsupported electrode potential reference")

    @property
    def parameters_hash(self) -> str:
        """Return deterministic condition identity for downstream scientific hashes."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class CHEHydrogenReference:
    """Exact H2 Gibbs reference consumed by the CHE reservoir.

    `raw` preserves the species-bound Block 3 thermochemistry source. `corrected`, when
    supplied, must be a Block 4 reference derived from that exact raw H2 result.
    """

    raw: BoundGasReferenceThermochemistry
    corrected: ReferenceThermochemistryResult | None = None

    def __post_init__(self) -> None:
        if self.raw.reference.species is not GasReferenceSpecies.H2:
            raise CHEError("CHE hydrogen reference requires explicit H2 thermochemistry")
        corrected = self.corrected
        if corrected is None:
            return
        if corrected.adjustment.reference != self.raw.reference:
            raise CHEError(
                "corrected CHE H2 reference species/state differs from the bound raw H2 reference"
            )
        if corrected.adjustment.target_phase is not ReferencePhase.IDEAL_GAS:
            raise CHEError("CHE H2 reference must retain the ideal-gas molecular reference phase")
        if corrected.source_result_hash != self.raw.result.result_hash:
            raise CHEError("corrected CHE H2 reference does not derive from the exact raw H2 result")
        if corrected.source_gibbs_free_energy_ev != self.raw.result.gibbs_free_energy_ev:
            raise CHEError("corrected CHE H2 source Gibbs energy differs from the raw H2 result")

    @property
    def temperature_k(self) -> float:
        """Return the temperature of the exact raw H2 thermochemistry source."""

        return self.raw.result.identity.conditions.temperature_k

    @property
    def gibbs_free_energy_ev(self) -> float:
        """Return corrected H2 Gibbs energy when present, otherwise the raw gas value."""

        if self.corrected is not None:
            return self.corrected.corrected_gibbs_free_energy_ev
        return self.raw.result.gibbs_free_energy_ev

    @property
    def source_hash(self) -> str:
        """Return a hash that changes with raw H2 identity or its explicit correction layer."""

        return canonical_sha256(
            {
                "bound_raw_reference_hash": self.raw.content_hash,
                "raw_result_hash": self.raw.result.result_hash,
                "corrected_result_hash": (
                    None if self.corrected is None else self.corrected.result_hash
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class CHEProtonElectronChemicalPotential:
    """Auditable chemical potential of one H+ + e- reservoir event in eV."""

    conditions: CHEConditions
    hydrogen_reference_hash: str
    hydrogen_gibbs_free_energy_ev: float
    half_h2_term_ev: float
    potential_term_ev: float
    ph_term_ev: float
    chemical_potential_ev: float
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        values = (
            self.hydrogen_gibbs_free_energy_ev,
            self.half_h2_term_ev,
            self.potential_term_ev,
            self.ph_term_ev,
            self.chemical_potential_ev,
        )
        if not all(isfinite(value) for value in values):
            raise CHEError("CHE chemical-potential components must be finite")
        expected = self.half_h2_term_ev + self.potential_term_ev + self.ph_term_ev
        if self.chemical_potential_ev != expected:
            raise CHEError("CHE chemical potential does not equal its explicit component sum")
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "conditions": self.conditions,
                    "hydrogen_reference_hash": self.hydrogen_reference_hash,
                    "hydrogen_gibbs_free_energy_ev": self.hydrogen_gibbs_free_energy_ev,
                    "half_h2_term_ev": self.half_h2_term_ev,
                    "potential_term_ev": self.potential_term_ev,
                    "ph_term_ev": self.ph_term_ev,
                    "chemical_potential_ev": self.chemical_potential_ev,
                }
            ),
        )


def rhe_to_she_potential_v(*, potential_rhe_v: float, ph: float, temperature_k: float) -> float:
    """Convert U_RHE to U_SHE using the same T and proton activity convention."""

    _validate_transform_inputs(potential_rhe_v, ph, temperature_k)
    return potential_rhe_v - BOLTZMANN_EV_PER_K * temperature_k * LN10 * ph


def she_to_rhe_potential_v(*, potential_she_v: float, ph: float, temperature_k: float) -> float:
    """Convert U_SHE to U_RHE using the same T and proton activity convention."""

    _validate_transform_inputs(potential_she_v, ph, temperature_k)
    return potential_she_v + BOLTZMANN_EV_PER_K * temperature_k * LN10 * ph


def proton_electron_chemical_potential(
    *,
    hydrogen_reference: CHEHydrogenReference,
    conditions: CHEConditions,
) -> CHEProtonElectronChemicalPotential:
    """Evaluate mu(H+ + e-) for one proton-electron pair under explicit CHE semantics."""

    if conditions.temperature_k != hydrogen_reference.temperature_k:
        raise CHEError(
            "CHE temperature must exactly match the bound H2 thermochemistry temperature"
        )
    hydrogen_g = hydrogen_reference.gibbs_free_energy_ev
    half_h2 = 0.5 * hydrogen_g
    potential_term = -conditions.potential_v
    if conditions.potential_reference is ElectrodePotentialReference.SHE:
        ph_term = -BOLTZMANN_EV_PER_K * conditions.temperature_k * LN10 * conditions.ph
    elif conditions.potential_reference is ElectrodePotentialReference.RHE:
        ph_term = 0.0
    else:
        raise CHEError("unsupported electrode potential reference")
    chemical_potential = half_h2 + potential_term + ph_term
    return CHEProtonElectronChemicalPotential(
        conditions=conditions,
        hydrogen_reference_hash=hydrogen_reference.source_hash,
        hydrogen_gibbs_free_energy_ev=hydrogen_g,
        half_h2_term_ev=half_h2,
        potential_term_ev=potential_term,
        ph_term_ev=ph_term,
        chemical_potential_ev=chemical_potential,
    )


def _validate_transform_inputs(potential_v: float, ph: float, temperature_k: float) -> None:
    if not isfinite(potential_v):
        raise CHEError("electrode potential must be finite")
    if not isfinite(ph):
        raise CHEError("pH must be finite")
    if not isfinite(temperature_k) or temperature_k <= 0.0:
        raise CHEError("temperature_k must be finite and positive")
