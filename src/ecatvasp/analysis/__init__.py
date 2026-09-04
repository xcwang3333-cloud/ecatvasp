"""Scientific analysis contracts and derived-result boundaries for ECatVASP."""

from ecatvasp.analysis.doscar import (
    DOSCAR_PARSER_NAME,
    DOSCAR_PARSER_VERSION,
    CanonicalDosIntake,
    DoscarParseError,
    parse_vasp_doscar,
)
from ecatvasp.analysis.electronic import (
    CanonicalDosResult,
    DosSeries,
    ElectronicEnergyAxis,
    ElectronicEnergyReference,
    ExternalInputDigest,
    ExternalToolInvocation,
    OrbitalChannel,
    ProjectionScope,
    SpinChannel,
)

__all__ = [
    "DOSCAR_PARSER_NAME",
    "DOSCAR_PARSER_VERSION",
    "CanonicalDosIntake",
    "CanonicalDosResult",
    "DosSeries",
    "DoscarParseError",
    "ElectronicEnergyAxis",
    "ElectronicEnergyReference",
    "ExternalInputDigest",
    "ExternalToolInvocation",
    "OrbitalChannel",
    "ProjectionScope",
    "SpinChannel",
    "parse_vasp_doscar",
]
