"""Typed v1.1 Model Studio application operations over schema-v3 authorities.

The functions in this module compose existing immutable domain/structure tooling with
``ProjectStore`` persistence. They do not introduce a second scientific model: all
geometry, atom identity, active-site, adsorption, and conformer semantics remain owned
by the existing ``ecatvasp.structures`` and ``ecatvasp.domain`` authorities.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ecatvasp.api.application import ApplicationServiceError, ProjectApplicationService
from ecatvasp.domain import (
    ActiveSite,
    ActiveSiteId,
    AdsorptionState,
    AdsorptionStateId,
    AtomUid,
    Catalyst,
    CatalystId,
    Project,
    ProjectId,
    SideLabel,
    StateConformer,
    StateConformerId,
    StructureSnapshot,
    StructureSnapshotId,
    StructureVariant,
    StructureVariantId,
    VariantType,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.structures import (
    AdsorbateBuildResult,
    AdsorbatePlacementSpec,
    DopantSubstitution,
    GrapheneBuildSpec,
    MultiMetalSiteResult,
    MultiMetalSiteSpec,
    SingleMetalSiteResult,
    SingleMetalSiteSpec,
    StructureDocument,
    StructureFormat,
    StructureMutationResult,
    active_site_from_multi_metal,
    active_site_from_single_metal,
    build_adsorbate,
    build_graphene,
    build_multi_metal_site,
    build_single_metal_site,
    create_active_site,
    create_adsorption_state,
    import_structure,
    mutate_structure,
    state_conformer_from_adsorbate_build,
)


@dataclass(frozen=True, slots=True)
class ProjectCreationResult:
    """Receipt for one newly initialized ProjectStore."""

    project_root: Path
    project: Project


@dataclass(frozen=True, slots=True)
class StructureModelResult:
    """Receipt for one persisted StructureVariant/current StructureSnapshot pair."""

    project_id: ProjectId
    variant: StructureVariant
    snapshot: StructureSnapshot


@dataclass(frozen=True, slots=True)
class SingleMetalModelResult:
    """Persisted child variant plus exact single-metal builder evidence."""

    project_id: ProjectId
    variant: StructureVariant
    build: SingleMetalSiteResult


@dataclass(frozen=True, slots=True)
class MultiMetalModelResult:
    """Persisted child variant plus exact multi-metal builder evidence."""

    project_id: ProjectId
    variant: StructureVariant
    build: MultiMetalSiteResult


@dataclass(frozen=True, slots=True)
class AdsorbateConformerResult:
    """Persisted adsorption-state/conformer graph and its immutable child snapshot."""

    project_id: ProjectId
    state: AdsorptionState
    conformer: StateConformer
    build: AdsorbateBuildResult


def create_project_store(
    project_root: Path | str,
    *,
    name: str,
    slug: str,
    description: str | None = None,
) -> ProjectCreationResult:
    """Create one new schema-v3 ProjectStore without overwriting existing content."""

    root = Path(project_root)
    if root.exists():
        if not root.is_dir():
            raise ApplicationServiceError("project root must be a directory path")
        if any(root.iterdir()):
            raise ApplicationServiceError("project creation requires a new or empty directory")

    project = Project(name=name, slug=slug, description=description)
    store = ProjectStore(root)
    store.save(ProjectBundle(project=project))
    reopened = store.open()
    if reopened.project != project:
        raise ApplicationServiceError("new project failed post-save verification")
    return ProjectCreationResult(project_root=root, project=project)


class ProjectModelStudioApplicationService(ProjectApplicationService):
    """Model Studio extension of the canonical project application-service seam."""

    def create_catalyst(
        self,
        *,
        name: str,
        slug: str,
        formula_label: str | None = None,
        support_type: str | None = None,
        series_key: str | None = None,
        series_value: str | int | float | None = None,
        tags: tuple[str, ...] = (),
    ) -> Catalyst:
        """Create one catalyst identity inside the current project."""

        bundle = self.store.open()
        normalized_slug = slug.strip().casefold()
        if any(item.slug.strip().casefold() == normalized_slug for item in bundle.catalysts):
            raise ApplicationServiceError("catalyst slug already exists in this project")
        catalyst = Catalyst(
            project_id=bundle.project.id,
            name=name,
            slug=slug,
            formula_label=formula_label,
            support_type=support_type,
            series_key=series_key,
            series_value=series_value,
            tags=tags,
        )
        self.store.save(replace(bundle, catalysts=(*bundle.catalysts, catalyst)))
        reopened = self.store.open()
        persisted = _require_catalyst(reopened, catalyst.id)
        if persisted != catalyst:
            raise ApplicationServiceError("catalyst creation failed post-save verification")
        return catalyst

    def build_graphene_model(
        self,
        *,
        catalyst_id: CatalystId,
        variant_name: str,
        spec: GrapheneBuildSpec,
    ) -> StructureModelResult:
        """Create one root graphene structure hypothesis for a catalyst."""

        bundle = self.store.open()
        catalyst = _require_catalyst(bundle, catalyst_id)
        _require_variant_name_available(bundle, catalyst.id, variant_name)
        snapshot = build_graphene(spec)
        variant = StructureVariant(
            catalyst_id=catalyst.id,
            name=variant_name,
            variant_type=VariantType.GEOMETRY,
            current_structure_snapshot_id=snapshot.id,
            topology_tags=("graphene",),
        )
        return self._persist_new_variant(bundle, variant, snapshot)

    def import_structure_model(
        self,
        *,
        catalyst_id: CatalystId,
        variant_name: str,
        source_path: Path | str,
        format: StructureFormat | str | None = None,
    ) -> tuple[StructureModelResult, StructureDocument]:
        """Import a local structure, preserving verifiable identity sidecars when present."""

        bundle = self.store.open()
        catalyst = _require_catalyst(bundle, catalyst_id)
        _require_variant_name_available(bundle, catalyst.id, variant_name)
        document = import_structure(source_path, format=format)
        snapshot = document.snapshot
        variant = StructureVariant(
            catalyst_id=catalyst.id,
            name=variant_name,
            variant_type=VariantType.GEOMETRY,
            current_structure_snapshot_id=snapshot.id,
            topology_tags=("imported",),
        )
        return self._persist_new_variant(bundle, variant, snapshot), document

    def mutate_structure_model(
        self,
        *,
        source_variant_id: StructureVariantId,
        source_snapshot_id: StructureSnapshotId,
        variant_name: str,
        vacancy_atom_uids: tuple[AtomUid, ...] = (),
        substitutions: tuple[DopantSubstitution, ...] = (),
        label: str | None = None,
    ) -> tuple[StructureModelResult, StructureMutationResult]:
        """Create a site-topology child variant from an exact current source snapshot."""

        bundle = self.store.open()
        source_variant, source_snapshot = _require_current_variant_snapshot(
            bundle,
            source_variant_id,
            source_snapshot_id,
        )
        _require_variant_name_available(bundle, source_variant.catalyst_id, variant_name)
        mutation = mutate_structure(
            source_snapshot,
            vacancy_atom_uids=vacancy_atom_uids,
            substitutions=substitutions,
            label=label,
        )
        tags: list[str] = []
        if vacancy_atom_uids:
            tags.append("vacancy")
        if substitutions:
            tags.append("dopant")
        variant = StructureVariant(
            catalyst_id=source_variant.catalyst_id,
            name=variant_name,
            variant_type=VariantType.SITE_TOPOLOGY,
            parent_variant_id=source_variant.id,
            topology_tags=tuple(tags),
            current_structure_snapshot_id=mutation.snapshot.id,
        )
        result = self._persist_new_variant(bundle, variant, mutation.snapshot)
        return result, mutation

    def build_single_metal_model(
        self,
        *,
        source_variant_id: StructureVariantId,
        source_snapshot_id: StructureSnapshotId,
        variant_name: str,
        spec: SingleMetalSiteSpec,
    ) -> SingleMetalModelResult:
        """Create a child site-topology variant containing one added metal center."""

        bundle = self.store.open()
        source_variant, source_snapshot = _require_current_variant_snapshot(
            bundle,
            source_variant_id,
            source_snapshot_id,
        )
        _require_variant_name_available(bundle, source_variant.catalyst_id, variant_name)
        build = build_single_metal_site(source_snapshot, spec)
        variant = StructureVariant(
            catalyst_id=source_variant.catalyst_id,
            name=variant_name,
            variant_type=VariantType.SITE_TOPOLOGY,
            parent_variant_id=source_variant.id,
            topology_tags=("single-metal",),
            current_structure_snapshot_id=build.snapshot.id,
        )
        self._persist_new_variant(bundle, variant, build.snapshot)
        return SingleMetalModelResult(
            project_id=bundle.project.id,
            variant=variant,
            build=build,
        )

    def build_multi_metal_model(
        self,
        *,
        source_variant_id: StructureVariantId,
        source_snapshot_id: StructureSnapshotId,
        variant_name: str,
        spec: MultiMetalSiteSpec,
    ) -> MultiMetalModelResult:
        """Create a child site-topology variant containing two or three metal centers."""

        bundle = self.store.open()
        source_variant, source_snapshot = _require_current_variant_snapshot(
            bundle,
            source_variant_id,
            source_snapshot_id,
        )
        _require_variant_name_available(bundle, source_variant.catalyst_id, variant_name)
        build = build_multi_metal_site(source_snapshot, spec)
        variant = StructureVariant(
            catalyst_id=source_variant.catalyst_id,
            name=variant_name,
            variant_type=VariantType.SITE_TOPOLOGY,
            parent_variant_id=source_variant.id,
            topology_tags=("multi-metal", build.side_topology.value),
            current_structure_snapshot_id=build.snapshot.id,
        )
        self._persist_new_variant(bundle, variant, build.snapshot)
        return MultiMetalModelResult(
            project_id=bundle.project.id,
            variant=variant,
            build=build,
        )

    def create_active_site(
        self,
        *,
        structure_variant_id: StructureVariantId,
        source_snapshot_id: StructureSnapshotId,
        center_atom_uids: tuple[AtomUid, ...],
        side_labels: tuple[SideLabel, ...] = (),
        topology: str | None = None,
        coordination_environment: str | None = None,
    ) -> ActiveSite:
        """Persist one exact ActiveSite against a variant's current snapshot."""

        bundle = self.store.open()
        variant, snapshot = _require_current_variant_snapshot(
            bundle,
            structure_variant_id,
            source_snapshot_id,
        )
        if any(
            item.structure_variant_id == variant.id
            and item.center_atom_uids == center_atom_uids
            for item in bundle.active_sites
        ):
            raise ApplicationServiceError(
                "an ActiveSite with the same ordered center atoms already exists"
            )
        active_site = create_active_site(
            variant=variant,
            snapshot=snapshot,
            center_atom_uids=center_atom_uids,
            side_labels=side_labels,
            topology=topology,
            coordination_environment=coordination_environment,
        )
        self.store.save(replace(bundle, active_sites=(*bundle.active_sites, active_site)))
        reopened = self.store.open()
        persisted = _require_active_site(reopened, active_site.id)
        if persisted != active_site:
            raise ApplicationServiceError("ActiveSite creation failed post-save verification")
        return active_site

    def create_active_site_from_single_metal(
        self,
        *,
        variant: StructureVariant,
        build: SingleMetalSiteResult,
    ) -> ActiveSite:
        """Persist the canonical single-metal ActiveSite for an already persisted variant."""

        bundle = self.store.open()
        persisted_variant, snapshot = _require_current_variant_snapshot(
            bundle,
            variant.id,
            build.snapshot.id,
        )
        if snapshot != build.snapshot:
            raise ApplicationServiceError("single-metal snapshot differs from persisted state")
        active_site = active_site_from_single_metal(variant=persisted_variant, result=build)
        self.store.save(replace(bundle, active_sites=(*bundle.active_sites, active_site)))
        return _require_active_site(self.store.open(), active_site.id)

    def create_active_site_from_multi_metal(
        self,
        *,
        variant: StructureVariant,
        build: MultiMetalSiteResult,
    ) -> ActiveSite:
        """Persist the canonical multi-metal ActiveSite for an already persisted variant."""

        bundle = self.store.open()
        persisted_variant, snapshot = _require_current_variant_snapshot(
            bundle,
            variant.id,
            build.snapshot.id,
        )
        if snapshot != build.snapshot:
            raise ApplicationServiceError("multi-metal snapshot differs from persisted state")
        active_site = active_site_from_multi_metal(variant=persisted_variant, result=build)
        self.store.save(replace(bundle, active_sites=(*bundle.active_sites, active_site)))
        return _require_active_site(self.store.open(), active_site.id)

    def build_adsorbate_conformer(
        self,
        *,
        structure_variant_id: StructureVariantId,
        source_snapshot_id: StructureSnapshotId,
        active_site_id: ActiveSiteId,
        state_label: str,
        placement: AdsorbatePlacementSpec,
        conformer_name: str,
        coverage: float | None = None,
        reaction_role: str | None = None,
        orientation: str | None = None,
        rank: int | None = None,
    ) -> AdsorbateConformerResult:
        """Create and persist one new adsorption state and its first exact conformer."""

        bundle = self.store.open()
        variant, source_snapshot = _require_current_variant_snapshot(
            bundle,
            structure_variant_id,
            source_snapshot_id,
        )
        active_site = _require_active_site(bundle, active_site_id)
        if active_site.structure_variant_id != variant.id:
            raise ApplicationServiceError("ActiveSite belongs to a different StructureVariant")
        build = build_adsorbate(source_snapshot, active_site, placement)
        existing_states = tuple(
            state
            for state in bundle.adsorption_states
            if state.structure_variant_id == variant.id and state.active_site_id == active_site.id
        )
        state = create_adsorption_state(
            variant,
            active_site,
            state_label=state_label,
            adsorbates=(build.template_key,),
            coverage=coverage,
            reaction_role=reaction_role,
            existing_states=existing_states,
        )
        conformer = state_conformer_from_adsorbate_build(
            state,
            active_site,
            build,
            name=conformer_name,
            orientation=orientation,
            rank=rank,
        )
        updated = replace(
            bundle,
            structure_snapshots=(*bundle.structure_snapshots, build.snapshot),
            adsorption_states=(*bundle.adsorption_states, state),
            state_conformers=(*bundle.state_conformers, conformer),
        )
        self.store.save(updated)
        reopened = self.store.open()
        persisted_state = _require_adsorption_state(reopened, state.id)
        persisted_conformer = _require_conformer(reopened, conformer.id)
        if persisted_state != state or persisted_conformer != conformer:
            raise ApplicationServiceError("adsorbate conformer failed post-save verification")
        _require_snapshot(reopened, build.snapshot.id)
        return AdsorbateConformerResult(
            project_id=reopened.project.id,
            state=state,
            conformer=conformer,
            build=build,
        )

    def _persist_new_variant(
        self,
        bundle: ProjectBundle,
        variant: StructureVariant,
        snapshot: StructureSnapshot,
    ) -> StructureModelResult:
        updated = replace(
            bundle,
            structure_variants=(*bundle.structure_variants, variant),
            structure_snapshots=(*bundle.structure_snapshots, snapshot),
        )
        self.store.save(updated)
        reopened = self.store.open()
        persisted_variant = _require_variant(reopened, variant.id)
        persisted_snapshot = _require_snapshot(reopened, snapshot.id)
        if persisted_variant != variant or persisted_snapshot != snapshot:
            raise ApplicationServiceError("structure model failed post-save verification")
        return StructureModelResult(
            project_id=reopened.project.id,
            variant=variant,
            snapshot=snapshot,
        )


