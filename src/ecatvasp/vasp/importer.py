"""Import existing VASP calculation folders into ECatVASP domain objects."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    Lattice,
    MethodFingerprint,
    Project,
    RetrievalPolicy,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    canonical_sha256,
    new_atom_uid,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.structures.identity import GeometrySite, validate_identity_preserving_revision

_IMPORTER_NAME = "ecatvasp.vasp.existing-folder-importer"
_IMPORTER_VERSION = "1"
_PARSED_RESULT_FORMAT = "ecatvasp-parsed-vasp-result"
_HASH_LARGE_FILE_THRESHOLD = 64 * 1024 * 1024

_ARTIFACT_FILENAMES: tuple[tuple[str, ArtifactType, RetrievalPolicy], ...] = (
    ("POSCAR", ArtifactType.POSCAR, RetrievalPolicy.ALWAYS),
    ("CONTCAR", ArtifactType.CONTCAR, RetrievalPolicy.ALWAYS),
    ("INCAR", ArtifactType.INCAR, RetrievalPolicy.ALWAYS),
    ("KPOINTS", ArtifactType.KPOINTS, RetrievalPolicy.ALWAYS),
    ("POTCAR.spec", ArtifactType.POTCAR_SPEC, RetrievalPolicy.ALWAYS),
    ("OUTCAR", ArtifactType.OUTCAR, RetrievalPolicy.ALWAYS),
    ("OSZICAR", ArtifactType.OSZICAR, RetrievalPolicy.ALWAYS),
    ("vasprun.xml", ArtifactType.VASPRUN_XML, RetrievalPolicy.ON_DEMAND),
    ("vaspout.h5", ArtifactType.VASPOUT_H5, RetrievalPolicy.ON_DEMAND),
    ("CHGCAR", ArtifactType.CHGCAR, RetrievalPolicy.ON_DEMAND),
    ("AECCAR0", ArtifactType.AECCAR0, RetrievalPolicy.ON_DEMAND),
    ("AECCAR1", ArtifactType.AECCAR1, RetrievalPolicy.ON_DEMAND),
    ("AECCAR2", ArtifactType.AECCAR2, RetrievalPolicy.ON_DEMAND),
    ("WAVECAR", ArtifactType.WAVECAR, RetrievalPolicy.ON_DEMAND),
    ("DOSCAR", ArtifactType.DOSCAR, RetrievalPolicy.ON_DEMAND),
)


class VaspImportError(ValueError):
    """Raised when an existing VASP folder cannot be imported without guessing."""


@dataclass(frozen=True, slots=True)
class ParsedVaspResult:
    """Small parser-neutral result extracted during the v0.1 import vertical slice."""

    calculation_type: CalculationType
    scientific_status: CalculationScientificStatus
    total_energy_ev: float | None
    fermi_energy_ev: float | None
    max_force_ev_per_angstrom: float | None
    electronic_converged: bool | None
    ionic_converged: bool | None
    ionic_steps: int | None
    electronic_steps: int | None
    vasp_version: str | None


@dataclass(frozen=True, slots=True)
class VaspFolderInspection:
    """Detected source files and warnings before domain materialization."""

    folder: Path
    detected_files: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExistingVaspImport:
    """Domain objects produced from one existing calculation folder."""

    updated_variant: StructureVariant
    input_snapshot: StructureSnapshot
    final_snapshot: StructureSnapshot
    calculation: Calculation
    execution_attempt: ExecutionAttempt
    inspection: VaspFolderInspection
    artifacts: tuple[Artifact, ...]
    parsed_result: ParsedVaspResult
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


@dataclass(frozen=True, slots=True)
class _ParsedGeometry:
    lattice: Lattice
    sites: tuple[GeometrySite, ...]


def inspect_vasp_folder(folder: Path | str) -> VaspFolderInspection:
    """Detect supported artifacts without interpreting scientific semantics."""

    root = Path(folder)
    if not root.is_dir():
        raise VaspImportError("VASP import source must be an existing directory")

    detected = tuple(
        filename for filename, _, _ in _ARTIFACT_FILENAMES if (root / filename).is_file()
    )
    missing_required = tuple(name for name in ("POSCAR", "INCAR", "OUTCAR") if name not in detected)
    if missing_required:
        raise VaspImportError("VASP import requires " + ", ".join(missing_required))

    warnings: list[str] = []
    if (root / "POTCAR").is_file():
        warnings.append(
            "POTCAR detected but is not imported or redistributed; "
            "the caller-supplied MethodFingerprint remains authoritative"
        )
    return VaspFolderInspection(folder=root, detected_files=detected, warnings=tuple(warnings))


def import_existing_vasp_folder(
    *,
    folder: Path | str,
    project_root: Path | str,
    project: Project,
    variant: StructureVariant,
    method_fingerprint: MethodFingerprint,
) -> ExistingVaspImport:
    """Materialize one existing VASP run into current v0.1 domain objects.

    The v0.1 importer deliberately requires a caller-supplied MethodFingerprint.
    It never guesses POTCAR identity, DFT+U, dispersion, or other physical-method
    semantics from incomplete files.
    """

    inspection = inspect_vasp_folder(folder)
    root = inspection.folder
    incar = _parse_incar((root / "INCAR").read_text(encoding="utf-8", errors="replace"))
    _validate_fingerprint_against_incar(method_fingerprint, incar)
    calculation_type = _infer_calculation_type(incar)
    if calculation_type not in {CalculationType.RELAX, CalculationType.STATIC}:
        raise VaspImportError(
            "v0.1 existing-folder import supports relax and static calculations only"
        )

    input_geometry = _parse_poscar((root / "POSCAR").read_text(encoding="utf-8"))
    input_snapshot = _new_snapshot(
        input_geometry,
        label=f"{variant.name} imported POSCAR",
        origin=StructureOrigin.IMPORTED,
    )

    contcar_path = root / "CONTCAR"
    if contcar_path.is_file():
        final_geometry = _parse_poscar(contcar_path.read_text(encoding="utf-8"))
        final_snapshot = _propagate_vasp_order(
            source=input_snapshot,
            target=final_geometry,
            label=f"{variant.name} imported CONTCAR",
        )
    elif calculation_type is CalculationType.RELAX:
        raise VaspImportError("relax import requires CONTCAR to establish the final structure")
    else:
        final_snapshot = input_snapshot

    outcar_text = (root / "OUTCAR").read_text(encoding="utf-8", errors="replace")
    oszicar_text = _read_optional_text(root / "OSZICAR")
    parsed = _parse_result(
        calculation_type=calculation_type,
        outcar_text=outcar_text,
        oszicar_text=oszicar_text,
    )
    expected_version = method_fingerprint.method.engine_version
    if (
        expected_version is not None
        and parsed.vasp_version is not None
        and expected_version != parsed.vasp_version
    ):
        raise VaspImportError(
            "OUTCAR VASP version does not match the caller-supplied MethodFingerprint"
        )

    calculation = Calculation(
        project_id=project.id,
        calculation_type=calculation_type,
        input_structure_snapshot_id=input_snapshot.id,
        recipe_id=method_fingerprint.recipe.recipe_id,
        method_fingerprint_id=method_fingerprint.id,
        status=parsed.scientific_status,
        slug=f"imported-{calculation_type.value}",
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )

    artifacts = list(_source_artifacts(root=root, attempt=attempt))
    parsed_artifact = _write_parsed_result_artifact(
        project_root=Path(project_root),
        calculation=calculation,
        attempt=attempt,
        parsed=parsed,
    )
    artifacts.append(parsed_artifact)

    updated_variant = replace(
        variant,
        current_structure_snapshot_id=final_snapshot.id,
    )

    outcar_artifact = next(
        artifact for artifact in artifacts if artifact.artifact_type is ArtifactType.OUTCAR
    )
    provenance_records = (
        ProvenanceRecord(
            subject_id=calculation.id,
            tool=_IMPORTER_NAME,
            tool_version=_IMPORTER_VERSION,
            method_fingerprint_id=method_fingerprint.id,
        ),
        ProvenanceRecord(
            subject_id=parsed_artifact.id,
            tool=_IMPORTER_NAME,
            tool_version=_IMPORTER_VERSION,
            parameters_hash=canonical_sha256(parsed),
            method_fingerprint_id=method_fingerprint.id,
        ),
    )
    dependency_records = (
        DependencyRecord(
            upstream_id=input_snapshot.id,
            downstream_id=calculation.id,
            kind=DependencyKind.SCIENTIFIC,
            role="input_structure",
            recorded_hash=scientific_hash(input_snapshot),
        ),
        DependencyRecord(
            upstream_id=calculation.id,
            downstream_id=parsed_artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="calculation_context",
            recorded_hash=scientific_hash(calculation),
        ),
        DependencyRecord(
            upstream_id=outcar_artifact.id,
            downstream_id=parsed_artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="parsed_from_outcar",
            recorded_hash=scientific_hash(outcar_artifact),
        ),
    )

    return ExistingVaspImport(
        updated_variant=updated_variant,
        input_snapshot=input_snapshot,
        final_snapshot=final_snapshot,
        calculation=calculation,
        execution_attempt=attempt,
        inspection=inspection,
        artifacts=tuple(artifacts),
        parsed_result=parsed,
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def _parse_incar(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("!", 1)[0].split("#", 1)[0].strip()
        if not line:
            continue
        for segment in line.split(";"):
            if "=" not in segment:
                continue
            key, value = segment.split("=", 1)
            normalized_key = key.strip().upper()
            if normalized_key:
                values[normalized_key] = value.strip()
    return values


def _validate_fingerprint_against_incar(
    fingerprint: MethodFingerprint,
    incar: dict[str, str],
) -> None:
    """Reject contradictions between explicit INCAR values and supplied scientific identity."""

    protocol_checks: tuple[tuple[str, object], ...] = (
        ("ENCUT", fingerprint.protocol.encut_ev),
        ("EDIFF", fingerprint.protocol.ediff_ev),
        ("EDIFFG", fingerprint.protocol.ediffg_ev_per_angstrom),
        ("ISMEAR", fingerprint.protocol.ismear),
        ("SIGMA", fingerprint.protocol.sigma_ev),
        ("PREC", fingerprint.protocol.precision),
    )
    for key, expected in protocol_checks:
        _check_incar_scalar(incar, key=key, expected=expected)

    spin = fingerprint.method.spin_treatment.value
    if spin == "unpolarized":
        _check_incar_scalar(incar, key="ISPIN", expected=1)
    elif spin == "collinear":
        _check_incar_scalar(incar, key="ISPIN", expected=2)

    for parameter in fingerprint.method.extra_parameters:
        _check_incar_scalar(incar, key=parameter.name, expected=parameter.value)
    for parameter in fingerprint.protocol.extra_parameters:
        _check_incar_scalar(incar, key=parameter.name, expected=parameter.value)
    for parameter in fingerprint.recipe.parameters:
        _check_incar_scalar(incar, key=parameter.name, expected=parameter.value)


def _check_incar_scalar(
    incar: dict[str, str],
    *,
    key: str,
    expected: object,
) -> None:
    raw = incar.get(key.upper())
    if raw is None or expected is None:
        return
    token = raw.split()[0]
    if isinstance(expected, bool):
        normalized = token.strip().upper()
        actual = normalized in {".TRUE.", "TRUE", "T"}
        if normalized not in {".TRUE.", "TRUE", "T", ".FALSE.", "FALSE", "F"}:
            raise VaspImportError(f"cannot parse boolean INCAR value for {key}: {raw}")
        matches = actual is expected
    elif isinstance(expected, int):
        try:
            actual_number = int(float(token))
        except ValueError as error:
            raise VaspImportError(f"cannot parse integer INCAR value for {key}: {raw}") from error
        matches = actual_number == expected
    elif isinstance(expected, float):
        try:
            actual_float = float(token)
        except ValueError as error:
            raise VaspImportError(f"cannot parse numeric INCAR value for {key}: {raw}") from error
        matches = math.isclose(actual_float, expected, rel_tol=1e-10, abs_tol=1e-12)
    elif isinstance(expected, str):
        matches = token.casefold() == expected.casefold()
    else:
        return
    if not matches:
        raise VaspImportError(
            f"INCAR {key} contradicts the caller-supplied MethodFingerprint"
        )


def _infer_calculation_type(incar: dict[str, str]) -> CalculationType:
    ibrion = _parse_int(incar.get("IBRION"), default=-1)
    nsw = _parse_int(incar.get("NSW"), default=0)
    if ibrion in {5, 6}:
        return CalculationType.FREQUENCY
    if nsw > 0:
        return CalculationType.RELAX
    return CalculationType.STATIC


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    token = value.split()[0]
    try:
        return int(float(token))
    except ValueError as error:
        raise VaspImportError(f"cannot parse integer INCAR value: {value}") from error


def _parse_poscar(text: str) -> _ParsedGeometry:
    lines = [line.rstrip() for line in text.splitlines()]
    if len(lines) < 8:
        raise VaspImportError("POSCAR/CONTCAR is too short")

    scale_tokens = lines[1].split()
    if len(scale_tokens) != 1:
        raise VaspImportError("v0.1 importer supports a single POSCAR scale factor")
    try:
        scale = float(scale_tokens[0])
    except ValueError as error:
        raise VaspImportError("invalid POSCAR scale factor") from error
    if not math.isfinite(scale) or scale <= 0:
        raise VaspImportError("v0.1 importer requires a finite positive POSCAR scale factor")

    vector_a = _parse_vector(lines[2], scale=scale)
    vector_b = _parse_vector(lines[3], scale=scale)
    vector_c = _parse_vector(lines[4], scale=scale)
    lattice = Lattice(vectors=(vector_a, vector_b, vector_c))

    element_tokens = lines[5].split()
    if not element_tokens or all(_looks_numeric(token) for token in element_tokens):
        raise VaspImportError("v0.1 importer requires VASP5-style element symbols in POSCAR")
    try:
        counts = tuple(int(token) for token in lines[6].split())
    except ValueError as error:
        raise VaspImportError("invalid POSCAR element counts") from error
    if len(counts) != len(element_tokens) or any(count < 1 for count in counts):
        raise VaspImportError("POSCAR element symbols/counts are inconsistent")

    cursor = 7
    if lines[cursor].strip().lower().startswith("s"):
        cursor += 1
    if cursor >= len(lines):
        raise VaspImportError("POSCAR is missing its coordinate mode")
    mode = lines[cursor].strip().lower()
    cursor += 1

    elements = tuple(
        element
        for element, count in zip(element_tokens, counts, strict=True)
        for _ in range(count)
    )
    if len(lines) < cursor + len(elements):
        raise VaspImportError("POSCAR has fewer coordinate rows than declared atoms")

    coordinates = tuple(
        _parse_coordinate_row(lines[cursor + index])
        for index in range(len(elements))
    )
    if mode.startswith("d"):
        fractional = coordinates
    elif mode.startswith("c") or mode.startswith("k"):
        scaled_cartesian = tuple(
            (coord[0] * scale, coord[1] * scale, coord[2] * scale)
            for coord in coordinates
        )
        fractional = tuple(_cartesian_to_fractional(coord, lattice) for coord in scaled_cartesian)
    else:
        raise VaspImportError(f"unsupported POSCAR coordinate mode: {lines[cursor - 1].strip()}")

    sites = tuple(
        GeometrySite(element=element, fractional_coords=coords)
        for element, coords in zip(elements, fractional, strict=True)
    )
    return _ParsedGeometry(lattice=lattice, sites=sites)


def _parse_vector(line: str, *, scale: float) -> tuple[float, float, float]:
    values = _parse_coordinate_row(line)
    return (values[0] * scale, values[1] * scale, values[2] * scale)


def _parse_coordinate_row(line: str) -> tuple[float, float, float]:
    tokens = line.split()
    if len(tokens) < 3:
        raise VaspImportError("structure coordinate row requires three numeric components")
    try:
        values = tuple(float(token) for token in tokens[:3])
    except ValueError as error:
        raise VaspImportError("invalid numeric structure coordinate") from error
    if not all(math.isfinite(value) for value in values):
        raise VaspImportError("structure coordinates must be finite")
    return (values[0], values[1], values[2])


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _cartesian_to_fractional(
    cartesian: tuple[float, float, float],
    lattice: Lattice,
) -> tuple[float, float, float]:
    a, b, c = lattice.vectors
    det = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    if abs(det) < 1e-14:
        raise VaspImportError("POSCAR lattice is singular")

    x, y, z = cartesian
    f1 = (
        x * (b[1] * c[2] - b[2] * c[1])
        - y * (b[0] * c[2] - b[2] * c[0])
        + z * (b[0] * c[1] - b[1] * c[0])
    ) / det
    f2 = (
        -x * (a[1] * c[2] - a[2] * c[1])
        + y * (a[0] * c[2] - a[2] * c[0])
        - z * (a[0] * c[1] - a[1] * c[0])
    ) / det
    f3 = (
        x * (a[1] * b[2] - a[2] * b[1])
        - y * (a[0] * b[2] - a[2] * b[0])
        + z * (a[0] * b[1] - a[1] * b[0])
    ) / det
    return (f1, f2, f3)


def _new_snapshot(
    geometry: _ParsedGeometry,
    *,
    label: str,
    origin: StructureOrigin,
) -> StructureSnapshot:
    return StructureSnapshot(
        lattice=geometry.lattice,
        sites=tuple(
            StructureSite(
                atom_uid=new_atom_uid(),
                element=site.element,
                fractional_coords=site.fractional_coords,
            )
            for site in geometry.sites
        ),
        label=label,
        origin=origin,
    )


def _propagate_vasp_order(
    *,
    source: StructureSnapshot,
    target: _ParsedGeometry,
    label: str,
) -> StructureSnapshot:
    if len(source.sites) != len(target.sites):
        raise VaspImportError("POSCAR and CONTCAR must contain the same number of atoms")
    sites: list[StructureSite] = []
    site_pairs = zip(source.sites, target.sites, strict=True)
    for index, (source_site, target_site) in enumerate(site_pairs):
        if source_site.element != target_site.element:
            raise VaspImportError(
                f"VASP order-preserving identity failed at atom {index}: "
                f"{source_site.element} != {target_site.element}"
            )
        sites.append(
            StructureSite(
                atom_uid=source_site.atom_uid,
                element=target_site.element,
                fractional_coords=target_site.fractional_coords,
            )
        )
    snapshot = StructureSnapshot(
        lattice=target.lattice,
        sites=tuple(sites),
        label=label,
        origin=StructureOrigin.RELAXED,
        parent_snapshot_id=source.id,
        periodic=source.periodic,
    )
    validate_identity_preserving_revision(source=source, target=snapshot)
    return snapshot


def _parse_result(
    *,
    calculation_type: CalculationType,
    outcar_text: str,
    oszicar_text: str | None,
) -> ParsedVaspResult:
    electronic_converged = "aborting loop because EDIFF is reached" in outcar_text
    ionic_marker = "reached required accuracy - stopping structural energy minimisation"
    ionic_converged: bool | None
    if calculation_type is CalculationType.RELAX:
        ionic_converged = ionic_marker in outcar_text
    else:
        ionic_converged = None

    scientifically_converged = electronic_converged and (
        calculation_type is not CalculationType.RELAX or bool(ionic_converged)
    )
    total_energy_ev = _last_float_match(
        outcar_text,
        re.compile(r"free\s+energy\s+TOTEN\s*=\s*([-+0-9.Ee]+)\s+eV"),
    )
    if total_energy_ev is None:
        status = CalculationScientificStatus.FAILED
    elif scientifically_converged:
        status = CalculationScientificStatus.CONVERGED
    else:
        status = CalculationScientificStatus.COMPLETED_UNCONVERGED

    return ParsedVaspResult(
        calculation_type=calculation_type,
        scientific_status=status,
        total_energy_ev=total_energy_ev,
        fermi_energy_ev=_last_float_match(
            outcar_text,
            re.compile(r"E-fermi\s*:\s*([-+0-9.Ee]+)"),
        ),
        max_force_ev_per_angstrom=_last_force_norm(outcar_text),
        electronic_converged=electronic_converged,
        ionic_converged=ionic_converged,
        ionic_steps=_count_ionic_steps(oszicar_text),
        electronic_steps=_last_electronic_iteration(oszicar_text),
        vasp_version=_parse_vasp_version(outcar_text),
    )


def _last_float_match(text: str, pattern: re.Pattern[str]) -> float | None:
    matches = pattern.findall(text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError as error:
        raise VaspImportError("matched VASP numeric output is not a valid float") from error


def _parse_vasp_version(text: str) -> str | None:
    match = re.search(r"\bvasp\.([0-9]+(?:\.[0-9]+)+)\b", text, re.IGNORECASE)
    return None if match is None else match.group(1)


def _last_force_norm(text: str) -> float | None:
    lines = text.splitlines()
    last_forces: list[tuple[float, float, float]] = []
    index = 0
    while index < len(lines):
        if "TOTAL-FORCE" not in lines[index]:
            index += 1
            continue
        index += 1
        while index < len(lines) and set(lines[index].strip()) <= {"-"}:
            index += 1
        block: list[tuple[float, float, float]] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped or (stripped and set(stripped) <= {"-"}):
                break
            tokens = stripped.split()
            if len(tokens) < 6:
                break
            try:
                force = (float(tokens[3]), float(tokens[4]), float(tokens[5]))
            except ValueError:
                break
            block.append(force)
            index += 1
        if block:
            last_forces = block
        index += 1
    if not last_forces:
        return None
    norms = (
        math.sqrt(sum(component * component for component in force))
        for force in last_forces
    )
    return max(norms)


def _count_ionic_steps(text: str | None) -> int | None:
    if text is None:
        return None
    count = sum(
        1 for line in text.splitlines() if re.match(r"^\s*\d+\s+F=", line) is not None
    )
    return count or None


def _last_electronic_iteration(text: str | None) -> int | None:
    if text is None:
        return None
    matches = re.findall(r"^\s*(?:DAV|RMM|CG|DMP):\s*(\d+)", text, flags=re.MULTILINE)
    return None if not matches else int(matches[-1])


def _read_optional_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _source_artifacts(
    *,
    root: Path,
    attempt: ExecutionAttempt,
) -> tuple[Artifact, ...]:
    artifacts: list[Artifact] = []
    for filename, artifact_type, retrieval_policy in _ARTIFACT_FILENAMES:
        path = root / filename
        if not path.is_file():
            continue
        size = path.stat().st_size
        always_hash = artifact_type in {
            ArtifactType.POSCAR,
            ArtifactType.CONTCAR,
            ArtifactType.INCAR,
            ArtifactType.KPOINTS,
            ArtifactType.POTCAR_SPEC,
            ArtifactType.OUTCAR,
            ArtifactType.OSZICAR,
        }
        digest = _sha256_file(path) if always_hash or size <= _HASH_LARGE_FILE_THRESHOLD else None
        artifacts.append(
            Artifact(
                artifact_type=artifact_type,
                producer=ExecutionAttemptProducerRef(attempt.id),
                availability=ArtifactAvailability.LOCAL,
                retrieval_policy=retrieval_policy,
                local_path=str(path.resolve()),
                size_bytes=size,
                sha256=digest,
            )
        )
    return tuple(artifacts)


def _write_parsed_result_artifact(
    *,
    project_root: Path,
    calculation: Calculation,
    attempt: ExecutionAttempt,
    parsed: ParsedVaspResult,
) -> Artifact:
    relative_path = Path("calculations") / str(calculation.id) / "parsed_result.json"
    absolute_path = project_root / relative_path
    payload = {
        "format": _PARSED_RESULT_FORMAT,
        "version": 1,
        "calculation_id": str(calculation.id),
        "execution_attempt_id": str(attempt.id),
        "calculation_type": parsed.calculation_type.value,
        "scientific_status": parsed.scientific_status.value,
        "total_energy_ev": parsed.total_energy_ev,
        "fermi_energy_ev": parsed.fermi_energy_ev,
        "max_force_ev_per_angstrom": parsed.max_force_ev_per_angstrom,
        "electronic_converged": parsed.electronic_converged,
        "ionic_converged": parsed.ionic_converged,
        "ionic_steps": parsed.ionic_steps,
        "electronic_steps": parsed.electronic_steps,
        "vasp_version": parsed.vasp_version,
    }
    content = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = absolute_path.with_name(f".{absolute_path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, absolute_path)
    return Artifact(
        artifact_type=ArtifactType.PARSED_RESULT,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative_path.as_posix(),
        size_bytes=absolute_path.stat().st_size,
        sha256=_sha256_file(absolute_path),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
