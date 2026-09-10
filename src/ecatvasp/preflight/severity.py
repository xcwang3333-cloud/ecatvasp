from enum import Enum


class Severity(str, Enum):
    """Preflight result severity levels."""

    PASS = "PASS"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    FATAL = "FATAL"
