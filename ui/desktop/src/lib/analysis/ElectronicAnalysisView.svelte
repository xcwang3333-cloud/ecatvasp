<script lang="ts">
  import type { ElectronicAnalysisClient } from "./client";
  import type {
    BandCenterInput,
    CohpAnalysisView,
    CohpInteraction,
    CohpSpinSeries,
    DosAnalysisView,
    DosSeries,
    ElectronicAnalysisCatalogPayload,
    ElectronicAnalysisViewPayload,
  } from "./contracts";

  export let client: ElectronicAnalysisClient;
  export let projectRoot: string;
  export let disabled = false;
  export let onMutation: () => Promise<void>;

  let observedProjectRoot = "";
  let requestGeneration = 0;
  let catalog: ElectronicAnalysisCatalogPayload | null = null;
  let selected: ElectronicAnalysisViewPayload | null = null;
  let busy = false;
  let busyId = "";
  let error = "";

  let dosAxis: "native" | "fermi" = "fermi";
  let dosSeriesIndex = 0;
  let mirrorSpinDown = true;
  let cohpSign: "native" | "negative" = "negative";
  let cohpInteractionIndex = 0;
  let cohpSpinIndex = 0;

  let descriptorKind: BandCenterInput["kind"] = "d_band";
  let descriptorScope: BandCenterInput["scope"] = "element";
  let descriptorSpin: BandCenterInput["spin"] = "sum";
  let descriptorAtomUid = "";
  let descriptorElement = "";
  let descriptorReference: BandCenterInput["energy_reference"] = "fermi_relative";
  let descriptorLower = -8;
  let descriptorUpper = 3;

  $: if (projectRoot !== observedProjectRoot) {
    observedProjectRoot = projectRoot;
    resetProjectState();
    if (projectRoot.trim()) void loadCatalog(projectRoot);
  }

  $: dosView = selected?.view.kind === "dos" ? selected.view : null;
  $: baderView = selected?.view.kind === "bader" ? selected.view : null;
  $: chargeView = selected?.view.kind === "charge_difference" ? selected.view : null;
  $: cohpView = selected?.view.kind === "cohp" ? selected.view : null;
  $: bandCenterView = selected?.view.kind === "band_center" ? selected.view : null;
  $: dosAtoms = dosView === null ? [] : uniqueDosAtoms(dosView);
  $: dosElements = dosView === null ? [] : uniqueDosElements(dosView);
  $: descriptorValid = descriptorFormValid();
  $: selectedDosSeries = dosView === null ? null : dosView.series[dosSeriesIndex] ?? dosView.series[0] ?? null;
  $: selectedCohpInteraction = cohpView === null ? null : cohpView.interactions[cohpInteractionIndex] ?? cohpView.interactions[0] ?? null;
  $: selectedCohpSeries = selectedCohpInteraction === null ? null : selectedCohpInteraction.series[cohpSpinIndex] ?? selectedCohpInteraction.series[0] ?? null;

  function resetProjectState(): void {
    requestGeneration += 1;
    catalog = null;
    selected = null;
    busy = false;
    busyId = "";
    error = "";
    dosSeriesIndex = 0;
    cohpInteractionIndex = 0;
    cohpSpinIndex = 0;
    descriptorAtomUid = "";
    descriptorElement = "";
  }

  function describeError(value: unknown, fallback: string): string {
    return value instanceof Error ? value.message : fallback;
  }

  async function loadCatalog(root: string = projectRoot): Promise<void> {
    if (!root.trim()) return;
    const generation = ++requestGeneration;
    busy = true;
    error = "";
    try {
      const payload = await client.catalog(root);
      if (generation !== requestGeneration || root !== projectRoot) return;
      catalog = payload;
    } catch (value: unknown) {
      if (generation !== requestGeneration || root !== projectRoot) return;
      error = describeError(value, "Electronic analyses could not be loaded");
    } finally {
      if (generation === requestGeneration && root === projectRoot) busy = false;
    }
  }

  async function materializeDos(calculationId: string): Promise<void> {
    if (disabled || busyId) return;
    const root = projectRoot;
    busyId = calculationId;
    error = "";
    try {
      const receipt = await client.materializeDos(root, calculationId);
      if (root !== projectRoot) return;
      await onMutation();
      await loadCatalog(root);
      await openAnalysis(receipt.analysis_id);
    } catch (value: unknown) {
      if (root !== projectRoot) return;
      error = describeError(value, "Canonical DOS could not be materialized");
    } finally {
      if (root === projectRoot) busyId = "";
    }
  }

  async function openAnalysis(analysisId: string): Promise<void> {
    const root = projectRoot;
    const generation = ++requestGeneration;
    busyId = analysisId;
    error = "";
    try {
      const payload = await client.view(root, analysisId);
      if (generation !== requestGeneration || root !== projectRoot) return;
      selected = payload;
      dosSeriesIndex = 0;
      cohpInteractionIndex = 0;
      cohpSpinIndex = 0;
      if (payload.view.kind === "dos") seedDescriptorSelector(payload.view);
    } catch (value: unknown) {
      if (generation !== requestGeneration || root !== projectRoot) return;
      error = describeError(value, "Electronic analysis could not be opened");
    } finally {
      if (generation === requestGeneration && root === projectRoot) busyId = "";
    }
  }

  async function createBandCenter(): Promise<void> {
    if (dosView === null || selected === null || !descriptorValid || disabled || busyId) return;
    const root = projectRoot;
    const sourceAnalysisId = selected.analysis_id;
    busyId = `descriptor:${sourceAnalysisId}`;
    error = "";
    const input: BandCenterInput = {
      source_analysis_id: sourceAnalysisId,
      kind: descriptorKind,
      scope: descriptorScope,
      spin: descriptorSpin,
      atom_uid: descriptorScope === "atom" ? descriptorAtomUid : null,
      element: descriptorScope === "system" ? null : descriptorElement,
      energy_reference: descriptorReference,
      window_lower_ev: descriptorLower,
      window_upper_ev: descriptorUpper,
    };
    try {
      const receipt = await client.materializeBandCenter(root, input);
      if (root !== projectRoot) return;
      await onMutation();
      await loadCatalog(root);
      await openAnalysis(receipt.analysis_id);
    } catch (value: unknown) {
      if (root !== projectRoot) return;
      error = describeError(value, "Band-center descriptor could not be materialized");
    } finally {
      if (root === projectRoot) busyId = "";
    }
  }

  function seedDescriptorSelector(view: DosAnalysisView): void {
    const atoms = uniqueDosAtoms(view);
    const elements = uniqueDosElements(view);
    descriptorAtomUid = atoms[0]?.atom_uid ?? "";
    descriptorElement = elements[0] ?? "";
  }

  function descriptorFormValid(): boolean {
    if (dosView === null || selected === null) return false;
    if (!Number.isFinite(descriptorLower) || !Number.isFinite(descriptorUpper)) return false;
    if (descriptorUpper <= descriptorLower) return false;
    if (descriptorKind !== "band" && descriptorScope === "system") return false;
    if (descriptorScope === "atom") return Boolean(descriptorAtomUid && descriptorElement);
    if (descriptorScope === "element") return Boolean(descriptorElement);
    return true;
  }

  function uniqueDosAtoms(view: DosAnalysisView): Array<{ atom_uid: string; element: string }> {
    const seen = new Set<string>();
    const result: Array<{ atom_uid: string; element: string }> = [];
    for (const series of view.series) {
      if (series.scope !== "atom" || series.atom_uid === null || series.element === null) continue;
      if (seen.has(series.atom_uid)) continue;
      seen.add(series.atom_uid);
      result.push({ atom_uid: series.atom_uid, element: series.element });
    }
    return result;
  }

  function uniqueDosElements(view: DosAnalysisView): string[] {
    return Array.from(
      new Set(
        view.series
          .filter((series) => series.scope === "atom" && series.element !== null)
          .map((series) => series.element as string),
      ),
    ).sort();
  }

  function selectDescriptorAtom(uid: string): void {
    descriptorAtomUid = uid;
    const atom = dosAtoms.find((item) => item.atom_uid === uid);
    if (atom !== undefined) descriptorElement = atom.element;
  }

  function shortId(value: string | null): string {
    if (value === null) return "—";
    return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
  }

  function seriesLabel(series: DosSeries, index: number): string {
    const selector = series.scope === "system"
      ? "system"
      : `${series.element ?? "?"} ${series.atom_uid === null ? "" : shortId(series.atom_uid)}`;
    return `${index + 1}. ${selector} · ${series.orbital?.label ?? "total"} · ${series.spin}`;
  }

  function dosPath(view: DosAnalysisView, series: DosSeries): string {
    const x = dosAxis === "native" ? view.energies_ev_native : view.energies_ev_relative_to_fermi;
    const y = series.values.map((value) =>
      mirrorSpinDown && series.spin === "down" ? -value : value,
    );
    return linePath(x, y);
  }

  function cohpPath(view: CohpAnalysisView, series: CohpSpinSeries): string {
    const values = cohpSign === "native" ? series.cohp_values_native : series.negative_cohp_values;
    return linePath(view.energies_ev_relative_to_fermi, values);
  }

  function linePath(x: number[], y: number[]): string {
    if (x.length < 2 || y.length !== x.length) return "";
    const xmin = Math.min(...x);
    const xmax = Math.max(...x);
    const ymin = Math.min(...y, 0);
    const ymax = Math.max(...y, 0);
    const xspan = xmax - xmin || 1;
    const yspan = ymax - ymin || 1;
    return x.map((xv, index) => {
      const px = 24 + ((xv - xmin) / xspan) * 552;
      const py = 176 - ((y[index]! - ymin) / yspan) * 152;
      return `${index === 0 ? "M" : "L"}${px.toFixed(2)},${py.toFixed(2)}`;
    }).join(" ");
  }

  function cohpInteractionLabel(item: CohpInteraction, index: number): string {
    return `${index + 1}. ${item.element_a}–${item.element_b} · ${item.bond_length_angstrom.toFixed(3)} Å`;
  }