def _require_catalyst(bundle: ProjectBundle, catalyst_id: CatalystId) -> Catalyst:
    for item in bundle.catalysts:
        if item.id == catalyst_id:
            return item
    raise ApplicationServiceError("Catalyst is absent from the current ProjectStore")


def _require_variant(bundle: ProjectBundle, variant_id: StructureVariantId) -> StructureVariant:
    for item in bundle.structure_variants:
        if item.id == variant_id:
            return item
    raise ApplicationServiceError("StructureVariant is absent from the current ProjectStore")


def _require_snapshot(bundle: ProjectBundle, snapshot_id: StructureSnapshotId) -> StructureSnapshot:
    for item in bundle.structure_snapshots:
        if item.id == snapshot_id:
            return item
    raise ApplicationServiceError("StructureSnapshot is absent from the current ProjectStore")


def _require_active_site(bundle: ProjectBundle, active_site_id: ActiveSiteId) -> ActiveSite:
    for item in bundle.active_sites:
        if item.id == active_site_id:
            return item
    raise ApplicationServiceError("ActiveSite is absent from the current ProjectStore")


def _require_adsorption_state(
    bundle: ProjectBundle,
    state_id: AdsorptionStateId,
) -> AdsorptionState:
    for item in bundle.adsorption_states:
        if item.id == state_id:
            return item
    raise ApplicationServiceError("AdsorptionState is absent from the current ProjectStore")


