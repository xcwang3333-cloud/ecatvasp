<script lang="ts">
  import { onMount } from "svelte";

  import type { JobCenterClient } from "./client";
  import type {
    ExecutionSettingsInput,
    ExecutionTargetInput,
    JobCalculationRow,
    JobCatalogPayload,
    PrepareExecutionPayload,
    RemotePotcarInput,
  } from "./contracts";

  export let client: JobCenterClient;
  export let projectRoot: string;
  export let disabled = false;
  export let onMutation: () => Promise<void>;

  let catalog: JobCatalogPayload | null = null;
  let selectedCalculationId = "";
  let preview: PrepareExecutionPayload | null = null;
  let busy = false;
  let error = "";
  let message = "";
  type JobView = "queue" | "prepare" | "cluster";
  let jobView: JobView = "queue";

  let localPotcarRoot = "";
  let nodes = 1;
  let cores = 32;
  let mpiRanks = 32;
  let walltimeMinutes = 120;
  let memoryMb = 0;
  let partition = "";
  let executable = "vasp_std";

  let targetId = "cluster";
  let hostAlias = "cluster";
  let remoteWorkRoot = "/scratch/ecatvasp";
  let launcher = "srun";
  let moduleLoads = "";
  let remoteVaspExecutable = "vasp_std";

  let remotePotcarResolverId = "pbe54-remote";
  let remotePotcarFamily = "PBE_54";
  let remotePotcarRoot = "";

  $: calculations = catalog?.calculations ?? [];
  $: selectedCalculation =
    calculations.find((item) => item.calculation_id === selectedCalculationId) ?? null;

  function describeError(value: unknown, fallback: string): string {
    return value instanceof Error ? value.message : fallback;
  }

  function executionSettings(): ExecutionSettingsInput {
    if (!Number.isInteger(nodes) || nodes < 1) throw new Error("Nodes must be a positive integer");
    if (!Number.isInteger(cores) || cores < 1) throw new Error("Cores must be a positive integer");
    if (!Number.isInteger(mpiRanks) || mpiRanks < 1) {
      throw new Error("MPI ranks must be a positive integer");
    }
    if (!Number.isFinite(walltimeMinutes) || walltimeMinutes <= 0) {
      throw new Error("Walltime must be positive");
    }
    const result: ExecutionSettingsInput = {
      nodes,
      cores,
      mpi_ranks: mpiRanks,
      walltime_seconds: Math.round(walltimeMinutes * 60),
      executable: executable.trim() || "vasp_std",
    };
    if (memoryMb > 0) result.memory_mb = Math.round(memoryMb);
    if (partition.trim()) result.partition = partition.trim();
    return result;
  }

  function target(): ExecutionTargetInput {
    const modules = moduleLoads
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!targetId.trim() || !hostAlias.trim() || !remoteWorkRoot.trim()) {
      throw new Error("Target name, SSH host alias, and remote work root are required");
    }
    return {
      target_id: targetId.trim(),
      host_alias: hostAlias.trim(),
      remote_work_root: remoteWorkRoot.trim(),
      potcar_resolver_id: remotePotcarResolverId.trim(),
      vasp_executable: remoteVaspExecutable.trim() || "vasp_std",
      ...(launcher.trim() ? { launcher: launcher.trim() } : {}),
      module_loads: modules,
    };
  }

  function remotePotcar(): RemotePotcarInput {
    if (!remotePotcarResolverId.trim() || !remotePotcarFamily.trim() || !remotePotcarRoot.trim()) {
      throw new Error("Remote POTCAR resolver, family, and root are required before submission");
    }
    return {
      resolver_id: remotePotcarResolverId.trim(),
      family: remotePotcarFamily.trim(),
      root: remotePotcarRoot.trim(),
    };
  }

  async function loadCatalog(preserveSelection = true): Promise<void> {
    busy = true;
    error = "";
    try {
      const current = preserveSelection ? selectedCalculationId : "";
      catalog = await client.catalog(projectRoot);
      if (current && catalog.calculations.some((item) => item.calculation_id === current)) {
        selectedCalculationId = current;
      } else {
        selectedCalculationId = catalog.calculations[0]?.calculation_id ?? "";
      }
    } catch (value: unknown) {
      error = describeError(value, "Job Center could not be loaded");
    } finally {
      busy = false;
    }
  }

  function selectCalculation(event: Event): void {
    selectedCalculationId = (event.currentTarget as HTMLSelectElement).value;
    preview = null;
    message = "";
    error = "";
  }

  async function prepareExecution(): Promise<void> {
    if (selectedCalculation === null || busy) return;
    if (!localPotcarRoot.trim()) {
      error = "Licensed local POTCAR root is required to prepare exact VASP inputs.";
      return;
    }
    busy = true;
    error = "";
    message = "";
    try {
      preview = await client.prepare(projectRoot, {
        calculation_id: selectedCalculation.calculation_id,
        potcar_root: localPotcarRoot.trim(),
        execution_settings: executionSettings(),
      });
      message = preview.reused_input_artifacts
        ? "Exact VASP input artifacts verified and reused."
        : "Exact VASP input artifacts prepared. Review the execution summary before submission.";
      await onMutation();
    } catch (value: unknown) {
      preview = null;
      error = describeError(value, "Execution preparation was rejected");
    } finally {
      busy = false;
    }
  }

  async function submit(): Promise<void> {
    if (selectedCalculation === null || preview === null || busy) return;
    busy = true;
    error = "";
    message = "";
    try {
      const receipt = await client.submit(projectRoot, {
        calculation_id: selectedCalculation.calculation_id,
        potcar_root: localPotcarRoot.trim(),
        execution_settings: executionSettings(),
        target: target(),
        remote_potcar: remotePotcar(),
      });
      message = `Slurm job ${receipt.scheduler_job_id} submitted; scheduler state: ${receipt.scheduler_state}. Scientific convergence remains unclassified until results are parsed.`;
      preview = null;
      await loadCatalog();
      await onMutation();
    } catch (value: unknown) {
      error = describeError(value, "Slurm submission was rejected");
    } finally {
      busy = false;
    }
  }

  async function refreshJob(row: JobCalculationRow): Promise<void> {
    if (row.remote_job_id === null || busy) return;
    busy = true;
    error = "";
    message = "";
    try {
      const receipt = await client.refresh(projectRoot, {
        remote_job_id: row.remote_job_id,
        target: target(),
      });
      message = `Scheduler state refreshed: ${receipt.scheduler_state}. Scientific status is unchanged by monitoring.`;
      await loadCatalog();
      await onMutation();
    } catch (value: unknown) {
      error = describeError(value, "Job state could not be refreshed");
    } finally {
      busy = false;
    }
  }

  async function cancelJob(row: JobCalculationRow): Promise<void> {
    if (row.remote_job_id === null || busy) return;
    busy = true;
    error = "";
    message = "";
    try {
      const receipt = await client.cancel(projectRoot, {
        remote_job_id: row.remote_job_id,
        target: target(),
      });
      message = `Cancellation observed with scheduler state: ${receipt.scheduler_state}.`;
      await loadCatalog();
      await onMutation();
    } catch (value: unknown) {
      error = describeError(value, "Job cancellation could not be confirmed");
    } finally {
      busy = false;
    }
  }

  async function retrieve(row: JobCalculationRow): Promise<void> {
    if (row.remote_job_id === null || busy) return;
    busy = true;
    error = "";
    message = "";
    try {
      const receipt = await client.retrieve(projectRoot, {
        remote_job_id: row.remote_job_id,
        target: target(),
      });
      message = `${receipt.retrieved_artifact_ids.length} output artifact(s) retrieved. Result parsing and scientific convergence assessment are handled separately.`;
      await loadCatalog();
      await onMutation();
    } catch (value: unknown) {
      error = describeError(value, "Outputs could not be retrieved");
    } finally {
      busy = false;
    }
  }

  function shortRecipe(recipeId: string): string {
    return recipeId.split(".").at(-1) ?? recipeId;
  }

  onMount(() => void loadCatalog(false));
