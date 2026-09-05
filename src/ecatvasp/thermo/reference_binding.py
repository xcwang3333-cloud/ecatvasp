"""Species-bound pure reference-correction API for v0.8 Block 4.

The lower-level additive helper in ``references`` intentionally operates on already validated
objects. This module supplies the public pure boundary that binds a raw gas thermochemistry
result to an explicit molecular reference before any correction can be applied.
"""

from __future__ import annotations

from dataclasses import dataclass

from ecatvasp.domain import canonical_sha256
from ecatvasp.thermo.contracts import (
    ThermochemistryResult,
    ThermochemistrySubjectKind,
)
from ecatvasp.thermo.gas import GasReferenceDefinition
from ecatvasp.thermo.references import (
    GasReferenceAdjustmentIdentity,
    ReferenceCorrectionError,
    ReferenceThermochemistryResult,
    apply_reference_corrections as _apply_reference_corrections,
)


@dataclass(frozen=True, slots=True)
class BoundGasReferenceThermochemistry:
    """Explicit species binding for one uncorrected raw gas thermochemistry result."""

    reference: GasReferenceDefinition
    result: ThermochemistryResult

    def __post_init__(self) -> None:
        if self.result.identity.subject_kind is not ThermochemistrySubjectKind.GAS:
            raise ReferenceCorrectionError(
                "bound molecular reference requires GAS thermochemistry"
            )
        if self.result.identity.corrections or self.result.components.corrections:
            raise ReferenceCorrectionError(
                "bound molecular reference requires uncorrected raw thermochemistry"
            )

    @property
    def content_hash(self) -> str:
        """Return deterministic species-plus-raw-result identity."""

        return canonical_sha256(
            {
                "reference": self.reference,
                "result_hash": self.result.result_hash,
            }
        )


def apply_bound_reference_corrections(
    *,
    source: BoundGasReferenceThermochemistry,
    adjustment: GasReferenceAdjustmentIdentity,
) -> ReferenceThermochemistryResult:
    """Apply corrections only when the explicit raw and adjustment species agree."""

    if source.reference != adjustment.reference:
        raise ReferenceCorrectionError(
            "reference adjustment species/state differs from bound raw gas reference"
        )
    return _apply_reference_corrections(
        source_result=source.result,
        adjustment=adjustment,
    )