</script>

<section class="analysis-workspace" aria-labelledby="electronic-analysis-heading">
  <header class="section-heading">
    <div>
      <span class="eyebrow">Electronic structure</span>
      <h3 id="electronic-analysis-heading">Electronic Analysis Workspace</h3>
      <p>DOS/PDOS, Bader, charge difference, COHP/ICOHP, and parameterized band-center facts from canonical Python authorities.</p>
    </div>
    <button type="button" class="secondary" disabled={disabled || busy} onclick={() => void loadCatalog()}>
      {busy ? "Refreshing…" : "Refresh"}
    </button>
  </header>

  {#if error}<div class="error" role="alert">{error}</div>{/if}

  {#if catalog === null}
    <div class="empty">{busy ? "Reading electronic-analysis state…" : "No analysis catalog loaded."}</div>
  {:else}
    <div class="summary-strip">
      <span><strong>{catalog.dos_sources.length}</strong> DOS sources</span>
      <span><strong>{catalog.analyses.length}</strong> electronic analyses</span>
      <span><strong>{catalog.analyses.filter((item) => item.freshness.readiness === "satisfied").length}</strong> current</span>
    </div>

    {#if catalog.dos_sources.length > 0}
      <section class="panel">
        <h4>Canonical DOS / PDOS intake</h4>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Calculation</th><th>Scientific state</th><th>Latest attempt</th><th>Readiness</th><th></th></tr></thead>
            <tbody>
              {#each catalog.dos_sources as row (row.calculation_id)}
                <tr>
                  <td><code title={row.calculation_id}>{shortId(row.calculation_id)}</code></td>
                  <td>{row.scientific_status}</td>
                  <td>{row.latest_attempt_status ?? "—"}</td>
                  <td><span class:ready={row.materialization_ready || row.materialized_analysis_id !== null}>{row.reason}</span></td>
                  <td>
                    {#if row.materialized_analysis_id !== null}
                      <button type="button" disabled={disabled || Boolean(busyId)} onclick={() => void openAnalysis(row.materialized_analysis_id!)}>Open</button>
                    {:else}
                      <button type="button" disabled={disabled || Boolean(busyId) || !row.materialization_ready} onclick={() => void materializeDos(row.calculation_id)}>
                        {busyId === row.calculation_id ? "Parsing…" : "Materialize DOS"}
                      </button>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </section>
    {/if}

    <section class="panel">
      <h4>Durable electronic analyses</h4>
      {#if catalog.analyses.length === 0}
        <p class="muted">No canonical electronic analyses are persisted yet.</p>
      {:else}
        <div class="analysis-list">
          {#each catalog.analyses as row (row.analysis_id)}
            <button
              type="button"
              class:selected-row={selected?.analysis_id === row.analysis_id}
              class="analysis-row"
              disabled={disabled || Boolean(busyId) || !row.view_supported}
              onclick={() => void openAnalysis(row.analysis_id)}
            >
              <span><strong>{row.analysis_type}</strong><small>{row.tool ?? "canonical"} · {row.status}</small></span>
              <span><strong>{row.freshness.readiness}</strong><small>{row.freshness.freshness_state ?? row.freshness.scientific_state}</small></span>
            </button>
          {/each}
        </div>
      {/if}
    </section>
  {/if}

  {#if selected !== null}
    <section class="panel detail-panel">
      <div class="detail-heading">
        <div><span class="eyebrow">Canonical view</span><h4>{selected.analysis_type}</h4></div>
        <div class="freshness"><strong>{selected.freshness.readiness}</strong><small>{selected.freshness.reason_codes.join(", ") || "current canonical evidence"}</small></div>
      </div>

      {#if dosView !== null}
        <div class="controls inline-controls">
          <label>Energy axis<select bind:value={dosAxis}><option value="fermi">E − E<sub>F</sub></option><option value="native">VASP native</option></select></label>
          <label>Series<select bind:value={dosSeriesIndex}>{#each dosView.series as series, index}<option value={index}>{seriesLabel(series, index)}</option>{/each}</select></label>
          <label class="checkbox"><input type="checkbox" bind:checked={mirrorSpinDown} /> Mirror spin-down for display</label>
        </div>
        {#if selectedDosSeries !== null}
          <svg class="plot" viewBox="0 0 600 200" role="img" aria-label="DOS or PDOS line plot">
            <line x1="24" y1="176" x2="576" y2="176" class="axis" />
            <line x1="24" y1="24" x2="24" y2="176" class="axis" />
            <path d={dosPath(dosView, selectedDosSeries)} class="trace" />
          </svg>
          <div class="facts"><span>E<sub>F</sub> {dosView.fermi_energy_ev.toFixed(4)} eV</span><span>{dosView.energies_ev_native.length} energy points</span><span>{dosView.series.length} canonical series</span></div>
        {/if}

        <details open class="descriptor-box">
          <summary>Derive band-center descriptor</summary>
          <div class="descriptor-grid">
            <label>Kind<select bind:value={descriptorKind}><option value="band">Band</option><option value="p_band">p-band</option><option value="d_band">d-band</option></select></label>
            <label>Scope<select bind:value={descriptorScope}><option value="system">System</option><option value="element">Element</option><option value="atom">Atom</option></select></label>
            <label>Spin<select bind:value={descriptorSpin}><option value="total">Total</option><option value="sum">Up + down</option><option value="up">Up</option><option value="down">Down</option></select></label>
            {#if descriptorScope === "element"}
              <label>Element<select bind:value={descriptorElement}>{#each dosElements as element}<option value={element}>{element}</option>{/each}</select></label>
            {:else if descriptorScope === "atom"}
              <label>Atom<select value={descriptorAtomUid} onchange={(event) => selectDescriptorAtom(event.currentTarget.value)}>{#each dosAtoms as atom}<option value={atom.atom_uid}>{atom.element} · {shortId(atom.atom_uid)}</option>{/each}</select></label>
            {/if}
            <label>Reference<select bind:value={descriptorReference}><option value="fermi_relative">E − E_F</option><option value="vasp_native">VASP native</option></select></label>
            <label>Lower (eV)<input type="number" step="0.1" bind:value={descriptorLower} /></label>
            <label>Upper (eV)<input type="number" step="0.1" bind:value={descriptorUpper} /></label>
          </div>
          {#if descriptorKind !== "band" && descriptorScope === "system"}<p class="error">p/d-band centers require atom or element projected DOS.</p>{/if}
          <button type="button" disabled={disabled || Boolean(busyId) || !descriptorValid} onclick={() => void createBandCenter()}>{busyId.startsWith("descriptor:") ? "Deriving…" : "Materialize descriptor"}</button>
        </details>
      {:else if baderView !== null}
        <div class="facts"><span>Reference: {baderView.reference_mode}</span><span>Total electrons: {baderView.number_of_electrons.toFixed(5)}</span><span>{baderView.sites.length} basins</span></div>
        <div class="table-wrap"><table><thead><tr><th>Atom</th><th>Electrons</th><th>Min distance (Å)</th><th>Volume (Å³)</th></tr></thead><tbody>{#each baderView.sites as site (site.atom_uid)}<tr><td><code>{shortId(site.atom_uid)}</code></td><td>{site.electron_count.toFixed(5)}</td><td>{site.min_distance_angstrom.toFixed(4)}</td><td>{site.basin_volume_angstrom3.toFixed(4)}</td></tr>{/each}</tbody></table></div>
        <p class="muted">Raw Bader basin facts only; oxidation state and charge-transfer labels are intentionally not inferred.</p>
      {:else if chargeView !== null}
        <div class="metric-grid"><div><span>Grid</span><strong>{chargeView.grid_shape_xyz.join(" × ")}</strong></div><div><span>Δρ range</span><strong>{chargeView.density_min.toExponential(3)} – {chargeView.density_max.toExponential(3)}</strong></div><div><span>Integrated Δe</span><strong>{chargeView.delta_electron_integral.toFixed(6)}</strong></div><div><span>Convention</span><strong>{chargeView.delta_convention}</strong></div></div>
        <p class="muted">The canonical 3D density remains artifact-backed; the desktop IPC intentionally exposes verified metadata rather than copying the full volume into application state.</p>
      {:else if cohpView !== null}
        <div class="controls inline-controls">
          <label>Interaction<select bind:value={cohpInteractionIndex}>{#each cohpView.interactions as item, index}<option value={index}>{cohpInteractionLabel(item, index)}</option>{/each}</select></label>
          {#if selectedCohpInteraction !== null}<label>Spin<select bind:value={cohpSpinIndex}>{#each selectedCohpInteraction.series as item, index}<option value={index}>{item.spin}</option>{/each}</select></label>{/if}
          <label>Display sign<select bind:value={cohpSign}><option value="negative">−COHP</option><option value="native">Native COHP</option></select></label>
        </div>
        {#if selectedCohpSeries !== null && selectedCohpInteraction !== null}
          <svg class="plot" viewBox="0 0 600 200" role="img" aria-label="COHP line plot"><line x1="24" y1="176" x2="576" y2="176" class="axis" /><line x1="24" y1="24" x2="24" y2="176" class="axis" /><path d={cohpPath(cohpView, selectedCohpSeries)} class="trace" /></svg>
          <div class="facts"><span>{selectedCohpInteraction.element_a}–{selectedCohpInteraction.element_b}</span><span>{selectedCohpInteraction.bond_length_angstrom.toFixed(4)} Å</span><span>ICOHP(E<sub>F</sub>) {selectedCohpSeries.icohp_at_fermi_ev_native?.toFixed(4) ?? "—"} eV</span></div>
          <p class="muted">Native COHP is canonical; −COHP is an explicit display transform only.</p>
        {/if}
      {:else if bandCenterView !== null}
        <div class="metric-grid"><div><span>{bandCenterView.descriptor_kind}</span><strong>{bandCenterView.center_ev.toFixed(5)} eV</strong></div><div><span>Window</span><strong>{bandCenterView.window_lower_ev} to {bandCenterView.window_upper_ev} eV</strong></div><div><span>Scope</span><strong>{bandCenterView.selector.scope} · {bandCenterView.selector.element ?? "system"}</strong></div><div><span>Spin</span><strong>{bandCenterView.selector.spin}</strong></div></div>
        <div class="facts"><span>Zeroth moment {bandCenterView.zeroth_moment_states.toFixed(6)}</span><span>{bandCenterView.quadrature_point_count} quadrature points</span><span>{bandCenterView.contributing_series_count} contributing series</span></div>
      {/if}
    </section>
  {/if}
</section>

<style>
  .analysis-workspace { display: grid; gap: 1rem; margin-top: 1.25rem; }
  .section-heading, .detail-heading { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; }
  h3, h4 { margin: .2rem 0 .35rem; }
  p { margin: 0; }
  .eyebrow { font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; opacity: .72; }
  .secondary, button { border: 0; border-radius: 8px; padding: .58rem .75rem; font-weight: 700; cursor: pointer; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .panel { border: 1px solid rgba(100,110,125,.22); border-radius: 12px; padding: 1rem; min-width: 0; }
  .summary-strip, .facts { display: flex; flex-wrap: wrap; gap: .7rem 1.2rem; font-size: .86rem; }
  .summary-strip { padding: .75rem 1rem; border-radius: 10px; background: rgba(100,110,125,.08); }
  .summary-strip span { display: flex; gap: .35rem; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: .84rem; }
  th, td { text-align: left; padding: .55rem .45rem; border-bottom: 1px solid rgba(100,110,125,.16); vertical-align: top; }
  code { font-size: .78rem; }
  .ready { font-weight: 650; }
  .analysis-list { display: grid; gap: .45rem; }
  .analysis-row { display: flex; justify-content: space-between; text-align: left; width: 100%; background: transparent; border: 1px solid rgba(100,110,125,.2); }
  .analysis-row span { display: grid; gap: .15rem; }
  .analysis-row small, .muted { opacity: .7; }
  .selected-row { outline: 2px solid currentColor; outline-offset: 1px; }
  .freshness { display: grid; justify-items: end; gap: .15rem; font-size: .82rem; }
  .freshness small { max-width: 26rem; text-align: right; opacity: .7; }
  .controls { display: flex; flex-wrap: wrap; gap: .65rem; align-items: end; }
  .controls label, .descriptor-grid label { display: grid; gap: .25rem; font-size: .78rem; font-weight: 650; }
  select, input { padding: .48rem .55rem; border: 1px solid rgba(100,110,125,.3); border-radius: 7px; background: inherit; color: inherit; }
  .checkbox { display: flex !important; grid-auto-flow: column; align-items: center; }
  .plot { width: 100%; min-height: 190px; margin-top: .75rem; border-radius: 8px; background: rgba(100,110,125,.055); }
  .axis { stroke: currentColor; stroke-opacity: .35; stroke-width: 1; }
  .trace { fill: none; stroke: currentColor; stroke-width: 1.6; vector-effect: non-scaling-stroke; }
  .descriptor-box { margin-top: 1rem; border-top: 1px solid rgba(100,110,125,.18); padding-top: .8rem; }
  .descriptor-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(135px,1fr)); gap: .6rem; margin: .75rem 0; }
  .metric-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(160px,1fr)); gap: .65rem; }
  .metric-grid > div { display: grid; gap: .25rem; padding: .75rem; border: 1px solid rgba(100,110,125,.18); border-radius: 8px; }
  .metric-grid span { font-size: .75rem; opacity: .65; }
  .error { padding: .65rem .75rem; border-radius: 8px; color: #8b1f2d; background: rgba(139,31,45,.08); }
  .empty { padding: 1rem; opacity: .7; }
  @media (max-width: 700px) { .section-heading, .detail-heading { display: grid; } .freshness { justify-items: start; } .freshness small { text-align: left; } }
</style>