</script>

<section class="job-center" aria-labelledby="job-center-heading">
  <header class="job-header">
    <div>
      <span class="eyebrow">Execution workspace</span>
      <h2 id="job-center-heading">Jobs & HPC</h2>
      <p>Prepare exact VASP inputs, submit through system OpenSSH/Slurm, monitor scheduler state, and retrieve outputs while keeping execution evidence separate from scientific convergence.</p>
    </div>
    <div class="header-actions">
      {#if calculations.length > 0}
        <div class="queue-summary">
          <span><strong>{calculations.length}</strong> calculations</span>
          <span><strong>{calculations.filter((row) => row.remote_job_id !== null).length}</strong> remote</span>
          <span><strong>{calculations.filter((row) => row.scheduler_state !== null).length}</strong> tracked</span>
        </div>
      {/if}
      <button class="secondary-button" type="button" disabled={busy || disabled} onclick={() => void loadCatalog()}>
        {busy ? "Working…" : "Refresh"}
      </button>
    </div>
  </header>

  {#if error}<div class="job-alert error" role="alert">{error}</div>{/if}
  {#if message}<div class="job-alert success">{message}</div>{/if}

  {#if calculations.length === 0}
    <div class="job-empty">
      <span class="empty-icon">HPC</span>
      <div><strong>No materialized calculations</strong><p>Prepare and materialize a calculation in VASP Setup before creating an execution attempt.</p></div>
    </div>
  {:else}
    <div class="job-layout">
      <main class="job-main">
        <nav class="job-tabs" aria-label="Job Center surface">
          <button type="button" class:active={jobView === "queue"} onclick={() => (jobView = "queue")}><span>01</span><strong>Queue</strong><small>Monitor & retrieve</small></button>
          <button type="button" class:active={jobView === "prepare"} onclick={() => (jobView = "prepare")}><span>02</span><strong>Prepare</strong><small>Exact execution</small></button>
          <button type="button" class:active={jobView === "cluster"} onclick={() => (jobView = "cluster")}><span>03</span><strong>Cluster</strong><small>SSH / Slurm target</small></button>
        </nav>

        {#if jobView === "queue"}
          <section class="queue-panel">
            <header class="panel-heading">
              <div><span class="eyebrow">Run history</span><h3>Calculation queue</h3><p>Select a row to inspect its scientific, execution-attempt, and scheduler states.</p></div>
            </header>
            <div class="job-table-wrap">
              <table>
                <thead>
                  <tr><th>Calculation</th><th>Scientific</th><th>Attempt</th><th>Scheduler</th><th>Slurm ID</th><th></th></tr>
                </thead>
                <tbody>
                  {#each calculations as row (row.calculation_id)}
                    <tr class:selected={row.calculation_id === selectedCalculationId}>
                      <td>
                        <button
                          class="calculation-cell"
                          type="button"
                          onclick={() => { selectedCalculationId = row.calculation_id; preview = null; message = ""; error = ""; }}
                        >
                          <strong>{shortRecipe(row.recipe_id)}</strong>
                          <small>{row.calculation_type}</small>
                        </button>
                      </td>
                      <td><span class="state-badge scientific">{row.scientific_status}</span></td>
                      <td><span class="state-badge">{row.attempt_status ?? "not started"}</span></td>
                      <td><span class="state-badge scheduler">{row.scheduler_state ?? "not submitted"}</span></td>
                      <td><code>{row.scheduler_job_id ?? "—"}</code></td>
                      <td>
                        {#if row.remote_job_id !== null}
                          <div class="row-actions">
                            <button type="button" disabled={busy || disabled} onclick={() => void refreshJob(row)}>Refresh</button>
                            <button type="button" disabled={busy || disabled} onclick={() => void retrieve(row)}>Retrieve</button>
                            <button class="danger-action" type="button" disabled={busy || disabled} onclick={() => void cancelJob(row)}>Cancel</button>
                          </div>
                        {:else}
                          <button class="prepare-link" type="button" onclick={() => { selectedCalculationId = row.calculation_id; jobView = "prepare"; }}>Prepare →</button>
                        {/if}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </section>
        {:else if jobView === "prepare"}
          <section class="execution-panel">
            <header class="panel-heading">
              <div><span class="eyebrow">Execution preparation</span><h3>Materialize and verify exact run inputs</h3><p>The local POTCAR resolver and resource request are validated before a remote submission can be created.</p></div>
            </header>

            <div class="execution-grid">
              <div class="execution-form">
                <label>Calculation<select value={selectedCalculationId} onchange={selectCalculation} disabled={busy || disabled}>
                  {#each calculations as row (row.calculation_id)}
                    <option value={row.calculation_id}>{shortRecipe(row.recipe_id)} · {row.calculation_type}</option>
                  {/each}
                </select></label>

                <label>Licensed local POTCAR root<input bind:value={localPotcarRoot} placeholder="C:\VASP\PBE_54" /></label>

                <div class="resource-block">
                  <div class="subheading"><strong>Compute resources</strong><span>Slurm request</span></div>
                  <div class="resource-grid">
                    <label>Nodes<input type="number" min="1" step="1" bind:value={nodes} /></label>
                    <label>Cores<input type="number" min="1" step="1" bind:value={cores} /></label>
                    <label>MPI ranks<input type="number" min="1" step="1" bind:value={mpiRanks} /></label>
                    <label>Walltime / min<input type="number" min="1" step="1" bind:value={walltimeMinutes} /></label>
                    <label>Memory / MB<input type="number" min="0" step="1" bind:value={memoryMb} /></label>
                    <label>Partition<input bind:value={partition} placeholder="optional" /></label>
                  </div>
                </div>

                <label>Local VASP executable<input bind:value={executable} /></label>
                <button class="primary-button" type="button" disabled={busy || disabled || selectedCalculation === null} onclick={() => void prepareExecution()}>
                  {busy ? "Preparing…" : "Prepare exact execution"}
                </button>
              </div>

              <aside class="prepare-explainer">
                <span class="eyebrow">Exact-input boundary</span>
                <h4>What happens here</h4>
                <ol>
                  <li><span>1</span><p><strong>Resolve POTCAR</strong><small>From your licensed local root.</small></p></li>
                  <li><span>2</span><p><strong>Verify inputs</strong><small>Reuse only byte-identical scientific artifacts.</small></p></li>
                  <li><span>3</span><p><strong>Freeze execution request</strong><small>Resources and expected outputs become explicit.</small></p></li>
                </ol>
              </aside>
            </div>

            {#if preview !== null}
              <div class="execution-preview">
                <div class="preview-heading">
                  <div><span class="eyebrow">Prepared execution</span><h4>{preview.step_key}</h4></div>
                  <span class="ready-chip">{preview.reused_input_artifacts ? "Verified / reused" : "Prepared"}</span>
                </div>
                <div class="preview-grid">
                  <div><span>Expected outputs</span><strong>{preview.expected_outputs.length}</strong></div>
                  <div><span>Required outputs</span><strong>{preview.expected_outputs.filter((item) => item.required).length}</strong></div>
                  <div><span>Input state</span><strong>{preview.reused_input_artifacts ? "Exact reuse" : "New materialization"}</strong></div>
                </div>
                <details>
                  <summary>Execution identity</summary>
                  <dl>
                    <div><dt>Plan hash</dt><dd><code>{preview.plan_hash}</code></dd></div>
                    <div><dt>Input manifest</dt><dd><code>{preview.input_manifest_sha256}</code></dd></div>
                  </dl>
                </details>
                <button class="primary-button" type="button" onclick={() => (jobView = "cluster")}>Continue to cluster →</button>
              </div>
            {/if}
          </section>
        {:else}
          <section class="cluster-panel">
            <header class="panel-heading">
              <div><span class="eyebrow">Remote execution target</span><h3>System OpenSSH + Slurm</h3><p>Authentication stays in your operating-system OpenSSH configuration. ECatVASP never accepts passwords, private keys, or tokens in this form.</p></div>
            </header>

            <div class="cluster-grid">
              <div class="cluster-form">
                <div class="subheading"><strong>SSH / scheduler</strong><span>Target identity</span></div>
                <div class="field-grid two">
                  <label>Target name<input bind:value={targetId} /></label>
                  <label>SSH host alias<input bind:value={hostAlias} placeholder="cluster-a" /></label>
                </div>
                <label>Remote work root<input bind:value={remoteWorkRoot} /></label>
                <div class="field-grid two">
                  <label>Launcher<input bind:value={launcher} /></label>
                  <label>Remote VASP executable<input bind:value={remoteVaspExecutable} /></label>
                </div>
                <label>Modules <small>comma/newline separated</small><input bind:value={moduleLoads} placeholder="intel, vasp/6" /></label>

                <div class="subheading potcar-heading"><strong>Remote POTCAR resolver</strong><span>Licensed site path</span></div>
                <div class="field-grid two">
                  <label>Resolver ID<input bind:value={remotePotcarResolverId} /></label>
                  <label>Family<input bind:value={remotePotcarFamily} /></label>
                </div>
                <label>Remote POTCAR root<input bind:value={remotePotcarRoot} placeholder="/apps/vasp/potpaw_PBE.54" /></label>
              </div>

              <aside class="submission-card">
                <span class="eyebrow">Submission</span>
                <h4>{selectedCalculation ? shortRecipe(selectedCalculation.recipe_id) : "Select calculation"}</h4>
                <dl>
                  <div><dt>Host alias</dt><dd>{hostAlias || "—"}</dd></div>
                  <div><dt>Work root</dt><dd>{remoteWorkRoot || "—"}</dd></div>
                  <div><dt>Launcher</dt><dd>{launcher || "—"}</dd></div>
                  <div><dt>Resources</dt><dd>{nodes} node · {mpiRanks} MPI · {walltimeMinutes} min</dd></div>
                </dl>
                <div class="boundary-note">
                  <strong>Scheduler completion ≠ scientific convergence</strong>
                  <p>Retrieved VASP outputs must still be parsed and classified by the Python scientific authority.</p>
                </div>
                <button class="primary-button" type="button" disabled={busy || disabled || preview === null} onclick={() => void submit()}>Submit to Slurm</button>
                {#if preview === null}<small class="submit-hint">Prepare exact execution first.</small>{/if}
              </aside>
            </div>
          </section>
        {/if}
      </main>

      <aside class="job-inspector">
        <div class="inspector-heading">
          <span class="eyebrow">Selected calculation</span>
          <h3>{selectedCalculation ? shortRecipe(selectedCalculation.recipe_id) : "None"}</h3>
          {#if selectedCalculation}<p>{selectedCalculation.calculation_type}</p>{/if}
        </div>

        {#if selectedCalculation !== null}
          <div class="state-stack">
            <div>
              <span>Scientific</span>
              <strong>{selectedCalculation.scientific_status}</strong>
              <small>Result interpretation</small>
            </div>
            <div>
              <span>Attempt</span>
              <strong>{selectedCalculation.attempt_status ?? "not started"}</strong>
              <small>Execution lifecycle</small>
            </div>
            <div>
              <span>Scheduler</span>
              <strong>{selectedCalculation.scheduler_state ?? "not submitted"}</strong>
              <small>Slurm observation</small>
            </div>
          </div>

          <div class="identity-block">
            <div><span>Calculation ID</span><code>{selectedCalculation.calculation_id}</code></div>
            <div><span>Slurm job</span><code>{selectedCalculation.scheduler_job_id ?? "—"}</code></div>
          </div>

          {#if selectedCalculation.remote_job_id !== null}
            <div class="inspector-actions">
              <button type="button" disabled={busy || disabled} onclick={() => void refreshJob(selectedCalculation)}>Refresh state</button>
              <button type="button" disabled={busy || disabled} onclick={() => void retrieve(selectedCalculation)}>Retrieve outputs</button>
            </div>
          {:else}
            <button class="primary-button inspector-primary" type="button" onclick={() => (jobView = "prepare")}>Prepare execution</button>
          {/if}
        {:else}
          <div class="inspector-empty">Select a calculation from the queue.</div>
        {/if}

        <div class="scientific-boundary">
          <strong>Three independent states</strong>
          <p>Scientific status, execution attempt state, and scheduler state are intentionally displayed separately.</p>
        </div>
      </aside>
    </div>
  {/if}
</section>

<style>
  .job-center { display: grid; gap: .8rem; color: var(--text, inherit); }
  .job-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1.4rem; padding-bottom: .85rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .job-header h2 { margin: .16rem 0 .25rem; font-size: 1.2rem; letter-spacing: -.025em; }
  .job-header p { max-width: 790px; margin: 0; color: var(--muted-text, #687570); font-size: .72rem; line-height: 1.55; }
  .eyebrow { color: var(--accent, #177b68); font-size: .58rem; font-weight: 750; letter-spacing: .1em; text-transform: uppercase; }
  .header-actions { display: flex; align-items: center; gap: .55rem; }
  .queue-summary { display: flex; gap: .3rem; }
  .queue-summary span { padding: .3rem .42rem; border: 1px solid var(--border, #dce4e0); border-radius: 7px; color: var(--muted-text, #687570); background: var(--surface, #fff); font-size: .52rem; white-space: nowrap; }
  .queue-summary strong { color: var(--text, #182321); font-size: .61rem; }
  .primary-button, .secondary-button { min-height: 34px; border-radius: 7px; padding: 0 .72rem; font-size: .62rem; font-weight: 700; cursor: pointer; }
  .primary-button { border: 1px solid var(--accent-strong, #0e6656); color: #fff; background: var(--accent, #177b68); }
  .secondary-button { border: 1px solid var(--border, #dce4e0); color: inherit; background: var(--surface, #fff); }
  button:disabled { cursor: not-allowed; opacity: .5; }
  .job-alert { padding: .55rem .7rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; font-size: .64rem; }
  .job-alert.error { color: #a53a43; border-color: #e8c1c5; background: #fff4f5; }
  .job-alert.success { color: #226c52; border-color: #bcdccf; background: #eff9f5; }
  .job-empty { display: flex; align-items: center; gap: .8rem; padding: 1.2rem; border: 1px dashed var(--border-strong, #cbd6d1); border-radius: 10px; background: var(--surface, #fff); }
  .empty-icon { display: inline-flex; width: 40px; height: 40px; align-items: center; justify-content: center; border-radius: 9px; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); font-size: .55rem; font-weight: 800; }
  .job-empty strong { font-size: .72rem; }
  .job-empty p { margin: .22rem 0 0; color: var(--muted-text, #687570); font-size: .61rem; }
  .job-layout { display: grid; grid-template-columns: minmax(0,1fr) 245px; gap: .75rem; }
  .job-main, .job-inspector { min-width: 0; border: 1px solid var(--border, #dce4e0); border-radius: 11px; background: var(--surface, #fff); overflow: hidden; }
  .job-tabs { display: grid; grid-template-columns: repeat(3,1fr); gap: 0; border-bottom: 1px solid var(--border, #dce4e0); background: var(--surface-soft, #f7f9f8); }
  .job-tabs button { display: grid; grid-template-columns: 26px minmax(0,1fr); grid-template-rows: auto auto; align-items: center; gap: .05rem .4rem; padding: .62rem .72rem; border: 0; border-right: 1px solid var(--border, #dce4e0); color: var(--muted-text, #687570); background: transparent; text-align: left; cursor: pointer; }
  .job-tabs button:last-child { border-right: 0; }
  .job-tabs button:hover, .job-tabs button.active { background: var(--surface, #fff); }
  .job-tabs button.active { box-shadow: inset 0 -2px 0 var(--accent, #177b68); color: var(--text, #182321); }
  .job-tabs button > span { grid-row: 1 / 3; display: inline-flex; width: 24px; height: 24px; align-items: center; justify-content: center; border: 1px solid var(--border, #dce4e0); border-radius: 6px; font-size: .47rem; font-weight: 750; }
  .job-tabs button.active > span { color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); }
  .job-tabs strong { font-size: .6rem; }
  .job-tabs small { color: var(--muted-text, #687570); font-size: .47rem; }
  .queue-panel, .execution-panel, .cluster-panel { display: grid; gap: 0; }
  .panel-heading { padding: .8rem .9rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .panel-heading h3 { margin: .12rem 0 .18rem; font-size: .8rem; }
  .panel-heading p { margin: 0; color: var(--muted-text, #687570); font-size: .55rem; line-height: 1.45; }
  .job-table-wrap { overflow: auto; }
  table { width: 100%; min-width: 760px; border-collapse: collapse; }
  th { padding: .48rem .55rem; border-bottom: 1px solid var(--border, #dce4e0); color: var(--subtle-text, #84908b); background: var(--surface-soft, #f7f9f8); font-size: .47rem; font-weight: 750; letter-spacing: .06em; text-align: left; text-transform: uppercase; }
  td { padding: .45rem .55rem; border-bottom: 1px solid var(--border, #dce4e0); vertical-align: middle; font-size: .54rem; }
  tbody tr.selected { background: var(--accent-soft, #e7f4ef); }
  .calculation-cell { display: grid; gap: .08rem; width: 100%; min-width: 120px; padding: 0; border: 0; color: inherit; background: transparent; text-align: left; cursor: pointer; }
  .calculation-cell strong { font-size: .58rem; }
  .calculation-cell small { color: var(--muted-text, #687570); font-size: .47rem; }
  .state-badge { display: inline-flex; max-width: 120px; align-items: center; padding: .22rem .35rem; border: 1px solid var(--border, #dce4e0); border-radius: 999px; color: var(--muted-text, #687570); background: var(--surface, #fff); font-size: .46rem; white-space: nowrap; }
  .state-badge.scientific { border-color: #b8d4ca; }
  .state-badge.scheduler { background: var(--surface-soft, #f7f9f8); }
  td code { color: var(--muted-text, #687570); font-size: .48rem; }
  .row-actions { display: flex; gap: .2rem; }
  .row-actions button, .prepare-link { border: 1px solid var(--border, #dce4e0); border-radius: 5px; padding: .25rem .34rem; color: #52625d; background: var(--surface, #fff); font-size: .45rem; font-weight: 650; cursor: pointer; }
  .row-actions .danger-action { color: #9e3d45; }
  .prepare-link { white-space: nowrap; }
  .execution-grid { display: grid; grid-template-columns: minmax(0,1fr) 220px; gap: .7rem; padding: .8rem; }
  .execution-form, .cluster-form { display: grid; align-content: start; gap: .62rem; }
  label { display: grid; gap: .24rem; color: #53615d; font-size: .57rem; font-weight: 650; }
  label small { color: var(--muted-text, #687570); font-weight: 400; }
  input, select { width: 100%; min-width: 0; box-sizing: border-box; padding: .49rem .54rem; border: 1px solid var(--border-strong, #cbd6d1); border-radius: 7px; outline: none; color: inherit; background: var(--surface, #fff); font-size: .63rem; }
  input:focus, select:focus { border-color: #70a999; box-shadow: 0 0 0 2px rgb(23 123 104 / 8%); }
  .resource-block { display: grid; gap: .45rem; padding: .58rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; background: var(--surface-soft, #f7f9f8); }
  .subheading { display: flex; justify-content: space-between; gap: .5rem; align-items: center; }
  .subheading strong { font-size: .58rem; }
  .subheading span { color: var(--muted-text, #687570); font-size: .47rem; }
  .resource-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: .42rem; }
  .prepare-explainer { padding: .7rem; border-radius: 9px; background: var(--surface-soft, #f7f9f8); }
  .prepare-explainer h4 { margin: .12rem 0 .45rem; font-size: .66rem; }
  .prepare-explainer ol { display: grid; gap: .45rem; margin: 0; padding: 0; list-style: none; }
  .prepare-explainer li { display: grid; grid-template-columns: 23px 1fr; gap: .4rem; align-items: start; }
  .prepare-explainer li > span { display: inline-flex; width: 22px; height: 22px; align-items: center; justify-content: center; border-radius: 6px; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); font-size: .47rem; font-weight: 800; }
  .prepare-explainer p { margin: 0; }
  .prepare-explainer strong, .prepare-explainer small { display: block; }
  .prepare-explainer strong { font-size: .54rem; }
  .prepare-explainer small { margin-top: .1rem; color: var(--muted-text, #687570); font-size: .47rem; line-height: 1.4; }
  .execution-preview { display: grid; gap: .55rem; margin: 0 .8rem .8rem; padding: .7rem; border: 1px solid #b5d6ca; border-radius: 9px; background: var(--accent-soft, #e7f4ef); }
  .preview-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: .7rem; }
  .preview-heading h4 { margin: .1rem 0 0; font-size: .67rem; }
  .ready-chip { padding: .24rem .4rem; border: 1px solid #98c7b8; border-radius: 999px; color: var(--accent-strong, #0e6656); background: rgb(255 255 255 / 55%); font-size: .47rem; font-weight: 700; }
  .preview-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: .4rem; }
  .preview-grid div { padding: .45rem; border-radius: 7px; background: rgb(255 255 255 / 55%); }
  .preview-grid span, .preview-grid strong { display: block; }
  .preview-grid span { color: var(--muted-text, #687570); font-size: .45rem; }
  .preview-grid strong { margin-top: .12rem; font-size: .55rem; }
  .execution-preview summary { font-size: .5rem; font-weight: 700; cursor: pointer; }
  .execution-preview dl { display: grid; gap: .3rem; margin: .4rem 0 0; }
  .execution-preview dl div { display: grid; grid-template-columns: 85px 1fr; gap: .4rem; font-size: .46rem; }
  .execution-preview dd { margin: 0; overflow-wrap: anywhere; }
  .cluster-grid { display: grid; grid-template-columns: minmax(0,1fr) 230px; gap: .7rem; padding: .8rem; }
  .field-grid { display: grid; gap: .48rem; }
  .field-grid.two { grid-template-columns: 1fr 1fr; }
  .potcar-heading { margin-top: .35rem; padding-top: .55rem; border-top: 1px solid var(--border, #dce4e0); }
  .submission-card { align-self: start; padding: .7rem; border: 1px solid var(--border, #dce4e0); border-radius: 9px; background: var(--surface-soft, #f7f9f8); }
  .submission-card h4 { margin: .12rem 0 .45rem; font-size: .68rem; }
  .submission-card dl { display: grid; gap: 0; margin: 0; }
  .submission-card dl div { padding: .4rem 0; border-bottom: 1px solid var(--border, #dce4e0); }
  .submission-card dt { color: var(--subtle-text, #84908b); font-size: .45rem; text-transform: uppercase; }
  .submission-card dd { margin: .1rem 0 0; overflow-wrap: anywhere; font-size: .53rem; }
  .boundary-note { margin: .65rem 0; padding: .55rem; border-radius: 7px; background: var(--accent-soft, #e7f4ef); }
  .boundary-note strong { font-size: .52rem; }
  .boundary-note p { margin: .15rem 0 0; color: var(--muted-text, #687570); font-size: .46rem; line-height: 1.4; }
  .submit-hint { display: block; margin-top: .3rem; color: var(--muted-text, #687570); font-size: .46rem; }
  .job-inspector { align-self: start; padding: .78rem; }
  .inspector-heading { padding-bottom: .55rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .inspector-heading h3 { margin: .12rem 0 0; overflow: hidden; font-size: .72rem; text-overflow: ellipsis; white-space: nowrap; }
  .inspector-heading p { margin: .12rem 0 0; color: var(--muted-text, #687570); font-size: .49rem; }
  .state-stack { display: grid; gap: .35rem; margin-top: .65rem; }
  .state-stack > div { padding: .52rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; background: var(--surface-soft, #f7f9f8); }
  .state-stack span, .state-stack strong, .state-stack small { display: block; }
  .state-stack span { color: var(--subtle-text, #84908b); font-size: .44rem; font-weight: 700; text-transform: uppercase; }
  .state-stack strong { margin-top: .12rem; overflow-wrap: anywhere; font-size: .57rem; }
  .state-stack small { margin-top: .12rem; color: var(--muted-text, #687570); font-size: .44rem; }
  .identity-block { display: grid; gap: .42rem; margin-top: .65rem; padding-top: .6rem; border-top: 1px solid var(--border, #dce4e0); }
  .identity-block span { display: block; margin-bottom: .14rem; color: var(--subtle-text, #84908b); font-size: .43rem; text-transform: uppercase; }
  .identity-block code { display: block; overflow-wrap: anywhere; color: var(--muted-text, #687570); font-size: .44rem; }
  .inspector-actions { display: grid; grid-template-columns: 1fr 1fr; gap: .35rem; margin-top: .65rem; }
  .inspector-actions button { min-height: 30px; border: 1px solid var(--border, #dce4e0); border-radius: 6px; color: inherit; background: var(--surface, #fff); font-size: .47rem; font-weight: 650; cursor: pointer; }
  .inspector-primary { width: 100%; margin-top: .65rem; }
  .inspector-empty { margin-top: .65rem; color: var(--muted-text, #687570); font-size: .52rem; }
  .scientific-boundary { margin-top: .7rem; padding: .6rem; border-radius: 8px; background: var(--surface-muted, #eef3f0); }
  .scientific-boundary strong { font-size: .52rem; }
  .scientific-boundary p { margin: .16rem 0 0; color: var(--muted-text, #687570); font-size: .46rem; line-height: 1.4; }
  @media (max-width: 1080px) {
    .job-layout { grid-template-columns: 1fr; }
    .job-inspector { display: grid; grid-template-columns: 180px 1fr 220px; gap: .65rem; }
    .state-stack { grid-template-columns: repeat(3,1fr); margin-top: 0; }
    .scientific-boundary { margin-top: 0; }
  }
  @media (max-width: 820px) {
    .execution-grid, .cluster-grid { grid-template-columns: 1fr; }
    .job-inspector { display: block; }
    .resource-grid, .field-grid.two { grid-template-columns: 1fr; }
    .job-header { flex-direction: column; }
  }
</style>
