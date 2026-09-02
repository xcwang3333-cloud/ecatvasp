"""Licensed-POTCAR metadata resolution and ENCUT convergence contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path

from ecatvasp.domain.method import (
    MethodDefinition,
    PotcarIdentity,
    canonical_sha256,
)
from ecatvasp.vasp.contracts import ProjectNumericalLock
from ecatvasp.vasp.poscar import PreparedPoscar

_FLOAT_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"


class PotcarPreparationError(ValueError):
    """Raised when licensed POTCAR metadata cannot be resolved without guessing."""


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_positive(value: float, field_name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be finite and positive")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized


@dataclass(frozen=True, slots=True)
class PotcarSpecEntry:
    """Metadata-only description of one licensed POTCAR dataset."""

    element: str
    symbol: str
    family: str
    titel: str
    zval: float
    enmax_ev: float
    sha256: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.element, "element"),
            (self.symbol, "symbol"),
            (self.family, "family"),
            (self.titel, "titel"),
        ):
            _require_text(value, field_name)
        _require_positive(self.zval, "zval")
        _require_positive(self.enmax_ev, "enmax_ev")
        object.__setattr__(self, "sha256", _normalized_sha256(self.sha256, "sha256"))


@dataclass(frozen=True, slots=True)
class PotcarSpec:
    """Ordered, redistribution-safe POTCAR specification for one prepared POSCAR."""

    core_method_hash: str
    entries: tuple[PotcarSpecEntry, ...]
    text: str
    sha256: str
    metadata_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("POTCAR spec requires at least one entry")
        elements = tuple(entry.element for entry in self.entries)
        if len(elements) != len(set(elements)):
            raise ValueError("POTCAR spec entries must have unique elements")
        families = {entry.family for entry in self.entries}
        if len(families) != 1:
            raise ValueError("POTCAR spec entries must come from one explicit family")

        object.__setattr__(
            self,
            "core_method_hash",
            _normalized_sha256(self.core_method_hash, "core_method_hash"),
        )
        expected_text = "".join(f"{entry.symbol}\n" for entry in self.entries)
        if self.text != expected_text:
            raise ValueError("POTCAR.spec text must list resolved symbols in entry order")
        expected_sha = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.sha256 != expected_sha:
            raise ValueError("POTCAR.spec sha256 does not match text content")
        object.__setattr__(
            self,
            "metadata_hash",
            canonical_sha256(
                {
                    "core_method_hash": self.core_method_hash,
                    "entries": self.entries,
                }
            ),
        )

    @property
    def species_order(self) -> tuple[str, ...]:
        """Return the element order that must match POSCAR and staged POTCAR concatenation."""

        return tuple(entry.element for entry in self.entries)

    @property
    def max_enmax_ev(self) -> float:
        """Return the largest recommended ENMAX across all ordered POTCAR entries."""

        return max(entry.enmax_ev for entry in self.entries)


@dataclass(frozen=True, slots=True)
class ResolvedPotcarFile:
    """One locally licensed POTCAR path plus redistribution-safe parsed metadata."""

    path: Path
    entry: PotcarSpecEntry

    def __post_init__(self) -> None:
        if self.path.name != "POTCAR":
            raise ValueError("resolved POTCAR path must point to a file named POTCAR")


@dataclass(frozen=True, slots=True)
class ResolvedPotcarSet:
    """Ordered local POTCAR resolution result without retaining licensed file contents."""

    spec: PotcarSpec
    files: tuple[ResolvedPotcarFile, ...]

    def __post_init__(self) -> None:
        if len(self.files) != len(self.spec.entries):
            raise ValueError("resolved POTCAR files must match POTCAR spec entries")
        if tuple(item.entry for item in self.files) != self.spec.entries:
            raise ValueError("resolved POTCAR files must follow POTCAR spec order")

    @property
    def ordered_paths(self) -> tuple[Path, ...]:
        """Return local paths in the exact concatenation order required by the POSCAR."""

        return tuple(item.path for item in self.files)


@dataclass(frozen=True, slots=True)
class LocalPotcarLibrary:
    """Explicit local directory for one licensed POTCAR family.

    ``root`` is the directory that directly contains one subdirectory per POTCAR
    symbol, for example ``root/C/POTCAR`` or ``root/Pb_d/POTCAR``.
    """

    family: str
    root: Path

    def __post_init__(self) -> None:
        _require_text(self.family, "family")

    def resolve(
        self,
        *,
        prepared_poscar: PreparedPoscar,
        method: MethodDefinition,
    ) -> ResolvedPotcarSet:
        """Resolve and verify local POTCAR files in exact POSCAR species order."""

        if method.potcar_family != self.family:
            raise PotcarPreparationError(
                "POTCAR library family does not match MethodDefinition.potcar_family"
            )

        identities = {identity.element: identity for identity in method.potcars}
        poscar_elements = prepared_poscar.species_order
        missing = tuple(element for element in poscar_elements if element not in identities)
        extra = tuple(
            identity.element
            for identity in method.potcars
            if identity.element not in poscar_elements
        )
        if missing or extra:
            raise PotcarPreparationError(
                "Method POTCAR identities must exactly cover PreparedPoscar species"
            )

        resolved: list[ResolvedPotcarFile] = []
        for element in poscar_elements:
            identity = identities[element]
            path = self.root / identity.symbol / "POTCAR"
            resolved.append(
                ResolvedPotcarFile(
                    path=path,
                    entry=_read_and_validate_potcar(
                        path=path,
                        family=self.family,
                        identity=identity,
                    ),
                )
            )

        entries = tuple(item.entry for item in resolved)
        text = "".join(f"{entry.symbol}\n" for entry in entries)
        spec = PotcarSpec(
            core_method_hash=canonical_sha256(method),
            entries=entries,
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        if spec.species_order != prepared_poscar.species_order:
            raise PotcarPreparationError("POTCAR element order does not match PreparedPoscar")
        return ResolvedPotcarSet(spec=spec, files=tuple(resolved))


@dataclass(frozen=True, slots=True)
class EncCutBaseline:
    """POTCAR-derived lower-bound suggestion, not a validated production ENCUT."""

    potcar_spec_hash: str
    max_enmax_ev: float
    suggested_encut_ev: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "potcar_spec_hash",
            _normalized_sha256(self.potcar_spec_hash, "potcar_spec_hash"),
        )
        _require_positive(self.max_enmax_ev, "max_enmax_ev")
        _require_positive(self.suggested_encut_ev, "suggested_encut_ev")
        if self.suggested_encut_ev != self.max_enmax_ev:
            raise ValueError("ENCUT baseline must equal the largest POTCAR ENMAX")


@dataclass(frozen=True, slots=True)
class EncCutValidationEvidence:
    """Reference to completed convergence evidence used to justify a project ENCUT lock."""

    core_method_hash: str
    potcar_spec_hash: str
    tested_encuts_ev: tuple[float, ...]
    selected_encut_ev: float
    analysis_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "core_method_hash",
            _normalized_sha256(self.core_method_hash, "core_method_hash"),
        )
        object.__setattr__(
            self,
            "potcar_spec_hash",
            _normalized_sha256(self.potcar_spec_hash, "potcar_spec_hash"),
        )
        object.__setattr__(
            self,
            "analysis_hash",
            _normalized_sha256(self.analysis_hash, "analysis_hash"),
        )
        if not self.tested_encuts_ev:
            raise ValueError("tested_encuts_ev must not be empty")
        if any(not isfinite(value) or value <= 0 for value in self.tested_encuts_ev):
            raise ValueError("tested ENCUT values must be finite and positive")
        if len(set(self.tested_encuts_ev)) != len(self.tested_encuts_ev):
            raise ValueError("tested ENCUT values must be unique")
        ordered = tuple(sorted(self.tested_encuts_ev))
        object.__setattr__(self, "tested_encuts_ev", ordered)
        _require_positive(self.selected_encut_ev, "selected_encut_ev")
        if self.selected_encut_ev not in ordered:
            raise ValueError("selected_encut_ev must be one of the tested ENCUT values")

    @property
    def evidence_hash(self) -> str:
        """Return a deterministic digest for manifest/provenance use."""

        return canonical_sha256(self)


def suggest_encut_baseline(spec: PotcarSpec) -> EncCutBaseline:
    """Return the maximum POTCAR ENMAX as a convergence starting baseline."""

    return EncCutBaseline(
        potcar_spec_hash=spec.metadata_hash,
        max_enmax_ev=spec.max_enmax_ev,
        suggested_encut_ev=spec.max_enmax_ev,
    )


def validate_encut_evidence(
    *,
    spec: PotcarSpec,
    evidence: EncCutValidationEvidence,
) -> None:
    """Validate convergence evidence against the exact POTCAR metadata identity."""

    if evidence.core_method_hash != spec.core_method_hash:
        raise PotcarPreparationError("ENCUT evidence core method does not match POTCAR spec")
    if evidence.potcar_spec_hash != spec.metadata_hash:
        raise PotcarPreparationError("ENCUT evidence POTCAR spec hash does not match")
    if evidence.selected_encut_ev < spec.max_enmax_ev:
        raise PotcarPreparationError("selected ENCUT is below the POTCAR ENMAX baseline")


def validate_project_lock_encut(
    *,
    lock: ProjectNumericalLock,
    spec: PotcarSpec,
    evidence: EncCutValidationEvidence,
) -> None:
    """Fail closed unless a project lock is backed by matching convergence evidence."""

    validate_encut_evidence(spec=spec, evidence=evidence)
    if lock.core_method_hash != spec.core_method_hash:
        raise PotcarPreparationError("project lock core method does not match POTCAR spec")
    if lock.encut_validation_hash != evidence.analysis_hash:
        raise PotcarPreparationError("project lock ENCUT validation hash does not match evidence")
    if lock.encut_ev != evidence.selected_encut_ev:
        raise PotcarPreparationError("project lock ENCUT does not match validated ENCUT")


def _read_and_validate_potcar(
    *,
    path: Path,
    family: str,
    identity: PotcarIdentity,
) -> PotcarSpecEntry:
    if not path.is_file():
        raise PotcarPreparationError(f"licensed POTCAR is missing: {path}")

    raw = path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != identity.sha256:
        raise PotcarPreparationError(
            f"POTCAR hash mismatch for {identity.element}/{identity.symbol}"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PotcarPreparationError(
            f"POTCAR is not UTF-8/ASCII text: {identity.element}/{identity.symbol}"
        ) from exc

    titel = _extract_text_tag(text, "TITEL")
    if identity.symbol not in titel.split():
        raise PotcarPreparationError(
            f"POTCAR TITEL does not match expected symbol {identity.symbol}"
        )
    zval = _extract_float_tag(text, "ZVAL")
    enmax = _extract_float_tag(text, "ENMAX")
    if zval <= 0 or enmax <= 0:
        raise PotcarPreparationError("POTCAR ZVAL and ENMAX must be positive")

    return PotcarSpecEntry(
        element=identity.element,
        symbol=identity.symbol,
        family=family,
        titel=titel,
        zval=zval,
        enmax_ev=enmax,
        sha256=actual_sha,
    )


def _extract_text_tag(text: str, tag: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(tag)}\s*=\s*(.+?)\s*$", text)
    if match is None:
        raise PotcarPreparationError(f"POTCAR is missing required {tag} metadata")
    value = match.group(1).strip()
    if not value:
        raise PotcarPreparationError(f"POTCAR {tag} metadata is blank")
    return value


def _extract_float_tag(text: str, tag: str) -> float:
    match = re.search(
        rf"\b{re.escape(tag)}\s*=\s*({_FLOAT_PATTERN})",
        text,
    )
    if match is None:
        raise PotcarPreparationError(f"POTCAR is missing required {tag} metadata")
    value = float(match.group(1))
    if not isfinite(value):
        raise PotcarPreparationError(f"POTCAR {tag} metadata must be finite")
    return value
