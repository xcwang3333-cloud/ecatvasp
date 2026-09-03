"""Recipe-aware scientific convergence classification for v0.5 Block 4.

Evidence collection and verdict classification are deliberately separate. This
module never mutates Calculation or ExecutionAttempt lifecycle state.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from ecatvasp.domain import Calculation, CalculationType, MethodFingerprint
from ecatvasp.domain.ids import CalculationId
from ecatvasp.vasp.recipes import (
    RECIPE_ADSORBATE_RELAX,
    RECIPE_CHARGE_DENSITY_STATIC,
    RECIPE_DOS_PREREQUISITE,
    RECIPE_ENCUT_CONVERGENCE_POINT,
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_GAS_RELAX,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_KPOINT_CONVERGENCE_POINT,
    RECIPE_LOBSTER_PREREQUISITE,
    RECIPE_SELECTED_ATOM_FREQUENCY,
    RECIPE_SLAB_RELAX,
    get_vasp_recipe_spec,
)
from ecatvasp.vasp.result_intake import VaspResultArtifactIntake, VaspResultInputFile
from ecatvasp.vasp.result_parser import VaspParserEvidenceCode
from ecatvasp.vasp.results import (
    ConvergenceVerdict,
    VaspConvergenceAssessment,
    VaspResultDocument,
    VaspResultSourceRole,
)

VASP_CONVERGENCE_CLASSIFIER_NAME = "ecatvasp.vasp.convergence-classifier"
VASP_CONVERGENCE_CLASSIFIER_VERSION = "1"


class VaspConvergenceError(ValueError):
    """Raised when convergence cannot be assessed without violating provenance."""


class VaspConvergenceEvidenceCode(StrEnum):
    """Stable evidence codes used by the convergence layer."""

    NORMAL_TERMINATION_OBSERVED = "convergence.normal_termination_observed"
    OUTPUT_INCOMPLETE = "convergence.output_incomplete"
    FINAL_TOTEN_OBSERVED = "convergence.final_toten_observed"
    FINAL_TOTEN_MISSING = "convergence.final_toten_missing"
    ELECTRONIC_EDIFF_OBSERVED = "convergence.electronic_ediff_observed"
    ELECTRONIC_MARKER_MISSING = "convergence.electronic_marker_missing"
    OUTCAR_NELM_OBSERVED = "convergence.outcar_nelm_observed"
    ELECTRONIC_LIMIT_EXHAUSTED = "convergence.electronic_limit_exhausted"
    OUTCAR_NSW_OBSERVED = "convergence.outcar_nsw_observed"
    IONIC_REQUIRED_ACCURACY_OBSERVED = "convergence.ionic_required_accuracy_observed"
    IONIC_LIMIT_MATCHES_RECIPE = "convergence.ionic_limit_matches_recipe"
    IONIC_LIMIT_MISMATCH_RECIPE = "convergence.ionic_limit_mismatch_recipe"
    IONIC_LIMIT_EXHAUSTED = "convergence.ionic_limit_exhausted"
    IONIC_MARKER_MISSING = "convergence.ionic_marker_missing"
    IONIC_NOT_APPLICABLE = "convergence.ionic_not_applicable"
    OSZICAR_MAX_ELECTRONIC_STEP_OBSERVED = (
        "convergence.oszicar_max_electronic_step_observed"
    )
    OSZICAR_IONIC_STEPS_OBSERVED = "convergence.oszicar_ionic_steps_observed"


_NELM_PATTERN = re.compile(r"\bNELM\b\s*=\s*(\d+)", re.IGNORECASE)
_NSW_PATTERN = re.compile(r"\bNSW\b\s*=\s*(\d+)", re.IGNORECASE)
_IONIC_STEP_PATTERN = re.compile(r"^\s*\d+\s+F=", re.IGNORECASE)
_ELECTRONIC_STEP_PATTERN = re.compile(
    r"^\s*(?:DAV|RMM|CG|DMP):\s*(\d+)\b",
    re.IGNORECASE,
)
_RELAX_TYPES = frozenset({CalculationType.RELAX, CalculationType.GAS_RELAX})
_RELAX_RECIPE_IDS = frozenset(
    {RECIPE_SLAB_RELAX, RECIPE_ADSORBATE_RELAX, RECIPE_GAS_RELAX}
)
_FREQUENCY_RECIPE_IDS = frozenset(
    {RECIPE_SELECTED_ATOM_FREQUENCY, RECIPE_FULL_FREQUENCY, RECIPE_GAS_FREQUENCY}
)
_STATIC_RECIPE_IDS = frozenset(
    {
        RECIPE_GROUND_STATE_STATIC,
        RECIPE_DOS_PREREQUISITE,
        RECIPE_CHARGE_DENSITY_STATIC,
        RECIPE_LOBSTER_PREREQUISITE,
        RECIPE_ENCUT_CONVERGENCE_POINT,
        RECIPE_KPOINT_CONVERGENCE_POINT,
    }
)


@dataclass(frozen=True, slots=True)
class VaspConvergenceEvidence:
    """Raw facts required by the pure recipe-aware convergence classifier."""

    calculation_id: CalculationId
    intake_hash: str
    calculation_type: CalculationType
    recipe_id: str
    termination_observed: bool | None
    final_toten_observed: bool
    electronic_ediff_reached: bool
    ionic_required_accuracy_reached: bool
    electronic_step_limit: int | None
    ionic_step_limit: int | None
    ionic_steps: int | None
    final_electronic_steps: int | None
    max_electronic_steps: int | None
    evidence_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.recipe_id.strip():
            raise VaspConvergenceError("recipe_id must not be blank")
        valid_hash = len(self.intake_hash) == 64 and all(
            char in "0123456789abcdefABCDEF" for char in self.intake_hash
        )
        if not valid_hash:
            raise VaspConvergenceError("intake_hash must be a SHA-256 digest")
        for name in (
            "electronic_step_limit",
            "ionic_step_limit",
            "ionic_steps",
            "final_electronic_steps",
            "max_electronic_steps",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise VaspConvergenceError(f"{name} must not be negative")
        if self.electronic_step_limit == 0:
            raise VaspConvergenceError("electronic_step_limit must be positive when present")
        if any(not code.strip() for code in self.evidence_codes):
            raise VaspConvergenceError("evidence_codes must not contain blank values")
        if len(self.evidence_codes) != len(set(self.evidence_codes)):
            raise VaspConvergenceError("evidence_codes must be unique")


def collect_vasp_convergence_evidence(
    *,
    project_root: Path | str,
    intake: VaspResultArtifactIntake,
    result: VaspResultDocument,
) -> VaspConvergenceEvidence:
    """Collect exact raw convergence facts without assigning a scientific verdict."""

    if result.calculation_type is not intake.calculation_type:
        raise VaspConvergenceError("parsed result CalculationType does not match result intake")
    if result.sources != intake.sources:
        raise VaspConvergenceError("parsed result sources do not match the exact result intake")

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise VaspConvergenceError("project_root must be an existing directory")

    outcar = _require_file(intake, VaspResultSourceRole.OUTCAR)
    nelm, nsw = _scan_outcar_limits(
        path=_resolve_input_path(root=root, item=outcar),
        item=outcar,
    )
    ionic_steps, final_electronic, max_electronic = _read_oszicar_evidence(
        root=root,
        intake=intake,
        result=result,
    )

    parser_codes = set(result.evidence_codes)
    electronic_marker = (
        VaspParserEvidenceCode.OUTCAR_ELECTRONIC_EDIFF_REACHED.value in parser_codes
    )
    ionic_marker = (
        VaspParserEvidenceCode.OUTCAR_IONIC_REQUIRED_ACCURACY_REACHED.value
        in parser_codes
    )
    final_toten = result.energies.free_energy_toten_ev is not None
    codes = _initial_evidence_codes(
        result=result,
        electronic_marker=electronic_marker,
        ionic_marker=ionic_marker,
        final_toten=final_toten,
        nelm=nelm,
        nsw=nsw,
        ionic_steps=ionic_steps,
        max_electronic=max_electronic,
    )
    return VaspConvergenceEvidence(
        calculation_id=intake.calculation_id,
        intake_hash=intake.intake_hash,
        calculation_type=intake.calculation_type,
        recipe_id=intake.recipe_id,
        termination_observed=result.termination_observed,
        final_toten_observed=final_toten,
        electronic_ediff_reached=electronic_marker,
        ionic_required_accuracy_reached=ionic_marker,
        electronic_step_limit=nelm,
        ionic_step_limit=nsw,
        ionic_steps=ionic_steps,
        final_electronic_steps=final_electronic,
        max_electronic_steps=max_electronic,
        evidence_codes=tuple(sorted(code.value for code in codes)),
    )


def assess_vasp_convergence(
    *,
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    evidence: VaspConvergenceEvidence,
) -> VaspConvergenceAssessment:
    """Return a pure scientific convergence verdict from exact recipe-aware evidence."""

    _validate_assessment_identity(calculation, fingerprint, evidence)
    expected_nsw = _expected_ionic_step_limit(fingerprint)
    codes = set(evidence.evidence_codes)
    limit_mismatch = (
        evidence.ionic_step_limit is not None
        and evidence.ionic_step_limit != expected_nsw
    )
    if limit_mismatch:
        codes.add(VaspConvergenceEvidenceCode.IONIC_LIMIT_MISMATCH_RECIPE.value)
    elif evidence.ionic_step_limit is not None:
        codes.add(VaspConvergenceEvidenceCode.IONIC_LIMIT_MATCHES_RECIPE.value)

    electronic = _assess_electronic(evidence, codes)
    ionic = _assess_ionic(evidence, expected_nsw, limit_mismatch, codes)
    if limit_mismatch:
        overall = ConvergenceVerdict.INDETERMINATE
    else:
        overall = _combine_verdicts(electronic, ionic)
    return VaspConvergenceAssessment(
        calculation_type=calculation.calculation_type,
        electronic=electronic,
        ionic=ionic,
        overall=overall,
        evidence_codes=tuple(sorted(codes)),
    )


def _initial_evidence_codes(
    *,
    result: VaspResultDocument,
    electronic_marker: bool,
    ionic_marker: bool,
    final_toten: bool,
    nelm: int | None,
    nsw: int | None,
    ionic_steps: int | None,
    max_electronic: int | None,
) -> set[VaspConvergenceEvidenceCode]:
    codes: set[VaspConvergenceEvidenceCode] = set()
    observations = (
        (
            result.termination_observed is True,
            VaspConvergenceEvidenceCode.NORMAL_TERMINATION_OBSERVED,
        ),
        (final_toten, VaspConvergenceEvidenceCode.FINAL_TOTEN_OBSERVED),
        (electronic_marker, VaspConvergenceEvidenceCode.ELECTRONIC_EDIFF_OBSERVED),
        (ionic_marker, VaspConvergenceEvidenceCode.IONIC_REQUIRED_ACCURACY_OBSERVED),
        (nelm is not None, VaspConvergenceEvidenceCode.OUTCAR_NELM_OBSERVED),
        (nsw is not None, VaspConvergenceEvidenceCode.OUTCAR_NSW_OBSERVED),
        (
            max_electronic is not None,
            VaspConvergenceEvidenceCode.OSZICAR_MAX_ELECTRONIC_STEP_OBSERVED,
        ),
        (ionic_steps is not None, VaspConvergenceEvidenceCode.OSZICAR_IONIC_STEPS_OBSERVED),
    )
    for observed, code in observations:
        if observed:
            codes.add(code)
    return codes


def _assess_electronic(
    evidence: VaspConvergenceEvidence,
    codes: set[str],
) -> ConvergenceVerdict:
    if evidence.termination_observed is not True:
        codes.add(VaspConvergenceEvidenceCode.OUTPUT_INCOMPLETE.value)
        return ConvergenceVerdict.INDETERMINATE
    if not evidence.final_toten_observed:
        codes.add(VaspConvergenceEvidenceCode.FINAL_TOTEN_MISSING.value)
        return ConvergenceVerdict.INDETERMINATE
    if (
        evidence.electronic_step_limit is not None
        and evidence.max_electronic_steps is not None
        and evidence.max_electronic_steps >= evidence.electronic_step_limit
    ):
        codes.add(VaspConvergenceEvidenceCode.ELECTRONIC_LIMIT_EXHAUSTED.value)
        return ConvergenceVerdict.UNCONVERGED
    if evidence.electronic_ediff_reached:
        return ConvergenceVerdict.CONVERGED
    codes.add(VaspConvergenceEvidenceCode.ELECTRONIC_MARKER_MISSING.value)
    return ConvergenceVerdict.INDETERMINATE


def _assess_ionic(
    evidence: VaspConvergenceEvidence,
    expected_nsw: int,
    limit_mismatch: bool,
    codes: set[str],
) -> ConvergenceVerdict:
    if evidence.calculation_type not in _RELAX_TYPES:
        codes.add(VaspConvergenceEvidenceCode.IONIC_NOT_APPLICABLE.value)
        return ConvergenceVerdict.NOT_APPLICABLE
    if limit_mismatch:
        return ConvergenceVerdict.INDETERMINATE
    if evidence.termination_observed is not True:
        codes.add(VaspConvergenceEvidenceCode.OUTPUT_INCOMPLETE.value)
        return ConvergenceVerdict.INDETERMINATE
    if evidence.ionic_required_accuracy_reached:
        return ConvergenceVerdict.CONVERGED
    if evidence.ionic_steps is not None and evidence.ionic_steps >= expected_nsw:
        codes.add(VaspConvergenceEvidenceCode.IONIC_LIMIT_EXHAUSTED.value)
        return ConvergenceVerdict.UNCONVERGED
    codes.add(VaspConvergenceEvidenceCode.IONIC_MARKER_MISSING.value)
    return ConvergenceVerdict.INDETERMINATE


def _combine_verdicts(
    electronic: ConvergenceVerdict,
    ionic: ConvergenceVerdict,
) -> ConvergenceVerdict:
    if ConvergenceVerdict.UNCONVERGED in {electronic, ionic}:
        return ConvergenceVerdict.UNCONVERGED
    if electronic is ConvergenceVerdict.CONVERGED and ionic in {
        ConvergenceVerdict.CONVERGED,
        ConvergenceVerdict.NOT_APPLICABLE,
    }:
        return ConvergenceVerdict.CONVERGED
    return ConvergenceVerdict.INDETERMINATE


def _validate_assessment_identity(
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    evidence: VaspConvergenceEvidence,
) -> None:
    if evidence.calculation_id != calculation.id:
        raise VaspConvergenceError("convergence evidence belongs to another Calculation")
    if calculation.method_fingerprint_id != fingerprint.id:
        raise VaspConvergenceError("Calculation does not reference the supplied MethodFingerprint")
    if calculation.recipe_id != fingerprint.recipe.recipe_id:
        raise VaspConvergenceError("Calculation recipe does not match MethodFingerprint recipe")
    if evidence.recipe_id != calculation.recipe_id:
        raise VaspConvergenceError("convergence evidence recipe does not match Calculation")
    if evidence.calculation_type is not calculation.calculation_type:
        raise VaspConvergenceError(
            "convergence evidence CalculationType does not match Calculation"
        )
    spec = get_vasp_recipe_spec(calculation.recipe_id)
    if spec.calculation_type is not calculation.calculation_type:
        raise VaspConvergenceError("CalculationType does not match canonical recipe contract")
    if fingerprint.recipe.version != spec.version:
        raise VaspConvergenceError("MethodFingerprint recipe version is not canonical")


def _expected_ionic_step_limit(fingerprint: MethodFingerprint) -> int:
    recipe = fingerprint.recipe
    if recipe.recipe_id in _RELAX_RECIPE_IDS:
        default = 200
    elif recipe.recipe_id in _FREQUENCY_RECIPE_IDS:
        default = 1
    elif recipe.recipe_id in _STATIC_RECIPE_IDS:
        default = 0
    else:
        raise VaspConvergenceError(f"unsupported VASP convergence recipe: {recipe.recipe_id}")
    overrides = tuple(item.value for item in recipe.parameters if item.name == "NSW")
    if len(overrides) > 1:
        raise VaspConvergenceError("RecipeIdentity contains multiple NSW parameters")
    value = default if not overrides else overrides[0]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VaspConvergenceError("recipe NSW must be a non-negative integer")
    if recipe.recipe_id in _RELAX_RECIPE_IDS and value < 1:
        raise VaspConvergenceError("relaxation recipes require NSW >= 1")
    if recipe.recipe_id in _FREQUENCY_RECIPE_IDS and value != 1:
        raise VaspConvergenceError("frequency recipes require NSW=1")
    if recipe.recipe_id in _STATIC_RECIPE_IDS and value != 0:
        raise VaspConvergenceError("static recipes require NSW=0")
    return value


def _read_oszicar_evidence(
    *,
    root: Path,
    intake: VaspResultArtifactIntake,
    result: VaspResultDocument,
) -> tuple[int | None, int | None, int | None]:
    items = tuple(
        item for item in intake.files if item.source.role is VaspResultSourceRole.OSZICAR
    )
    if len(items) > 1:
        raise VaspConvergenceError("result intake contains multiple OSZICAR sources")
    if not items:
        return None, None, None
    values = _scan_oszicar_steps(
        path=_resolve_input_path(root=root, item=items[0]),
        item=items[0],
    )
    ionic_steps, final_electronic, _ = values
    if ionic_steps != result.ionic_steps:
        raise VaspConvergenceError("OSZICAR ionic-step evidence changed after result parsing")
    if final_electronic != result.electronic_steps:
        raise VaspConvergenceError(
            "OSZICAR final electronic-step evidence changed after result parsing"
        )
    return values


def _require_file(
    intake: VaspResultArtifactIntake,
    role: VaspResultSourceRole,
) -> VaspResultInputFile:
    matches = tuple(item for item in intake.files if item.source.role is role)
    if len(matches) != 1:
        raise VaspConvergenceError(f"result intake requires exactly one {role.value} source")
    return matches[0]


def _resolve_input_path(*, root: Path, item: VaspResultInputFile) -> Path:
    relative = PurePosixPath(item.local_relative_path)
    invalid = (
        relative.is_absolute()
        or item.local_relative_path != relative.as_posix()
        or ".." in relative.parts
        or item.local_relative_path in {"", "."}
    )
    if invalid:
        raise VaspConvergenceError(
            "convergence source path must be a normalized relative POSIX path"
        )
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise VaspConvergenceError("convergence source resolves outside project_root")
    if not path.is_file():
        raise VaspConvergenceError(
            f"convergence source file is missing for role {item.source.role.value!r}"
        )
    return path


def _scan_outcar_limits(
    *,
    path: Path,
    item: VaspResultInputFile,
) -> tuple[int | None, int | None]:
    digest = hashlib.sha256()
    size = 0
    nelm_values: set[int] = set()
    nsw_values: set[int] = set()
    with path.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            size += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace")
            nelm_values.update(int(match) for match in _NELM_PATTERN.findall(line))
            nsw_values.update(int(match) for match in _NSW_PATTERN.findall(line))
    _validate_integrity(item, size, digest.hexdigest())
    if len(nelm_values) > 1:
        raise VaspConvergenceError("OUTCAR contains multiple distinct NELM values")
    if len(nsw_values) > 1:
        raise VaspConvergenceError("OUTCAR contains multiple distinct NSW values")
    nelm = None if not nelm_values else next(iter(nelm_values))
    nsw = None if not nsw_values else next(iter(nsw_values))
    if nelm == 0:
        raise VaspConvergenceError("OUTCAR NELM must be positive")
    return nelm, nsw


def _scan_oszicar_steps(
    *,
    path: Path,
    item: VaspResultInputFile,
) -> tuple[int | None, int | None, int | None]:
    digest = hashlib.sha256()
    size = 0
    ionic_count = 0
    final_electronic: int | None = None
    max_electronic: int | None = None
    with path.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            size += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace")
            if _IONIC_STEP_PATTERN.match(line) is not None:
                ionic_count += 1
            match = _ELECTRONIC_STEP_PATTERN.match(line)
            if match is not None:
                value = int(match.group(1))
                final_electronic = value
                max_electronic = (
                    value
                    if max_electronic is None
                    else max(max_electronic, value)
                )
    _validate_integrity(item, size, digest.hexdigest())
    return ionic_count or None, final_electronic, max_electronic


def _validate_integrity(
    item: VaspResultInputFile,
    observed_size: int,
    observed_sha256: str,
) -> None:
    if observed_size != item.size_bytes:
        raise VaspConvergenceError(
            f"convergence source size changed for role {item.source.role.value!r}"
        )
    if observed_sha256 != item.source.sha256.lower():
        raise VaspConvergenceError(
            f"convergence source SHA-256 changed for role {item.source.role.value!r}"
        )