def _require_conformer(
    bundle: ProjectBundle,
    conformer_id: StateConformerId,
) -> StateConformer:
    for item in bundle.state_conformers:
        if item.id == conformer_id:
            return item
    raise ApplicationServiceError("StateConformer is absent from the current ProjectStore")


def _require_current_variant_snapshot(
    bundle: ProjectBundle,
    variant_id: StructureVariantId,
    snapshot_id: StructureSnapshotId,
) -> tuple[StructureVariant, StructureSnapshot]:
    variant = _require_variant(bundle, variant_id)
    snapshot = _require_snapshot(bundle, snapshot_id)
    if variant.current_structure_snapshot_id != snapshot.id:
        raise ApplicationServiceError(
            "selected source snapshot is stale; StructureVariant current snapshot has changed"
        )
    return variant, snapshot


def _require_variant_name_available(
    bundle: ProjectBundle,
    catalyst_id: CatalystId,
    variant_name: str,
) -> None:
    normalized = variant_name.strip().casefold()
    if not normalized:
        raise ApplicationServiceError("variant name must not be blank")
    if any(
        item.catalyst_id == catalyst_id and item.name.strip().casefold() == normalized
        for item in bundle.structure_variants
    ):
        raise ApplicationServiceError("StructureVariant name already exists for this catalyst")
