"""Fail-closed finite-difference frequency contracts for v0.3 Block 8."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from math import isfinite

from ecatvasp.domain.entities import StructureSnapshot
from ecatvasp.domain.ids import AtomUid
from ecatvasp.domain.method import (
    MethodDefinition,
    MethodFingerprint,
    ParameterEntry,
    ProtocolDefinition,
    RecipeIdentity,
    ScientificInputDigest,
    canonical_sha256,
)
from ecatvasp.vasp.contracts import ProjectNumericalLock, VaspSystemContext
from ecatvasp.vasp.incar import (
    EffectiveIncarParameter,
    IncarSourceLayer,
    PreparedIncar,
    UidMagmom,
    prepare_incar,
)
from ecatvasp.vasp.kpoints import PreparedKPoints
from ecatvasp.vasp.poscar import (
    AtomSelectiveFlags,
    PreparedPoscar,
    UidSelectiveDynamics,
    prepare_poscar,
)
from ecatvasp.vasp.potcar import PotcarSpec
from ecatvasp.vasp.recipes import (
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_SELECTED_ATOM_FREQUENCY,
)

ECATVASP_FREQUENCY_SELECTION_DIGEST = "frequency-selection-uids"
ECATVASP_FREQUENCY_MAX_EDIFF_EV = 1e-8
_FREQUENCY_RECIPE_IDS = frozenset(
    {
        RECIPE_SELECTED_ATOM_FREQUENCY,
        RECIPE_FULL_FREQUENCY,
        RECIPE_GAS_FREQUENCY,
    }
)


class FrequencyPreparationError(ValueError):
    """Raised when a finite-difference frequency input would require inference."""


@dataclass(frozen=True, slots=True)
class FrequencySelection:
    """Permanent-UID selection for an IBRION=5 partial Hessian calculation."""

    atom_uids: tuple[AtomUid, ...]
    selection_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.atom_uids:
            raise ValueError("FrequencySelection requires at least one atom_uid")
        ordered = tuple(sorted(self.atom_uids, key=str))
        if len(ordered) != len(set(ordered)):
            raise ValueError("FrequencySelection atom_uids must be unique")
        object.__setattr__(self, "atom_uids", ordered)
        object.__setattr__(
            self,
            "selection_hash",
            canonical_sha256({"atom_uids": ordered}),
        )

    @property
    def input_digest(self) -> ScientificInputDigest:
        """Return the MethodFingerprint digest binding this exact UID set."""

        return ScientificInputDigest(
            label=ECATVASP_FREQUENCY_SELECTION_DIGEST,
            sha256=self.selection_hash,
        )

    @property
    def selective_dynamics(self) -> UidSelectiveDynamics:
        """Compile selected atoms to T T T and every other atom to F F F."""

        return UidSelectiveDynamics(
            default_flags=(False, False, False),
            overrides=tuple(
                AtomSelectiveFlags(atom_uid, (True, True, True))
                for atom_uid in self.atom_uids
            ),
        )


def frequency_recipe_parameters(
    *,
    potim_angstrom: float,
    nfree: int = 2,
) -> tuple[ParameterEntry, ...]:
    """Return explicit fingerprinted finite-difference controls.

    Block 8 fixes the supported central-difference stencil to ``NFREE=2``.
    ``POTIM`` remains an explicit scientific recipe parameter rather than a hidden
    VASP default so the displacement amplitude is retained in the recipe hash.
    """

    if isinstance(nfree, bool) or nfree != 2:
        raise FrequencyPreparationError("Block 8 frequency recipes require NFREE=2")
    if not isfinite(potim_angstrom) or potim_angstrom <= 0:
        raise FrequencyPreparationError("frequency POTIM must be finite and positive")
    return (
        ParameterEntry("NFREE", nfree),
        ParameterEntry("POTIM", float(potim_angstrom)),
    )


def validate_frequency_recipe(recipe: RecipeIdentity) -> tuple[int, float]:
    """Require exact Block 8 finite-difference recipe controls."""

    if recipe.recipe_id not in _FREQUENCY_RECIPE_IDS:
        raise FrequencyPreparationError("recipe is not a Block 8 frequency recipe")
    values = {item.name: item.value for item in recipe.parameters}
    if set(values) != {"NFREE", "POTIM"}:
        raise FrequencyPreparationError(
            "frequency RecipeIdentity requires exactly NFREE and POTIM parameters"
        )
    nfree = values["NFREE"]
    potim = values["POTIM"]
    if isinstance(nfree, bool) or not isinstance(nfree, int) or nfree != 2:
        raise FrequencyPreparationError("Block 8 frequency recipes require NFREE=2")
    if isinstance(potim, bool) or not isinstance(potim, (int, float)):
        raise FrequencyPreparationError("frequency POTIM must be a numeric scalar")
    potim_float = float(potim)
    if not isfinite(potim_float) or potim_float <= 0:
        raise FrequencyPreparationError("frequency POTIM must be finite and positive")
    return nfree, potim_float


def validate_frequency_fingerprint_selection(
    *,
    fingerprint: MethodFingerprint,
    selection: FrequencySelection | None,
) -> None:
    """Bind selected-atom semantics to MethodFingerprint input digests."""

    recipe_id = fingerprint.recipe.recipe_id
    matches = tuple(
        item
        for item in fingerprint.input_digests
        if item.label == ECATVASP_FREQUENCY_SELECTION_DIGEST
    )
    if recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY:
        if selection is None:
            raise FrequencyPreparationError(
                "SelectedAtomFrequency requires an explicit FrequencySelection"
            )
        if len(matches) != 1 or matches[0].sha256 != selection.selection_hash:
            raise FrequencyPreparationError(
                "SelectedAtomFrequency fingerprint does not bind the exact UID selection"
            )
        return
    if recipe_id in {RECIPE_FULL_FREQUENCY, RECIPE_GAS_FREQUENCY}:
        if selection is not None or matches:
            raise FrequencyPreparationError(
                "full/gas frequency must not carry a selected-atom UID digest"
            )
        return
    raise FrequencyPreparationError("fingerprint recipe is not a Block 8 frequency recipe")


def prepare_frequency_poscar(
    snapshot: StructureSnapshot,
    *,
    fingerprint: MethodFingerprint,
    selection: FrequencySelection | None,
) -> PreparedPoscar:
    """Prepare the exact POSCAR mobility semantics for one frequency recipe."""

    validate_frequency_fingerprint_selection(fingerprint=fingerprint, selection=selection)
    if fingerprint.recipe.recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY:
        assert selection is not None
        snapshot_uids = {site.atom_uid for site in snapshot.sites}
        if any(atom_uid not in snapshot_uids for atom_uid in selection.atom_uids):
            raise FrequencyPreparationError(
                "frequency selection contains atom_uid absent from StructureSnapshot"
            )
        prepared = prepare_poscar(
            snapshot,
            selective_dynamics=selection.selective_dynamics,
        )
    else:
        prepared = prepare_poscar(snapshot)
    validate_frequency_prepared_poscar(
        prepared_poscar=prepared,
        fingerprint=fingerprint,
    )
    return prepared


def validate_frequency_prepared_poscar(
    *,
    prepared_poscar: PreparedPoscar,
    fingerprint: MethodFingerprint,
) -> None:
    """Verify POSCAR selective flags against the exact frequency fingerprint."""

    recipe_id = fingerprint.recipe.recipe_id
    if recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY:
        flags = prepared_poscar.selective_flags
        if flags is None:
            raise FrequencyPreparationError(
                "SelectedAtomFrequency requires Selective Dynamics in POSCAR"
            )
        selected_uids: list[AtomUid] = []
        for entry, atom_flags in zip(prepared_poscar.index_map.entries, flags, strict=True):
            if atom_flags == (True, True, True):
                selected_uids.append(entry.atom_uid)
            elif atom_flags != (False, False, False):
                raise FrequencyPreparationError(
                    "SelectedAtomFrequency only supports whole-atom T T T / F F F selection"
                )
        if not selected_uids:
            raise FrequencyPreparationError("SelectedAtomFrequency selected no atoms")
        selection = FrequencySelection(tuple(selected_uids))
        matches = tuple(
            item
            for item in fingerprint.input_digests
            if item.label == ECATVASP_FREQUENCY_SELECTION_DIGEST
        )
        if len(matches) != 1 or matches[0].sha256 != selection.selection_hash:
            raise FrequencyPreparationError(
                "prepared POSCAR selected atoms do not match MethodFingerprint"
            )
        return
    if recipe_id in {RECIPE_FULL_FREQUENCY, RECIPE_GAS_FREQUENCY}:
        if prepared_poscar.selective_flags is not None:
            raise FrequencyPreparationError(
                "full/gas frequency requires all atoms and must not use Selective Dynamics"
            )
        return
    raise FrequencyPreparationError("prepared POSCAR recipe is not a Block 8 frequency recipe")


def prepare_frequency_incar(
    *,
    snapshot: StructureSnapshot,
    method: MethodDefinition,
    protocol: ProtocolDefinition,
    recipe: RecipeIdentity,
    system_context: VaspSystemContext,
    prepared_poscar: PreparedPoscar,
    prepared_kpoints: PreparedKPoints,
    potcar_spec: PotcarSpec,
    project_lock: ProjectNumericalLock,
    magmom: UidMagmom | None = None,
) -> PreparedIncar:
    """Compile frequency INCAR while reusing the established Method/Protocol compiler."""

    nfree, potim = validate_frequency_recipe(recipe)
    if protocol.ediff_ev > ECATVASP_FREQUENCY_MAX_EDIFF_EV:
        raise FrequencyPreparationError(
            "ECAT_STANDARD frequency calculations require EDIFF <= 1e-8 eV"
        )

    # Reuse Block 5's already-audited Method/Protocol/context compiler with a
    # static surrogate recipe, then replace only the ionic recipe layer below.
    base = prepare_incar(
        snapshot=snapshot,
        method=method,
        protocol=protocol,
        recipe=RecipeIdentity(RECIPE_GROUND_STATE_STATIC),
        system_context=system_context,
        prepared_poscar=prepared_poscar,
        prepared_kpoints=prepared_kpoints,
        potcar_spec=potcar_spec,
        project_lock=project_lock,
        magmom=magmom,
    )
    replaced = {"IBRION", "LCHARG", "LWAVE", "NSW"}
    parameters = {item.name: item for item in base.parameters if item.name not in replaced}
    frequency_values: dict[str, int | float | bool] = {
        "IBRION": 5 if recipe.recipe_id == RECIPE_SELECTED_ATOM_FREQUENCY else 6,
        "LCHARG": False,
        "LWAVE": False,
        "NFREE": nfree,
        "NSW": 1,
        "POTIM": potim,
    }
    for name, value in frequency_values.items():
        parameters[name] = EffectiveIncarParameter(name, value, IncarSourceLayer.RECIPE)
    ordered = tuple(parameters[name] for name in sorted(parameters))
    text = "".join(f"{item.name} = {_format_frequency_value(item.value)}\n" for item in ordered)
    return PreparedIncar(
        structure_snapshot_id=prepared_poscar.structure_snapshot_id,
        recipe_id=recipe.recipe_id,
        parameters=ordered,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _format_frequency_value(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, float):
        if abs(value) < 5e-16:
            value = 0.0
        return f"{value:.12g}"
    return str(value)
