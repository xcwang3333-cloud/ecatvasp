<script lang="ts">
  import { onMount } from "svelte";

  import type { DesktopBackendClientV2 } from "../backend/client-v2";
  import type {
    DesktopModelCatalogPayload,
    DesktopStructurePresentationPayload,
    ModelActiveSiteSummary,
    ModelAdsorbateTemplateSummary,
  } from "../backend/contracts-v2";
  import ModelStructureSelector from "./ModelStructureSelector.svelte";

  export let client: DesktopBackendClientV2;
  export let projectRoot: string;
  export let disabled = false;
  export let onMutation: () => Promise<void>;

  type Tool = "graphene" | "import" | "mutate" | "single" | "multi" | "site" | "adsorbate";

  let catalog: DesktopModelCatalogPayload | null = null;
  let catalogBusy = false;
  let catalogError = "";
  let actionBusy = false;
  let actionError = "";
  let actionMessage = "";
  let selectedCatalystId = "";
  let selectedVariantId = "";
  let selectedAtomUids: string[] = [];
  let presentation: DesktopStructurePresentationPayload | null = null;
  let presentationBusy = false;
  let presentationError = "";
  let tool: Tool = "graphene";

  let catalystName = "";
  let catalystSlug = "";
  let catalystFormula = "";
  let catalystSupport = "graphene";

  let variantName = "";
  let grapheneNx = 4;
  let grapheneNy = 4;
  let grapheneBond = 1.42;
  let grapheneVacuum = 15;
  let importPath = "";
  let importFormat: "poscar" | "cif" | "xyz" | "extxyz" = "poscar";

  let mutationMode: "vacancy" | "dopant" = "vacancy";
  let dopant: "N" | "S" | "P" = "N";

  let metalElement = "Fe";
  let metalSide: "top" | "bottom" | "in_plane" = "top";
  let metalHeight = 1.8;

  let multiCount: 2 | 3 = 2;
  let multiElements = ["Fe", "Co", "Ni"];
  let multiSides: Array<"top" | "bottom" | "in_plane"> = ["top", "top", "top"];
  let multiHeights = [1.8, 1.8, 1.8];
  let multiAnchors: string[][] = [[], [], []];
  let multiTopology = "proximal";

  let activeTopology = "";
  let coordinationEnvironment = "";

  let activeSiteId = "";
  let templateKey = "OOH";
  let bindingMode: "single_center" | "bridge" | "multicenter" = "single_center";
  let adsorbateHeight = 1.8;
  let stateLabel = "*OOH";
  let conformerName = "OOH conformer 1";
  let reactionRole = "ORR intermediate";
  let contactAtomKeys: string[] = [];

  let presentationSequence = 0;

  $: catalysts = catalog?.catalysts ?? [];
  $: variants = (catalog?.variants ?? []).filter(
    (item) => selectedCatalystId.length === 0 || item.catalyst_id === selectedCatalystId,
  );
  $: selectedVariant =
    (catalog?.variants ?? []).find((item) => item.structure_variant_id === selectedVariantId) ?? null;
  $: activeSites = (catalog?.active_sites ?? []).filter(
    (item) => item.structure_variant_id === selectedVariantId,
  );
  $: selectedActiveSite =
    activeSites.find((item) => item.active_site_id === activeSiteId) ?? null;
  $: selectedTemplate =
    (catalog?.adsorbate_templates ?? []).find((item) => item.key === templateKey) ?? null;
  $: currentSnapshotId = selectedVariant?.current_structure_snapshot_id ?? null;

  $: if (
    selectedCatalystId.length > 0 &&
    !catalysts.some((item) => item.catalyst_id === selectedCatalystId)
  ) selectedCatalystId = "";
  $: if (
    selectedVariantId.length > 0 &&
    !variants.some((item) => item.structure_variant_id === selectedVariantId)
  ) selectedVariantId = "";
  $: if (activeSiteId.length > 0 && !activeSites.some((item) => item.active_site_id === activeSiteId)) {
    activeSiteId = "";
  }

  onMount(() => {
    void refreshCatalog();
  });

  function describeError(error: unknown, fallback: string): string {
    return error instanceof Error ? error.message : fallback;
  }

  function siteLabel(site: ModelActiveSiteSummary, index: number): string {
    return `Site ${index + 1} · ${site.center_atom_uids.length} center${site.center_atom_uids.length === 1 ? "" : "s"}`;
  }

  function resetStructureBoundState(): void {
    selectedAtomUids = [];
    multiAnchors = [[], [], []];
    activeSiteId = "";
    contactAtomKeys = [];
  }

  function updateAtomSelection(values: string[]): void {
    selectedAtomUids = values;
    const template = (catalog?.adsorbate_templates ?? []).find((item) => item.key === templateKey);
    if (template === undefined) {
      contactAtomKeys = [];
      return;
    }
    contactAtomKeys = values.map((_, index) => {
      const current = contactAtomKeys[index];
      return current !== undefined && template.anchor_atom_keys.includes(current)
        ? current
        : template.primary_anchor_atom_key;
    });
  }

  function selectAdsorbateTemplate(key: string): void {
    templateKey = key;
    contactAtomKeys = [];
    updateAtomSelection(selectedAtomUids);
  }

  async function refreshCatalog(): Promise<void> {
    catalogBusy = true;
    catalogError = "";
    try {
      const response = await client.modelCatalog(projectRoot);
      catalog = response.payload;
      if (selectedCatalystId.length === 0 && catalog.catalysts.length > 0) {
        selectedCatalystId = catalog.catalysts[0].catalyst_id;
      }
      const candidate = catalog.variants.find((item) => item.structure_variant_id === selectedVariantId)
        ?? catalog.variants.find((item) => item.catalyst_id === selectedCatalystId)
        ?? catalog.variants[0]
        ?? null;
      selectedVariantId = candidate?.structure_variant_id ?? "";
      await loadSelectedPresentation();
    } catch (error: unknown) {
      catalogError = describeError(error, "Model Studio catalog could not be loaded");
    } finally {
      catalogBusy = false;
    }
  }

  async function loadSelectedPresentation(): Promise<void> {
    const snapshotId = currentSnapshotId;
    const sequence = ++presentationSequence;
    resetStructureBoundState();
    presentation = null;
    presentationError = "";
    presentationBusy = false;
    if (snapshotId === null) return;
    presentationBusy = true;
    try {
      const response = await client.structurePresentation(projectRoot, snapshotId);
      if (sequence !== presentationSequence) return;
      presentation = response.payload;
    } catch (error: unknown) {
      if (sequence !== presentationSequence) return;
      presentationError = describeError(error, "Structure presentation could not be loaded");
    } finally {
      if (sequence === presentationSequence) presentationBusy = false;
    }
  }

  async function selectCatalyst(catalystId: string): Promise<void> {
    selectedCatalystId = catalystId;
    const candidate = (catalog?.variants ?? []).find((item) => item.catalyst_id === catalystId) ?? null;
    selectedVariantId = candidate?.structure_variant_id ?? "";
    await loadSelectedPresentation();
  }

  async function selectVariant(variantId: string): Promise<void> {
    selectedVariantId = variantId;
    await loadSelectedPresentation();
  }

  async function runAction(label: string, action: () => Promise<unknown>): Promise<void> {
    if (disabled || actionBusy) return;
    actionBusy = true;
    actionError = "";
    actionMessage = "";
    try {
      await action();
      actionMessage = label;
      resetStructureBoundState();
      await refreshCatalog();
      await onMutation();
    } catch (error: unknown) {
      actionError = describeError(error, `${label} failed`);
    } finally {
      actionBusy = false;
    }
  }

  async function createCatalyst(): Promise<void> {
    await runAction("Catalyst created", async () => {
      await client.createCatalyst(projectRoot, {
        name: catalystName,
        slug: catalystSlug,
        ...(catalystFormula.trim() ? { formula_label: catalystFormula } : {}),
        ...(catalystSupport.trim() ? { support_type: catalystSupport } : {}),
      });
      catalystName = "";
      catalystSlug = "";
    });
  }

  async function buildGraphene(): Promise<void> {
    if (!selectedCatalystId) return;
    await runAction("Graphene structure created", async () => {
      const response = await client.buildGraphene(projectRoot, {
        catalyst_id: selectedCatalystId,
        variant_name: variantName,
        nx: grapheneNx,
        ny: grapheneNy,
        bond_length_angstrom: grapheneBond,
        vacuum_gap_angstrom: grapheneVacuum,
        label: variantName,
      });
      selectedVariantId = response.payload.structure_variant_id;
      variantName = "";
    });
  }

  async function importStructure(): Promise<void> {
    if (!selectedCatalystId) return;
    await runAction("Structure imported", async () => {
      const response = await client.importStructure(projectRoot, {
        catalyst_id: selectedCatalystId,
        variant_name: variantName,
        source_path: importPath,
        format: importFormat,
      });
      selectedVariantId = response.payload.structure_variant_id;
      variantName = "";
    });
  }

  async function mutateStructure(): Promise<void> {
    if (selectedVariant === null || currentSnapshotId === null || selectedAtomUids.length === 0) return;
    if (mutationMode === "dopant" && selectedAtomUids.length !== 1) return;
    await runAction(mutationMode === "vacancy" ? "Vacancy variant created" : "Dopant variant created", async () => {
      const response = await client.mutateStructure(projectRoot, {
        source_variant_id: selectedVariant.structure_variant_id,
        source_snapshot_id: currentSnapshotId,
        variant_name: variantName,
        ...(mutationMode === "vacancy"
          ? { vacancy_atom_uids: selectedAtomUids }
          : { substitutions: [{ atom_uid: selectedAtomUids[0], dopant }] }),
      });
      selectedVariantId = response.payload.structure_variant_id;
      variantName = "";
    });
  }

  async function buildSingleMetal(): Promise<void> {
    if (selectedVariant === null || currentSnapshotId === null || selectedAtomUids.length === 0) return;
    await runAction("Single-metal variant created", async () => {
      const response = await client.buildSingleMetalSite(projectRoot, {
        source_variant_id: selectedVariant.structure_variant_id,
        source_snapshot_id: currentSnapshotId,
        variant_name: variantName,
        metal_element: metalElement,
        coordination_atom_uids: selectedAtomUids,
        side: metalSide,
        height_angstrom: metalSide === "in_plane" ? 0 : metalHeight,
      });
      selectedVariantId = response.payload.structure_variant_id;
      variantName = "";
    });
  }

  function captureMultiAnchors(index: number): void {
    multiAnchors[index] = [...selectedAtomUids];
    multiAnchors = [...multiAnchors];
  }

  async function buildMultiMetal(): Promise<void> {
    if (selectedVariant === null || currentSnapshotId === null) return;
    const centers = multiAnchors.slice(0, multiCount).map((anchors, index) => ({
      metal_element: multiElements[index],
      coordination_atom_uids: anchors,
      side: multiSides[index],
      height_angstrom: multiSides[index] === "in_plane" ? 0 : multiHeights[index],
    }));
    if (centers.some((center) => center.coordination_atom_uids.length === 0)) return;
    await runAction(`${multiCount}-metal variant created`, async () => {
      const response = await client.buildMultiMetalSite(projectRoot, {
        source_variant_id: selectedVariant.structure_variant_id,
        source_snapshot_id: currentSnapshotId,
        variant_name: variantName,
        centers,
        ...(multiTopology.trim() ? { metal_metal_topology_intent: multiTopology } : {}),
      });
      selectedVariantId = response.payload.structure_variant_id;
      variantName = "";
    });
  }

  async function createActiveSite(): Promise<void> {
    if (selectedVariant === null || currentSnapshotId === null || selectedAtomUids.length === 0) return;
    await runAction("Active site defined", async () => {
      await client.createActiveSite(projectRoot, {
        structure_variant_id: selectedVariant.structure_variant_id,
        source_snapshot_id: currentSnapshotId,
        center_atom_uids: selectedAtomUids,
        ...(activeTopology.trim() ? { topology: activeTopology } : {}),
        ...(coordinationEnvironment.trim() ? { coordination_environment: coordinationEnvironment } : {}),
      });
    });
  }

  async function buildAdsorbate(): Promise<void> {
    if (
      selectedVariant === null ||
      currentSnapshotId === null ||
      selectedActiveSite === null ||
      selectedTemplate === null ||
      selectedAtomUids.length === 0
    ) return;
    const contacts = selectedAtomUids.map((siteAtomUid, index) => ({
      adsorbate_atom_key: contactAtomKeys[index] ?? selectedTemplate.primary_anchor_atom_key,
      site_atom_uid: siteAtomUid,
    }));
    await runAction("Adsorbate conformer created", async () => {
      await client.buildAdsorbateConformer(projectRoot, {
        structure_variant_id: selectedVariant.structure_variant_id,
        source_snapshot_id: currentSnapshotId,
        active_site_id: selectedActiveSite.active_site_id,
        state_label: stateLabel,
        template_key: selectedTemplate.key,
        target_center_atom_uids: selectedAtomUids,
        binding_mode: bindingMode,
        height_angstrom: adsorbateHeight,
        contacts,
        conformer_name: conformerName,
        ...(reactionRole.trim() ? { reaction_role: reactionRole } : {}),
      });
    });
  }

  function toolNeedsVariant(value: Tool): boolean {
    return !["graphene", "import"].includes(value);
  }

  function templateLabel(template: ModelAdsorbateTemplateSummary): string {
    return `${template.key} · ${template.reaction_families.join("/")}`;
  }
