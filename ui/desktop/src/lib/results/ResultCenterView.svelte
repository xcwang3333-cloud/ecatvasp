<script lang="ts">
  import type { ResultCenterClient } from "./client";
  import type {
    AnalyzeResultPayload,
    PromoteResultPayload,
    ResultCalculationRow,
    ResultCatalogPayload,
  } from "./contracts";

  export let client: ResultCenterClient;
  export let projectRoot: string;
  export let disabled = false;
  export let onMutation: () => Promise<void>;

  let catalog: ResultCatalogPayload | null = null;
  let loadedProjectRoot = "";
  let busyCalculationId = "";
  let error = "";
  let message = "";
  let lastAnalysis: AnalyzeResultPayload | null = null;
  let lastPromotion: PromoteResultPayload | null = null;

  $: calculations = catalog?.calculations ?? [];
  $: if (projectRoot.trim() && projectRoot !== loadedProjectRoot) {
    loadedProjectRoot = projectRoot;
    catalog = null;
    busyCalculationId = "";
    error = "";
    message = "";
    lastAnalysis = null;
    lastPromotion = null;
    void loadCatalog(projectRoot);
  }

  function describeError(value: unknown, fallback: string): string {
    return value instanceof Error ? value.message : fallback;
  }

  function shortRecipe(recipeId: string): string {
    return recipeId.split(".").at(-1) ?? recipeId;
  }

  function energyLabel(value: number | null): string {
    return value === null ? "not reported" : `${value.toFixed(6)} eV`;
  }

  async function loadCatalog(root: string = projectRoot): Promise<void> {
    error = "";
    try {
      const next = await client.catalog(root);
      if (root !== projectRoot) return;
      catalog = next;
    } catch (value: unknown) {
      if (root !== projectRoot) return;
      error = describeError(value, "Result Center could not be loaded");
    }
  }

  async function analyze(row: ResultCalculationRow): Promise<void> {
    if (disabled || busyCalculationId || !row.analysis_ready) return;
    const root = projectRoot;
    busyCalculationId = row.calculation_id;
    error = "";
    message = "";
    lastPromotion = null;
    try {
      const analyzed = await client.analyze(root, row.calculation_id);
      if (root !== projectRoot) return;
      lastAnalysis = analyzed;
      message = `Scientific result classified as ${lastAnalysis.scientific_verdict}. Scheduler state was not used as convergence evidence.`;
      await loadCatalog(root);
      await onMutation();
    } catch (value: unknown) {
      if (root !== projectRoot) return;
      error = describeError(value, "Scientific result analysis was rejected");
    } finally {
      if (root === projectRoot) busyCalculationId = "";
    }
  }

  async function promote(row: ResultCalculationRow): Promise<void> {
    if (disabled || busyCalculationId || !row.promotion_ready) return;
    const root = projectRoot;
    busyCalculationId = row.calculation_id;
    error = "";
    message = "";
    lastAnalysis = null;
    try {
      const promoted = await client.promote(root, {
        calculation_id: row.calculation_id,
      });
      if (root !== projectRoot) return;
      lastPromotion = promoted;
      message = "Scientifically converged CONTCAR promoted to the current structure snapshot.";
      await loadCatalog(root);
      await onMutation();
    } catch (value: unknown) {
      if (root !== projectRoot) return;
      error = describeError(value, "Structure promotion was rejected");
    } finally {
      if (root === projectRoot) busyCalculationId = "";
    }
  }
</script>

