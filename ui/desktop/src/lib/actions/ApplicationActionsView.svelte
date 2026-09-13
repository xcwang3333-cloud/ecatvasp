<script lang="ts">
  import { ElectronicAnalysisClient } from "../analysis/client";
  import ElectronicAnalysisView from "../analysis/ElectronicAnalysisView.svelte";
  import type { DesktopBackendClientV2 } from "../backend/client-v2";
  import type {
    ApplicationReportPayload,
    PrepareWorkflowPayload,
    ReportExportReceipt,
    ReportFormat,
    WorkflowRecipeSummary,
  } from "../backend/contracts";
  import { ResultCenterClient } from "../results/client";
  import ResultCenterView from "../results/ResultCenterView.svelte";
  import { ThermochemistryClient } from "../thermochemistry/client";
  import ThermochemistryReactionView from "../thermochemistry/ThermochemistryReactionView.svelte";
  import type { ScientificWorkspace } from "../workspace/contracts";

  type ScientificTaskSurface = "results" | "electronic" | "thermochemistry";

  export let client: DesktopBackendClientV2;
  export let projectRoot: string;
  export let projectId: string;
  export let workflowRecipes: WorkflowRecipeSummary[];
  export let workspace: ScientificWorkspace;
  export let disabled = false;
  export let onMutation: () => Promise<void>;

  const resultCenterClient = new ResultCenterClient();
  const electronicAnalysisClient = new ElectronicAnalysisClient();
  const thermochemistryClient = new ThermochemistryClient();

  let reportFormat: ReportFormat = "json";
  let reportBusy = false;
  let reportError = "";
  let reportReceipt: ApplicationReportPayload | null = null;
  let exportDirectory = "";
  let exportBusy = false;
  let exportError = "";
  let exportReceipt: ReportExportReceipt | null = null;

  let recipeKey = "";
  let rootSnapshotId = "";
  let parametersHash = "";
  let workflowBusy = false;
  let workflowError = "";
  let workflowReceipt: PrepareWorkflowPayload | null = null;

  let activeScientificSurface: ScientificTaskSurface = "results";
  let surfaceProjectRoot = projectRoot;

  $: if (surfaceProjectRoot !== projectRoot) {
    surfaceProjectRoot = projectRoot;
    activeScientificSurface = "results";
  }
  $: structureSnapshots = workspace.inventory.rows.filter(
    (row) => row.entity_kind === "structure_snapshot",
  );
  $: selectedRecipe =
    workflowRecipes.find((recipe) => `${recipe.recipe_id}@${recipe.version}` === recipeKey) ?? null;
  $: parametersHashValid =
    parametersHash.length === 0 || /^[0-9a-fA-F]{64}$/.test(parametersHash);
  $: prepareReady =
    selectedRecipe !== null &&
    rootSnapshotId.length > 0 &&
    parametersHashValid &&
    !disabled &&
    !workflowBusy;

  function describeError(error: unknown, fallback: string): string {
    return error instanceof Error ? error.message : fallback;
  }

  function shortHash(value: string): string {
    return `${value.slice(0, 10)}…${value.slice(-6)}`;
  }

  function toggleScientificSurface(surface: ScientificTaskSurface): void {
    activeScientificSurface = surface;
  }

  async function generateReport(): Promise<void> {
    if (disabled || reportBusy) return;
    reportBusy = true;
    reportError = "";
    reportReceipt = null;
    exportReceipt = null;
    try {
      const response = await client.applicationReport(projectRoot, reportFormat);
      if (response.payload.project_id !== projectId) {
        throw new Error("application report belongs to a different project");
      }
      reportReceipt = response.payload;
    } catch (error: unknown) {
      reportError = describeError(error, "Scientific report could not be generated");
    } finally {
      reportBusy = false;
    }
  }

  async function exportReport(): Promise<void> {
    if (reportReceipt === null || exportBusy || exportDirectory.trim().length === 0) return;
    exportBusy = true;
    exportError = "";
    exportReceipt = null;
    try {
      exportReceipt = await client.exportReport(exportDirectory, reportReceipt);
    } catch (error: unknown) {
      exportError = describeError(error, "Report could not be exported");
    } finally {
      exportBusy = false;
    }
  }

  async function prepareWorkflow(): Promise<void> {
    if (!prepareReady || selectedRecipe === null) return;
    workflowBusy = true;
    workflowError = "";
    workflowReceipt = null;
    try {
      const response = await client.prepareWorkflow(projectRoot, {
        workflow_recipe_id: selectedRecipe.recipe_id,
        workflow_recipe_version: selectedRecipe.version,
        root_structure_snapshot_id: rootSnapshotId,
        ...(parametersHash.length === 0 ? {} : { parameters_hash: parametersHash }),
      });
      if (response.payload.project_id !== projectId) {
        throw new Error("workflow receipt belongs to a different project");
      }
      workflowReceipt = response.payload;
      await onMutation();
    } catch (error: unknown) {
      workflowError = describeError(error, "Workflow could not be prepared");
    } finally {
      workflowBusy = false;
    }
  }
