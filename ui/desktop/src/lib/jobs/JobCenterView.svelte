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

<section class="job-center">
  <div class="section-heading">
    <div>
      <span class="eyebrow">Jobs</span>
      <h2>HPC execution & Job Center</h2>
      <p>Prepare exact inputs, submit through system OpenSSH, and track scheduler state without conflating it with scientific convergence.</p>
    </div>
    <button class="text-button" type="button" disabled={busy || disabled} onclick={() => void loadCatalog()}>Refresh</button>
  </div>

  {#if error}<div class="job-alert error" role="alert">{error}</div>{/if}
  {#if message}<div class="job-alert">{message}</div>{/if}

  {#if calculations.length === 0}
    <div class="job-empty">
      <strong>No materialized calculations yet</strong>
      <p>Prepare and materialize a calculation in the Calculation wizard before creating an execution attempt.</p>
    </div>
  {:else}
    <div class="job-grid">
      <section class="job-card">
        <div class="card-heading">
          <div><span class="eyebrow">1 · Calculation</span><h3>Select scientific intent</h3></div>
        </div>
        <label>
          Calculation
          <select value={selectedCalculationId} onchange={selectCalculation} disabled={busy || disabled}>
            {#each calculations as row (row.calculation_id)}
              <option value={row.calculation_id}>{shortRecipe(row.recipe_id)} · {row.calculation_type}</option>
            {/each}
          </select>
        </label>
        {#if selectedCalculation !== null}
          <div class="status-grid">
            <div><span>Scientific</span><strong>{selectedCalculation.scientific_status}</strong></div>
            <div><span>Attempt</span><strong>{selectedCalculation.attempt_status ?? "not started"}</strong></div>
            <div><span>Scheduler</span><strong>{selectedCalculation.scheduler_state ?? "not submitted"}</strong></div>
          </div>
          <p class="boundary-note">Scheduler state is execution evidence only. It never establishes VASP convergence or scientific success.</p>
        {/if}
      </section>

      <section class="job-card">
        <div class="card-heading"><div><span class="eyebrow">2 · Resources</span><h3>Prepare execution</h3></div></div>
        <label>Licensed local POTCAR root<input bind:value={localPotcarRoot} placeholder="C:\\VASP\\PBE_54" /></label>
        <div class="form-grid compact">
          <label>Nodes<input type="number" min="1" step="1" bind:value={nodes} /></label>
          <label>Cores<input type="number" min="1" step="1" bind:value={cores} /></label>
          <label>MPI ranks<input type="number" min="1" step="1" bind:value={mpiRanks} /></label>
          <label>Walltime (min)<input type="number" min="1" step="1" bind:value={walltimeMinutes} /></label>
          <label>Memory MB <small>optional</small><input type="number" min="0" step="1" bind:value={memoryMb} /></label>
          <label>Partition <small>optional</small><input bind:value={partition} /></label>
        </div>
        <label>Local VASP executable<input bind:value={executable} /></label>
        <button class="primary-button" type="button" disabled={busy || disabled || selectedCalculation === null} onclick={() => void prepareExecution()}>{busy ? "Working…" : "Prepare exact execution"}</button>
      </section>
    </div>

    {#if preview !== null}
      <section class="job-card execution-preview">
        <div class="card-heading"><div><span class="eyebrow">Execution preview</span><h3>{preview.step_key}</h3></div><span class="state-chip">{preview.expected_outputs.length} expected outputs</span></div>
        <div class="preview-metrics">
          <div><span>Input artifacts</span><strong>{preview.reused_input_artifacts ? "Verified / reused" : "Prepared"}</strong></div>
          <div><span>Required outputs</span><strong>{preview.expected_outputs.filter((item) => item.required).length}</strong></div>
        </div>
        <details>
          <summary>Advanced execution identity</summary>
          <dl class="advanced-identities">
            <div><dt>Plan hash</dt><dd><code>{preview.plan_hash}</code></dd></div>
            <div><dt>Input manifest</dt><dd><code>{preview.input_manifest_sha256}</code></dd></div>
          </dl>
        </details>
      </section>
    {/if}

    <section class="job-card">
      <div class="card-heading"><div><span class="eyebrow">3 · Cluster</span><h3>OpenSSH + Slurm target</h3></div></div>
      <p class="boundary-note">Authentication stays in your system OpenSSH configuration. ECatVASP does not accept passwords, private keys, or tokens here.</p>
      <div class="form-grid">
        <label>Target name<input bind:value={targetId} /></label>
        <label>SSH host alias<input bind:value={hostAlias} placeholder="cluster-a" /></label>
        <label>Remote work root<input bind:value={remoteWorkRoot} /></label>
        <label>Launcher<input bind:value={launcher} /></label>
        <label>Remote VASP executable<input bind:value={remoteVaspExecutable} /></label>
        <label>Modules <small>comma/newline separated</small><input bind:value={moduleLoads} placeholder="intel, vasp/6" /></label>
        <label>Remote POTCAR resolver<input bind:value={remotePotcarResolverId} /></label>
        <label>Remote POTCAR family<input bind:value={remotePotcarFamily} /></label>
        <label>Remote POTCAR root<input bind:value={remotePotcarRoot} placeholder="/apps/vasp/potpaw_PBE.54" /></label>
      </div>
      <button class="primary-button" type="button" disabled={busy || disabled || preview === null} onclick={() => void submit()}>Submit to Slurm</button>
    </section>

    <section class="job-card">
      <div class="card-heading"><div><span class="eyebrow">Run history</span><h3>Current calculation states</h3></div></div>
      <div class="job-table-wrap">
        <table>
          <thead><tr><th>Calculation</th><th>Scientific</th><th>Attempt</th><th>Scheduler</th><th>Slurm</th><th>Actions</th></tr></thead>
          <tbody>
            {#each calculations as row (row.calculation_id)}
              <tr>
                <td><strong>{shortRecipe(row.recipe_id)}</strong><small>{row.calculation_type}</small></td>
                <td>{row.scientific_status}</td>
                <td>{row.attempt_status ?? "—"}</td>
                <td>{row.scheduler_state ?? "—"}</td>
                <td>{row.scheduler_job_id ?? "—"}</td>
                <td class="job-actions">
                  {#if row.remote_job_id !== null}
                    <button class="text-button" type="button" disabled={busy || disabled} onclick={() => void refreshJob(row)}>Refresh</button>
                    <button class="text-button" type="button" disabled={busy || disabled} onclick={() => void cancelJob(row)}>Cancel</button>
                    <button class="text-button" type="button" disabled={busy || disabled} onclick={() => void retrieve(row)}>Retrieve</button>
                  {:else}
                    <span>Not submitted</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {/if}
</section>

<style>
  .job-center { display: grid; gap: 1rem; }
  .section-heading, .card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
  .section-heading h2, .card-heading h3 { margin: 0.2rem 0; }
  .section-heading p, .boundary-note, .job-empty p { margin: 0.25rem 0 0; color: var(--muted, #667085); }
  .eyebrow { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted, #667085); }
  .job-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1rem; }
  .job-card { border: 1px solid rgba(120, 120, 120, 0.24); border-radius: 12px; padding: 1rem; display: grid; gap: 0.85rem; background: rgba(127, 127, 127, 0.035); }
  label { display: grid; gap: 0.35rem; font-size: 0.84rem; font-weight: 600; }
  label small { font-weight: 400; color: var(--muted, #667085); }
  input, select { width: 100%; box-sizing: border-box; border: 1px solid rgba(120, 120, 120, 0.35); border-radius: 8px; padding: 0.65rem 0.7rem; background: transparent; color: inherit; }
  .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 0.75rem; }
  .form-grid.compact { grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }
  .status-grid, .preview-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; }
  .status-grid div, .preview-metrics div { border: 1px solid rgba(120, 120, 120, 0.18); border-radius: 9px; padding: 0.65rem; display: grid; gap: 0.25rem; }
  .status-grid span, .preview-metrics span { font-size: 0.72rem; color: var(--muted, #667085); }
  .boundary-note { font-size: 0.82rem; }
  .job-alert { border: 1px solid rgba(120, 120, 120, 0.25); border-radius: 8px; padding: 0.75rem; }
  .job-alert.error { border-color: rgba(190, 50, 50, 0.45); }
  .job-empty { padding: 1.5rem; border: 1px dashed rgba(120, 120, 120, 0.3); border-radius: 10px; }
  .state-chip { font-size: 0.75rem; padding: 0.3rem 0.55rem; border-radius: 999px; border: 1px solid rgba(120, 120, 120, 0.25); }
  .advanced-identities { display: grid; gap: 0.5rem; }
  .advanced-identities div { display: grid; gap: 0.2rem; }
  .advanced-identities dd { margin: 0; overflow-wrap: anywhere; }
  .job-table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; min-width: 760px; }
  th, td { text-align: left; padding: 0.65rem; border-bottom: 1px solid rgba(120, 120, 120, 0.18); vertical-align: top; }
  th { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted, #667085); }
  td small { display: block; color: var(--muted, #667085); margin-top: 0.15rem; }
  .job-actions { display: flex; gap: 0.35rem; align-items: center; }
  @media (max-width: 720px) { .status-grid, .preview-metrics { grid-template-columns: 1fr; } }
</style>
