"""Scientific analysis contracts and derived-result boundaries for ECatVASP."""

from ecatvasp.analysis.dos_materialization import (
    CANONICAL_DOS_ARTIFACT_FORMAT,
    CANONICAL_DOS_ARTIFACT_VERSION,
    DOS_MATERIALIZER_NAME,
    DOS_MATERIALIZER_VERSION,
    DosMaterializationError,
    DurableDosMaterialization,
    load_canonical_dos_artifact,
    materialize_canonical_dos_analysis,
)
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
    "CANONICAL_DOS_ARTIFACT_FORMAT",
    "CANONICAL_DOS_ARTIFACT_VERSION",
    "DOSCAR_PARSER_NAME",
    "DOSCAR_PARSER_VERSION",
    "DOS_MATERIALIZER_NAME",
    "DOS_MATERIALIZER_VERSION",
    "CanonicalDosIntake",
    "CanonicalDosResult",
    "DosMaterializationError",
    "DosSeries",
    "DoscarParseError",
    "DurableDosMaterialization",
    "ElectronicEnergyAxis",
    "ElectronicEnergyReference",
    "ExternalInputDigest",
    "ExternalToolInvocation",
    "OrbitalChannel",
    "ProjectionScope",
    "SpinChannel",
    "load_canonical_dos_artifact",
    "materialize_canonical_dos_analysis",
    "parse_vasp_doscar",
]
