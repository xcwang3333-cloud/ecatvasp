from dataclasses import dataclass, field
from typing import Any

from .severity import Severity


@dataclass(frozen=True)
class PreflightResult:
    rule_id: str
    severity: Severity
    message: str
    suggestion: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightReport:
    results: list[PreflightResult] = field(default_factory=list)

    @property
    def overall_status(self) -> Severity:
        if any(r.severity == Severity.FATAL for r in self.results):
            return Severity.FATAL
        if any(r.severity == Severity.BLOCKED for r in self.results):
            return Severity.BLOCKED
        if any(r.severity == Severity.WARNING for r in self.results):
            return Severity.WARNING
        return Severity.PASS
