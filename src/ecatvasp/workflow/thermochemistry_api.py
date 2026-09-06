"""Fail-closed public constructor surface for v0.8 thermochemistry reconciliation."""

from ecatvasp.domain import AnalysisType
from ecatvasp.workflow.thermochemistry import (
    ThermochemistryAnalysisRequirement as _ThermochemistryAnalysisRequirement,
)
from ecatvasp.workflow.thermochemistry import ThermochemistryReconciliationError


class ThermochemistryAnalysisRequirement(_ThermochemistryAnalysisRequirement):
    """Public requirement that rejects string lookalikes for AnalysisType."""

    __slots__ = ()

    def __post_init__(self) -> None:
        if not isinstance(self.analysis_type, AnalysisType):
            raise ThermochemistryReconciliationError(
                "requirement analysis_type must be an AnalysisType enum"
            )
        super().__post_init__()


__all__ = ["ThermochemistryAnalysisRequirement"]
