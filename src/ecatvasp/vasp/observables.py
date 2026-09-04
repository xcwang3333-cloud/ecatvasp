"""Final force and magnetization enrichment for v0.5 Block 5.

The adapter consumes one exact managed result bundle and maps VASP-local atom
ordinals back to permanent ``atom_uid`` values through the immutable
``atom-index-map.json`` staged with the Calculation. It performs no convergence
classification, lifecycle mutation, persistence, or structure promotion.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from uuid import UUID

from ecatvasp.domain import ArtifactType, Calculation, MethodFingerprint, SpinTreatment
from ecatvasp.domain.ids import AtomUid
from ecatvasp.vasp.execution_plan import ExecutionPlan, StagingInput, StagingInputKind
from ecatvasp.vasp.result_intake import VaspResultArtifactIntake, VaspResultInputFile
from ecatvasp.vasp.results import (
    VaspCollinearMagnetization,
    VaspForceDataset,
    VaspNoncollinearMagnetization,
    VaspResultDocument,
    VaspResultSourceRole,
    VaspSiteForce,
    VaspSiteScalarMagnetization,
    VaspSiteVectorMagnetization,
)

VASP_FORCE_MAGNETIZATION_PARSER_NAME = "ecatvasp.vasp.force-magnetization-parser"
VASP_FORCE_MAGNETIZATION_PARSER_VERSION = "1"

_FLOAT_TOKEN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_FLOAT_PATTERN = re.compile(_FLOAT_TOKEN)
_FORCE_HEADER = re.compile(r"POSITION\s+TOTAL-FORCE\s*\(eV/Angst\)", re.IGNORECASE)
_MAG_HEADER = re.compile(r"^\s*magnetization\s*\(([xyz])\)\s*$", re.IGNORECASE)
_MAG_SITE_ROW = re.compile(r"^\s*(\d+)\s+(.+)$")
_MAG_TOTAL_ROW = re.compile(r"^\s*tot\s+(.+)$", re.IGNORECASE)
_CELL_MAG_LINE = re.compile(
    r"\bnumber\s+of\s+electron\b.*?\bmagnetization\b(.*)$",
    re.IGNORECASE,
)


class VaspObservableParseError(ValueError):
    """Raised when final observables cannot be mapped without guessing."""


class VaspObservableEvidenceCode(StrEnum):
    """Stable raw-observation codes emitted by the Block 5 enrichment layer."""

    ATOM_INDEX_MAP_VERIFIED = "observables.atom_index_map_verified"
    FINAL_FORCES = "observables.final_forces"
    CELL_MAGNETIZATION = "observables.cell_magnetization"
    COLLINEAR_SITE_MAGNETIZATION = "observables.collinear_site_magnetization"
    NONCOLLINEAR_SITE_MAGNETIZATION = "observables.noncollinear_site_magnetization"


@dataclass(frozen=True, slots=True)
class _AtomMapEntry:
    atom_uid: AtomUid
    poscar_index: int
    vasp_ordinal: int


@dataclass(frozen=True, slots=True)
class _MagTable:
    component: str
    generation: int
    site_values: tuple[float, ...]
    projected_total: float


@dataclass(frozen=True, slots=True)
class _OutcarObservables:
    final_forces: tuple[tuple[float, float, float], ...] | None
    cell_magnetization: tuple[float, ...] | None
    magnetization_tables: tuple[_MagTable, ...]
    evidence_codes: tuple[str, ...]


def parse_vasp_forces_magnetization(
    *,
    project_root: Path | str,
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    plan: ExecutionPlan,
    intake: VaspResultArtifactIntake,
    result: VaspResultDocument,
) -> VaspResultDocument:
    """Return an immutable result enriched with final forces and spin magnetization."""

    if result.forces is not None or result.magnetization is not None:
        raise VaspObservableParseError("result is already enriched with observables")
    _validate_identity(
        calculation=calculation,
        fingerprint=fingerprint,
        plan=plan,
        intake=intake,
        result=result,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise VaspObservableParseError("project_root must be an existing directory")

    atom_map = _load_atom_index_map(
        root=root,
        calculation=calculation,
        plan=plan,
    )
    outcar = _require_result_file(intake, VaspResultSourceRole.OUTCAR)
    parsed = _scan_outcar(
        path=_resolve_result_path(root=root, item=outcar),
        item=outcar,
        atom_count=len(atom_map),
    )
    forces = _force_dataset(parsed.final_forces, atom_map)
    magnetization = _magnetization_dataset(
        spin_treatment=fingerprint.method.spin_treatment,
        parsed=parsed,
        atom_map=atom_map,
    )
    evidence = set(result.evidence_codes)
    evidence.add(VaspObservableEvidenceCode.ATOM_INDEX_MAP_VERIFIED.value)
    evidence.update(parsed.evidence_codes)
    if isinstance(magnetization, VaspCollinearMagnetization) and magnetization.site_moments:
        evidence.add(VaspObservableEvidenceCode.COLLINEAR_SITE_MAGNETIZATION.value)
    if isinstance(magnetization, VaspNoncollinearMagnetization) and magnetization.site_moments:
        evidence.add(VaspObservableEvidenceCode.NONCOLLINEAR_SITE_MAGNETIZATION.value)
    return replace(
        result,
        forces=forces,
        magnetization=magnetization,
        evidence_codes=tuple(sorted(evidence)),
    )


def _validate_identity(
    *,
    calculation: Calculation,
    fingerprint: MethodFingerprint,
    plan: ExecutionPlan,
    intake: VaspResultArtifactIntake,
    result: VaspResultDocument,
) -> None:
    if calculation.method_fingerprint_id != fingerprint.id:
        raise VaspObservableParseError(
            "Calculation does not reference the supplied MethodFingerprint"
        )
    if calculation.recipe_id != fingerprint.recipe.recipe_id:
        raise VaspObservableParseError("Calculation recipe does not match MethodFingerprint")
    if plan.calculation_id != calculation.id or intake.calculation_id != calculation.id:
        raise VaspObservableParseError("plan/intake belongs to another Calculation")
    if plan.recipe_id != calculation.recipe_id or intake.recipe_id != calculation.recipe_id:
        raise VaspObservableParseError("plan/intake recipe does not match Calculation")
    if intake.calculation_type is not calculation.calculation_type:
        raise VaspObservableParseError("result intake CalculationType does not match Calculation")
    if result.calculation_type is not calculation.calculation_type:
        raise VaspObservableParseError("parsed result CalculationType does not match Calculation")
    if intake.plan_hash != plan.plan_hash:
        raise VaspObservableParseError("result intake does not reference the exact ExecutionPlan")
    if intake.input_manifest_hash != plan.input_manifest_sha256:
        raise VaspObservableParseError("result intake input manifest does not match ExecutionPlan")
    if result.sources != intake.sources:
        raise VaspObservableParseError("parsed result sources do not match exact result intake")


def _load_atom_index_map(
    *,
    root: Path,
    calculation: Calculation,
    plan: ExecutionPlan,
) -> tuple[_AtomMapEntry, ...]:
    atom_map_input = _require_staging_input(plan, "atom_index_map")
    poscar_input = _require_staging_input(plan, "poscar")
    _validate_staging_role(
        item=atom_map_input,
        artifact_type=ArtifactType.DERIVED_DATASET,
        kind=StagingInputKind.METADATA,
        target="atom-index-map.json",
    )
    _validate_staging_role(
        item=poscar_input,
        artifact_type=ArtifactType.POSCAR,
        kind=StagingInputKind.VASP_INPUT,
        target="POSCAR",
    )
    atom_map_bytes = _read_staging_bytes(root=root, item=atom_map_input)
    _read_staging_bytes(root=root, item=poscar_input)
    try:
        payload = json.loads(atom_map_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VaspObservableParseError("atom-index-map.json is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise VaspObservableParseError("atom-index-map.json root must be an object")
    if payload.get("format") != "ecatvasp-v03-atom-index-map" or payload.get("version") != 1:
        raise VaspObservableParseError("unsupported atom-index-map.json format/version")
    if payload.get("structure_snapshot_id") != str(calculation.input_structure_snapshot_id):
        raise VaspObservableParseError("atom index map belongs to another input snapshot")
    if payload.get("poscar_sha256") != poscar_input.sha256:
        raise VaspObservableParseError("atom index map does not bind the exact staged POSCAR")

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise VaspObservableParseError("atom index map requires non-empty entries")
    entries: list[_AtomMapEntry] = []
    for expected_index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise VaspObservableParseError("atom index map entry must be an object")
        poscar_index = raw.get("poscar_index")
        vasp_ordinal = raw.get("vasp_ordinal")
        if poscar_index != expected_index or vasp_ordinal != expected_index + 1:
            raise VaspObservableParseError("atom index map indices/ordinals are not contiguous")
        raw_uid = raw.get("atom_uid")
        if not isinstance(raw_uid, str):
            raise VaspObservableParseError("atom index map atom_uid must be a UUID string")
        try:
            atom_uid = AtomUid(UUID(raw_uid))
        except ValueError as error:
            raise VaspObservableParseError("atom index map atom_uid is not a UUID") from error
        entries.append(
            _AtomMapEntry(
                atom_uid=atom_uid,
                poscar_index=expected_index,
                vasp_ordinal=expected_index + 1,
            )
        )
    atom_uids = tuple(item.atom_uid for item in entries)
    if len(atom_uids) != len(set(atom_uids)):
        raise VaspObservableParseError("atom index map atom_uids must be unique")
    species_counts = payload.get("species_counts")
    if not isinstance(species_counts, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in species_counts
    ):
        raise VaspObservableParseError("atom index map species_counts are invalid")
    if sum(species_counts) != len(entries):
        raise VaspObservableParseError("atom index map species_counts do not match entries")
    return tuple(entries)


def _validate_staging_role(
    *,
    item: StagingInput,
    artifact_type: ArtifactType,
    kind: StagingInputKind,
    target: str,
) -> None:
    if item.artifact_type is not artifact_type or item.kind is not kind:
        raise VaspObservableParseError(f"staging role {item.role!r} has incompatible metadata")
    if item.target_relative_path != target:
        raise VaspObservableParseError(f"staging role {item.role!r} has unexpected target path")


def _require_staging_input(plan: ExecutionPlan, role: str) -> StagingInput:
    matches = tuple(item for item in plan.staging_inputs if item.role == role)
    if len(matches) != 1:
        raise VaspObservableParseError(f"ExecutionPlan requires exactly one {role} input")
    return matches[0]


def _read_staging_bytes(*, root: Path, item: StagingInput) -> bytes:
    path = _resolve_relative_path(root, item.source_relative_path, "staging input")
    body = path.read_bytes()
    if len(body) != item.size_bytes:
        raise VaspObservableParseError(f"staging input size changed for role {item.role!r}")
    if hashlib.sha256(body).hexdigest() != item.sha256.lower():
        raise VaspObservableParseError(f"staging input SHA-256 changed for role {item.role!r}")
    return body


def _require_result_file(
    intake: VaspResultArtifactIntake,
    role: VaspResultSourceRole,
) -> VaspResultInputFile:
    matches = tuple(item for item in intake.files if item.source.role is role)
    if len(matches) != 1:
        raise VaspObservableParseError(f"result intake requires exactly one {role.value} source")
    return matches[0]


def _resolve_result_path(*, root: Path, item: VaspResultInputFile) -> Path:
    return _resolve_relative_path(root, item.local_relative_path, "result source")


def _resolve_relative_path(root: Path, value: str, field_name: str) -> Path:
    relative = PurePosixPath(value)
    invalid = (
        relative.is_absolute()
        or value != relative.as_posix()
        or ".." in relative.parts
        or value in {"", "."}
    )
    if invalid:
        raise VaspObservableParseError(
            f"{field_name} path must be a normalized relative POSIX path"
        )
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise VaspObservableParseError(f"{field_name} resolves outside project_root")
    if not path.is_file():
        raise VaspObservableParseError(f"{field_name} file is missing")
    return path


def _scan_outcar(
    *,
    path: Path,
    item: VaspResultInputFile,
    atom_count: int,
) -> _OutcarObservables:
    digest = hashlib.sha256()
    observed_size = 0
    force_blocks: list[tuple[tuple[float, float, float], ...]] = []
    active_force: list[tuple[float, float, float]] | None = None
    active_force_closed = False
    mag_tables: list[_MagTable] = []
    active_mag_component: str | None = None
    active_mag_generation = 0
    active_mag_values: list[tuple[int, float]] = []
    active_mag_total: float | None = None
    mag_generation = 0
    cell_magnetization: tuple[float, ...] | None = None

    with path.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            observed_size += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace")

            if _FORCE_HEADER.search(line) is not None:
                if active_force is not None:
                    force_blocks.append(tuple(active_force))
                active_force = []
                active_force_closed = False
                continue
            if active_force is not None and not active_force_closed:
                stripped = line.strip()
                if stripped and set(stripped) <= {"-", " "}:
                    if active_force:
                        active_force_closed = True
                    continue
                vector = _force_vector(line)
                if vector is not None:
                    active_force.append(vector)
                    continue
                if active_force:
                    active_force_closed = True

            mag_match = _MAG_HEADER.match(line)
            if mag_match is not None:
                _finish_mag_table(
                    tables=mag_tables,
                    component=active_mag_component,
                    generation=active_mag_generation,
                    values=active_mag_values,
                    projected_total=active_mag_total,
                    atom_count=atom_count,
                )
                component = mag_match.group(1).lower()
                if component == "x":
                    mag_generation += 1
                elif mag_generation == 0:
                    raise VaspObservableParseError(
                        "magnetization y/z table appears before magnetization x"
                    )
                active_mag_component = component
                active_mag_generation = mag_generation
                active_mag_values = []
                active_mag_total = None
                continue
            if active_mag_component is not None:
                total_match = _MAG_TOTAL_ROW.match(line)
                if total_match is not None:
                    active_mag_total = _last_finite_float(
                        total_match.group(1),
                        "magnetization total",
                    )
                    continue
                if active_mag_total is None:
                    site_match = _MAG_SITE_ROW.match(line)
                    if site_match is not None:
                        ordinal = int(site_match.group(1))
                        values = _float_values(site_match.group(2))
                        if values:
                            active_mag_values.append((ordinal, values[-1]))
                            continue

            cell_match = _CELL_MAG_LINE.search(line)
            if cell_match is not None:
                values = tuple(_float_values(cell_match.group(1)))
                if values:
                    cell_magnetization = values

    if active_force is not None:
        force_blocks.append(tuple(active_force))
    _finish_mag_table(
        tables=mag_tables,
        component=active_mag_component,
        generation=active_mag_generation,
        values=active_mag_values,
        projected_total=active_mag_total,
        atom_count=atom_count,
    )
    _validate_result_integrity(item, observed_size, digest.hexdigest())

    final_forces: tuple[tuple[float, float, float], ...] | None = None
    evidence: set[str] = set()
    if force_blocks:
        final_forces = force_blocks[-1]
        if len(final_forces) != atom_count:
            raise VaspObservableParseError(
                "final OUTCAR force block is incomplete; earlier forces are not reused"
            )
        evidence.add(VaspObservableEvidenceCode.FINAL_FORCES.value)
    if cell_magnetization is not None:
        evidence.add(VaspObservableEvidenceCode.CELL_MAGNETIZATION.value)
    return _OutcarObservables(
        final_forces=final_forces,
        cell_magnetization=cell_magnetization,
        magnetization_tables=tuple(mag_tables),
        evidence_codes=tuple(sorted(evidence)),
    )


def _force_vector(line: str) -> tuple[float, float, float] | None:
    values = _float_values(line)
    if len(values) < 6:
        return None
    return values[-3], values[-2], values[-1]


def _finish_mag_table(
    *,
    tables: list[_MagTable],
    component: str | None,
    generation: int,
    values: list[tuple[int, float]],
    projected_total: float | None,
    atom_count: int,
) -> None:
    if component is None:
        return
    if not values and projected_total is None:
        return
    if len(values) != atom_count or projected_total is None:
        raise VaspObservableParseError(f"magnetization ({component}) table is incomplete")
    ordinals = tuple(item[0] for item in values)
    if ordinals != tuple(range(1, atom_count + 1)):
        raise VaspObservableParseError(
            f"magnetization ({component}) ordinals are not contiguous"
        )
    tables.append(
        _MagTable(
            component=component,
            generation=generation,
            site_values=tuple(item[1] for item in values),
            projected_total=projected_total,
        )
    )


def _force_dataset(
    values: tuple[tuple[float, float, float], ...] | None,
    atom_map: tuple[_AtomMapEntry, ...],
) -> VaspForceDataset | None:
    if values is None:
        return None
    if len(values) != len(atom_map):
        raise VaspObservableParseError("force count does not match atom identity map")
    return VaspForceDataset(
        site_forces=tuple(
            VaspSiteForce(
                atom_uid=entry.atom_uid,
                vector_ev_per_angstrom=vector,
            )
            for entry, vector in zip(atom_map, values, strict=True)
        )
    )


def _magnetization_dataset(
    *,
    spin_treatment: SpinTreatment,
    parsed: _OutcarObservables,
    atom_map: tuple[_AtomMapEntry, ...],
) -> VaspCollinearMagnetization | VaspNoncollinearMagnetization | None:
    tables = parsed.magnetization_tables
    cell = parsed.cell_magnetization
    if spin_treatment is SpinTreatment.UNPOLARIZED:
        if tables:
            raise VaspObservableParseError(
                "unpolarized MethodFingerprint contradicts site magnetization tables"
            )
        if cell is not None and any(abs(value) > 1e-10 for value in cell):
            raise VaspObservableParseError(
                "unpolarized MethodFingerprint contradicts nonzero cell magnetization"
            )
        return None
    if spin_treatment is SpinTreatment.COLLINEAR:
        return _collinear_magnetization(tables=tables, cell=cell, atom_map=atom_map)
    return _noncollinear_magnetization(tables=tables, cell=cell, atom_map=atom_map)


def _collinear_magnetization(
    *,
    tables: tuple[_MagTable, ...],
    cell: tuple[float, ...] | None,
    atom_map: tuple[_AtomMapEntry, ...],
) -> VaspCollinearMagnetization | None:
    if cell is not None and len(cell) != 1:
        raise VaspObservableParseError("collinear cell magnetization must be scalar")
    if any(item.component != "x" for item in tables):
        raise VaspObservableParseError(
            "collinear MethodFingerprint contradicts magnetization y/z tables"
        )
    table = tables[-1] if tables else None
    if table is None and cell is None:
        return None
    site_moments: tuple[VaspSiteScalarMagnetization, ...] = ()
    projected_total = None
    if table is not None:
        site_moments = tuple(
            VaspSiteScalarMagnetization(entry.atom_uid, value)
            for entry, value in zip(atom_map, table.site_values, strict=True)
        )
        projected_total = table.projected_total
    return VaspCollinearMagnetization(
        site_moments=site_moments,
        projected_total_mu_b=projected_total,
        cell_total_mu_b=None if cell is None else cell[0],
    )


def _noncollinear_magnetization(
    *,
    tables: tuple[_MagTable, ...],
    cell: tuple[float, ...] | None,
    atom_map: tuple[_AtomMapEntry, ...],
) -> VaspNoncollinearMagnetization | None:
    if cell is not None and len(cell) != 3:
        raise VaspObservableParseError(
            "noncollinear cell magnetization must contain three components"
        )
    if not tables and cell is None:
        return None
    site_moments: tuple[VaspSiteVectorMagnetization, ...] = ()
    projected_total = None
    if tables:
        final_generation = max(item.generation for item in tables)
        final_tables = tuple(item for item in tables if item.generation == final_generation)
        by_component = {item.component: item for item in final_tables}
        if set(by_component) != {"x", "y", "z"} or len(final_tables) != 3:
            raise VaspObservableParseError(
                "final noncollinear magnetization group requires x/y/z tables together"
            )
        x_table = by_component["x"]
        y_table = by_component["y"]
        z_table = by_component["z"]
        site_moments = tuple(
            VaspSiteVectorMagnetization(
                atom_uid=entry.atom_uid,
                projected_moment_mu_b=(
                    x_table.site_values[index],
                    y_table.site_values[index],
                    z_table.site_values[index],
                ),
            )
            for index, entry in enumerate(atom_map)
        )
        projected_total = (
            x_table.projected_total,
            y_table.projected_total,
            z_table.projected_total,
        )
    return VaspNoncollinearMagnetization(
        site_moments=site_moments,
        projected_total_mu_b=projected_total,
        cell_total_mu_b=None if cell is None else (cell[0], cell[1], cell[2]),
    )


def _float_values(text: str) -> list[float]:
    return [_finite_float(token) for token in _FLOAT_PATTERN.findall(text)]


def _last_finite_float(text: str, field_name: str) -> float:
    values = _float_values(text)
    if not values:
        raise VaspObservableParseError(f"{field_name} contains no finite value")
    return values[-1]


def _finite_float(token: str) -> float:
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except ValueError as error:
        raise VaspObservableParseError("OUTCAR numeric token is invalid") from error
    if not math.isfinite(value):
        raise VaspObservableParseError("OUTCAR numeric value must be finite")
    return value


def _validate_result_integrity(
    item: VaspResultInputFile,
    observed_size: int,
    observed_sha256: str,
) -> None:
    if observed_size != item.size_bytes:
        raise VaspObservableParseError(
            f"result source size changed for role {item.source.role.value!r}"
        )
    if observed_sha256 != item.source.sha256.lower():
        raise VaspObservableParseError(
            f"result source SHA-256 changed for role {item.source.role.value!r}"
        )