</script>

<section class="model-studio" aria-labelledby="model-studio-heading">
  <header class="studio-header">
    <div>
      <span class="eyebrow">Structure workspace</span>
      <h2 id="model-studio-heading">Model Studio</h2>
      <p>Build catalyst structures, select atoms in the 3D scene, and derive explicit scientific variants without losing atom identity or lineage.</p>
    </div>
    <div class="studio-header-actions">
      {#if catalog !== null}
        <div class="catalog-metrics" aria-label="Model catalog summary">
          <span><strong>{catalog.catalysts.length}</strong> catalysts</span>
          <span><strong>{catalog.variants.length}</strong> models</span>
          <span><strong>{catalog.active_sites.length}</strong> sites</span>
        </div>
      {/if}
      <button type="button" class="secondary" disabled={catalogBusy} onclick={() => void refreshCatalog()}>
        {catalogBusy ? "Refreshing…" : "Refresh"}
      </button>
    </div>
  </header>

  {#if catalogError}<div class="studio-alert error" role="alert">{catalogError}</div>{/if}
  {#if actionError}<div class="studio-alert error" role="alert">{actionError}</div>{/if}
  {#if actionMessage}<div class="studio-alert success" aria-live="polite">{actionMessage}</div>{/if}

  <div class="studio-layout">
    <aside class="inventory-pane" aria-label="Catalyst and structure inventory">
      <div class="pane-heading">
        <div>
          <span class="eyebrow">Project inventory</span>
          <h3>Structures</h3>
        </div>
        <span class="count-chip">{variants.length}</span>
      </div>

      <details class="create-catalyst">
        <summary>New catalyst system</summary>
        <div class="create-fields">
          <input bind:value={catalystName} placeholder="Catalyst name" />
          <input bind:value={catalystSlug} placeholder="Stable slug" />
          <input bind:value={catalystFormula} placeholder="Formula label (optional)" />
          <input bind:value={catalystSupport} placeholder="Support type (optional)" />
          <button
            class="primary"
            disabled={actionBusy || !catalystName.trim() || !catalystSlug.trim()}
            onclick={() => void createCatalyst()}
          >Create catalyst</button>
        </div>
      </details>

      {#if catalysts.length === 0}
        <div class="inventory-empty">
          <strong>No catalyst systems</strong>
          <p>Create the scientific system first, then build or import its starting structure.</p>
        </div>
      {:else}
        <div class="catalyst-list">
          {#each catalysts as catalyst (catalyst.catalyst_id)}
            <section class:active={selectedCatalystId === catalyst.catalyst_id} class="catalyst-group">
              <button
                type="button"
                class="catalyst-button"
                disabled={catalogBusy || actionBusy}
                onclick={() => void selectCatalyst(catalyst.catalyst_id)}
              >
                <span class="catalyst-symbol">{(catalyst.formula_label ?? catalyst.name).slice(0, 2).toUpperCase()}</span>
                <span class="catalyst-copy">
                  <strong>{catalyst.name}</strong>
                  <small>{catalyst.formula_label ?? catalyst.support_type ?? "Catalyst system"}</small>
                </span>
                <span class="catalyst-count">{(catalog?.variants ?? []).filter((item) => item.catalyst_id === catalyst.catalyst_id).length}</span>
              </button>

              {#if selectedCatalystId === catalyst.catalyst_id}
                <div class="variant-list">
                  {#each variants as variant (variant.structure_variant_id)}
                    <button
                      type="button"
                      class:selected={selectedVariantId === variant.structure_variant_id}
                      class="variant-button"
                      disabled={catalogBusy || actionBusy}
                      onclick={() => void selectVariant(variant.structure_variant_id)}
                    >
                      <span class="variant-line" aria-hidden="true"></span>
                      <span>
                        <strong>{variant.name}</strong>
                        <small>{variant.variant_type.replaceAll("_", " ")} · {variant.atom_count ?? 0} atoms</small>
                      </span>
                    </button>
                  {/each}
                  {#if variants.length === 0}
                    <div class="variant-empty">No structure models yet.</div>
                  {/if}
                </div>
              {/if}
            </section>
          {/each}
        </div>
      {/if}

      <details class="identity-details">
        <summary>Scientific identifiers</summary>
        <dl>
          <div><dt>Project</dt><dd>{catalog?.project_id ?? "—"}</dd></div>
          <div><dt>Variant</dt><dd>{selectedVariant?.structure_variant_id ?? "—"}</dd></div>
          <div><dt>Snapshot</dt><dd>{currentSnapshotId ?? "—"}</dd></div>
        </dl>
      </details>
    </aside>

    <section class="viewer-pane" aria-label="Interactive structure viewer">
      <header class="viewer-heading">
        <div>
          <span class="eyebrow">{selectedVariant?.variant_type.replaceAll("_", " ") ?? "Structure viewer"}</span>
          <h3>{selectedVariant?.name ?? "Select a structure model"}</h3>
          {#if selectedVariant !== null}
            <p>{selectedVariant.atom_count ?? 0} atoms · {selectedVariant.structure_origin ?? "project structure"} · {activeSites.length} active site{activeSites.length === 1 ? "" : "s"}</p>
          {/if}
        </div>
        {#if selectedVariant !== null}
          <div class="selection-counter" class:active={selectedAtomUids.length > 0}>
            <strong>{selectedAtomUids.length}</strong>
            <span>selected</span>
          </div>
        {/if}
      </header>

      <div class="viewer-surface">
        {#if presentationBusy}
          <div class="viewer-state"><div class="mini-spinner" aria-hidden="true"></div><strong>Loading structure</strong></div>
        {:else if presentationError}
          <div class="viewer-state error"><strong>Structure unavailable</strong><p>{presentationError}</p></div>
        {:else if presentation !== null}
          <ModelStructureSelector
            presentation={presentation.presentation}
            {selectedAtomUids}
            onSelectionChange={updateAtomSelection}
          />
        {:else}
          <div class="viewer-state">
            <span class="empty-orbit" aria-hidden="true"><i></i><i></i><i></i></span>
            <strong>No structure selected</strong>
            <p>Select a model from the inventory, or use the Build / Import tools to create the first structure.</p>
          </div>
        {/if}
      </div>
    </section>

    <aside class="tool-pane" aria-label="Structure construction tools">
      <div class="pane-heading tool-heading">
        <div>
          <span class="eyebrow">Construction</span>
          <h3>Model tools</h3>
        </div>
        {#if actionBusy}<span class="working-chip">Working…</span>{/if}
      </div>

      <div class="tool-switcher" role="group" aria-label="Model construction tool">
        <button type="button" class:active={tool === "graphene"} onclick={() => (tool = "graphene")}><span>Base</span><strong>Graphene</strong></button>
        <button type="button" class:active={tool === "import"} onclick={() => (tool = "import")}><span>File</span><strong>Import</strong></button>
        <button type="button" class:active={tool === "mutate"} onclick={() => (tool = "mutate")}><span>Edit</span><strong>Defect</strong></button>
        <button type="button" class:active={tool === "single"} onclick={() => (tool = "single")}><span>Site</span><strong>Single M</strong></button>
        <button type="button" class:active={tool === "multi"} onclick={() => (tool = "multi")}><span>Site</span><strong>Multi M</strong></button>
        <button type="button" class:active={tool === "site"} onclick={() => (tool = "site")}><span>Map</span><strong>Active site</strong></button>
        <button type="button" class:active={tool === "adsorbate"} onclick={() => (tool = "adsorbate")}><span>State</span><strong>Adsorbate</strong></button>
      </div>

      <section class="tool-form">
        {#if toolNeedsVariant(tool) && selectedVariant === null}
          <div class="tool-empty"><strong>Structure required</strong><p>Select a model in the left inventory before using this operation.</p></div>
        {:else if tool === "graphene"}
          <div class="tool-title"><span>Build base support</span><h4>Graphene supercell</h4><p>Create a periodic graphene support as a new structure model for the selected catalyst.</p></div>
          <label>Model name<input bind:value={variantName} placeholder="graphene-4x4" /></label>
          <div class="two-col">
            <label>Cells X<input type="number" min="1" bind:value={grapheneNx} /></label>
            <label>Cells Y<input type="number" min="1" bind:value={grapheneNy} /></label>
          </div>
          <div class="two-col">
            <label>C–C / Å<input type="number" step="0.01" bind:value={grapheneBond} /></label>
            <label>Vacuum / Å<input type="number" step="0.5" bind:value={grapheneVacuum} /></label>
          </div>
          <button class="primary" disabled={actionBusy || !selectedCatalystId || !variantName.trim()} onclick={() => void buildGraphene()}>Build graphene</button>
        {:else if tool === "import"}
          <div class="tool-title"><span>Bring external structure</span><h4>Import model</h4><p>Import a POSCAR, CIF, XYZ, or extXYZ structure into the selected catalyst lineage.</p></div>
          <label>Model name<input bind:value={variantName} placeholder="Imported surface model" /></label>
          <label>Structure file<input bind:value={importPath} placeholder="Absolute structure file path" /></label>
          <label>Format<select bind:value={importFormat}>
            <option value="poscar">POSCAR / VASP</option><option value="cif">CIF</option>
            <option value="xyz">XYZ</option><option value="extxyz">extXYZ</option>
          </select></label>
          <button class="primary" disabled={actionBusy || !selectedCatalystId || !variantName.trim() || !importPath.trim()} onclick={() => void importStructure()}>Import structure</button>
        {:else if tool === "mutate"}
          <div class="tool-title"><span>Derive child structure</span><h4>Defect / dopant</h4><p>Use the atom selection in the 3D scene to create a traceable child variant.</p></div>
          <label>Child model name<input bind:value={variantName} placeholder="N-doped active-site model" /></label>
          <label>Operation<select bind:value={mutationMode}><option value="vacancy">Remove selected atoms</option><option value="dopant">Substitute one selected atom</option></select></label>
          {#if mutationMode === "dopant"}
            <label>Dopant<select bind:value={dopant}><option value="N">N</option><option value="S">S</option><option value="P">P</option></select></label>
          {/if}
          <div class="selection-note"><strong>{selectedAtomUids.length}</strong><span>atom{selectedAtomUids.length === 1 ? "" : "s"} selected in viewer</span></div>
          <button class="primary" disabled={actionBusy || !variantName.trim() || selectedAtomUids.length === 0 || (mutationMode === "dopant" && selectedAtomUids.length !== 1)} onclick={() => void mutateStructure()}>Create child variant</button>
        {:else if tool === "single"}
          <div class="tool-title"><span>Construct active center</span><h4>Single-metal site</h4><p>Select coordination atoms in the viewer, then place the metal center relative to them.</p></div>
          <label>Child model name<input bind:value={variantName} placeholder="FeN4 model" /></label>
          <label>Metal element<input bind:value={metalElement} placeholder="Fe" /></label>
          <div class="two-col">
            <label>Side<select bind:value={metalSide}><option value="top">Top</option><option value="bottom">Bottom</option><option value="in_plane">In plane</option></select></label>
            <label>Height / Å<input type="number" step="0.1" bind:value={metalHeight} disabled={metalSide === "in_plane"} /></label>
          </div>
          <div class="selection-note"><strong>{selectedAtomUids.length}</strong><span>coordination atom{selectedAtomUids.length === 1 ? "" : "s"}</span></div>
          <button class="primary" disabled={actionBusy || !variantName.trim() || selectedAtomUids.length === 0} onclick={() => void buildSingleMetal()}>Add metal center</button>
        {:else if tool === "multi"}
          <div class="tool-title"><span>Construct ensemble</span><h4>Multi-metal site</h4><p>Capture a distinct coordination selection for each metal center before building the ensemble.</p></div>
          <label>Child model name<input bind:value={variantName} placeholder="FeCo dual-site model" /></label>
          <label>Ensemble<select bind:value={multiCount}><option value={2}>Dual metal</option><option value={3}>Triple metal</option></select></label>
          {#each Array(multiCount) as _, index (index)}
            <div class="center-card">
              <div class="center-heading"><strong>Center {index + 1}</strong><span>{multiAnchors[index].length} anchors</span></div>
              <div class="two-col">
                <label>Element<input bind:value={multiElements[index]} placeholder="Fe" /></label>
                <label>Side<select bind:value={multiSides[index]}><option value="top">Top</option><option value="bottom">Bottom</option><option value="in_plane">In plane</option></select></label>
              </div>
              <label>Height / Å<input type="number" step="0.1" bind:value={multiHeights[index]} disabled={multiSides[index] === "in_plane"} /></label>
              <button type="button" class="secondary" disabled={selectedAtomUids.length === 0} onclick={() => captureMultiAnchors(index)}>Capture current selection</button>
            </div>
          {/each}
          <label>Topology intent<input bind:value={multiTopology} placeholder="proximal" /></label>
          <button class="primary" disabled={actionBusy || !variantName.trim() || multiAnchors.slice(0, multiCount).some((item) => item.length === 0)} onclick={() => void buildMultiMetal()}>Build ensemble</button>
        {:else if tool === "site"}
          <div class="tool-title"><span>Define scientific identity</span><h4>Active site</h4><p>Select one or more center atoms, then annotate the site topology and coordination environment.</p></div>
          <label>Topology<input bind:value={activeTopology} placeholder="single_atom / bridge / hollow" /></label>
          <label>Coordination environment<input bind:value={coordinationEnvironment} placeholder="FeN4" /></label>
          <div class="selection-note"><strong>{selectedAtomUids.length}</strong><span>active-center atom{selectedAtomUids.length === 1 ? "" : "s"}</span></div>
          <button class="primary" disabled={actionBusy || selectedAtomUids.length === 0} onclick={() => void createActiveSite()}>Define active site</button>
        {:else if tool === "adsorbate"}
          <div class="tool-title"><span>Electrocatalytic state</span><h4>Adsorbate conformer</h4><p>Bind a reaction intermediate to an explicit active site and preserve the contact mapping.</p></div>
          <label>Active site<select bind:value={activeSiteId}>
            <option value="">Select active site</option>
            {#each activeSites as site, index (site.active_site_id)}<option value={site.active_site_id}>{siteLabel(site, index)}</option>{/each}
          </select></label>
          <label>Template<select value={templateKey} onchange={(event) => selectAdsorbateTemplate(event.currentTarget.value)}>
            {#each catalog?.adsorbate_templates ?? [] as template (template.key)}<option value={template.key}>{templateLabel(template)}</option>{/each}
          </select></label>
          <div class="two-col">
            <label>Binding<select bind:value={bindingMode}><option value="single_center">Single center</option><option value="bridge">Bridge</option><option value="multicenter">Multicenter</option></select></label>
            <label>Height / Å<input type="number" step="0.1" bind:value={adsorbateHeight} /></label>
          </div>
          <label>State label<input bind:value={stateLabel} placeholder="*OOH" /></label>
          <label>Conformer name<input bind:value={conformerName} placeholder="OOH conformer 1" /></label>
          <label>Reaction role<input bind:value={reactionRole} placeholder="ORR intermediate" /></label>
          <div class="selection-note"><strong>{selectedAtomUids.length}</strong><span>target atom{selectedAtomUids.length === 1 ? "" : "s"} selected</span></div>
          {#if selectedTemplate !== null && selectedAtomUids.length > 0}
            <div class="contact-list">
              {#each selectedAtomUids as atomUid, index (atomUid)}
                <label class="contact-row">
                  <span>Atom {presentation?.presentation.matterviz.atom_index_map.viewer_index_by_atom_uid[atomUid] ?? index}</span>
                  <select bind:value={contactAtomKeys[index]}>
                    {#each selectedTemplate.anchor_atom_keys as atomKey (atomKey)}<option value={atomKey}>{atomKey}</option>{/each}
                  </select>
                </label>
              {/each}
            </div>
          {/if}
          <button class="primary" disabled={actionBusy || !activeSiteId || selectedAtomUids.length === 0 || !stateLabel.trim() || !conformerName.trim()} onclick={() => void buildAdsorbate()}>Add adsorbate conformer</button>
        {/if}
      </section>
    </aside>
  </div>
</section>

<style>
  .model-studio { display: grid; gap: .8rem; color: var(--text, inherit); }
  .studio-header { display: flex; justify-content: space-between; gap: 1.5rem; align-items: flex-start; padding-bottom: .85rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .studio-header h2 { margin: .16rem 0 .25rem; font-size: 1.2rem; letter-spacing: -.025em; }
  .studio-header p { max-width: 720px; margin: 0; color: var(--muted-text, #687570); font-size: .72rem; line-height: 1.55; }
  .eyebrow { color: var(--accent, #177b68); font-size: .58rem; font-weight: 750; letter-spacing: .1em; text-transform: uppercase; }
  .studio-header-actions { display: flex; align-items: center; gap: .65rem; }
  .catalog-metrics { display: flex; gap: .35rem; }
  .catalog-metrics span { padding: .32rem .45rem; border: 1px solid var(--border, #dce4e0); border-radius: 7px; color: var(--muted-text, #687570); background: var(--surface, #fff); font-size: .56rem; white-space: nowrap; }
  .catalog-metrics strong { color: var(--text, #182321); font-size: .64rem; }
  button { font: inherit; }
  .primary, .secondary { border-radius: 7px; padding: .5rem .65rem; font-size: .64rem; font-weight: 700; cursor: pointer; }
  .primary { border: 1px solid var(--accent-strong, #0e6656); color: #fff; background: var(--accent, #177b68); }
  .secondary { border: 1px solid var(--border, #dce4e0); color: inherit; background: var(--surface, #fff); }
  button:disabled { cursor: not-allowed; opacity: .5; }
  .studio-alert { padding: .55rem .7rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; background: var(--surface, #fff); font-size: .65rem; }
  .studio-alert.error { color: #a53a43; border-color: #e8c1c5; background: #fff4f5; }
  .studio-alert.success { color: #226c52; border-color: #bcdccf; background: #eff9f5; }
  .studio-layout { display: grid; grid-template-columns: 226px minmax(430px, 1fr) 300px; min-height: 610px; gap: .75rem; }
  .inventory-pane, .viewer-pane, .tool-pane { min-width: 0; border: 1px solid var(--border, #dce4e0); border-radius: 11px; background: var(--surface, #fff); overflow: hidden; }
  .inventory-pane, .tool-pane { display: flex; flex-direction: column; }
  .pane-heading, .viewer-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: .75rem; padding: .8rem .85rem; border-bottom: 1px solid var(--border, #dce4e0); background: var(--surface-soft, #f7f9f8); }
  .pane-heading h3, .viewer-heading h3 { margin: .12rem 0 0; font-size: .78rem; letter-spacing: -.015em; }
  .count-chip, .working-chip { display: inline-flex; min-width: 22px; min-height: 20px; align-items: center; justify-content: center; border: 1px solid var(--border, #dce4e0); border-radius: 999px; color: var(--muted-text, #687570); background: var(--surface, #fff); font-size: .55rem; font-weight: 700; }
  .working-chip { padding: 0 .45rem; }
  .create-catalyst { border-bottom: 1px solid var(--border, #dce4e0); }
  .create-catalyst > summary, .identity-details > summary { padding: .62rem .82rem; color: var(--muted-text, #687570); font-size: .61rem; font-weight: 700; cursor: pointer; }
  .create-fields { display: grid; gap: .4rem; padding: 0 .78rem .75rem; }
  input, select { width: 100%; min-width: 0; box-sizing: border-box; padding: .47rem .52rem; border: 1px solid var(--border-strong, #cbd6d1); border-radius: 7px; outline: none; color: inherit; background: var(--surface, #fff); font-size: .64rem; }
  input:focus, select:focus { border-color: #70a999; box-shadow: 0 0 0 2px rgb(23 123 104 / 8%); }
  .catalyst-list { flex: 1; min-height: 0; overflow: auto; padding: .35rem; }
  .catalyst-group { border-radius: 8px; }
  .catalyst-group.active { background: var(--surface-soft, #f7f9f8); }
  .catalyst-button { display: grid; grid-template-columns: 30px minmax(0,1fr) auto; width: 100%; align-items: center; gap: .5rem; padding: .55rem; border: 0; border-radius: 8px; color: inherit; background: transparent; text-align: left; cursor: pointer; }
  .catalyst-button:hover { background: var(--surface-muted, #eef3f0); }
  .catalyst-symbol { display: inline-flex; width: 29px; height: 29px; align-items: center; justify-content: center; border-radius: 7px; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); font-size: .58rem; font-weight: 800; }
  .catalyst-copy { min-width: 0; }
  .catalyst-copy strong, .catalyst-copy small, .variant-button strong, .variant-button small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .catalyst-copy strong { font-size: .65rem; }
  .catalyst-copy small { margin-top: .1rem; color: var(--muted-text, #687570); font-size: .52rem; }
  .catalyst-count { color: var(--subtle-text, #84908b); font-size: .53rem; }
  .variant-list { display: grid; gap: .12rem; padding: 0 .25rem .42rem 1rem; }
  .variant-button { display: grid; grid-template-columns: 8px minmax(0,1fr); align-items: stretch; gap: .42rem; width: 100%; padding: .38rem .4rem; border: 0; border-radius: 6px; color: inherit; background: transparent; text-align: left; cursor: pointer; }
  .variant-button:hover, .variant-button.selected { background: var(--surface-muted, #eef3f0); }
  .variant-line { width: 2px; margin: .08rem auto; border-radius: 2px; background: var(--border-strong, #cbd6d1); }
  .variant-button.selected .variant-line { background: var(--accent, #177b68); }
  .variant-button strong { font-size: .59rem; }
  .variant-button small { margin-top: .08rem; color: var(--muted-text, #687570); font-size: .49rem; }
  .inventory-empty, .variant-empty { color: var(--muted-text, #687570); font-size: .6rem; line-height: 1.45; }
  .inventory-empty { padding: 1rem .8rem; }
  .inventory-empty strong { color: var(--text, #182321); }
  .inventory-empty p { margin: .3rem 0 0; }
  .variant-empty { padding: .45rem; }
  .identity-details { margin-top: auto; border-top: 1px solid var(--border, #dce4e0); }
  .identity-details dl { display: grid; gap: .45rem; margin: 0; padding: 0 .8rem .75rem; }
  .identity-details dt { margin-bottom: .12rem; color: var(--subtle-text, #84908b); font-size: .48rem; text-transform: uppercase; }
  .identity-details dd { margin: 0; overflow-wrap: anywhere; color: var(--muted-text, #687570); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .48rem; line-height: 1.35; }
  .viewer-pane { display: grid; grid-template-rows: auto minmax(0,1fr); }
  .viewer-heading { align-items: center; }
  .viewer-heading h3 { font-size: .83rem; }
  .viewer-heading p { margin: .18rem 0 0; color: var(--muted-text, #687570); font-size: .55rem; }
  .selection-counter { display: grid; min-width: 50px; place-items: center; padding: .32rem .45rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; color: var(--muted-text, #687570); background: var(--surface, #fff); }
  .selection-counter.active { border-color: #91c7b7; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); }
  .selection-counter strong { font-size: .75rem; }
  .selection-counter span { font-size: .46rem; text-transform: uppercase; }
  .viewer-surface { min-height: 0; padding: .5rem; background: #eef2f0; }
  .viewer-state { display: grid; min-height: 500px; place-items: center; align-content: center; gap: .55rem; border: 1px dashed var(--border-strong, #cbd6d1); border-radius: 9px; color: var(--muted-text, #687570); background: var(--surface-soft, #f7f9f8); text-align: center; }
  .viewer-state strong { color: var(--text, #182321); font-size: .72rem; }
  .viewer-state p { max-width: 330px; margin: 0; font-size: .58rem; line-height: 1.5; }
  .viewer-state.error strong { color: #a53a43; }
  .mini-spinner { width: 20px; height: 20px; border: 2px solid var(--border, #dce4e0); border-top-color: var(--accent, #177b68); border-radius: 50%; animation: model-spin .9s linear infinite; }
  @keyframes model-spin { to { transform: rotate(360deg); } }
  .empty-orbit { position: relative; width: 46px; height: 46px; border: 1px solid #b8c8c2; border-radius: 50%; }
  .empty-orbit i { position: absolute; width: 8px; height: 8px; border-radius: 50%; background: #6fb29f; }
  .empty-orbit i:nth-child(1) { top: 8px; left: 8px; }
  .empty-orbit i:nth-child(2) { right: 7px; top: 18px; }
  .empty-orbit i:nth-child(3) { left: 18px; bottom: 6px; }
  .tool-pane { max-height: calc(100vh - 150px); overflow: hidden; }
  .tool-heading { flex: none; }
  .tool-switcher { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .28rem; padding: .55rem; border-bottom: 1px solid var(--border, #dce4e0); background: var(--surface-soft, #f7f9f8); }
  .tool-switcher button { display: grid; gap: .08rem; padding: .42rem .45rem; border: 1px solid transparent; border-radius: 7px; color: var(--muted-text, #687570); background: transparent; text-align: left; cursor: pointer; }
  .tool-switcher button:hover { background: var(--surface, #fff); }
  .tool-switcher button.active { border-color: #a9cec2; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); }
  .tool-switcher span { font-size: .46rem; text-transform: uppercase; letter-spacing: .07em; }
  .tool-switcher strong { font-size: .58rem; }
  .tool-form { display: grid; align-content: start; gap: .52rem; min-height: 0; padding: .75rem; overflow: auto; }
  .tool-title { padding-bottom: .45rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .tool-title > span { color: var(--accent, #177b68); font-size: .5rem; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
  .tool-title h4 { margin: .13rem 0 .18rem; font-size: .72rem; }
  .tool-title p, .tool-empty p { margin: 0; color: var(--muted-text, #687570); font-size: .55rem; line-height: 1.45; }
  .tool-form label { display: grid; gap: .23rem; color: #53615d; font-size: .56rem; font-weight: 650; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: .42rem; }
  .selection-note { display: flex; align-items: center; gap: .45rem; padding: .45rem .5rem; border-radius: 7px; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); }
  .selection-note strong { font-size: .78rem; }
  .selection-note span { font-size: .54rem; }
  .center-card { display: grid; gap: .4rem; padding: .5rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; background: var(--surface-soft, #f7f9f8); }
  .center-heading { display: flex; justify-content: space-between; font-size: .58rem; }
  .center-heading span { color: var(--muted-text, #687570); font-size: .5rem; }
  .contact-list { display: grid; gap: .3rem; }
  .contact-row { grid-template-columns: auto minmax(0,1fr); align-items: center; gap: .4rem; }
  .tool-empty { padding: .7rem; border: 1px dashed var(--border-strong, #cbd6d1); border-radius: 8px; }
  .tool-empty strong { font-size: .65rem; }
  @media (max-width: 1180px) {
    .studio-layout { grid-template-columns: 205px minmax(420px,1fr); }
    .tool-pane { grid-column: 1 / -1; max-height: none; }
    .tool-switcher { grid-template-columns: repeat(7, minmax(82px,1fr)); }
    .tool-form { overflow: visible; }
  }
  @media (max-width: 930px) {
    .studio-header { flex-direction: column; }
    .studio-layout { grid-template-columns: 1fr; }
    .inventory-pane, .tool-pane { max-height: none; }
    .tool-switcher { grid-template-columns: repeat(2,minmax(0,1fr)); }
  }
</style>