<section class="result-center">
  <div class="section-heading">
    <div>
      <span class="eyebrow">Results</span>
      <h2>Scientific Result Center</h2>
      <p>Parse retrieved VASP outputs, classify convergence from exact evidence, and explicitly promote converged relaxed structures.</p>
    </div>
    <button class="text-button" type="button" disabled={disabled || !!busyCalculationId} onclick={() => void loadCatalog()}>Refresh</button>
  </div>

  <div class="result-boundary">
    <strong>Execution completion is not scientific success.</strong>
    <span>OUTCAR/OSZICAR evidence and the exact managed ExecutionPlan determine the scientific verdict. Promotion is a separate explicit action.</span>
  </div>

  {#if error}<div class="result-alert error" role="alert">{error}</div>{/if}
  {#if message}<div class="result-alert">{message}</div>{/if}

  {#if calculations.length === 0}
    <div class="result-empty">
      <strong>No calculations available</strong>
      <p>Prepare and execute a calculation, then retrieve its outputs from Job Center.</p>
    </div>
  {:else}
    <div class="result-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Calculation</th>
            <th>Scientific</th>
            <th>Attempt</th>
            <th>Retrieved</th>
            <th>Result state</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {#each calculations as row (row.calculation_id)}
            <tr>
              <td><strong>{shortRecipe(row.recipe_id)}</strong><small>{row.calculation_type}</small></td>
              <td><span class="state-chip">{row.scientific_status}</span></td>
              <td>{row.attempt_status ?? "—"}</td>
              <td>{row.retrieved_artifact_types.length ? row.retrieved_artifact_types.join(", ") : "—"}</td>
              <td>
                <strong>{row.analyzed ? "Analyzed" : row.analysis_ready ? "Ready to analyze" : "Waiting"}</strong>
                <small>{row.analysis_readiness_reason}</small>
              </td>
              <td class="result-actions">
                <button
                  class="text-button"
                  type="button"
                  disabled={disabled || !!busyCalculationId || !row.analysis_ready}
                  onclick={() => void analyze(row)}
                >
                  {busyCalculationId === row.calculation_id ? "Working…" : "Analyze"}
                </button>
                <button
                  class="primary-button"
                  type="button"
                  disabled={disabled || !!busyCalculationId || !row.promotion_ready}
                  onclick={() => void promote(row)}
                >Promote relaxed structure</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  {#if lastAnalysis !== null}
    <section class="result-summary">
      <div class="summary-heading">
        <div><span class="eyebrow">Latest analysis</span><h3>{lastAnalysis.scientific_verdict}</h3></div>
        <span class="state-chip">{lastAnalysis.termination_observed ? "normal termination" : "termination unproven"}</span>
      </div>
      <div class="result-metrics">
        <div><span>TOTEN</span><strong>{energyLabel(lastAnalysis.free_energy_toten_ev)}</strong></div>
        <div><span>Electronic</span><strong>{lastAnalysis.electronic_verdict}</strong></div>
        <div><span>Ionic</span><strong>{lastAnalysis.ionic_verdict}</strong></div>
        <div><span>Forces</span><strong>{lastAnalysis.force_count}</strong></div>
        <div><span>Frequency modes</span><strong>{lastAnalysis.frequency_mode_count}</strong></div>
      </div>
      <details>
        <summary>Advanced scientific evidence</summary>
        <p>{lastAnalysis.evidence_codes.join(" · ") || "No additional evidence codes"}</p>
        <code>{lastAnalysis.intake_hash}</code>
      </details>
    </section>
  {/if}

  {#if lastPromotion !== null}
    <section class="result-summary">
      <div class="summary-heading">
        <div><span class="eyebrow">Structure promotion</span><h3>Current structure updated</h3></div>
        <span class="state-chip">{lastPromotion.scientific_verdict}</span>
      </div>
      <p>The promoted snapshot preserves atom identities and descends from the exact relaxation input snapshot.</p>
      <details>
        <summary>Advanced promotion identity</summary>
        <dl>
          <div><dt>Promoted snapshot</dt><dd><code>{lastPromotion.promoted_structure_snapshot_id}</code></dd></div>
          <div><dt>Parent snapshot</dt><dd><code>{lastPromotion.parent_structure_snapshot_id}</code></dd></div>
          <div><dt>Source artifact</dt><dd><code>{lastPromotion.source_artifact_id}</code></dd></div>
        </dl>
      </details>
    </section>
  {/if}
</section>

<style>
  .result-center { display: grid; gap: 1rem; }
  .result-boundary, .result-alert, .result-empty, .result-summary { border: 1px solid var(--border-color, #d7dce2); border-radius: 0.75rem; padding: 1rem; background: var(--panel-background, #fff); }
  .result-boundary { display: grid; gap: 0.25rem; }
  .result-alert.error { border-color: #c44; }
  .result-table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.75rem; border-bottom: 1px solid var(--border-color, #d7dce2); vertical-align: top; }
  td small { display: block; margin-top: 0.25rem; opacity: 0.7; }
  .result-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
  .summary-heading { display: flex; justify-content: space-between; gap: 1rem; align-items: center; }
  .result-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 0.75rem; margin: 1rem 0; }
  .result-metrics > div { display: grid; gap: 0.2rem; }
  .result-metrics span { font-size: 0.8rem; opacity: 0.7; }
  code { overflow-wrap: anywhere; }
</style>
