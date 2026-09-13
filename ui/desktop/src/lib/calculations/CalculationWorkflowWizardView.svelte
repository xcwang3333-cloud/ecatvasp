<script lang="ts">
  import { onMount } from "svelte";

  import type { CalculationWizardClient } from "./client";
  import type {
    CalculationCatalogPayload,
    CalculationRootSummary,
    KPointKind,
    MaterializeCalculationStepInput,
    PrepareCalculationWorkflowPayload,
    SpinTreatment,
    VacuumAxis,
    WizardMethodInput,
    WizardProtocolInput,
    WizardRecipeInput,
  } from "./contracts";

  export let client: CalculationWizardClient;
  export let projectRoot: string;
  export let disabled = false;
  export let onMutation: () => Promise<void>;

  let catalog: CalculationCatalogPayload | null = null;
  let busy = false;
  let error = "";
  let message = "";
  let selectedRootId = "";
  let preparation: PrepareCalculationWorkflowPayload | null = null;
  type SetupStep = "model" | "method" | "numerical" | "recipe";
  let setupStep: SetupStep = "model";

  let xcFunctional = "PBE";
  let potcarFamily = "PBE_54";
  let potcarRoot = "";
  let potcarSymbols: Record<string, string> = {};
  let spinTreatment: SpinTreatment = "collinear";
  let encut = 450;
  let kpointKind: KPointKind = "explicit_mesh";
  let kx = 3;
  let ky = 3;
  let kz = 1;
  let kpointValue = 30;
  let vacuumAxis: VacuumAxis = "c";
  let ediff = 1e-5;
  let ediffg = -0.02;
  let frequencyPotim = 0.015;
  let frequencyAtomUids = "";
  let dosNedos = 2001;
  let lobsterNbands = 96;

  let encutCoreMethodHash = "";
  let potcarSpecHash = "";
  let testedEncuts = "";
  let encutAnalysisHash = "";
  let kpointCoreMethodHash = "";
  let testedPlanHashes = "";
  let selectedPlanHash = "";
  let kpointAnalysisHash = "";

  $: roots = catalog?.catalog.roots ?? [];
  $: selectedRoot = roots.find((item) => item.structure_snapshot_id === selectedRootId) ?? null;
  $: task = selectedRoot?.eligible_task ?? null;
  $: rootStep = preparation?.steps.find((item) =>
    item.blocker_codes.includes("validated_numerical_evidence_required"),
  ) ?? null;
  $: if (selectedRoot !== null) synchronizeSymbols(selectedRoot);

  function describeError(value: unknown, fallback: string): string {
    return value instanceof Error ? value.message : fallback;
  }

  function synchronizeSymbols(root: CalculationRootSummary): void {
    const next: Record<string, string> = { ...potcarSymbols };
    let changed = false;
    for (const element of root.elements) {
      if (!(element in next)) {
        next[element] = element;
        changed = true;
      }
    }
    for (const element of Object.keys(next)) {
      if (!root.elements.includes(element)) {
        delete next[element];
        changed = true;
      }
    }
    if (changed) potcarSymbols = next;
  }

  async function loadCatalog(): Promise<void> {
    busy = true;
    error = "";
    try {
      catalog = await client.catalog(projectRoot);
      if (!selectedRootId && catalog.catalog.roots.length > 0) {
        selectedRootId = catalog.catalog.roots[0].structure_snapshot_id;
      }
    } catch (value: unknown) {
      error = describeError(value, "Calculation catalog could not be loaded");
    } finally {
      busy = false;
    }
  }

  function methodInput(): WizardMethodInput {
    if (selectedRoot === null) throw new Error("Select a model before preparing calculations");
    if (!potcarRoot.trim()) throw new Error("Licensed POTCAR root is required");
    return {
      xc_functional: xcFunctional,
      potcar_family: potcarFamily,
      potcar_root: potcarRoot,
      potcar_symbols: selectedRoot.elements.map((element) => ({
        element,
        symbol: potcarSymbols[element] ?? element,
      })),
      spin_treatment: spinTreatment,
    };
  }

  function protocolInput(): WizardProtocolInput {
    const protocol: WizardProtocolInput = {
      encut_ev: encut,
      kpoint_kind: kpointKind,
      kpoint_centering: "gamma",
      precision: "Accurate",
      ediff_ev: ediff,
      ediffg_ev_per_angstrom: ediffg,
      ismear: 0,
      sigma_ev: 0.05,
    };
    if (kpointKind === "explicit_mesh") protocol.kpoint_mesh = [kx, ky, kz];
    if (kpointKind === "reciprocal_density" || kpointKind === "kspacing") {
      protocol.kpoint_value = kpointValue;
    }
    if (task !== "gas_reference") protocol.vacuum_axis = vacuumAxis;
    return protocol;
  }

  function recipeInput(): WizardRecipeInput {
    const atomUids = frequencyAtomUids
      .split(/[\s,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    return {
      frequency_potim_angstrom: frequencyPotim,
      frequency_atom_uids: atomUids,
      dos_nedos: dosNedos,
      lobster_nbands: lobsterNbands,
    };
  }

  async function prepare(): Promise<void> {
    if (selectedRoot === null || task === null || busy) return;
    busy = true;
    error = "";
    message = "";
    try {
      preparation = await client.prepare(projectRoot, {
        task,
        root_structure_snapshot_id: selectedRoot.structure_snapshot_id,
        method: methodInput(),
        protocol: protocolInput(),
        recipe: recipeInput(),
      });
      message = preparation.reused_plan
        ? "Existing scientific workflow plan reused."
        : "Scientific workflow plan prepared.";
      await onMutation();
      await loadCatalog();
    } catch (value: unknown) {
      error = describeError(value, "Calculation workflow could not be prepared");
    } finally {
      busy = false;
    }
  }

  function blockerLabel(code: string): string {
    const labels: Record<string, string> = {
      validated_numerical_evidence_required: "Validated ENCUT / k-point evidence required",
      accepted_structure_required: "Waiting for accepted promoted structure",
      frequency_atom_selection_required: "Select atoms for finite-difference frequency",
      lobster_nbands_required: "LOBSTER NBANDS is required",
      scientific_settings_invalid: "Scientific settings need review",
    };
    return labels[code] ?? code.replaceAll("_", " ");
  }

  function splitHashes(value: string): string[] {
    return value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
  }

  function splitNumbers(value: string): number[] {
    const values = value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
    const numbers = values.map(Number);
    if (numbers.length === 0 || numbers.some((item) => !Number.isFinite(item))) {
      throw new Error("Tested ENCUT values must be a non-empty numeric list");
    }
    return numbers;
  }

  async function materializeRoot(): Promise<void> {
    if (preparation === null || rootStep?.method_fingerprint_id == null || task === null || busy) return;
    busy = true;
    error = "";
    message = "";
    try {
      const input: MaterializeCalculationStepInput = {
        workflow_plan_id: preparation.workflow_plan_id,
        step_key: rootStep.step_key,
        method_fingerprint_id: rootStep.method_fingerprint_id,
        task,
        method: methodInput(),
        protocol: protocolInput(),
        numerical_evidence: {
          encut: {
            core_method_hash: encutCoreMethodHash,
            potcar_spec_hash: potcarSpecHash,
            tested_encuts_ev: splitNumbers(testedEncuts),
            selected_encut_ev: encut,
            analysis_hash: encutAnalysisHash,
          },
        },
      };
      if (task !== "gas_reference") {
        input.numerical_evidence.kpoints = {
          core_method_hash: kpointCoreMethodHash,
          system_kind: "slab_2d",
          tested_plan_hashes: splitHashes(testedPlanHashes),
          selected_plan_hash: selectedPlanHash,
          analysis_hash: kpointAnalysisHash,
        };
      }
      const receipt = await client.materialize(projectRoot, input);
      message = receipt.reused
        ? `Existing ${receipt.step_key} calculation reused.`
        : `${receipt.step_key} calculation materialized.`;
      await onMutation();
      await loadCatalog();
    } catch (value: unknown) {
      error = describeError(value, "Calculation step could not be materialized");
    } finally {
      busy = false;
    }
  }

  onMount(() => void loadCatalog());
</script>

<section class="calculation-workbench" aria-labelledby="vasp-setup-heading">
  <header class="workbench-header">
    <div>
      <span class="eyebrow">Scientific calculation setup</span>
      <h2 id="vasp-setup-heading">VASP Setup</h2>
      <p>Define the scientific model, method, numerical protocol, and workflow recipe before execution. Preparation records identities and blockers; it does not claim convergence or scheduler success.</p>
    </div>
    <button class="secondary-button" type="button" disabled={busy || disabled} onclick={() => void loadCatalog()}>
      {busy ? "Working…" : "Refresh"}
    </button>
  </header>

  {#if error}<div class="setup-alert error" role="alert">{error}</div>{/if}
  {#if message}<div class="setup-alert success">{message}</div>{/if}

  {#if roots.length === 0}
    <div class="setup-empty">
      <span class="empty-index">01</span>
      <div><strong>No calculation-ready model</strong><p>Create or import a current structure in Model Studio before defining a VASP workflow.</p></div>
    </div>
  {:else}
    <div class="setup-layout">
      <aside class="step-rail" aria-label="VASP setup steps">
        <div class="rail-heading">
          <span class="eyebrow">Setup sequence</span>
          <strong>Scientific intent</strong>
        </div>

        <button type="button" class:active={setupStep === "model"} onclick={() => (setupStep = "model")}>
          <span class="step-index">01</span>
          <span class="step-copy"><strong>Model</strong><small>{selectedRoot?.label ?? "Choose structure"}</small></span>
          <span class:done={selectedRoot !== null} class="step-state">{selectedRoot !== null ? "✓" : "·"}</span>
        </button>

        <button type="button" class:active={setupStep === "method"} onclick={() => (setupStep = "method")}>
          <span class="step-index">02</span>
          <span class="step-copy"><strong>Method</strong><small>{xcFunctional} · {potcarFamily}</small></span>
          <span class:done={potcarRoot.trim().length > 0} class="step-state">{potcarRoot.trim().length > 0 ? "✓" : "·"}</span>
        </button>

        <button type="button" class:active={setupStep === "numerical"} onclick={() => (setupStep = "numerical")}>
          <span class="step-index">03</span>
          <span class="step-copy"><strong>Numerics</strong><small>ENCUT {encut} eV · {kpointKind.replaceAll("_", " ")}</small></span>
          <span class="step-state done">✓</span>
        </button>

        <button type="button" class:active={setupStep === "recipe"} onclick={() => (setupStep = "recipe")}>
          <span class="step-index">04</span>
          <span class="step-copy"><strong>Workflow</strong><small>{task?.replaceAll("_", " ") ?? "Recipe controls"}</small></span>
          <span class="step-state done">✓</span>
        </button>

        <div class="rail-boundary">
          <strong>Authority boundary</strong>
          <p>Scheduler state and scientific convergence remain separate. This screen only defines calculation intent and workflow identity.</p>
        </div>
      </aside>

      <main class="setup-canvas">
        {#if setupStep === "model"}
          <section class="step-panel">
            <header class="panel-header">
              <div><span class="panel-index">01</span><span class="eyebrow">Scientific root</span><h3>Select the structure to calculate</h3><p>Each eligible structure snapshot carries durable scientific identity into the workflow plan.</p></div>
              <span class="panel-status">{roots.length} available</span>
            </header>

            <div class="root-grid">
              {#each roots as root (root.structure_snapshot_id)}
                <button
                  type="button"
                  class:selected={selectedRootId === root.structure_snapshot_id}
                  class="root-card"
                  onclick={() => (selectedRootId = root.structure_snapshot_id)}
                  disabled={busy || disabled}
                >
                  <span class="root-kind">{root.is_conformer ? "Adsorbate" : "Structure"}</span>
                  <strong>{root.label}</strong>
                  <small>{root.atom_count} atoms · {root.elements.join(" · ")}</small>
                  <span class="root-task">{root.eligible_task.replaceAll("_", " ")}</span>
                </button>
              {/each}
            </div>

            <footer class="panel-actions">
              <span>Select one durable StructureSnapshot.</span>
              <button class="primary-button" type="button" disabled={selectedRoot === null} onclick={() => (setupStep = "method")}>Continue to method →</button>
            </footer>
          </section>
        {:else if setupStep === "method"}
          <section class="step-panel">
            <header class="panel-header">
              <div><span class="panel-index">02</span><span class="eyebrow">Electronic-structure method</span><h3>Define XC, POTCAR policy, and spin</h3><p>The licensed POTCAR root stays local. ECatVASP records the selected family and exact symbols as method identity.</p></div>
            </header>

            <div class="form-section">
              <div class="field-grid two">
                <label>XC functional<input bind:value={xcFunctional} /></label>
                <label>POTCAR family<input bind:value={potcarFamily} /></label>
              </div>
              <label>Licensed POTCAR root<input bind:value={potcarRoot} placeholder="C:\VASP\potpaw_PBE.54" /></label>
              {#if selectedRoot}
                <div class="potcar-symbols">
                  <div class="subheading"><strong>POTCAR symbols</strong><span>{selectedRoot.elements.length} elements</span></div>
                  <div class="field-grid symbols">
                    {#each selectedRoot.elements as element (element)}
                      <label><span>{element}</span><input bind:value={potcarSymbols[element]} placeholder={element} /></label>
                    {/each}
                  </div>
                </div>
              {/if}
              <label>Spin treatment<select bind:value={spinTreatment}>
                <option value="collinear">Collinear</option>
                <option value="unpolarized">Unpolarized</option>
                <option value="noncollinear">Noncollinear</option>
              </select></label>
            </div>

            <footer class="panel-actions">
              <button class="secondary-button" type="button" onclick={() => (setupStep = "model")}>← Model</button>
              <button class="primary-button" type="button" disabled={!potcarRoot.trim()} onclick={() => (setupStep = "numerical")}>Continue to numerics →</button>
            </footer>
          </section>
        {:else if setupStep === "numerical"}
          <section class="step-panel">
            <header class="panel-header">
              <div><span class="panel-index">03</span><span class="eyebrow">Numerical protocol</span><h3>Set cutoff, Brillouin-zone sampling, and convergence controls</h3><p>These settings become part of the MethodFingerprint and are validated again before materialization.</p></div>
            </header>

            <div class="form-section">
              <div class="field-grid two">
                <label>ENCUT / eV<input type="number" step="10" bind:value={encut} /></label>
                <label>K-point policy<select bind:value={kpointKind}>
                  <option value="explicit_mesh">Explicit mesh</option>
                  <option value="gamma_only">Gamma only</option>
                  <option value="reciprocal_density">Reciprocal density</option>
                  <option value="kspacing">KSPACING</option>
                </select></label>
              </div>

              {#if kpointKind === "explicit_mesh"}
                <div class="mesh-block">
                  <div class="subheading"><strong>K-point mesh</strong><span>Gamma centered</span></div>
                  <div class="mesh-grid">
                    <label>Kx<input type="number" min="1" bind:value={kx} /></label>
                    <label>Ky<input type="number" min="1" bind:value={ky} /></label>
                    <label>Kz<input type="number" min="1" bind:value={kz} /></label>
                  </div>
                </div>
              {:else if kpointKind !== "gamma_only"}
                <label>Policy value<input type="number" step="0.01" bind:value={kpointValue} /></label>
              {/if}

              {#if task !== "gas_reference"}
                <label>Vacuum axis<select bind:value={vacuumAxis}><option value="a">a</option><option value="b">b</option><option value="c">c</option></select></label>
              {/if}

              <details class="advanced-panel">
                <summary>Advanced convergence controls</summary>
                <div class="field-grid two">
                  <label>EDIFF / eV<input type="number" step="0.000001" bind:value={ediff} /></label>
                  <label>EDIFFG / eV Å⁻¹<input type="number" step="0.01" bind:value={ediffg} /></label>
                </div>
              </details>
            </div>

            <footer class="panel-actions">
              <button class="secondary-button" type="button" onclick={() => (setupStep = "method")}>← Method</button>
              <button class="primary-button" type="button" onclick={() => (setupStep = "recipe")}>Continue to workflow →</button>
            </footer>
          </section>
        {:else}
          <section class="step-panel">
            <header class="panel-header">
              <div><span class="panel-index">04</span><span class="eyebrow">Workflow recipe</span><h3>Configure downstream scientific tasks</h3><p>Frequency, DOS, and LOBSTER controls are carried into the canonical workflow. Unsupported evidence is never fabricated.</p></div>
            </header>

            <div class="form-section">
              <div class="field-grid two">
                <label>Frequency POTIM / Å<input type="number" step="0.001" bind:value={frequencyPotim} /></label>
                <label>DOS NEDOS<input type="number" min="2" bind:value={dosNedos} /></label>
              </div>
              <label>LOBSTER NBANDS<input type="number" min="1" bind:value={lobsterNbands} /></label>
              <details class="advanced-panel">
                <summary>Selected-atom finite-difference frequency</summary>
                <label>Atom UIDs<input bind:value={frequencyAtomUids} placeholder="Comma-separated exact atom UIDs" /></label>
              </details>
            </div>

            <div class="prepare-callout">
              <div><span class="eyebrow">Ready to plan</span><strong>{selectedRoot?.label ?? "No model selected"}</strong><p>Prepare the canonical workflow and record MethodFingerprint identities. Job submission happens later in Jobs & HPC.</p></div>
              <button class="primary-button" type="button" disabled={busy || disabled || selectedRoot === null || !potcarRoot.trim()} onclick={() => void prepare()}>
                {busy ? "Preparing…" : "Prepare scientific workflow"}
              </button>
            </div>

            <footer class="panel-actions">
              <button class="secondary-button" type="button" onclick={() => (setupStep = "numerical")}>← Numerics</button>
            </footer>
          </section>
        {/if}
      </main>

      <aside class="setup-inspector">
        <div class="inspector-heading">
          <span class="eyebrow">Current setup</span>
          <h3>VASP intent summary</h3>
        </div>

        <dl class="setup-summary">
          <div><dt>Model</dt><dd>{selectedRoot?.label ?? "Not selected"}</dd></div>
          <div><dt>Task</dt><dd>{task?.replaceAll("_", " ") ?? "—"}</dd></div>
          <div><dt>XC / spin</dt><dd>{xcFunctional} · {spinTreatment}</dd></div>
          <div><dt>POTCAR</dt><dd>{potcarFamily}{potcarRoot.trim() ? " · root set" : " · root missing"}</dd></div>
          <div><dt>ENCUT</dt><dd>{encut} eV</dd></div>
          <div><dt>K points</dt><dd>{kpointKind === "explicit_mesh" ? kx + " × " + ky + " × " + kz : kpointKind.replaceAll("_", " ")}</dd></div>
        </dl>

        <section class="file-intent">
          <div class="subheading"><strong>Execution input boundary</strong><span>Later</span></div>
          <p>Exact VASP files are materialized/verified during the validated calculation and execution path, not invented by this UI.</p>
          <ul>
            <li><span>POSCAR</span><small>from selected structure snapshot</small></li>
            <li><span>INCAR</span><small>from method + numerical protocol</small></li>
            <li><span>KPOINTS</span><small>from explicit sampling policy</small></li>
            <li><span>POTCAR</span><small>from licensed local resolver</small></li>
          </ul>
        </section>

        {#if preparation !== null}
          <section class="plan-status">
            <span class="eyebrow">Prepared plan</span>
            <strong>{preparation.reused_plan ? "Existing plan reused" : "New workflow plan"}</strong>
            <p>{preparation.steps.length} workflow step{preparation.steps.length === 1 ? "" : "s"} · {preparation.task.replaceAll("_", " ")}</p>
          </section>
        {/if}
      </aside>
    </div>
  {/if}

  {#if preparation}
    <section class="workflow-result">
      <header class="result-heading">
        <div><span class="eyebrow">Prepared workflow</span><h3>{preparation.task.replaceAll("_", " ")}</h3><p>Scientific blockers remain explicit until validated evidence or accepted predecessor structures exist.</p></div>
        <span class="status-pill">{preparation.reused_plan ? "Reused plan" : "New plan"}</span>
      </header>

      <div class="workflow-timeline">
        {#each preparation.steps as step, index (step.step_key)}
          <article class:done={step.materialized} class="workflow-step">
            <span class="timeline-index">{String(index + 1).padStart(2, "0")}</span>
            <div class="timeline-copy">
              <strong>{step.step_key.replaceAll("_", " ")}</strong>
              <small>{step.calculation_type.replaceAll("_", " ")}</small>
            </div>
            <div class="timeline-state">
              <span>{step.materialized ? "Materialized" : step.blocker_codes.map(blockerLabel).join(" · ") || "Ready"}</span>
            </div>
            <details>
              <summary>Identity</summary>
              <dl>
                <div><dt>Recipe</dt><dd>{step.recipe_id}</dd></div>
                <div><dt>Method fingerprint</dt><dd>{step.method_fingerprint_id ?? "Not available"}</dd></div>
                <div><dt>Fingerprint hash</dt><dd>{step.fingerprint_hash ?? "Not available"}</dd></div>
              </dl>
            </details>
          </article>
        {/each}
      </div>

      {#if rootStep && !rootStep.materialized && rootStep.method_fingerprint_id}
        <details class="evidence-panel">
          <summary>Validated convergence evidence · advanced</summary>
          <div class="evidence-copy">
            <strong>Materialize the root calculation only from real convergence-study evidence.</strong>
            <p>ECatVASP validates hashes and tested values against the selected method/numerical plan. This form does not create convergence evidence.</p>
          </div>
          <div class="field-grid two">
            <label>ENCUT core-method hash<input bind:value={encutCoreMethodHash} /></label>
            <label>POTCAR-spec hash<input bind:value={potcarSpecHash} /></label>
          </div>
          <label>Tested ENCUT values / eV<input bind:value={testedEncuts} placeholder="400, 450, 500" /></label>
          <label>ENCUT analysis hash<input bind:value={encutAnalysisHash} /></label>
          {#if task !== "gas_reference"}
            <div class="field-grid two">
              <label>K-point core-method hash<input bind:value={kpointCoreMethodHash} /></label>
              <label>Selected plan hash<input bind:value={selectedPlanHash} /></label>
            </div>
            <label>Tested plan hashes<input bind:value={testedPlanHashes} placeholder="comma-separated SHA-256 digests" /></label>
            <label>K-point analysis hash<input bind:value={kpointAnalysisHash} /></label>
          {/if}
          <button class="primary-button" type="button" disabled={busy || disabled} onclick={() => void materializeRoot()}>Validate evidence & materialize {rootStep.step_key}</button>
        </details>
      {/if}
    </section>
  {/if}
</section>

<style>
  .calculation-workbench { display: grid; gap: .8rem; color: var(--text, inherit); }
  .workbench-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1.4rem; padding-bottom: .85rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .workbench-header h2 { margin: .16rem 0 .25rem; font-size: 1.2rem; letter-spacing: -.025em; }
  .workbench-header p { max-width: 780px; margin: 0; color: var(--muted-text, #687570); font-size: .72rem; line-height: 1.55; }
  .eyebrow { color: var(--accent, #177b68); font-size: .58rem; font-weight: 750; letter-spacing: .1em; text-transform: uppercase; }
  .primary-button, .secondary-button { min-height: 34px; border-radius: 7px; padding: 0 .72rem; font-size: .62rem; font-weight: 700; cursor: pointer; }
  .primary-button { border: 1px solid var(--accent-strong, #0e6656); color: #fff; background: var(--accent, #177b68); }
  .secondary-button { border: 1px solid var(--border, #dce4e0); color: inherit; background: var(--surface, #fff); }
  button:disabled { cursor: not-allowed; opacity: .5; }
  .setup-alert { padding: .55rem .7rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; font-size: .64rem; }
  .setup-alert.error { color: #a53a43; border-color: #e8c1c5; background: #fff4f5; }
  .setup-alert.success { color: #226c52; border-color: #bcdccf; background: #eff9f5; }
  .setup-empty { display: flex; gap: .8rem; align-items: center; padding: 1.2rem; border: 1px dashed var(--border-strong, #cbd6d1); border-radius: 10px; background: var(--surface, #fff); }
  .setup-empty strong { font-size: .72rem; }
  .setup-empty p { margin: .25rem 0 0; color: var(--muted-text, #687570); font-size: .62rem; }
  .empty-index { display: inline-flex; width: 34px; height: 34px; align-items: center; justify-content: center; border-radius: 8px; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); font-size: .64rem; font-weight: 800; }
  .setup-layout { display: grid; grid-template-columns: 190px minmax(430px,1fr) 250px; min-height: 570px; gap: .75rem; }
  .step-rail, .setup-canvas, .setup-inspector { min-width: 0; border: 1px solid var(--border, #dce4e0); border-radius: 11px; background: var(--surface, #fff); overflow: hidden; }
  .step-rail { display: flex; flex-direction: column; padding: .55rem; }
  .rail-heading { display: grid; gap: .12rem; padding: .35rem .35rem .65rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .rail-heading strong { font-size: .7rem; }
  .step-rail > button { display: grid; grid-template-columns: 28px minmax(0,1fr) 18px; align-items: center; gap: .38rem; width: 100%; padding: .58rem .38rem; border: 0; border-bottom: 1px solid var(--border, #dce4e0); color: inherit; background: transparent; text-align: left; cursor: pointer; }
  .step-rail > button:hover, .step-rail > button.active { background: var(--surface-soft, #f7f9f8); }
  .step-rail > button.active { box-shadow: inset 2px 0 0 var(--accent, #177b68); }
  .step-index { display: inline-flex; width: 25px; height: 25px; align-items: center; justify-content: center; border: 1px solid var(--border, #dce4e0); border-radius: 6px; color: var(--muted-text, #687570); font-size: .48rem; font-weight: 750; }
  .step-copy { min-width: 0; }
  .step-copy strong, .step-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .step-copy strong { font-size: .6rem; }
  .step-copy small { margin-top: .1rem; color: var(--muted-text, #687570); font-size: .48rem; }
  .step-state { display: inline-flex; width: 17px; height: 17px; align-items: center; justify-content: center; border-radius: 50%; color: var(--subtle-text, #84908b); font-size: .52rem; }
  .step-state.done { color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); }
  .rail-boundary { margin-top: auto; padding: .65rem .5rem .35rem; }
  .rail-boundary strong { font-size: .54rem; }
  .rail-boundary p { margin: .2rem 0 0; color: var(--muted-text, #687570); font-size: .48rem; line-height: 1.45; }
  .setup-canvas { background: var(--surface, #fff); }
  .step-panel { display: flex; min-height: 100%; flex-direction: column; }
  .panel-header { display: flex; justify-content: space-between; gap: 1rem; padding: 1rem 1.05rem .85rem; border-bottom: 1px solid var(--border, #dce4e0); background: var(--surface-soft, #f7f9f8); }
  .panel-header > div { position: relative; padding-left: 38px; }
  .panel-index { position: absolute; left: 0; top: 1px; display: inline-flex; width: 29px; height: 29px; align-items: center; justify-content: center; border-radius: 7px; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); font-size: .56rem; font-weight: 800; }
  .panel-header h3 { margin: .12rem 0 .2rem; font-size: .86rem; letter-spacing: -.018em; }
  .panel-header p { max-width: 620px; margin: 0; color: var(--muted-text, #687570); font-size: .58rem; line-height: 1.5; }
  .panel-status { align-self: flex-start; padding: .28rem .45rem; border: 1px solid var(--border, #dce4e0); border-radius: 999px; color: var(--muted-text, #687570); background: var(--surface, #fff); font-size: .5rem; }
  .root-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: .55rem; padding: .85rem; }
  .root-card { display: grid; min-width: 0; gap: .2rem; padding: .72rem; border: 1px solid var(--border, #dce4e0); border-radius: 9px; color: inherit; background: var(--surface, #fff); text-align: left; cursor: pointer; }
  .root-card:hover { border-color: #a9c5bb; background: var(--surface-soft, #f7f9f8); }
  .root-card.selected { border-color: #7bb4a3; box-shadow: inset 0 0 0 1px #7bb4a3; background: var(--accent-soft, #e7f4ef); }
  .root-kind, .root-task { color: var(--accent, #177b68); font-size: .48rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }
  .root-card strong { overflow: hidden; font-size: .65rem; text-overflow: ellipsis; white-space: nowrap; }
  .root-card small { color: var(--muted-text, #687570); font-size: .51rem; }
  .root-task { margin-top: .22rem; color: var(--muted-text, #687570); }
  .form-section { display: grid; align-content: start; gap: .68rem; padding: .9rem 1rem; }
  label { display: grid; gap: .25rem; color: #53615d; font-size: .58rem; font-weight: 650; }
  input, select { width: 100%; min-width: 0; box-sizing: border-box; padding: .5rem .55rem; border: 1px solid var(--border-strong, #cbd6d1); border-radius: 7px; outline: none; color: inherit; background: var(--surface, #fff); font-size: .64rem; }
  input:focus, select:focus { border-color: #70a999; box-shadow: 0 0 0 2px rgb(23 123 104 / 8%); }
  .field-grid { display: grid; gap: .55rem; }
  .field-grid.two { grid-template-columns: 1fr 1fr; }
  .field-grid.symbols { grid-template-columns: repeat(auto-fit,minmax(95px,1fr)); }
  .field-grid.symbols label { gap: .15rem; }
  .potcar-symbols, .mesh-block { display: grid; gap: .45rem; padding: .6rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; background: var(--surface-soft, #f7f9f8); }
  .subheading { display: flex; justify-content: space-between; align-items: center; gap: .6rem; }
  .subheading strong { font-size: .58rem; }
  .subheading span { color: var(--muted-text, #687570); font-size: .48rem; }
  .mesh-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: .45rem; }
  .advanced-panel { border-top: 1px solid var(--border, #dce4e0); padding-top: .5rem; }
  .advanced-panel summary { color: var(--muted-text, #687570); font-size: .55rem; font-weight: 700; cursor: pointer; }
  .advanced-panel > .field-grid, .advanced-panel > label { margin-top: .55rem; }
  .panel-actions { display: flex; align-items: center; justify-content: flex-end; gap: .55rem; margin-top: auto; padding: .72rem 1rem; border-top: 1px solid var(--border, #dce4e0); background: var(--surface-soft, #f7f9f8); }
  .panel-actions > span { margin-right: auto; color: var(--muted-text, #687570); font-size: .52rem; }
  .prepare-callout { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin: .25rem 1rem .8rem; padding: .75rem; border: 1px solid #b8d8cd; border-radius: 9px; background: var(--accent-soft, #e7f4ef); }
  .prepare-callout > div { min-width: 0; }
  .prepare-callout strong { display: block; margin-top: .12rem; overflow: hidden; font-size: .68rem; text-overflow: ellipsis; white-space: nowrap; }
  .prepare-callout p { max-width: 520px; margin: .18rem 0 0; color: var(--muted-text, #687570); font-size: .52rem; line-height: 1.45; }
  .setup-inspector { align-self: start; padding: .8rem; }
  .inspector-heading { padding-bottom: .6rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .inspector-heading h3 { margin: .12rem 0 0; font-size: .72rem; }
  .setup-summary { display: grid; gap: 0; margin: 0; }
  .setup-summary > div { padding: .55rem 0; border-bottom: 1px solid var(--border, #dce4e0); }
  .setup-summary dt { margin-bottom: .16rem; color: var(--subtle-text, #84908b); font-size: .46rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
  .setup-summary dd { margin: 0; overflow-wrap: anywhere; color: #43534e; font-size: .56rem; line-height: 1.4; }
  .file-intent { margin-top: .7rem; padding: .6rem; border-radius: 8px; background: var(--surface-soft, #f7f9f8); }
  .file-intent p { margin: .35rem 0 .5rem; color: var(--muted-text, #687570); font-size: .49rem; line-height: 1.45; }
  .file-intent ul { display: grid; gap: .25rem; margin: 0; padding: 0; list-style: none; }
  .file-intent li { display: grid; grid-template-columns: 54px 1fr; gap: .35rem; font-size: .49rem; }
  .file-intent li span { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-weight: 700; }
  .file-intent li small { color: var(--muted-text, #687570); }
  .plan-status { margin-top: .65rem; padding: .6rem; border: 1px solid #b9d8ce; border-radius: 8px; background: var(--accent-soft, #e7f4ef); }
  .plan-status strong { display: block; margin-top: .12rem; font-size: .6rem; }
  .plan-status p { margin: .18rem 0 0; color: var(--muted-text, #687570); font-size: .49rem; }
  .workflow-result { border: 1px solid var(--border, #dce4e0); border-radius: 11px; background: var(--surface, #fff); overflow: hidden; }
  .result-heading { display: flex; justify-content: space-between; gap: 1rem; padding: .85rem 1rem; border-bottom: 1px solid var(--border, #dce4e0); background: var(--surface-soft, #f7f9f8); }
  .result-heading h3 { margin: .12rem 0 .18rem; font-size: .8rem; }
  .result-heading p { margin: 0; color: var(--muted-text, #687570); font-size: .54rem; }
  .status-pill { align-self: flex-start; padding: .28rem .48rem; border: 1px solid #aad0c4; border-radius: 999px; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); font-size: .5rem; font-weight: 700; }
  .workflow-timeline { display: grid; padding: .35rem .8rem .7rem; }
  .workflow-step { display: grid; grid-template-columns: 30px minmax(120px,.7fr) minmax(180px,1.3fr); gap: .55rem; align-items: center; padding: .55rem .15rem; border-bottom: 1px solid var(--border, #dce4e0); }
  .workflow-step:last-child { border-bottom: 0; }
  .timeline-index { display: inline-flex; width: 26px; height: 26px; align-items: center; justify-content: center; border: 1px solid var(--border, #dce4e0); border-radius: 7px; color: var(--muted-text, #687570); font-size: .48rem; font-weight: 750; }
  .workflow-step.done .timeline-index { border-color: #9ac9ba; color: var(--accent-strong, #0e6656); background: var(--accent-soft, #e7f4ef); }
  .timeline-copy strong, .timeline-copy small { display: block; }
  .timeline-copy strong { font-size: .58rem; }
  .timeline-copy small, .timeline-state { color: var(--muted-text, #687570); font-size: .49rem; }
  .workflow-step details { grid-column: 2 / -1; }
  .workflow-step summary { color: var(--muted-text, #687570); font-size: .48rem; cursor: pointer; }
  .workflow-step dl { display: grid; gap: .25rem; margin: .4rem 0 0; padding: .45rem; border-radius: 7px; background: var(--surface-soft, #f7f9f8); font-size: .46rem; }
  .workflow-step dl div { display: grid; grid-template-columns: 110px minmax(0,1fr); gap: .4rem; }
  .workflow-step dt { color: var(--subtle-text, #84908b); }
  .workflow-step dd { margin: 0; overflow-wrap: anywhere; }
  .evidence-panel { margin: .2rem .8rem .8rem; padding: .7rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; }
  .evidence-panel > summary { font-size: .58rem; font-weight: 700; cursor: pointer; }
  .evidence-panel[open] { display: grid; gap: .55rem; }
  .evidence-copy { margin-top: .3rem; }
  .evidence-copy strong { font-size: .57rem; }
  .evidence-copy p { margin: .18rem 0 0; color: var(--muted-text, #687570); font-size: .5rem; line-height: 1.45; }
  @media (max-width: 1180px) {
    .setup-layout { grid-template-columns: 175px minmax(430px,1fr); }
    .setup-inspector { grid-column: 1 / -1; display: grid; grid-template-columns: 220px 1fr 220px; gap: .7rem; }
    .file-intent, .plan-status { margin-top: 0; }
  }
  @media (max-width: 900px) {
    .setup-layout { grid-template-columns: 1fr; }
    .step-rail { display: grid; grid-template-columns: repeat(4,1fr); }
    .rail-heading, .rail-boundary { grid-column: 1 / -1; }
    .step-rail > button { border-right: 1px solid var(--border, #dce4e0); }
    .setup-inspector { display: block; }
    .root-grid, .field-grid.two { grid-template-columns: 1fr; }
  }
</style>
