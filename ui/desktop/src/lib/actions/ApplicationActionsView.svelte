<script lang="ts">
  import type { DesktopBackendClient } from "../backend/client";
  import type {
    ApplicationReportPayload,
    PrepareWorkflowPayload,
    ReportFormat,
    WorkflowRecipeSummary,
  } from "../backend/contracts";
  import type { ScientificWorkspace } from "../workspace/contracts";

  export let client: DesktopBackendClient;
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
  $: if (
    rootSnapshotId.length > 0 &&
    !structureSnapshots.some((row) => row.entity_id === rootSnapshotId)
  ) {
    rootSnapshotId = "";
  }

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
    try {
      const response = await client.applicationReport(projectRoot, reportFormat);
      if (response.payload.project_id !== projectId) {
        throw new Error("application report receipt belongs to a different project");
      }
      reportReceipt = response.payload;
    } catch (error: unknown) {
      reportError = describeError(error, "application report could not be generated");
    } finally {
      reportBusy = false;
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
        throw new Error("prepare workflow receipt belongs to a different project");
      }
      workflowReceipt = response.payload;
      await onMutation();
    } catch (error: unknown) {
      workflowError = describeError(error, "workflow could not be prepared");
    } finally {
      workflowBusy = false;
    }
  }
</script>

<section class="actions-workspace" aria-labelledby="typed-actions-heading">
  <header class="actions-header">
    <div>
      <span class="eyebrow">Python application authority</span>
      <h2 id="typed-actions-heading">Typed application actions</h2>
      <p>
        These forms call explicit Python operations. They do not expose arbitrary entity mutation,
        scheduler-success shortcuts, or frontend-owned scientific defaults.
      </p>
    </div>
  </header>

  <div class="action-grid">
    <section class="action-card" aria-labelledby="report-action-heading">
      <div class="card-heading">
        <div>
          <span class="eyebrow">Transient deterministic output</span>
          <h3 id="report-action-heading">Scientific report</h3>
        </div>
      </div>
      <p class="authority-note">
        Delegates to <code>ProjectApplicationService.report()</code>. No project entity is created.
      </p>

      <form
        onsubmit={(event) => {
          event.preventDefault();
          void generateReport();
        }}
      >
        <label for="report-format">Format</label>
        <select id="report-format" bind:value={reportFormat} disabled={disabled || reportBusy}>
          <option value="json">JSON manifest</option>
          <option value="csv">Inventory CSV</option>
          <option value="markdown">Markdown</option>
        </select>
        <button type="submit" class="primary-action" disabled={disabled || reportBusy}>
          {reportBusy ? "Generating…" : "Generate current report"}
        </button>
      </form>

      {#if reportError.length > 0}
        <div class="action-error" role="alert">{reportError}</div>
      {/if}

      {#if reportReceipt !== null}
        <div class="receipt" aria-live="polite">
          <div class="receipt-grid">
            <div>
              <span>Report hash</span>
              <code title={reportReceipt.report_hash}>{shortHash(reportReceipt.report_hash)}</code>
            </div>
            <div>
              <span>Content hash</span>
              <code title={reportReceipt.content_sha256}>{shortHash(reportReceipt.content_sha256)}</code>
            </div>
          </div>
          <pre>{reportReceipt.content}</pre>
        </div>
      {/if}
    </section>

    <section class="action-card" aria-labelledby="workflow-action-heading">
      <div class="card-heading">
        <div>
          <span class="eyebrow">Durable workflow intent</span>
          <h3 id="workflow-action-heading">Prepare scientific workflow</h3>
        </div>
      </div>
      <p class="authority-note">
        Recipe identities come from the Python health catalog. The selected root is an exact persisted
        <code>StructureSnapshot</code> UUID.
      </p>

      {#if workflowRecipes.length === 0}
        <div class="action-error" role="alert">No canonical workflow recipes were advertised.</div>
      {:else if structureSnapshots.length === 0}
        <div class="empty-action">This project has no StructureSnapshot available as a workflow root.</div>
      {:else}
        <form
          onsubmit={(event) => {
            event.preventDefault();
            void prepareWorkflow();
          }}
        >
          <label for="workflow-recipe">Canonical workflow recipe</label>
          <select id="workflow-recipe" bind:value={recipeKey} disabled={disabled || workflowBusy}>
            <option value="">Select a recipe explicitly</option>
            {#each workflowRecipes as recipe (`${recipe.recipe_id}@${recipe.version}`)}
              <option value={`${recipe.recipe_id}@${recipe.version}`}>
                {recipe.recipe_id} @ {recipe.version}
              </option>
            {/each}
          </select>
          {#if selectedRecipe?.description}
            <p class="field-help">{selectedRecipe.description}</p>
          {/if}

          <label for="workflow-root">Root StructureSnapshot</label>
          <select id="workflow-root" bind:value={rootSnapshotId} disabled={disabled || workflowBusy}>
            <option value="">Select an exact snapshot</option>
            {#each structureSnapshots as row (row.entity_id)}
              <option value={row.entity_id}>
                {row.display_label} — {row.entity_id} — {row.freshness.state}
              </option>
            {/each}
          </select>

          <label for="parameters-hash">Parameters hash <span>(optional)</span></label>
          <input
            id="parameters-hash"
            bind:value={parametersHash}
            autocomplete="off"
            disabled={disabled || workflowBusy}
            placeholder="64-character SHA-256 only"
            aria-invalid={!parametersHashValid}
          />
          {#if !parametersHashValid}
            <p class="field-error">Parameters hash must be an exact SHA-256 digest.</p>
          {/if}

          <button type="submit" class="primary-action" disabled={!prepareReady}>
            {workflowBusy ? "Preparing…" : "Prepare / reuse workflow plan"}
          </button>
        </form>
      {/if}

      {#if workflowError.length > 0}
        <div class="action-error" role="alert">{workflowError}</div>
      {/if}

      {#if workflowReceipt !== null}
        <div class="receipt" aria-live="polite">
          <dl>
            <div>
              <dt>Workflow plan</dt>
              <dd>{workflowReceipt.workflow_plan_id}</dd>
            </div>
            <div>
              <dt>Plan hash</dt>
              <dd title={workflowReceipt.plan_hash}>{shortHash(workflowReceipt.plan_hash)}</dd>
            </div>
            <div>
              <dt>Planning hash</dt>
              <dd title={workflowReceipt.planning_hash}>{shortHash(workflowReceipt.planning_hash)}</dd>
            </div>
            <div>
              <dt>Persistence</dt>
              <dd>{workflowReceipt.reused ? "Reused exact existing plan" : "Persisted new plan"}</dd>
            </div>
          </dl>
          <p class="refresh-note">
            The workspace above was reloaded from ProjectStore after this mutation; the receipt was not
            used to patch scientific state locally.
          </p>
        </div>
      {/if}
    </section>
  </div>
</section>

<style>
  .actions-workspace {
    display: grid;
    gap: 1.25rem;
  }

  .actions-header h2,
  .action-card h3 {
    margin: 0.2rem 0 0.45rem;
  }

  .actions-header p,
  .authority-note,
  .field-help,
  .refresh-note {
    margin: 0;
    color: var(--muted-text, #5d6470);
    line-height: 1.55;
  }

  .eyebrow {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted-text, #5d6470);
  }

  .action-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
    gap: 1rem;
  }

  .action-card {
    border: 1px solid rgba(100, 110, 125, 0.24);
    border-radius: 12px;
    padding: 1rem;
    background: rgba(255, 255, 255, 0.42);
    min-width: 0;
  }

  .action-card form {
    display: grid;
    gap: 0.55rem;
    margin-top: 1rem;
  }

  label {
    font-weight: 650;
  }

  label span {
    font-weight: 400;
    color: var(--muted-text, #5d6470);
  }

  select,
  input {
    width: 100%;
    box-sizing: border-box;
    padding: 0.65rem 0.7rem;
    border: 1px solid rgba(100, 110, 125, 0.34);
    border-radius: 8px;
    background: inherit;
    color: inherit;
  }

  .primary-action {
    justify-self: start;
    margin-top: 0.35rem;
    padding: 0.65rem 0.85rem;
    border: 0;
    border-radius: 8px;
    font-weight: 700;
    cursor: pointer;
  }

  .primary-action:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }

  .action-error,
  .empty-action,
  .receipt {
    margin-top: 0.9rem;
    border: 1px solid rgba(100, 110, 125, 0.24);
    border-radius: 8px;
    padding: 0.75rem;
  }

  .action-error,
  .field-error {
    color: #8b1f2d;
  }

  .field-error {
    margin: 0;
    font-size: 0.86rem;
  }

  .receipt-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0.75rem;
    margin-bottom: 0.75rem;
  }

  .receipt-grid > div {
    display: grid;
    gap: 0.2rem;
  }

  .receipt-grid span,
  dt {
    color: var(--muted-text, #5d6470);
    font-size: 0.8rem;
  }

  pre {
    max-height: 22rem;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    margin: 0;
    padding: 0.75rem;
    border-radius: 6px;
    background: rgba(20, 25, 32, 0.06);
    font-size: 0.78rem;
  }

  dl {
    display: grid;
    gap: 0.6rem;
    margin: 0 0 0.75rem;
  }

  dl > div {
    display: grid;
    grid-template-columns: minmax(100px, 0.35fr) minmax(0, 1fr);
    gap: 0.5rem;
  }

  dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
</style>
