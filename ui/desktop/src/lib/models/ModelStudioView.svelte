<script lang="ts">
  import { onMount } from "svelte";

  import type { DesktopBackendClientV2 } from "../backend/client-v2";
  import type {
    DesktopModelCatalogPayload,
    DesktopStructurePresentationPayload,
    ModelActiveSiteSummary,
    ModelAdsorbateTemplateSummary,
    ModelVariantSummary,
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
  $: if (selectedTemplate !== null) {
    contactAtomKeys = selectedAtomUids.map(
      (_, index) => contactAtomKeys[index] ?? selectedTemplate.primary_anchor_atom_key,
    );
  }

  onMount(() => {
    void refreshCatalog();
  });

  function describeError(error: unknown, fallback: string): string {
    return error instanceof Error ? error.message : fallback;
  }

  function catalystLabel(catalystId: string): string {
    return catalog?.catalysts.find((item) => item.catalyst_id === catalystId)?.name ?? "Catalyst";
  }

  function siteLabel(site: ModelActiveSiteSummary, index: number): string {
    return `Site ${index + 1} · ${site.center_atom_uids.length} center${site.center_atom_uids.length === 1 ? "" : "s"}`;
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
    selectedAtomUids = [];
    presentation = null;
    presentationError = "";
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

  async function selectVariant(variantId: string): Promise<void> {
    selectedVariantId = variantId;
    activeSiteId = "";
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
      selectedAtomUids = [];
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
      multiAnchors = [[], [], []];
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
      <span class="eyebrow">Build scientific structures</span>
      <h2 id="model-studio-heading">Model Studio</h2>
      <p>Create and edit catalyst models while atom identity and structure lineage remain Python-owned.</p>
    </div>
    <button type="button" class="secondary" disabled={catalogBusy} onclick={() => void refreshCatalog()}>
      {catalogBusy ? "Refreshing…" : "Refresh models"}
    </button>
  </header>

  {#if catalogError}<div class="error" role="alert">{catalogError}</div>{/if}
  {#if actionError}<div class="error" role="alert">{actionError}</div>{/if}
  {#if actionMessage}<div class="success" aria-live="polite">{actionMessage}</div>{/if}

  <div class="catalog-row">
    <label>
      Catalyst
      <select bind:value={selectedCatalystId} disabled={catalogBusy || actionBusy}>
        <option value="">Select catalyst</option>
        {#each catalysts as catalyst (catalyst.catalyst_id)}
          <option value={catalyst.catalyst_id}>{catalyst.name}{catalyst.formula_label ? ` · ${catalyst.formula_label}` : ""}</option>
        {/each}
      </select>
    </label>
    <label>
      Structure model
      <select
        value={selectedVariantId}
        disabled={catalogBusy || actionBusy || variants.length === 0}
        onchange={(event) => void selectVariant(event.currentTarget.value)}
      >
        <option value="">Select model</option>
        {#each variants as variant (variant.structure_variant_id)}
          <option value={variant.structure_variant_id}>{variant.name} · {variant.atom_count ?? 0} atoms</option>
        {/each}
      </select>
    </label>
    <details class="advanced">
      <summary>Advanced identifiers</summary>
      <code>project: {catalog?.project_id ?? "—"}</code>
      <code>variant: {selectedVariant?.structure_variant_id ?? "—"}</code>
      <code>snapshot: {currentSnapshotId ?? "—"}</code>
    </details>
  </div>

  <div class="workspace-grid">
    <section class="viewer-card">
      {#if presentationBusy}
        <div class="empty">Loading selected structure…</div>
      {:else if presentationError}
        <div class="error">{presentationError}</div>
      {:else if presentation !== null}
        <ModelStructureSelector
          presentation={presentation.presentation}
          {selectedAtomUids}
          onSelectionChange={(values) => (selectedAtomUids = values)}
        />
      {:else}
        <div class="empty">Select or create a structure model to begin atom-level operations.</div>
      {/if}
    </section>

    <aside class="task-panel">
      <section class="task-card">
        <h3>New catalyst</h3>
        <input bind:value={catalystName} placeholder="Catalyst name" />
        <input bind:value={catalystSlug} placeholder="Stable slug" />
        <input bind:value={catalystFormula} placeholder="Formula label (optional)" />
        <input bind:value={catalystSupport} placeholder="Support type (optional)" />
        <button disabled={actionBusy || !catalystName.trim() || !catalystSlug.trim()} onclick={() => void createCatalyst()}>
          Create catalyst
        </button>
      </section>

      <section class="task-card">
        <h3>Model action</h3>
        <select bind:value={tool}>
          <option value="graphene">Build graphene</option>
          <option value="import">Import structure</option>
          <option value="mutate">Vacancy / dopant</option>
          <option value="single">Single metal center</option>
          <option value="multi">Dual / triple metal centers</option>
          <option value="site">Define active site</option>
          <option value="adsorbate">Add adsorbate conformer</option>
        </select>

        {#if toolNeedsVariant(tool) && selectedVariant === null}
          <p class="hint">Select a structure model first.</p>
        {:else if tool === "graphene"}
          <input bind:value={variantName} placeholder="Model name, e.g. graphene-4x4" />
          <div class="two-col">
            <label>nx <input type="number" min="1" bind:value={grapheneNx} /></label>
            <label>ny <input type="number" min="1" bind:value={grapheneNy} /></label>
          </div>
          <div class="two-col">
            <label>C–C Å <input type="number" step="0.01" bind:value={grapheneBond} /></label>
            <label>Vacuum Å <input type="number" step="0.5" bind:value={grapheneVacuum} /></label>
          </div>
          <button disabled={actionBusy || !selectedCatalystId || !variantName.trim()} onclick={() => void buildGraphene()}>
            Build graphene
          </button>
        {:else if tool === "import"}
          <input bind:value={variantName} placeholder="Imported model name" />
          <input bind:value={importPath} placeholder="Structure file path" />
          <select bind:value={importFormat}>
            <option value="poscar">POSCAR / VASP</option><option value="cif">CIF</option>
            <option value="xyz">XYZ</option><option value="extxyz">extXYZ</option>
          </select>
          <button disabled={actionBusy || !selectedCatalystId || !variantName.trim() || !importPath.trim()} onclick={() => void importStructure()}>
            Import structure
          </button>
        {:else if tool === "mutate"}
          <input bind:value={variantName} placeholder="Child model name" />
          <select bind:value={mutationMode}><option value="vacancy">Remove selected atoms</option><option value="dopant">Substitute one selected atom</option></select>
          {#if mutationMode === "dopant"}
            <select bind:value={dopant}><option value="N">N</option><option value="S">S</option><option value="P">P</option></select>
          {/if}
          <p class="hint">Selected atoms: {selectedAtomUids.length}{mutationMode === "dopant" ? " (exactly 1 required)" : ""}</p>
          <button disabled={actionBusy || !variantName.trim() || selectedAtomUids.length === 0 || (mutationMode === "dopant" && selectedAtomUids.length !== 1)} onclick={() => void mutateStructure()}>
            Create child variant
          </button>
        {:else if tool === "single"}
          <input bind:value={variantName} placeholder="Child model name" />
          <input bind:value={metalElement} placeholder="Metal element, e.g. Fe" />
          <div class="two-col">
            <select bind:value={metalSide}><option value="top">Top</option><option value="bottom">Bottom</option><option value="in_plane">In plane</option></select>
            <input type="number" step="0.1" bind:value={metalHeight} disabled={metalSide === "in_plane"} />
          </div>
          <p class="hint">Select coordination atoms in the viewer ({selectedAtomUids.length} selected).</p>
          <button disabled={actionBusy || !variantName.trim() || selectedAtomUids.length === 0} onclick={() => void buildSingleMetal()}>
            Add metal center
          </button>
        {:else if tool === "multi"}
          <input bind:value={variantName} placeholder="Child model name" />
          <select bind:value={multiCount}><option value={2}>Dual metal</option><option value={3}>Triple metal</option></select>
          {#each Array(multiCount) as _, index (index)}
            <div class="center-card">
              <strong>Center {index + 1}</strong>
              <div class="two-col">
                <input bind:value={multiElements[index]} placeholder="Element" />
                <select bind:value={multiSides[index]}><option value="top">Top</option><option value="bottom">Bottom</option><option value="in_plane">In plane</option></select>
              </div>
              <input type="number" step="0.1" bind:value={multiHeights[index]} disabled={multiSides[index] === "in_plane"} />
              <button type="button" class="secondary" disabled={selectedAtomUids.length === 0} onclick={() => captureMultiAnchors(index)}>
                Use current selection ({multiAnchors[index].length} saved)
              </button>
            </div>
          {/each}
          <input bind:value={multiTopology} placeholder="Metal–metal topology intent" />
          <button disabled={actionBusy || !variantName.trim() || multiAnchors.slice(0, multiCount).some((item) => item.length === 0)} onclick={() => void buildMultiMetal()}>
            Build ensemble
          </button>
        {:else if tool === "site"}
          <input bind:value={activeTopology} placeholder="Topology label (optional)" />
          <input bind:value={coordinationEnvironment} placeholder="Coordination environment (optional)" />
          <p class="hint">Select the active-center atoms ({selectedAtomUids.length} selected).</p>
          <button disabled={actionBusy || selectedAtomUids.length === 0} onclick={() => void createActiveSite()}>
            Define active site
          </button>
        {:else if tool === "adsorbate"}
          <select bind:value={activeSiteId}>
            <option value="">Select active site</option>
            {#each activeSites as site, index (site.active_site_id)}<option value={site.active_site_id}>{siteLabel(site, index)}</option>{/each}
          </select>
          <select bind:value={templateKey}>
            {#each catalog?.adsorbate_templates ?? [] as template (template.key)}<option value={template.key}>{templateLabel(template)}</option>{/each}
          </select>
          <div class="two-col">
            <select bind:value={bindingMode}><option value="single_center">Single center</option><option value="bridge">Bridge</option><option value="multicenter">Multicenter</option></select>
            <input type="number" step="0.1" bind:value={adsorbateHeight} />
          </div>
          <input bind:value={stateLabel} placeholder="State label, e.g. *OOH" />
          <input bind:value={conformerName} placeholder="Conformer name" />
          <input bind:value={reactionRole} placeholder="Reaction role (optional)" />
          <p class="hint">Select target active-center atoms in the viewer.</p>
          {#if selectedTemplate !== null && selectedAtomUids.length > 0}
            {#each selectedAtomUids as atomUid, index (atomUid)}
              <label class="contact-row">
                Target atom {presentation?.presentation.matterviz.atom_index_map.viewer_index_by_atom_uid[atomUid] ?? index}
                <select bind:value={contactAtomKeys[index]}>
                  {#each selectedTemplate.anchor_atom_keys as atomKey (atomKey)}<option value={atomKey}>{atomKey}</option>{/each}
                </select>
              </label>
            {/each}
          {/if}
          <button disabled={actionBusy || !activeSiteId || selectedAtomUids.length === 0 || !stateLabel.trim() || !conformerName.trim()} onclick={() => void buildAdsorbate()}>
            Add adsorbate conformer
          </button>
        {/if}
      </section>
    </aside>
  </div>

  {#if catalog !== null}
    <section class="model-summary">
      <strong>{catalog.project_name}</strong>
      <span>{catalog.catalysts.length} catalysts</span>
      <span>{catalog.variants.length} structure models</span>
      <span>{catalog.active_sites.length} active sites</span>
      <span>{catalog.state_conformers.length} adsorbate conformers</span>
    </section>
  {/if}
</section>

<style>
  .model-studio { display: grid; gap: 1rem; }
  .studio-header { display: flex; justify-content: space-between; gap: 1rem; align-items: start; }
  .studio-header h2 { margin: 0.2rem 0 0.35rem; }
  .studio-header p { margin: 0; color: var(--muted-text, #5d6470); }
  .eyebrow { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted-text, #5d6470); }
  .catalog-row { display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)) minmax(180px, 0.6fr); gap: 0.8rem; align-items: end; }
  label, .task-card { display: grid; gap: 0.35rem; }
  select, input { width: 100%; box-sizing: border-box; padding: 0.58rem 0.65rem; border: 1px solid rgba(100,110,125,.32); border-radius: 8px; background: inherit; color: inherit; }
  button { border: 0; border-radius: 8px; padding: 0.62rem 0.8rem; font-weight: 700; cursor: pointer; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .secondary { border: 1px solid rgba(100,110,125,.3); background: transparent; color: inherit; }
  .workspace-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(300px, .7fr); gap: 1rem; }
  .viewer-card, .task-card, .model-summary { border: 1px solid rgba(100,110,125,.22); border-radius: 12px; padding: 1rem; }
  .task-panel { display: grid; gap: 1rem; align-content: start; }
  .task-card h3 { margin: 0 0 .3rem; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: .55rem; }
  .center-card { display: grid; gap: .45rem; padding: .65rem; border: 1px solid rgba(100,110,125,.18); border-radius: 8px; }
  .hint { margin: .1rem 0; color: var(--muted-text,#5d6470); font-size: .86rem; line-height: 1.4; }
  .empty { min-height: 220px; display: grid; place-items: center; color: var(--muted-text,#5d6470); }
  .error, .success { border-radius: 8px; padding: .7rem .8rem; }
  .error { border: 1px solid rgba(139,31,45,.35); color: #8b1f2d; }
  .success { border: 1px solid rgba(46,108,72,.32); }
  .advanced { align-self: center; font-size: .78rem; }
  .advanced code { display: block; margin-top: .25rem; overflow-wrap: anywhere; }
  .contact-row { grid-template-columns: 1fr 1fr; align-items: center; font-size: .82rem; }
  .model-summary { display: flex; flex-wrap: wrap; gap: .7rem 1rem; align-items: center; }
  .model-summary span { color: var(--muted-text,#5d6470); }
  @media (max-width: 1050px) { .workspace-grid, .catalog-row { grid-template-columns: 1fr; } }
</style>