</script>

<section class="analysis-workbench" aria-labelledby="analysis-heading">
  <header class="analysis-header">
    <div>
      <span class="eyebrow">Scientific interpretation</span>
      <h2 id="analysis-heading">Results & analysis</h2>
      <p>Parse retrieved VASP outputs, inspect electronic structure, apply thermochemistry, and evaluate electrocatalytic reaction energetics without conflating scheduler completion with scientific validity.</p>
    </div>
    <div class="analysis-summary">
      <span><strong>{workspace.inventory.rows.length}</strong> project entities</span>
      <span><strong>{structureSnapshots.length}</strong> structures</span>
    </div>
  </header>

  <nav class="analysis-tabs" aria-label="Scientific analysis surface">
    <button
      type="button"
      class:active={activeScientificSurface === "results"}
      aria-pressed={activeScientificSurface === "results"}
      disabled={disabled}
      onclick={() => toggleScientificSurface("results")}
    >
      <span class="tab-index">01</span>
      <span><strong>Results</strong><small>Convergence & promotion</small></span>
    </button>
    <button
      type="button"
      class:active={activeScientificSurface === "electronic"}
      aria-pressed={activeScientificSurface === "electronic"}
      disabled={disabled}
      onclick={() => toggleScientificSurface("electronic")}
    >
      <span class="tab-index">02</span>
      <span><strong>Electronic</strong><small>DOS · Bader · COHP</small></span>
    </button>
    <button
      type="button"
      class:active={activeScientificSurface === "thermochemistry"}
      aria-pressed={activeScientificSurface === "thermochemistry"}
      disabled={disabled}
      onclick={() => toggleScientificSurface("thermochemistry")}
    >
      <span class="tab-index">03</span>
      <span><strong>Thermo & reactions</strong><small>CHE · free-energy pathways</small></span>
    </button>
  </nav>

  <section class="analysis-surface">
    {#if activeScientificSurface === "results"}
      <ResultCenterView
        client={resultCenterClient}
        {projectRoot}
        {disabled}
        {onMutation}
      />
    {:else if activeScientificSurface === "electronic"}
      <ElectronicAnalysisView
        client={electronicAnalysisClient}
        {projectRoot}
        {disabled}
        {onMutation}
      />
    {:else}
      <ThermochemistryReactionView
        client={thermochemistryClient}
        {projectRoot}
        {disabled}
        {onMutation}
      />
    {/if}
  </section>

  <details class="project-tools">
    <summary>
      <span><strong>Reports & advanced project tools</strong><small>Export exact scientific reports or prepare a workflow directly from a StructureSnapshot.</small></span>
      <span class="summary-action">Open</span>
    </summary>

    <div class="tools-grid">
      <section class="tool-card">
        <div class="tool-heading">
          <span class="eyebrow">Project report</span>
          <h3>Scientific report</h3>
          <p>Generate a deterministic project report from the current Python-authoritative ProjectStore state.</p>
        </div>
        <form onsubmit={(event) => { event.preventDefault(); void generateReport(); }}>
          <label>Format<select bind:value={reportFormat} disabled={disabled || reportBusy}>
            <option value="json">JSON</option>
            <option value="csv">CSV</option>
            <option value="markdown">Markdown</option>
          </select></label>
          <button class="primary-button" type="submit" disabled={disabled || reportBusy}>{reportBusy ? "Generating…" : "Generate report"}</button>
        </form>
        {#if reportError}<div class="tool-alert error" role="alert">{reportError}</div>{/if}
        {#if reportReceipt !== null}
          <div class="receipt">
            <div class="receipt-heading"><span>Report hash</span><code title={reportReceipt.report_hash}>{shortHash(reportReceipt.report_hash)}</code></div>
            <pre>{reportReceipt.content}</pre>
            <form onsubmit={(event) => { event.preventDefault(); void exportReport(); }}>
              <label>Export directory<input bind:value={exportDirectory} placeholder="Existing absolute directory" /></label>
              <button class="secondary-button" type="submit" disabled={exportBusy || !exportDirectory.trim()}>{exportBusy ? "Exporting…" : "Export exact report"}</button>
            </form>
            {#if exportError}<div class="tool-alert error">{exportError}</div>{/if}
            {#if exportReceipt !== null}<small>{exportReceipt.reused ? "Existing exact file" : "Exported"} · {exportReceipt.file_name}</small>{/if}
          </div>
        {/if}
      </section>

      <section class="tool-card">
        <div class="tool-heading">
          <span class="eyebrow">Advanced workflow API</span>
          <h3>Prepare from snapshot</h3>
          <p>Use the explicit recipe/snapshot API directly. The guided VASP Setup remains the primary path for routine calculation preparation.</p>
        </div>
        {#if structureSnapshots.length === 0}
          <div class="tool-empty">Create or import a structure in Model Studio first.</div>
        {:else}
          <form onsubmit={(event) => { event.preventDefault(); void prepareWorkflow(); }}>
            <label>Workflow recipe<select bind:value={recipeKey} disabled={disabled || workflowBusy}>
              <option value="">Select recipe</option>
              {#each workflowRecipes as recipe (recipe.recipe_id + "@" + recipe.version)}
                <option value={recipe.recipe_id + "@" + recipe.version}>{recipe.recipe_id} @ {recipe.version}</option>
              {/each}
            </select></label>
            <label>Structure snapshot<select bind:value={rootSnapshotId} disabled={disabled || workflowBusy}>
              <option value="">Select structure</option>
              {#each structureSnapshots as row (row.entity_id)}
                <option value={row.entity_id}>{row.display_label} · {row.freshness.state}</option>
              {/each}
            </select></label>
            <details class="advanced-fields">
              <summary>Parameters hash</summary>
              <label>Optional SHA-256<input bind:value={parametersHash} placeholder="64 hexadecimal characters" aria-invalid={!parametersHashValid} /></label>
              {#if !parametersHashValid}<p class="tool-alert error">Parameters hash must be SHA-256.</p>{/if}
            </details>
            <button class="primary-button" type="submit" disabled={!prepareReady}>{workflowBusy ? "Preparing…" : "Prepare workflow"}</button>
          </form>
        {/if}
        {#if workflowError}<div class="tool-alert error" role="alert">{workflowError}</div>{/if}
        {#if workflowReceipt !== null}
          <div class="receipt compact">
            <strong>{workflowReceipt.reused ? "Exact workflow reused" : "Workflow prepared"}</strong>
            <small>Plan {shortHash(workflowReceipt.plan_hash)}</small>
          </div>
        {/if}
      </section>
    </div>
  </details>
</section>

<style>
  .analysis-workbench { display: grid; gap: .8rem; color: var(--text, inherit); }
  .analysis-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1.4rem; padding-bottom: .85rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .analysis-header h2 { margin: .16rem 0 .25rem; font-size: 1.2rem; letter-spacing: -.025em; }
  .analysis-header p { max-width: 800px; margin: 0; color: var(--muted-text, #687570); font-size: .72rem; line-height: 1.55; }
  .eyebrow { color: var(--accent, #177b68); font-size: .58rem; font-weight: 750; letter-spacing: .1em; text-transform: uppercase; }
  .analysis-summary { display: flex; gap: .3rem; }
  .analysis-summary span { padding: .3rem .44rem; border: 1px solid var(--border, #dce4e0); border-radius: 7px; color: var(--muted-text, #687570); background: var(--surface, #fff); font-size: .52rem; white-space: nowrap; }
  .analysis-summary strong { color: var(--text, #182321); font-size: .61rem; }
  .analysis-tabs { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); border: 1px solid var(--border, #dce4e0); border-radius: 10px; overflow: hidden; background: var(--surface-soft, #f7f9f8); }
  .analysis-tabs button { display: grid; grid-template-columns: 30px minmax(0,1fr); align-items: center; gap: .5rem; min-width: 0; padding: .62rem .72rem; border: 0; border-right: 1px solid var(--border, #dce4e0); color: var(--muted-text, #687570); background: transparent; text-align: left; cursor: pointer; }
  .analysis-tabs button:last-child { border-right: 0; }
  .analysis-tabs button:hover, .analysis-tabs button.active { background: var(--surface, #fff); }
  .analysis-tabs button.active { box-shadow: inset 0 -2px 0 var(--accent, #177b68); color: var(--text, #182321); }
  .tab-index { display: inline-flex; width: 27px; height: 27px; align-items: center; justify-content: center; border: 1px solid var(--border, #dce4e0); border-radius: 7px; font-size: .48rem; font-weight: 750; }
  .analysis-tabs button.active .tab-index { color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); }
  .analysis-tabs strong, .analysis-tabs small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .analysis-tabs strong { font-size: .62rem; }
  .analysis-tabs small { margin-top: .1rem; color: var(--muted-text, #687570); font-size: .49rem; }
  .analysis-surface { min-width: 0; }
  .project-tools { border: 1px solid var(--border, #dce4e0); border-radius: 10px; background: var(--surface, #fff); overflow: hidden; }
  .project-tools > summary { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .68rem .8rem; cursor: pointer; list-style: none; }
  .project-tools > summary::-webkit-details-marker { display: none; }
  .project-tools > summary strong, .project-tools > summary small { display: block; }
  .project-tools > summary strong { font-size: .61rem; }
  .project-tools > summary small { margin-top: .12rem; color: var(--muted-text, #687570); font-size: .49rem; }
  .summary-action { padding: .24rem .4rem; border: 1px solid var(--border, #dce4e0); border-radius: 6px; color: var(--muted-text, #687570); font-size: .47rem; }
  .tools-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: .7rem; padding: .75rem; border-top: 1px solid var(--border, #dce4e0); background: var(--surface-soft, #f7f9f8); }
  .tool-card { display: grid; align-content: start; gap: .6rem; padding: .72rem; border: 1px solid var(--border, #dce4e0); border-radius: 9px; background: var(--surface, #fff); }
  .tool-heading h3 { margin: .12rem 0 .16rem; font-size: .72rem; }
  .tool-heading p { margin: 0; color: var(--muted-text, #687570); font-size: .52rem; line-height: 1.45; }
  form { display: grid; gap: .48rem; }
  label { display: grid; gap: .22rem; color: #53615d; font-size: .56rem; font-weight: 650; }
  input, select { width: 100%; min-width: 0; box-sizing: border-box; padding: .48rem .54rem; border: 1px solid var(--border-strong, #cbd6d1); border-radius: 7px; outline: none; color: inherit; background: var(--surface, #fff); font-size: .62rem; }
  input:focus, select:focus { border-color: #70a999; box-shadow: 0 0 0 2px rgb(23 123 104 / 8%); }
  .primary-button, .secondary-button { justify-self: start; min-height: 32px; border-radius: 7px; padding: 0 .68rem; font-size: .59rem; font-weight: 700; cursor: pointer; }
  .primary-button { border: 1px solid var(--accent-strong, #0e6656); color: #fff; background: var(--accent, #177b68); }
  .secondary-button { border: 1px solid var(--border, #dce4e0); color: inherit; background: var(--surface, #fff); }
  button:disabled { cursor: not-allowed; opacity: .5; }
  .tool-alert { padding: .45rem .52rem; border: 1px solid var(--border, #dce4e0); border-radius: 7px; font-size: .53rem; }
  .tool-alert.error { color: #a53a43; border-color: #e8c1c5; background: #fff4f5; }
  .tool-empty { padding: .7rem; border: 1px dashed var(--border-strong, #cbd6d1); border-radius: 8px; color: var(--muted-text, #687570); font-size: .54rem; }
  .receipt { display: grid; gap: .48rem; padding: .55rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; background: var(--surface-soft, #f7f9f8); }
  .receipt.compact { gap: .16rem; }
  .receipt.compact strong { font-size: .58rem; }
  .receipt small { color: var(--muted-text, #687570); font-size: .48rem; }
  .receipt-heading { display: flex; align-items: center; justify-content: space-between; gap: .4rem; font-size: .5rem; }
  .receipt-heading code { color: var(--muted-text, #687570); }
  pre { max-height: 14rem; overflow: auto; margin: 0; padding: .5rem; border: 1px solid var(--border, #dce4e0); border-radius: 6px; background: var(--surface, #fff); font-size: .5rem; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
  .advanced-fields { padding-top: .2rem; }
  .advanced-fields summary { color: var(--muted-text, #687570); font-size: .52rem; font-weight: 650; cursor: pointer; }
  .advanced-fields label { margin-top: .45rem; }
  @media (max-width: 820px) {
    .analysis-header { flex-direction: column; }
    .tools-grid { grid-template-columns: 1fr; }
    .analysis-tabs { grid-template-columns: 1fr; }
    .analysis-tabs button { border-right: 0; border-bottom: 1px solid var(--border, #dce4e0); }
    .analysis-tabs button:last-child { border-bottom: 0; }
  }
</style>
