<script lang="ts">
  import type { DesktopBackendClientV2 } from "../backend/client-v2";
  import type {
    ApplicationReportPayload,
    PrepareWorkflowPayload,
    ReportExportReceipt,
    ReportFormat,
    WorkflowRecipeSummary,
  } from "../backend/contracts";
  import type { ScientificWorkspace } from "../workspace/contracts";

  export let client: DesktopBackendClientV2;
  export let projectRoot: string;
  export let projectId: string;
  export let workflowRecipes: WorkflowRecipeSummary[];
  export let workspace: ScientificWorkspace;
  export let disabled = false;
  export let onMutation: () => Promise<void>;

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

<section class="actions-workspace" aria-labelledby="actions-heading">
  <header>
    <span class="eyebrow">Calculations and outputs</span>
    <h2 id="actions-heading">Project actions</h2>
    <p>Generate reports or prepare a canonical scientific workflow from current project state.</p>
  </header>

  <div class="action-grid">
    <section class="action-card">
      <h3>Scientific report</h3>
      <form onsubmit={(event) => { event.preventDefault(); void generateReport(); }}>
        <label for="report-format">Format</label>
        <select id="report-format" bind:value={reportFormat} disabled={disabled || reportBusy}>
          <option value="json">JSON</option><option value="csv">CSV</option><option value="markdown">Markdown</option>
        </select>
        <button type="submit" disabled={disabled || reportBusy}>{reportBusy ? "Generating…" : "Generate report"}</button>
      </form>
      {#if reportError}<div class="error" role="alert">{reportError}</div>{/if}
      {#if reportReceipt !== null}
        <div class="receipt">
          <div><span>Report hash</span><code title={reportReceipt.report_hash}>{shortHash(reportReceipt.report_hash)}</code></div>
          <pre>{reportReceipt.content}</pre>
          <form onsubmit={(event) => { event.preventDefault(); void exportReport(); }}>
            <label for="export-dir">Export directory</label>
            <input id="export-dir" bind:value={exportDirectory} placeholder="Existing absolute directory" />
            <button type="submit" disabled={exportBusy || !exportDirectory.trim()}>{exportBusy ? "Exporting…" : "Export exact report"}</button>
          </form>
          {#if exportError}<div class="error">{exportError}</div>{/if}
          {#if exportReceipt !== null}<small>{exportReceipt.reused ? "Existing exact file" : "Exported"} · {exportReceipt.file_name}</small>{/if}
        </div>
      {/if}
    </section>

    <section class="action-card">
      <h3>Prepare scientific workflow</h3>
      {#if structureSnapshots.length === 0}
        <p class="muted">Create or import a structure in Model Studio first.</p>
      {:else}
        <form onsubmit={(event) => { event.preventDefault(); void prepareWorkflow(); }}>
          <label for="workflow-recipe">Workflow recipe</label>
          <select id="workflow-recipe" bind:value={recipeKey} disabled={disabled || workflowBusy}>
            <option value="">Select recipe</option>
            {#each workflowRecipes as recipe (`${recipe.recipe_id}@${recipe.version}`)}
              <option value={`${recipe.recipe_id}@${recipe.version}`}>{recipe.recipe_id} @ {recipe.version}</option>
            {/each}
          </select>
          <label for="workflow-root">Structure</label>
          <select id="workflow-root" bind:value={rootSnapshotId} disabled={disabled || workflowBusy}>
            <option value="">Select structure</option>
            {#each structureSnapshots as row (row.entity_id)}
              <option value={row.entity_id}>{row.display_label} · {row.freshness.state}</option>
            {/each}
          </select>
          <details>
            <summary>Advanced parameters</summary>
            <label for="parameters-hash">Parameters hash</label>
            <input id="parameters-hash" bind:value={parametersHash} placeholder="Optional SHA-256" aria-invalid={!parametersHashValid} />
            {#if !parametersHashValid}<p class="error">Parameters hash must be SHA-256.</p>{/if}
          </details>
          <button type="submit" disabled={!prepareReady}>{workflowBusy ? "Preparing…" : "Prepare workflow"}</button>
        </form>
      {/if}
      {#if workflowError}<div class="error" role="alert">{workflowError}</div>{/if}
      {#if workflowReceipt !== null}
        <div class="receipt">
          <strong>{workflowReceipt.reused ? "Exact workflow reused" : "Workflow prepared"}</strong>
          <small>Plan {shortHash(workflowReceipt.plan_hash)}</small>
        </div>
      {/if}
    </section>
  </div>
</section>

<style>
  .actions-workspace { display: grid; gap: 1rem; }
  header h2, .action-card h3 { margin: .2rem 0 .35rem; }
  header p, .muted { margin: 0; color: var(--muted-text,#5d6470); }
  .eyebrow { font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted-text,#5d6470); }
  .action-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(310px,1fr)); gap: 1rem; }
  .action-card { border: 1px solid rgba(100,110,125,.22); border-radius: 12px; padding: 1rem; min-width: 0; }
  form { display: grid; gap: .55rem; margin-top: .8rem; }
  select, input { width: 100%; box-sizing: border-box; padding: .6rem .65rem; border: 1px solid rgba(100,110,125,.32); border-radius: 8px; background: inherit; color: inherit; }
  button { justify-self: start; border: 0; border-radius: 8px; padding: .62rem .8rem; font-weight: 700; cursor: pointer; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .error { margin-top: .7rem; color: #8b1f2d; }
  .receipt { display: grid; gap: .65rem; margin-top: .8rem; padding: .7rem; border: 1px solid rgba(100,110,125,.2); border-radius: 8px; }
  .receipt > div { display: flex; justify-content: space-between; gap: .5rem; }
  pre { max-height: 18rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; padding: .65rem; border-radius: 6px; background: rgba(20,25,32,.06); font-size: .76rem; }
  code { overflow-wrap: anywhere; }
  details { margin-top: .3rem; }
</style>
