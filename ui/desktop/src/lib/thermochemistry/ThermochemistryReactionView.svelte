<script lang="ts">
  import type { ThermochemistryClient } from "./client";
  import type {
    FrequencySourceRow,
    GasReferenceMaterializationInput,
    HarmonicMaterializationInput,
    ReactionBindings,
    ReactionConditions,
    ReactionPresetKind,
    ReactionPreviewPayload,
    ReactionRequestInput,
    ThermochemistryAnalysisRow,
    ThermochemistryAnalysisViewPayload,
    ThermochemistryCatalogPayload,
  } from "./contracts";
  import { isPositiveSafeInteger } from "./input_validation";
  import { LatestThermochemistryLoad } from "./load_guard";

  export let client: ThermochemistryClient;
  export let projectRoot: string;
  export let disabled = false;
  export let onMutation: () => Promise<void>;

  const requestGuard = new LatestThermochemistryLoad();
  const mutationGuard = new LatestThermochemistryLoad();
  const previewGuard = new LatestThermochemistryLoad();

  let observedProjectRoot = "";
  let catalog: ThermochemistryCatalogPayload | null = null;
  let selected: ThermochemistryAnalysisViewPayload | null = null;
  let preview: ReactionPreviewPayload | null = null;
  let busy = false;
  let busyId = "";
  let error = "";

  let harmonicCalculationId = "";
  let harmonicSubject: HarmonicMaterializationInput["subject_kind"] = "adsorbate";
  let harmonicTemperature = 298.15;
  let harmonicEnergy: HarmonicMaterializationInput["electronic_energy_kind"] = "energy_sigma0_ev";
  let harmonicCutoff = 50;

  let gasCalculationId = "";
  let observedGasCalculationId = "";
  let gasSpecies: GasReferenceMaterializationInput["species"] = "H2";
  let gasTemperature = 298.15;
  let gasPressure = 100000;
  let gasStandardState: GasReferenceMaterializationInput["standard_state"] = "ideal_gas_1_bar";
  let gasEnergy: GasReferenceMaterializationInput["electronic_energy_kind"] = "energy_sigma0_ev";
  let gasEntropy: GasReferenceMaterializationInput["electronic_entropy_policy"] = "neglected";
  let gasGeometry: GasReferenceMaterializationInput["geometry_kind"] = "linear";
  let gasSymmetry = 1;
  let gasMultiplicity = 1;
  let gasCutoff = 50;
  let gasMasses: Record<string, string> = {};

  let presetKind: ReactionPresetKind = "her_volmer_heyrovsky";
  let roleValues: Record<string, string> = {};
  let reactionTemperature = 298.15;
  let baselinePotential = 0;
  let baselinePh = 0;
  let baselineReference: ReactionConditions["potential_reference"] = "she";
  let baselinePhSemantics: ReactionConditions["ph_semantics"] = "explicit_activity";
  let requestedPotential = 0;
  let requestedPh = 0;
  let requestedReference: ReactionConditions["potential_reference"] = "she";
  let requestedPhSemantics: ReactionConditions["ph_semantics"] = "explicit_activity";

  $: if (projectRoot !== observedProjectRoot) {
    observedProjectRoot = projectRoot;
    resetProjectState();
    if (projectRoot.trim()) void loadCatalog(projectRoot);
  }

  $: frequencySources = catalog?.frequency_sources.filter((row) => row.calculation_type === "frequency") ?? [];
  $: gasSources = catalog?.frequency_sources.filter((row) => row.calculation_type === "gas_frequency") ?? [];
  $: satisfiedThermochemistry = catalog?.analyses.filter((row) =>
    row.analysis_type === "thermochemistry" &&
    row.freshness.scientific_state === "completed" &&
    row.freshness.readiness === "satisfied"
  ) ?? [];
  $: selectedGasSource = gasSources.find((row) => row.calculation_id === gasCalculationId) ?? null;
  $: gasSymmetryValid = isPositiveSafeInteger(gasSymmetry);
  $: gasMultiplicityValid = isPositiveSafeInteger(gasMultiplicity);
  $: if (gasCalculationId !== observedGasCalculationId) {
    observedGasCalculationId = gasCalculationId;
    gasMasses = Object.fromEntries((selectedGasSource?.atoms ?? []).map((atom) => [atom.atom_uid, ""]));
  }
  $: requiredReactionRoles = rolesForPreset(presetKind);
  $: reactionInput = buildReactionInput();
  $: selectedThermochemistry = selected?.analysis_type === "thermochemistry" ? selected : null;
  $: selectedDiagram = selected?.view.kind === "reaction_diagram" ? selected.view : null;

  function resetProjectState(): void {
    requestGuard.invalidate();
    mutationGuard.invalidate();
    previewGuard.invalidate();
    catalog = null;
    selected = null;
    preview = null;
    busy = false;
    busyId = "";
    error = "";
    harmonicCalculationId = "";
    gasCalculationId = "";
    observedGasCalculationId = "";
    gasMasses = {};
    roleValues = {};
  }

  function describeError(value: unknown, fallback: string): string {
    return value instanceof Error ? value.message : fallback;
  }

  function shortId(value: string | null | undefined): string {
    if (!value) return "—";
    return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
  }

  function formatValue(value: unknown): string {
    if (typeof value === "number") return Number.isFinite(value) ? value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "") : "—";
    if (typeof value === "string" || typeof value === "boolean") return String(value);
    if (value === null || value === undefined) return "—";
    return JSON.stringify(value);
  }

  async function loadCatalog(root: string = projectRoot): Promise<void> {
    if (!root.trim()) return;
    const token = requestGuard.begin(root);
    busy = true;
    error = "";
    try {
      const payload = await client.catalog(root);
      if (!requestGuard.isCurrent(token, projectRoot)) return;
      catalog = payload;
    } catch (value: unknown) {
      if (!requestGuard.isCurrent(token, projectRoot)) return;
      error = describeError(value, "Thermochemistry catalog could not be loaded");
    } finally {
      if (requestGuard.isCurrent(token, projectRoot)) busy = false;
    }
  }

  async function materializeHarmonic(): Promise<void> {
    if (disabled || busyId || !harmonicCalculationId) return;
    const root = projectRoot;
    const token = mutationGuard.begin(root);
    busyId = `harmonic:${harmonicCalculationId}`;
    error = "";
    const input: HarmonicMaterializationInput = {
      calculation_id: harmonicCalculationId,
      subject_kind: harmonicSubject,
      temperature_k: harmonicTemperature,
      electronic_energy_kind: harmonicEnergy,
      electronic_entropy_policy: "neglected",
      frequency_cutoff_cm_inverse: harmonicCutoff,
      imaginary_mode_policy: "reject_any",
      low_frequency_policy: "reject_below_cutoff",
      exclusions: [],
    };
    try {
      const receipt = await client.materializeHarmonic(root, input);
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await onMutation();
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await loadCatalog(root);
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await openAnalysisById(receipt.analysis_id, "thermochemistry");
    } catch (value: unknown) {
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      error = describeError(value, "Harmonic thermochemistry could not be materialized");
    } finally {
      if (mutationGuard.isCurrent(token, projectRoot)) busyId = "";
    }
  }

  function setGasMass(atomUid: string, event: Event): void {
    const input = event.currentTarget as HTMLInputElement;
    gasMasses = { ...gasMasses, [atomUid]: input.value };
  }

  function gasInput(): GasReferenceMaterializationInput | null {
    if (!gasCalculationId || selectedGasSource === null || !selectedGasSource.source_ready) return null;
    const atoms = selectedGasSource.atoms ?? [];
    if (atoms.length === 0) return null;
    const atomic_masses = atoms.map((atom) => ({
      atom_uid: atom.atom_uid,
      mass_amu: Number(gasMasses[atom.atom_uid]),
    }));
    if (atomic_masses.some((item) => !Number.isFinite(item.mass_amu) || item.mass_amu <= 0)) return null;
    if (![gasTemperature, gasPressure, gasCutoff].every((value) => Number.isFinite(value) && value > 0)) return null;
    if (!gasSymmetryValid || !gasMultiplicityValid) return null;
    return {
      calculation_id: gasCalculationId,
      species: gasSpecies,
      temperature_k: gasTemperature,
      pressure_pa: gasPressure,
      standard_state: gasStandardState,
      electronic_energy_kind: gasEnergy,
      electronic_entropy_policy: gasEntropy,
      geometry_kind: gasGeometry,
      symmetry_number: gasSymmetry,
      spin_multiplicity: gasMultiplicity,
      atomic_masses,
      frequency_cutoff_cm_inverse: gasCutoff,
      imaginary_mode_policy: "reject_any",
      low_frequency_policy: "reject_below_cutoff",
      exclusions: [],
    };
  }

  async function materializeGas(): Promise<void> {
    const input = gasInput();
    if (disabled || busyId || input === null) return;
    const root = projectRoot;
    const token = mutationGuard.begin(root);
    busyId = `gas:${input.calculation_id}`;
    error = "";
    try {
      const receipt = await client.materializeGasReference(root, input);
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await onMutation();
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await loadCatalog(root);
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await openAnalysisById(receipt.analysis_id, "thermochemistry");
    } catch (value: unknown) {
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      error = describeError(value, "Gas reference could not be materialized");
    } finally {
      if (mutationGuard.isCurrent(token, projectRoot)) busyId = "";
    }
  }

  async function openAnalysis(row: ThermochemistryAnalysisRow): Promise<void> {
    await openAnalysisById(row.analysis_id, row.analysis_type);
  }

  async function openAnalysisById(
    analysisId: string,
    analysisType: "thermochemistry" | "reaction_diagram",
  ): Promise<void> {
    const root = projectRoot;
    const token = requestGuard.begin(root);
    busyId = analysisId;
    error = "";
    try {
      const payload = analysisType === "reaction_diagram"
        ? await client.reactionDiagramView(root, analysisId)
        : await client.view(root, analysisId);
      if (!requestGuard.isCurrent(token, projectRoot)) return;
      selected = payload;
    } catch (value: unknown) {
      if (!requestGuard.isCurrent(token, projectRoot)) return;
      error = describeError(value, "Thermochemistry analysis could not be opened");
    } finally {
      if (requestGuard.isCurrent(token, projectRoot)) busyId = "";
    }
  }

  function rolesForPreset(kind: ReactionPresetKind): Array<{ key: string; label: string }> {
    if (kind === "her_volmer_heyrovsky") return [
      { key: "clean_surface_analysis_id", label: "Clean surface" },
      { key: "h_adsorbed_analysis_id", label: "H*" },
      { key: "h2_reference_analysis_id", label: "H₂ reference" },
    ];
    if (kind === "orr_associative_4e") return [
      { key: "clean_surface_analysis_id", label: "Clean surface" },
      { key: "ooh_adsorbed_analysis_id", label: "OOH*" },
      { key: "o_adsorbed_analysis_id", label: "O*" },
      { key: "oh_adsorbed_analysis_id", label: "OH*" },
      { key: "o2_reference_analysis_id", label: "O₂ reference" },
      { key: "h2o_reference_analysis_id", label: "H₂O reference" },
      { key: "h2_reference_analysis_id", label: "H₂ / CHE reference" },
    ];
    if (kind === "oer_associative_4e") return [
      { key: "clean_surface_analysis_id", label: "Clean surface" },
      { key: "oh_adsorbed_analysis_id", label: "OH*" },
      { key: "o_adsorbed_analysis_id", label: "O*" },
      { key: "ooh_adsorbed_analysis_id", label: "OOH*" },
      { key: "h2o_reference_analysis_id", label: "H₂O reference" },
      { key: "o2_reference_analysis_id", label: "O₂ reference" },
      { key: "h2_reference_analysis_id", label: "H₂ / CHE reference" },
    ];
    return [
      { key: "clean_surface_analysis_id", label: "Clean surface" },
      { key: "cooh_adsorbed_analysis_id", label: "COOH*" },
      { key: "co_adsorbed_analysis_id", label: "CO*" },
      { key: "co2_reference_analysis_id", label: "CO₂ reference" },
      { key: "h2o_reference_analysis_id", label: "H₂O reference" },
      { key: "co_reference_analysis_id", label: "CO reference" },
      { key: "h2_reference_analysis_id", label: "H₂ / CHE reference" },
    ];
  }

  function setRole(key: string, event: Event): void {
    const select = event.currentTarget as HTMLSelectElement;
    roleValues = { ...roleValues, [key]: select.value };
    preview = null;
  }

  function buildBindings(): ReactionBindings | null {
    if (requiredReactionRoles.some((role) => !roleValues[role.key])) return null;
    if (presetKind === "her_volmer_heyrovsky") return {
      clean_surface_analysis_id: roleValues.clean_surface_analysis_id!,
      h_adsorbed_analysis_id: roleValues.h_adsorbed_analysis_id!,
      h2_reference_analysis_id: roleValues.h2_reference_analysis_id!,
    };
    if (presetKind === "orr_associative_4e") return {
      clean_surface_analysis_id: roleValues.clean_surface_analysis_id!,
      ooh_adsorbed_analysis_id: roleValues.ooh_adsorbed_analysis_id!,
      o_adsorbed_analysis_id: roleValues.o_adsorbed_analysis_id!,
      oh_adsorbed_analysis_id: roleValues.oh_adsorbed_analysis_id!,
      o2_reference_analysis_id: roleValues.o2_reference_analysis_id!,
      h2o_reference_analysis_id: roleValues.h2o_reference_analysis_id!,
      h2_reference_analysis_id: roleValues.h2_reference_analysis_id!,
    };
    if (presetKind === "oer_associative_4e") return {
      clean_surface_analysis_id: roleValues.clean_surface_analysis_id!,
      oh_adsorbed_analysis_id: roleValues.oh_adsorbed_analysis_id!,
      o_adsorbed_analysis_id: roleValues.o_adsorbed_analysis_id!,
      ooh_adsorbed_analysis_id: roleValues.ooh_adsorbed_analysis_id!,
      h2o_reference_analysis_id: roleValues.h2o_reference_analysis_id!,
      o2_reference_analysis_id: roleValues.o2_reference_analysis_id!,
      h2_reference_analysis_id: roleValues.h2_reference_analysis_id!,
    };
    return {
      clean_surface_analysis_id: roleValues.clean_surface_analysis_id!,
      cooh_adsorbed_analysis_id: roleValues.cooh_adsorbed_analysis_id!,
      co_adsorbed_analysis_id: roleValues.co_adsorbed_analysis_id!,
      co2_reference_analysis_id: roleValues.co2_reference_analysis_id!,
      h2o_reference_analysis_id: roleValues.h2o_reference_analysis_id!,
      co_reference_analysis_id: roleValues.co_reference_analysis_id!,
      h2_reference_analysis_id: roleValues.h2_reference_analysis_id!,
    };
  }

  function buildReactionInput(): ReactionRequestInput | null {
    const bindings = buildBindings();
    const numbers = [reactionTemperature, baselinePotential, baselinePh, requestedPotential, requestedPh];
    if (bindings === null || !numbers.every(Number.isFinite) || reactionTemperature <= 0) return null;
    return {
      preset_kind: presetKind,
      bindings,
      baseline_conditions: {
        temperature_k: reactionTemperature,
        potential_v: baselinePotential,
        ph: baselinePh,
        potential_reference: baselineReference,
        ph_semantics: baselinePhSemantics,
      },
      requested_conditions: {
        temperature_k: reactionTemperature,
        potential_v: requestedPotential,
        ph: requestedPh,
        potential_reference: requestedReference,
        ph_semantics: requestedPhSemantics,
      },
    };
  }

  async function runPreview(): Promise<void> {
    if (disabled || reactionInput === null) return;
    const root = projectRoot;
    const token = previewGuard.begin(root);
    busyId = "reaction-preview";
    error = "";
    try {
      const payload = await client.reactionPreview(root, reactionInput);
      if (!previewGuard.isCurrent(token, projectRoot)) return;
      preview = payload;
    } catch (value: unknown) {
      if (!previewGuard.isCurrent(token, projectRoot)) return;
      error = describeError(value, "Reaction preview could not be evaluated");
    } finally {
      if (previewGuard.isCurrent(token, projectRoot)) busyId = "";
    }
  }

  async function materializeDiagram(): Promise<void> {
    if (disabled || busyId || reactionInput === null) return;
    const root = projectRoot;
    const token = mutationGuard.begin(root);
    busyId = "reaction-materialize";
    error = "";
    try {
      const receipt = await client.materializeReactionDiagram(root, reactionInput);
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await onMutation();
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await loadCatalog(root);
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      await openAnalysisById(receipt.analysis_id, "reaction_diagram");
    } catch (value: unknown) {
      if (!mutationGuard.isCurrent(token, projectRoot)) return;
      error = describeError(value, "Reaction diagram could not be materialized");
    } finally {
      if (mutationGuard.isCurrent(token, projectRoot)) busyId = "";
    }
  }

  function diagramPoints(values: number[]): string {
    if (values.length === 0) return "";
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const xSpan = values.length > 1 ? 520 / (values.length - 1) : 0;
    return values.map((value, index) => {
      const x = 30 + index * xSpan;
      const y = 180 - ((value - min) / span) * 140;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  }

  function analysisLabel(row: ThermochemistryAnalysisRow): string {
    return `${row.analysis_type} · ${shortId(row.analysis_id)} · ${row.freshness.scientific_state}/${row.freshness.readiness}`;
  }
</script>

<section class="thermo-workspace" aria-labelledby="thermo-heading">
  <header class="section-header">
    <div>
      <span class="eyebrow">v1.1 Block 7</span>
      <h2 id="thermo-heading">Thermochemistry & Reaction Workspace</h2>
      <p>Materialize canonical thermochemistry, bind exact reaction sources, and view Python-evaluated free-energy pathways.</p>
    </div>
    <button type="button" onclick={() => void loadCatalog()} disabled={busy || !projectRoot.trim()}>Refresh</button>
  </header>

  {#if error}<div class="error" role="alert">{error}</div>{/if}

  {#if catalog === null}
    <p class="muted">{busy ? "Loading thermochemistry evidence…" : "No thermochemistry catalog loaded."}</p>
  {:else}
    <div class="grid two">
      <section class="card">
        <h3>Surface / adsorbate thermochemistry</h3>
        <label for="harmonic-source">Frequency calculation</label>
        <select id="harmonic-source" bind:value={harmonicCalculationId} disabled={disabled || !!busyId}>
          <option value="">Select exact frequency source</option>
          {#each frequencySources as source (source.calculation_id)}
            <option value={source.calculation_id} disabled={!source.source_ready}>
              {shortId(source.calculation_id)} · {source.scientific_status} · {source.source_ready ? "ready" : "blocked"}
            </option>
          {/each}
        </select>
        <div class="fields">
          <label>Subject<select bind:value={harmonicSubject}><option value="surface">Surface</option><option value="adsorbate">Adsorbate</option></select></label>
          <label>Temperature (K)<input type="number" min="0.001" step="0.01" bind:value={harmonicTemperature} /></label>
          <label>Energy semantic<select bind:value={harmonicEnergy}><option value="energy_sigma0_ev">σ→0 energy</option><option value="energy_without_entropy_ev">Without entropy</option><option value="free_energy_toten_ev">TOTEN</option></select></label>
          <label>Frequency cutoff (cm⁻¹)<input type="number" min="0.001" step="1" bind:value={harmonicCutoff} /></label>
        </div>
        <p class="hint">This UI uses the fail-closed reject-any / reject-below-cutoff policies. Explicit exclusions remain available through the typed API contract.</p>
        <button type="button" onclick={() => void materializeHarmonic()} disabled={disabled || !!busyId || !harmonicCalculationId}>Materialize / reuse</button>
      </section>

      <section class="card">
        <h3>Ideal-gas reference</h3>
        <label for="gas-source">Gas-frequency calculation</label>
        <select id="gas-source" bind:value={gasCalculationId} disabled={disabled || !!busyId}>
          <option value="">Select exact gas-frequency source</option>
          {#each gasSources as source (source.calculation_id)}
            <option value={source.calculation_id} disabled={!source.source_ready}>
              {shortId(source.calculation_id)} · {source.scientific_status} · {source.source_ready ? "ready" : "blocked"}
            </option>
          {/each}
        </select>
        <div class="fields">
          <label>Species<select bind:value={gasSpecies}><option>H2</option><option>H2O</option><option>O2</option><option>CO</option><option>CO2</option></select></label>
          <label>Temperature (K)<input type="number" min="0.001" step="0.01" bind:value={gasTemperature} /></label>
          <label>Pressure (Pa)<input type="number" min="0.001" step="1" bind:value={gasPressure} /></label>
          <label>Standard state<select bind:value={gasStandardState}><option value="ideal_gas_1_bar">1 bar</option><option value="ideal_gas_1_atm">1 atm</option></select></label>
          <label>Geometry<select bind:value={gasGeometry}><option value="monatomic">Monatomic</option><option value="linear">Linear</option><option value="nonlinear">Nonlinear</option></select></label>
          <label>Symmetry number
            <input type="number" min="1" step="1" bind:value={gasSymmetry} aria-invalid={!gasSymmetryValid} />
            {#if !gasSymmetryValid}<span class="field-error">Positive integer required; fractional values are rejected.</span>{/if}
          </label>
          <label>Spin multiplicity
            <input type="number" min="1" step="1" bind:value={gasMultiplicity} aria-invalid={!gasMultiplicityValid} />
            {#if !gasMultiplicityValid}<span class="field-error">Positive integer required; fractional values are rejected.</span>{/if}
          </label>
          <label>Electronic entropy<select bind:value={gasEntropy}><option value="neglected">Neglected</option><option value="spin_degeneracy">Spin degeneracy</option></select></label>
          <label>Energy semantic<select bind:value={gasEnergy}><option value="energy_sigma0_ev">σ→0 energy</option><option value="energy_without_entropy_ev">Without entropy</option><option value="free_energy_toten_ev">TOTEN</option></select></label>
          <label>Frequency cutoff (cm⁻¹)<input type="number" min="0.001" step="1" bind:value={gasCutoff} /></label>
        </div>
        {#if selectedGasSource !== null}
          <div class="atom-table">
            <strong>Atom-UID-bound masses</strong>
            {#if (selectedGasSource.atoms ?? []).length === 0}
              <p class="error">The selected source does not expose exact snapshot atoms.</p>
            {:else}
              {#each selectedGasSource.atoms ?? [] as atom (atom.atom_uid)}
                <label class="atom-row">
                  <span>{atom.element} · {shortId(atom.atom_uid)}</span>
                  <input type="number" min="0.000001" step="0.00001" placeholder="mass amu" value={gasMasses[atom.atom_uid] ?? ""} oninput={(event) => setGasMass(atom.atom_uid, event)} />
                </label>
              {/each}
            {/if}
          </div>
        {/if}
        <p class="hint">Masses are explicit scientific inputs. The desktop does not silently assign isotope or atomic-mass constants.</p>
        <button type="button" onclick={() => void materializeGas()} disabled={disabled || !!busyId || gasInput() === null}>Materialize / reuse reference</button>
      </section>
    </div>

    <section class="card">
      <div class="card-header"><h3>Durable thermochemistry & diagrams</h3><span>{catalog.analyses.length} analyses</span></div>
      {#if catalog.analyses.length === 0}
        <p class="muted">No durable thermochemistry or reaction diagram yet.</p>
      {:else}
        <div class="analysis-list">
          {#each catalog.analyses as row (row.analysis_id)}
            <button class="analysis-row" type="button" onclick={() => void openAnalysis(row)} disabled={!!busyId}>
              <span>{analysisLabel(row)}</span><small>{row.tool ?? "unknown tool"}</small>
            </button>
          {/each}
        </div>
      {/if}
    </section>

    {#if selectedThermochemistry !== null && selectedThermochemistry.view.kind !== "reaction_diagram"}
      <section class="card">
        <div class="card-header"><h3>Thermochemistry result</h3><span>{selectedThermochemistry.freshness.scientific_state}/{selectedThermochemistry.freshness.readiness}</span></div>
        <p><strong>{selectedThermochemistry.view.kind}</strong> · result {shortId(selectedThermochemistry.view.result_hash)}</p>
        <div class="component-grid">
          {#each Object.entries(selectedThermochemistry.view.result.components) as [key, value] (key)}
            <div><span>{key}</span><strong>{formatValue(value)}</strong></div>
          {/each}
        </div>
        <details><summary>Scientific identity</summary><pre>{JSON.stringify(selectedThermochemistry.view.result.identity, null, 2)}</pre></details>
        <details><summary>Mode selection</summary><pre>{JSON.stringify(selectedThermochemistry.view.result.mode_selection, null, 2)}</pre></details>
        <details><summary>Source receipt</summary><pre>{JSON.stringify(selectedThermochemistry.view.source_receipt, null, 2)}</pre></details>
      </section>
    {/if}

    <section class="card reaction-card">
      <h3>Electrocatalysis reaction pathway</h3>
      <div class="fields">
        <label>Preset<select bind:value={presetKind} onchange={() => { roleValues = {}; preview = null; }}><option value="her_volmer_heyrovsky">HER · Volmer–Heyrovsky</option><option value="orr_associative_4e">ORR · associative 4e</option><option value="oer_associative_4e">OER · associative 4e</option><option value="co2rr_to_co_2e">CO₂RR · CO 2e</option></select></label>
        <label>Temperature (K)<input type="number" min="0.001" step="0.01" bind:value={reactionTemperature} /></label>
      </div>
      <div class="roles">
        {#each requiredReactionRoles as role (role.key)}
          <label>{role.label}
            <select value={roleValues[role.key] ?? ""} onchange={(event) => setRole(role.key, event)}>
              <option value="">Select exact THERMOCHEMISTRY Analysis</option>
              {#each satisfiedThermochemistry as row (row.analysis_id)}
                <option value={row.analysis_id}>{shortId(row.analysis_id)} · {row.tool ?? "thermochemistry"}</option>
              {/each}
            </select>
          </label>
        {/each}
      </div>
      <div class="condition-grid">
        <fieldset><legend>Baseline CHE conditions</legend>
          <label>Potential (V)<input type="number" step="0.01" bind:value={baselinePotential} /></label>
          <label>pH<input type="number" step="0.1" bind:value={baselinePh} /></label>
          <label>Reference<select bind:value={baselineReference}><option value="she">SHE</option><option value="rhe">RHE</option></select></label>
          <label>pH semantics<select bind:value={baselinePhSemantics}><option value="explicit_activity">Explicit activity</option><option value="included_in_rhe">Included in RHE</option></select></label>
        </fieldset>
        <fieldset><legend>Requested CHE conditions</legend>
          <label>Potential (V)<input type="number" step="0.01" bind:value={requestedPotential} /></label>
          <label>pH<input type="number" step="0.1" bind:value={requestedPh} /></label>
          <label>Reference<select bind:value={requestedReference}><option value="she">SHE</option><option value="rhe">RHE</option></select></label>
          <label>pH semantics<select bind:value={requestedPhSemantics}><option value="explicit_activity">Explicit activity</option><option value="included_in_rhe">Included in RHE</option></select></label>
        </fieldset>
      </div>
      <div class="button-row">
        <button type="button" onclick={() => void runPreview()} disabled={disabled || !!busyId || reactionInput === null}>Evaluate preview</button>
        <button type="button" onclick={() => void materializeDiagram()} disabled={disabled || !!busyId || reactionInput === null}>Materialize / reuse canonical diagram</button>
      </div>
      <p class="hint">CHE shifts, cumulative free energies, limiting/reversible potentials and descriptors are evaluated in Python. The SVG below only maps returned values to pixels.</p>
    </section>

    {#if preview !== null}
      <section class="card">
        <div class="card-header"><h3>Reaction preview</h3><span>{preview.preset_kind}</span></div>
        <svg viewBox="0 0 580 220" role="img" aria-label="Python-evaluated reaction free-energy pathway">
          <polyline points={diagramPoints(preview.potential_view.cumulative_state_free_energies_ev)} fill="none" stroke="currentColor" stroke-width="2" />
          {#each preview.potential_view.cumulative_state_free_energies_ev as value, index}
            <circle cx={30 + index * (preview.potential_view.cumulative_state_free_energies_ev.length > 1 ? 520 / (preview.potential_view.cumulative_state_free_energies_ev.length - 1) : 0)} cy={180 - ((value - Math.min(...preview.potential_view.cumulative_state_free_energies_ev)) / (Math.max(...preview.potential_view.cumulative_state_free_energies_ev) - Math.min(...preview.potential_view.cumulative_state_free_energies_ev) || 1)) * 140} r="4" fill="currentColor" />
          {/each}
        </svg>
        <div class="state-grid">
          {#each preview.potential_view.state_keys as state, index (state)}
            <div><span>{state}</span><strong>{preview.potential_view.cumulative_state_free_energies_ev[index]?.toFixed(4)} eV</strong></div>
          {/each}
        </div>
        <div class="descriptor-grid">
          {#each preview.descriptor_definitions as descriptor (descriptor.definition_hash)}
            <div><span>{descriptor.kind}</span><strong>{descriptor.value.toFixed(4)} {descriptor.unit}</strong></div>
          {/each}
        </div>
        <details><summary>Step facts</summary><pre>{JSON.stringify(preview.potential_view.step_views, null, 2)}</pre></details>
      </section>
    {/if}

    {#if selectedDiagram !== null}
      <section class="card">
        <div class="card-header"><h3>Canonical reaction diagram</h3><span>{shortId(selectedDiagram.dataset_hash)}</span></div>
        <svg viewBox="0 0 580 220" role="img" aria-label="Canonical reaction diagram">
          <polyline points={diagramPoints(selectedDiagram.dataset.potential_view.cumulative_state_free_energies_ev)} fill="none" stroke="currentColor" stroke-width="2" />
        </svg>
        <div class="state-grid">
          {#each selectedDiagram.dataset.potential_view.state_keys as state, index (state)}
            <div><span>{state}</span><strong>{selectedDiagram.dataset.potential_view.cumulative_state_free_energies_ev[index]?.toFixed(4)} eV</strong></div>
          {/each}
        </div>
        <div class="descriptor-grid">
          {#each selectedDiagram.dataset.descriptor_definitions as descriptor (descriptor.definition_hash)}
            <div><span>{descriptor.kind}</span><strong>{descriptor.value.toFixed(4)} {descriptor.unit}</strong></div>
          {/each}
        </div>
        <details><summary>Exact source receipts</summary><pre>{JSON.stringify(selectedDiagram.dataset.source_receipts, null, 2)}</pre></details>
      </section>
    {/if}
  {/if}
</section>

<style>
  .thermo-workspace { display: grid; gap: 1rem; margin-top: 1.25rem; }
  .section-header, .card-header, .button-row { display: flex; justify-content: space-between; align-items: center; gap: .75rem; flex-wrap: wrap; }
  .section-header h2, .card h3 { margin: .2rem 0 .35rem; }
  .section-header p, .muted, .hint { margin: 0; color: var(--muted-text,#5d6470); }
  .eyebrow { font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted-text,#5d6470); }
  .grid.two { display: grid; grid-template-columns: repeat(auto-fit,minmax(330px,1fr)); gap: 1rem; }
  .card { border: 1px solid rgba(100,110,125,.22); border-radius: 12px; padding: 1rem; min-width: 0; }
  .fields, .roles, .condition-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: .65rem; margin: .7rem 0; }
  label { display: grid; gap: .3rem; font-size: .86rem; }
  select, input { width: 100%; box-sizing: border-box; padding: .55rem .6rem; border: 1px solid rgba(100,110,125,.32); border-radius: 8px; background: inherit; color: inherit; }
  button { border: 0; border-radius: 8px; padding: .6rem .8rem; font-weight: 700; cursor: pointer; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .analysis-list { display: grid; gap: .45rem; }
  .analysis-row { display: flex; justify-content: space-between; gap: .8rem; text-align: left; width: 100%; background: rgba(100,110,125,.08); }
  .atom-table { display: grid; gap: .45rem; margin: .8rem 0; }
  .atom-row { grid-template-columns: minmax(150px,1fr) minmax(120px,.6fr); align-items: center; }
  .component-grid, .state-grid, .descriptor-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: .5rem; margin: .7rem 0; }
  .component-grid div, .state-grid div, .descriptor-grid div { display: grid; gap: .2rem; padding: .55rem; border-radius: 8px; background: rgba(100,110,125,.08); }
  .component-grid span, .state-grid span, .descriptor-grid span { font-size: .78rem; color: var(--muted-text,#5d6470); overflow-wrap: anywhere; }
  fieldset { display: grid; gap: .5rem; border: 1px solid rgba(100,110,125,.22); border-radius: 10px; padding: .75rem; }
  legend { font-weight: 700; padding: 0 .25rem; }
  svg { width: 100%; min-height: 180px; border: 1px solid rgba(100,110,125,.18); border-radius: 8px; background: rgba(100,110,125,.035); }
  pre { max-height: 20rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: .65rem; border-radius: 8px; background: rgba(20,25,32,.06); font-size: .75rem; }
  details { margin-top: .55rem; }
  .error, .field-error { color: #8b1f2d; }
  .field-error { font-size: .76rem; }
  .hint { font-size: .8rem; margin: .6rem 0; }
</style>