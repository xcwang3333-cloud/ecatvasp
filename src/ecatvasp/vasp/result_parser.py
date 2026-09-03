"""Concrete v0.5 parser for explicit VASP energies and lightweight metadata.

The parser consumes a Block 2 ``VaspResultArtifactIntake`` and returns the
parser-neutral ``VaspResultDocument`` from ADR-030.  It intentionally does not
classify convergence, mutate Calculation state, parse forces/magnetization, or
promote CONTCAR structures.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from ecatvasp.vasp.result_intake import VaspResultArtifactIntake, VaspResultInputFile
from ecatvasp.vasp.results import (
    VaspEnergySummary,
    VaspResultDocument,
    VaspResultSourceRole,
)

VASP_ENERGY_METADATA_PARSER_NAME = "ecatvasp.vasp.energy-metadata-parser"
VASP_ENERGY_METADATA_PARSER_VERSION = "1"


class VaspResultParseError(ValueError):
    """Raised when an admitted result source cannot be parsed without guessing."""


class VaspParserEvidenceCode(StrEnum):
    """Stable raw-observation codes emitted without assigning a convergence verdict."""

    OUTCAR_FREE_ENERGY_TOTEN = "outcar.free_energy_toten"
    OUTCAR_FREE_ENERGY_TOTEN_UNPARSEABLE = "outcar.free_energy_toten_unparseable"
    OUTCAR_ENERGY_WITHOUT_ENTROPY = "outcar.energy_without_entropy"
    OUTCAR_ENERGY_WITHOUT_ENTROPY_UNPARSEABLE = (
        "outcar.energy_without_entropy_unparseable"
    )
    OUTCAR_ENERGY_SIGMA0 = "outcar.energy_sigma0"
    OUTCAR_ENERGY_SIGMA0_UNPARSEABLE = "outcar.energy_sigma0_unparseable"
    OUTCAR_FERMI_ENERGY = "outcar.fermi_energy"
    OUTCAR_FERMI_ENERGY_UNPARSEABLE = "outcar.fermi_energy_unparseable"
    OUTCAR_VASP_VERSION = "outcar.vasp_version"
    OUTCAR_NORMAL_TERMINATION = "outcar.normal_termination"
    OUTCAR_ELECTRONIC_EDIFF_REACHED = "outcar.electronic_ediff_reached"
    OUTCAR_IONIC_REQUIRED_ACCURACY_REACHED = "outcar.ionic_required_accuracy_reached"
    OSZICAR_IONIC_STEPS = "oszicar.ionic_steps"
    OSZICAR_ELECTRONIC_STEPS = "oszicar.electronic_steps"


_FLOAT_TOKEN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_TOTEN_PATTERN = re.compile(
    rf"\bfree\s+energy\s+TOTEN\s*=\s*({_FLOAT_TOKEN})\s+eV\b",
    re.IGNORECASE,
)
_TOTEN_PREFIX = re.compile(r"\bfree\s+energy\s+TOTEN\s*=", re.IGNORECASE)
_ENTROPY_PATTERN = re.compile(
    rf"\benergy\s+without\s+entropy\s*=\s*({_FLOAT_TOKEN})"
    rf"\s+energy\(sigma->0\)\s*=\s*({_FLOAT_TOKEN})",
    re.IGNORECASE,
)
_ENTROPY_PREFIX = re.compile(r"\benergy\s+without\s+entropy\s*=", re.IGNORECASE)
_SIGMA0_PREFIX = re.compile(r"\benergy\(sigma->0\)\s*=", re.IGNORECASE)
_FERMI_PATTERN = re.compile(rf"\bE-fermi\s*:\s*({_FLOAT_TOKEN})\b", re.IGNORECASE)
_FERMI_PREFIX = re.compile(r"\bE-fermi\s*:", re.IGNORECASE)
_VASP_VERSION_PATTERN = re.compile(
    r"\bvasp\.([0-9]+(?:\.[0-9]+)+)\b",
    re.IGNORECASE,
)
_TERMINATION_PATTERN = re.compile(
    r"general\s+timing\s+and\s+accounting\s+information(?:s)?\s+for\s+this\s+job",
    re.IGNORECASE,
)
_IONIC_STEP_PATTERN = re.compile(r"^\s*\d+\s+F=", re.IGNORECASE)
_ELECTRONIC_STEP_PATTERN = re.compile(
    r"^\s*(?:DAV|RMM|CG|DMP):\s*(\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _OutcarFacts:
    free_energy_toten_ev: float | None
    energy_without_entropy_ev: float | None
    energy_sigma0_ev: float | None
    fermi_energy_ev: float | None
    vasp_version: str | None
    termination_observed: bool
    evidence: frozenset[VaspParserEvidenceCode]


@dataclass(frozen=True, slots=True)
class _OszicarFacts:
    ionic_steps: int | None
    electronic_steps: int | None
    evidence: frozenset[VaspParserEvidenceCode]


def parse_vasp_energy_metadata(
    *,
    project_root: Path | str,
    intake: VaspResultArtifactIntake,
) -> VaspResultDocument:
    """Parse explicit energy semantics and lightweight metadata from one exact intake.

    OUTCAR is authoritative for the four energy fields, VASP version, raw convergence
    markers, and normal-termination marker.  OSZICAR supplements step metadata only.
    CONTCAR and vasprun.xml remain part of the exact admitted source bundle but are not
    scientifically interpreted by this block.
    """

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise VaspResultParseError("project_root must be an existing directory")

    outcar_facts: _OutcarFacts | None = None
    oszicar_facts: _OszicarFacts | None = None

    for item in intake.files:
        path = _resolve_input_path(root=root, item=item)
        if item.source.role is VaspResultSourceRole.OUTCAR:
            outcar_facts = _parse_outcar(path=path, item=item)
        elif item.source.role is VaspResultSourceRole.OSZICAR:
            oszicar_facts = _parse_oszicar(path=path, item=item)
        else:
            _verify_file_integrity(path=path, item=item)

    if outcar_facts is None:
        raise VaspResultParseError("result intake does not contain a parseable OUTCAR")

    evidence = set(outcar_facts.evidence)
    if oszicar_facts is not None:
        evidence.update(oszicar_facts.evidence)

    return VaspResultDocument(
        calculation_type=intake.calculation_type,
        sources=intake.sources,
        energies=VaspEnergySummary(
            free_energy_toten_ev=outcar_facts.free_energy_toten_ev,
            energy_without_entropy_ev=outcar_facts.energy_without_entropy_ev,
            energy_sigma0_ev=outcar_facts.energy_sigma0_ev,
            fermi_energy_ev=outcar_facts.fermi_energy_ev,
        ),
        vasp_version=outcar_facts.vasp_version,
        ionic_steps=None if oszicar_facts is None else oszicar_facts.ionic_steps,
        electronic_steps=(
            None if oszicar_facts is None else oszicar_facts.electronic_steps
        ),
        termination_observed=outcar_facts.termination_observed,
        evidence_codes=tuple(sorted(code.value for code in evidence)),
    )


def _resolve_input_path(*, root: Path, item: VaspResultInputFile) -> Path:
    relative = PurePosixPath(item.local_relative_path)
    if (
        relative.is_absolute()
        or item.local_relative_path != relative.as_posix()
        or ".." in relative.parts
        or item.local_relative_path in {"", "."}
    ):
        raise VaspResultParseError("intake local path must be a normalized relative POSIX path")
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise VaspResultParseError("result source resolves outside project_root")
    if not path.is_file():
        raise VaspResultParseError(
            f"result source file is missing for role {item.source.role.value!r}"
        )
    return path


def _parse_outcar(*, path: Path, item: VaspResultInputFile) -> _OutcarFacts:
    digest = hashlib.sha256()
    observed_size = 0
    free_energy_toten_ev: float | None = None
    energy_without_entropy_ev: float | None = None
    energy_sigma0_ev: float | None = None
    fermi_energy_ev: float | None = None
    versions: set[str] = set()
    termination_observed = False
    evidence: set[VaspParserEvidenceCode] = set()

    with path.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            observed_size += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace")

            if _TOTEN_PREFIX.search(line) is not None:
                match = _TOTEN_PATTERN.search(line)
                if match is None:
                    free_energy_toten_ev = None
                    evidence.add(
                        VaspParserEvidenceCode.OUTCAR_FREE_ENERGY_TOTEN_UNPARSEABLE
                    )
                else:
                    free_energy_toten_ev = _parse_finite_float(
                        match.group(1), "free energy TOTEN"
                    )
                    evidence.add(VaspParserEvidenceCode.OUTCAR_FREE_ENERGY_TOTEN)

            entropy_prefix = _ENTROPY_PREFIX.search(line) is not None
            sigma0_prefix = _SIGMA0_PREFIX.search(line) is not None
            if entropy_prefix or sigma0_prefix:
                match = _ENTROPY_PATTERN.search(line)
                if match is None:
                    if entropy_prefix:
                        energy_without_entropy_ev = None
                        evidence.add(
                            VaspParserEvidenceCode.OUTCAR_ENERGY_WITHOUT_ENTROPY_UNPARSEABLE
                        )
                    if sigma0_prefix:
                        energy_sigma0_ev = None
                        evidence.add(
                            VaspParserEvidenceCode.OUTCAR_ENERGY_SIGMA0_UNPARSEABLE
                        )
                else:
                    energy_without_entropy_ev = _parse_finite_float(
                        match.group(1), "energy without entropy"
                    )
                    energy_sigma0_ev = _parse_finite_float(
                        match.group(2), "energy(sigma->0)"
                    )
                    evidence.add(VaspParserEvidenceCode.OUTCAR_ENERGY_WITHOUT_ENTROPY)
                    evidence.add(VaspParserEvidenceCode.OUTCAR_ENERGY_SIGMA0)

            if _FERMI_PREFIX.search(line) is not None:
                match = _FERMI_PATTERN.search(line)
                if match is None:
                    fermi_energy_ev = None
                    evidence.add(VaspParserEvidenceCode.OUTCAR_FERMI_ENERGY_UNPARSEABLE)
                else:
                    fermi_energy_ev = _parse_finite_float(match.group(1), "E-fermi")
                    evidence.add(VaspParserEvidenceCode.OUTCAR_FERMI_ENERGY)

            for match in _VASP_VERSION_PATTERN.finditer(line):
                versions.add(match.group(1))

            if _TERMINATION_PATTERN.search(line) is not None:
                termination_observed = True
                evidence.add(VaspParserEvidenceCode.OUTCAR_NORMAL_TERMINATION)

            lowered = line.casefold()
            if "aborting loop because ediff is reached" in lowered:
                evidence.add(VaspParserEvidenceCode.OUTCAR_ELECTRONIC_EDIFF_REACHED)
            if "reached required accuracy - stopping structural energy minimisation" in lowered:
                evidence.add(
                    VaspParserEvidenceCode.OUTCAR_IONIC_REQUIRED_ACCURACY_REACHED
                )

    _validate_observed_integrity(
        item=item,
        observed_size=observed_size,
        observed_sha256=digest.hexdigest(),
    )
    if len(versions) > 1:
        raise VaspResultParseError(
            "OUTCAR contains multiple distinct VASP versions and is ambiguous"
        )
    vasp_version = None if not versions else next(iter(versions))
    if vasp_version is not None:
        evidence.add(VaspParserEvidenceCode.OUTCAR_VASP_VERSION)

    return _OutcarFacts(
        free_energy_toten_ev=free_energy_toten_ev,
        energy_without_entropy_ev=energy_without_entropy_ev,
        energy_sigma0_ev=energy_sigma0_ev,
        fermi_energy_ev=fermi_energy_ev,
        vasp_version=vasp_version,
        termination_observed=termination_observed,
        evidence=frozenset(evidence),
    )


def _parse_oszicar(*, path: Path, item: VaspResultInputFile) -> _OszicarFacts:
    digest = hashlib.sha256()
    observed_size = 0
    ionic_steps = 0
    electronic_steps: int | None = None

    with path.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            observed_size += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace")
            if _IONIC_STEP_PATTERN.match(line) is not None:
                ionic_steps += 1
            match = _ELECTRONIC_STEP_PATTERN.match(line)
            if match is not None:
                electronic_steps = int(match.group(1))

    _validate_observed_integrity(
        item=item,
        observed_size=observed_size,
        observed_sha256=digest.hexdigest(),
    )

    evidence: set[VaspParserEvidenceCode] = set()
    if ionic_steps:
        evidence.add(VaspParserEvidenceCode.OSZICAR_IONIC_STEPS)
    if electronic_steps is not None:
        evidence.add(VaspParserEvidenceCode.OSZICAR_ELECTRONIC_STEPS)

    return _OszicarFacts(
        ionic_steps=ionic_steps or None,
        electronic_steps=electronic_steps,
        evidence=frozenset(evidence),
    )


def _verify_file_integrity(*, path: Path, item: VaspResultInputFile) -> None:
    digest = hashlib.sha256()
    observed_size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            observed_size += len(chunk)
    _validate_observed_integrity(
        item=item,
        observed_size=observed_size,
        observed_sha256=digest.hexdigest(),
    )


def _validate_observed_integrity(
    *,
    item: VaspResultInputFile,
    observed_size: int,
    observed_sha256: str,
) -> None:
    if observed_size != item.size_bytes:
        raise VaspResultParseError(
            f"result source size changed for role {item.source.role.value!r}"
        )
    if observed_sha256 != item.source.sha256.lower():
        raise VaspResultParseError(
            f"result source SHA-256 changed for role {item.source.role.value!r}"
        )


def _parse_finite_float(value: str, field_name: str) -> float:
    try:
        parsed = float(value.replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise VaspResultParseError(f"{field_name} is not a valid float") from exc
    if not math.isfinite(parsed):
        raise VaspResultParseError(f"{field_name} must be finite")
    return parsed
