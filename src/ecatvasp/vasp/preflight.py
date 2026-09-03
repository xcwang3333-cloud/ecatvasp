"""Global fail-closed identifiers for v0.3 VASP preparation and reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import NoReturn


class VaspFailClosedCode(StrEnum):
    """Stable machine-readable identifiers for fail-closed VASP validation."""

    ENCUT_NOT_LOCKED = "ENCUT_NOT_LOCKED"
    POTCAR_IDENTITY_MISSING = "POTCAR_IDENTITY_MISSING"
    POTCAR_FAMILY_MISMATCH = "POTCAR_FAMILY_MISMATCH"
    POTCAR_SYMBOL_MISMATCH = "POTCAR_SYMBOL_MISMATCH"
    POTCAR_HASH_MISMATCH = "POTCAR_HASH_MISMATCH"
    POTCAR_METADATA_INVALID = "POTCAR_METADATA_INVALID"
    POTCAR_ELEMENTS_DO_NOT_COVER_STRUCTURE = "POTCAR_ELEMENTS_DO_NOT_COVER_STRUCTURE"
    KPOINTS_VACUUM_AXIS_NOT_ONE = "KPOINTS_VACUUM_AXIS_NOT_ONE"
    KSPACING_WITH_KPOINTS_CONFLICT = "KSPACING_WITH_KPOINTS_CONFLICT"
    ILLEGAL_KPOINT_CENTERING = "ILLEGAL_KPOINT_CENTERING"
    SPIN_POLICY_UNRESOLVED = "SPIN_POLICY_UNRESOLVED"
    MAGMOM_UID_MISSING = "MAGMOM_UID_MISSING"
    VDW_POLICY_UNRESOLVED = "VDW_POLICY_UNRESOLVED"
    DIPOLE_CONTEXT_INVALID = "DIPOLE_CONTEXT_INVALID"
    CHARGED_LDIPOL_CELL_UNSUPPORTED = "CHARGED_LDIPOL_CELL_UNSUPPORTED"
    FREQUENCY_UID_NOT_FOUND = "FREQUENCY_UID_NOT_FOUND"
    FREQUENCY_UID_DUPLICATED = "FREQUENCY_UID_DUPLICATED"
    FREQUENCY_SELECTED_IBRION_CONFLICT = "FREQUENCY_SELECTED_IBRION_CONFLICT"
    FREQUENCY_FULL_IBRION_CONFLICT = "FREQUENCY_FULL_IBRION_CONFLICT"
    RECIPE_PROTOCOL_CONFLICT = "RECIPE_PROTOCOL_CONFLICT"
    SNAPSHOT_FINGERPRINT_MISMATCH = "SNAPSHOT_FINGERPRINT_MISMATCH"
    CHARGE_DIFFERENCE_CELL_MISMATCH = "CHARGE_DIFFERENCE_CELL_MISMATCH"
    CHARGE_DIFFERENCE_UID_PARTITION_MISMATCH = "CHARGE_DIFFERENCE_UID_PARTITION_MISMATCH"
    CHARGE_DIFFERENCE_GEOMETRY_MISMATCH = "CHARGE_DIFFERENCE_GEOMETRY_MISMATCH"
    CHARGE_DIFFERENCE_METHOD_MISMATCH = "CHARGE_DIFFERENCE_METHOD_MISMATCH"
    CHARGE_DIFFERENCE_PROTOCOL_MISMATCH = "CHARGE_DIFFERENCE_PROTOCOL_MISMATCH"
    CHARGE_DIFFERENCE_MAGMOM_MISMATCH = "CHARGE_DIFFERENCE_MAGMOM_MISMATCH"
    INPUT_MANIFEST_MISSING = "INPUT_MANIFEST_MISSING"
    INPUT_MANIFEST_INVALID = "INPUT_MANIFEST_INVALID"
    INPUT_MANIFEST_IDENTITY_MISMATCH = "INPUT_MANIFEST_IDENTITY_MISMATCH"
    INPUT_FILE_MISSING = "INPUT_FILE_MISSING"
    INPUT_FILE_HASH_MISMATCH = "INPUT_FILE_HASH_MISMATCH"
    INPUT_FILE_SIZE_MISMATCH = "INPUT_FILE_SIZE_MISMATCH"
    INPUT_FILE_PATH_INVALID = "INPUT_FILE_PATH_INVALID"
    ATOM_INDEX_MAP_INVALID = "ATOM_INDEX_MAP_INVALID"
    ATOM_INDEX_MAP_UID_MISMATCH = "ATOM_INDEX_MAP_UID_MISMATCH"
    GENERATED_POSCAR_MISMATCH = "GENERATED_POSCAR_MISMATCH"
    POTCAR_SPEC_RECONCILIATION_MISMATCH = "POTCAR_SPEC_RECONCILIATION_MISMATCH"


@dataclass(frozen=True, slots=True)
class VaspFailClosedRule:
    """Audit metadata for one stable fail-closed rule."""

    code: VaspFailClosedCode
    layer: str
    enforcement: str

    def __post_init__(self) -> None:
        if not self.layer.strip():
            raise ValueError("fail-closed rule layer must not be blank")
        if not self.enforcement.strip():
            raise ValueError("fail-closed rule enforcement must not be blank")


_RULES = (
    VaspFailClosedRule(VaspFailClosedCode.ENCUT_NOT_LOCKED, "numerical_lock", "recipes/pipeline"),
    VaspFailClosedRule(VaspFailClosedCode.POTCAR_IDENTITY_MISSING, "potcar", "potcar resolver"),
    VaspFailClosedRule(VaspFailClosedCode.POTCAR_FAMILY_MISMATCH, "potcar", "potcar resolver"),
    VaspFailClosedRule(VaspFailClosedCode.POTCAR_SYMBOL_MISMATCH, "potcar", "potcar resolver"),
    VaspFailClosedRule(VaspFailClosedCode.POTCAR_HASH_MISMATCH, "potcar", "potcar resolver"),
    VaspFailClosedRule(VaspFailClosedCode.POTCAR_METADATA_INVALID, "potcar", "potcar resolver"),
    VaspFailClosedRule(
        VaspFailClosedCode.POTCAR_ELEMENTS_DO_NOT_COVER_STRUCTURE,
        "potcar",
        "potcar resolver/materialization",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.KPOINTS_VACUUM_AXIS_NOT_ONE,
        "kpoints",
        "kpoints compiler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.KSPACING_WITH_KPOINTS_CONFLICT,
        "kpoints",
        "kpoints compiler/reconciliation",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.ILLEGAL_KPOINT_CENTERING,
        "kpoints",
        "kpoints compiler",
    ),
    VaspFailClosedRule(VaspFailClosedCode.SPIN_POLICY_UNRESOLVED, "incar", "incar compiler"),
    VaspFailClosedRule(VaspFailClosedCode.MAGMOM_UID_MISSING, "incar", "incar/frequency compiler"),
    VaspFailClosedRule(VaspFailClosedCode.VDW_POLICY_UNRESOLVED, "incar", "incar compiler"),
    VaspFailClosedRule(VaspFailClosedCode.DIPOLE_CONTEXT_INVALID, "incar", "incar compiler"),
    VaspFailClosedRule(
        VaspFailClosedCode.CHARGED_LDIPOL_CELL_UNSUPPORTED,
        "incar",
        "incar compiler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.FREQUENCY_UID_NOT_FOUND,
        "frequency",
        "frequency compiler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.FREQUENCY_UID_DUPLICATED,
        "frequency",
        "frequency selection",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.FREQUENCY_SELECTED_IBRION_CONFLICT,
        "frequency",
        "frequency compiler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.FREQUENCY_FULL_IBRION_CONFLICT,
        "frequency",
        "frequency compiler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.RECIPE_PROTOCOL_CONFLICT,
        "identity",
        "recipes/materialization guard",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.SNAPSHOT_FINGERPRINT_MISMATCH,
        "identity",
        "pipeline/materialization guard/reconciliation",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.CHARGE_DIFFERENCE_CELL_MISMATCH,
        "charge_difference",
        "analysis prerequisite pipeline",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.CHARGE_DIFFERENCE_UID_PARTITION_MISMATCH,
        "charge_difference",
        "analysis prerequisite pipeline",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.CHARGE_DIFFERENCE_GEOMETRY_MISMATCH,
        "charge_difference",
        "analysis prerequisite pipeline",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.CHARGE_DIFFERENCE_METHOD_MISMATCH,
        "charge_difference",
        "analysis prerequisite pipeline",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.CHARGE_DIFFERENCE_PROTOCOL_MISMATCH,
        "charge_difference",
        "analysis prerequisite pipeline",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.CHARGE_DIFFERENCE_MAGMOM_MISMATCH,
        "charge_difference",
        "analysis prerequisite pipeline",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.INPUT_MANIFEST_MISSING,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.INPUT_MANIFEST_INVALID,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.INPUT_MANIFEST_IDENTITY_MISMATCH,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.INPUT_FILE_MISSING,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.INPUT_FILE_HASH_MISMATCH,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.INPUT_FILE_SIZE_MISMATCH,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.INPUT_FILE_PATH_INVALID,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.ATOM_INDEX_MAP_INVALID,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.ATOM_INDEX_MAP_UID_MISMATCH,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.GENERATED_POSCAR_MISMATCH,
        "reconciliation",
        "generated input reconciler",
    ),
    VaspFailClosedRule(
        VaspFailClosedCode.POTCAR_SPEC_RECONCILIATION_MISMATCH,
        "reconciliation",
        "generated input reconciler",
    ),
)

VASP_FAIL_CLOSED_RULES: Mapping[VaspFailClosedCode, VaspFailClosedRule] = MappingProxyType(
    {rule.code: rule for rule in _RULES}
)

if len(VASP_FAIL_CLOSED_RULES) != len(_RULES):
    raise RuntimeError("VASP fail-closed codes must be unique")
if set(VASP_FAIL_CLOSED_RULES) != set(VaspFailClosedCode):
    raise RuntimeError("every VASP fail-closed code must have a registry rule")


class VaspPreflightError(ValueError):
    """Fail-closed error carrying one stable machine-readable code."""

    def __init__(self, code: VaspFailClosedCode, message: str) -> None:
        if not message.strip():
            raise ValueError("VaspPreflightError message must not be blank")
        self.code = code
        super().__init__(f"{code.value}: {message}")


def fail_closed(code: VaspFailClosedCode, message: str) -> NoReturn:
    """Raise a coded VASP fail-closed error."""

    raise VaspPreflightError(code, message)
