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
    WizardStepSummary,
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

  let presentationSequence = 0;

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

<section class="calculation-workbench">
  <div class="section-heading">
    <div>
      <span class="eyebrow">Calculations</span>
      <h2>Calculation & workflow wizard</h2>
      <p>Choose a scientific model, review explicit VASP settings, and prepare the canonical workflow.</p>
    </div>
    <button class="text-button" type="button" disabled={busy || disabled} onclick={() => void loadCatalog()}>Refresh</button>
  </div>

  {#if error}<div class="wizard-alert error" role="alert">{error}</div>{/if}
  {#if message}<div class="wizard-alert">{message}</div>{/if}

  {#if roots.length === 0}
    <div class="wizard-empty">Create or import a current model in Model Studio before preparing calculations.</div>
  {:else}
    <div class="wizard-grid">
      <section class="wizard-card">
        <h3>1. Scientific model</h3>
        <label>
          Model
          <select bind:value={selectedRootId} disabled={busy || disabled}>
            {#each roots as root (root.structure_snapshot_id)}
              <option value={root.structure_snapshot_id}>{root.label} · {root.atom_count} atoms · {root.eligible_task.replaceAll("_", " ")}</option>
            {/each}
          </select>
        </label>
        {#if selectedRoot}
          <div class="science-summary">
            <span>{selectedRoot.elements.join(" · ")}</span>
            <span>{selectedRoot.is_conformer ? "Adsorbate conformer" : "Current model"}</span>
          </div>
        {/if}
      </section>

      <section class="wizard-card">
        <h3>2. Method</h3>
        <div class="field-grid">
          <label>XC functional<input bind:value={xcFunctional} /></label>
          <label>POTCAR family<input bind:value={potcarFamily} /></label>
        </div>
        <label>Licensed POTCAR root<input bind:value={potcarRoot} placeholder="C:\\VASP\\potpaw_PBE.54" /></label>
        {#if selectedRoot}
          <div class="potcar-grid">
            {#each selectedRoot.elements as element (element)}
              <label>{element} POTCAR symbol<input bind:value={potcarSymbols[element]} placeholder={element} /></label>
            {/each}
          </div>
        {/if}
        <label>Spin treatment<select bind:value={spinTreatment}><option value="collinear">Collinear</option><option value="unpolarized">Unpolarized</option><option value="noncollinear">Noncollinear</option></select></label>
      </section>

      <section class="wizard-card">
        <h3>3. Numerical protocol</h3>
        <div class="field-grid">
          <label>ENCUT (eV)<input type="number" step="10" bind:value={encut} /></label>
          <label>K-point policy<select bind:value={kpointKind}><option value="explicit_mesh">Explicit mesh</option><option value="gamma_only">Gamma only</option><option value="reciprocal_density">Reciprocal density</option><option value="kspacing">KSPACING</option></select></label>
        </div>
        {#if kpointKind === "explicit_mesh"}
          <div class="mesh-grid"><label>Kx<input type="number" min="1" bind:value={kx} /></label><label>Ky<input type="number" min="1" bind:value={ky} /></label><label>Kz<input type="number" min="1" bind:value={kz} /></label></div>
        {:else if kpointKind !== "gamma_only"}
          <label>Policy value<input type="number" step="0.01" bind:value={kpointValue} /></label>
        {/if}
        {#if task !== "gas_reference"}
          <label>Vacuum axis<select bind:value={vacuumAxis}><option value="a">a</option><option value="b">b</option><option value="c">c</option></select></label>
        {/if}
        <details>
          <summary>Advanced numerical settings</summary>
          <div class="field-grid"><label>EDIFF (eV)<input type="number" step="0.000001" bind:value={ediff} /></label><label>EDIFFG (eV Å⁻¹)<input type="number" step="0.01" bind:value={ediffg} /></label></div>
        </details>
      </section>

      <section class="wizard-card">
        <h3>4. Recipe controls</h3>
        <div class="field-grid"><label>Frequency POTIM (Å)<input type="number" step="0.001" bind:value={frequencyPotim} /></label><label>DOS NEDOS<input type="number" min="2" bind:value={dosNedos} /></label></div>
        <label>LOBSTER NBANDS<input type="number" min="1" bind:value={lobsterNbands} /></label>
        <details><summary>Selected-atom frequency</summary><label>Atom UIDs<input bind:value={frequencyAtomUids} placeholder="Select through structure tooling; comma-separated exact UIDs" /></label></details>
      </section>
    </div>

    <div class="wizard-actions">
      <button class="primary-button" type="button" disabled={busy || disabled || selectedRoot === null || !potcarRoot.trim()} onclick={() => void prepare()}>{busy ? "Working…" : "Prepare scientific workflow"}</button>
      <p>Preparation records workflow and MethodFingerprint identities. It does not fabricate convergence evidence or submit jobs.</p>
    </div>
  {/if}

  {#if preparation}
    <section class="workflow-result">
      <div class="section-heading compact-heading"><div><span class="eyebrow">Prepared workflow</span><h3>{preparation.task.replaceAll("_", " ")}</h3></div><span class="status-pill">{preparation.reused_plan ? "Reused" : "New plan"}</span></div>
      <div class="step-list">
        {#each preparation.steps as step (step.step_key)}
          <article class:done={step.materialized} class="step-card">
            <div><strong>{step.step_key.replaceAll("_", " ")}</strong><small>{step.calculation_type.replaceAll("_", " ")}</small></div>
            <div class="step-state">{step.materialized ? "Materialized" : step.blocker_codes.map(blockerLabel).join(" · ") || "Ready"}</div>
            <details><summary>Advanced identity</summary><dl><div><dt>Recipe</dt><dd>{step.recipe_id}</dd></div><div><dt>Method fingerprint</dt><dd>{step.method_fingerprint_id ?? "Not available"}</dd></div><div><dt>Fingerprint hash</dt><dd>{step.fingerprint_hash ?? "Not available"}</dd></div></dl></details>
          </article>
        {/each}
      </div>

      {#if rootStep && !rootStep.materialized && rootStep.method_fingerprint_id}
        <details class="evidence-panel">
          <summary>Advanced · materialize root calculation from validated convergence evidence</summary>
          <p>Only paste evidence produced by an actual convergence study. ECatVASP validates it against the selected method and numerical plan.</p>
          <div class="field-grid"><label>ENCUT core-method hash<input bind:value={encutCoreMethodHash} /></label><label>POTCAR-spec hash<input bind:value={potcarSpecHash} /></label></div>
          <label>Tested ENCUT values (eV)<input bind:value={testedEncuts} placeholder="400, 450, 500" /></label>
          <label>ENCUT analysis hash<input bind:value={encutAnalysisHash} /></label>
          {#if task !== "gas_reference"}
            <div class="field-grid"><label>K-point core-method hash<input bind:value={kpointCoreMethodHash} /></label><label>Selected plan hash<input bind:value={selectedPlanHash} /></label></div>
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
  .calculation-workbench { display: grid; gap: 1rem; }
  .section-heading, .compact-heading { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; }
  .section-heading h2, .section-heading h3 { margin: .2rem 0; }
  .section-heading p, .wizard-actions p, .evidence-panel p { margin: .25rem 0 0; color: var(--muted-text, #667085); }
  .eyebrow { font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; color: var(--muted-text, #667085); }
  .wizard-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: .8rem; }
  .wizard-card, .workflow-result { border: 1px solid var(--border-color, #d0d5dd); border-radius: 12px; padding: 1rem; background: var(--panel-background, transparent); }
  .wizard-card { display: grid; gap: .7rem; align-content: start; }
  .wizard-card h3 { margin: 0; }
  label { display: grid; gap: .3rem; font-size: .82rem; }
  input, select { min-width: 0; padding: .55rem .65rem; border: 1px solid var(--border-color, #d0d5dd); border-radius: 7px; background: var(--input-background, transparent); color: inherit; }
  .field-grid, .mesh-grid, .potcar-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: .6rem; }
  .science-summary { display: flex; gap: .45rem; flex-wrap: wrap; font-size: .78rem; color: var(--muted-text, #667085); }
  .science-summary span, .status-pill { border: 1px solid var(--border-color, #d0d5dd); border-radius: 999px; padding: .2rem .5rem; }
  .wizard-actions { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
  .wizard-actions p { max-width: 52rem; font-size: .8rem; }
  .wizard-alert, .wizard-empty { border: 1px solid var(--border-color, #d0d5dd); border-radius: 8px; padding: .7rem .8rem; }
  .wizard-alert.error { border-style: dashed; }
  .step-list { display: grid; gap: .6rem; margin-top: .8rem; }
  .step-card { display: grid; grid-template-columns: minmax(130px, .7fr) minmax(220px, 1.3fr); gap: .5rem 1rem; align-items: center; border-top: 1px solid var(--border-color, #d0d5dd); padding-top: .7rem; }
  .step-card:first-child { border-top: 0; }
  .step-card strong, .step-card small { display: block; }
  .step-card small, .step-state { color: var(--muted-text, #667085); font-size: .78rem; }
  .step-card details { grid-column: 1 / -1; }
  .step-card dl { display: grid; gap: .35rem; font-size: .75rem; overflow-wrap: anywhere; }
  .step-card dl div { display: grid; grid-template-columns: 130px 1fr; gap: .5rem; }
  .step-card dt { color: var(--muted-text, #667085); }
  .step-card dd { margin: 0; }
  .evidence-panel { margin-top: 1rem; border-top: 1px solid var(--border-color, #d0d5dd); padding-top: .8rem; display: grid; gap: .6rem; }
  details summary { cursor: pointer; }
  @media (max-width: 680px) { .step-card { grid-template-columns: 1fr; } .step-card details { grid-column: auto; } }
</style>