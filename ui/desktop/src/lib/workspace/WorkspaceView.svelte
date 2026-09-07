<script lang="ts">
  import StructurePresentationView from "./StructurePresentationView.svelte";
  import type { ScientificWorkspace, StatusPair, WorkspaceInventoryRow } from "./contracts";

  export let workspace: ScientificWorkspace;
  export let refreshing = false;
  export let onRefresh: () => void;

  $: attentionRows = workspace.inventory.rows.filter(
    (row) => row.attention_codes.length > 0 || row.freshness.state !== "fresh",
  );
  $: provenanceRecords = workspace.inventory.rows.flatMap((row) => row.provenance);

  function shortHash(value: string | null): string {
    return value === null ? "—" : `${value.slice(0, 10)}…${value.slice(-6)}`;
  }

  function codes(values: string[]): string {
    return values.length === 0 ? "—" : values.join(", ");
  }

  function statusLabel(row: WorkspaceInventoryRow): string {
    if (row.status_domain === null || row.status === null) return "—";
    return `${row.status_domain}: ${row.status}`;
  }

  function countPairs(values: StatusPair[]): number {
    return values.reduce((total, [, count]) => total + count, 0);
  }
</script>

<section class="scientific-workspace" aria-labelledby="scientific-workspace-heading">
  <header class="workspace-header">
    <div>
      <span class="eyebrow">Authoritative read surface</span>
      <h2 id="scientific-workspace-heading">Scientific workspace</h2>
      <p>
        Read from the current Python handoff. Scientific identity, freshness, workflow gates, and
        source hashes are displayed but never recomputed here.
      </p>
    </div>
    <button type="button" class="refresh-button" disabled={refreshing} onclick={onRefresh}>
      {refreshing ? "Refreshing…" : "Refresh current state"}
    </button>
  </header>

  <section class="workspace-section identity-section" aria-labelledby="workspace-identity-heading">
    <div class="section-heading">
      <div>
        <span class="eyebrow">Current ProjectStore projection</span>
        <h3 id="workspace-identity-heading">{workspace.project.project_name}</h3>
      </div>
      <span class="schema-chip">schema v{workspace.project.schema_version}</span>
    </div>
    <dl class="hash-grid">
      <div>
        <dt>Project ID</dt>
        <dd>{workspace.project.project_id}</dd>
      </div>
      <div>
        <dt>Projection hash</dt>
        <dd title={workspace.project.projection_hash}>{shortHash(workspace.project.projection_hash)}</dd>
      </div>
      <div>
        <dt>Report hash</dt>
        <dd title={workspace.report_hash}>{shortHash(workspace.report_hash)}</dd>
      </div>
      <div>
        <dt>Handoff hash</dt>
        <dd title={workspace.handoff_hash}>{shortHash(workspace.handoff_hash)}</dd>
      </div>
    </dl>
  </section>

  <section class="workspace-section" aria-labelledby="lifecycle-heading">
    <div class="section-heading">
      <div>
        <span class="eyebrow">Do not collapse</span>
        <h3 id="lifecycle-heading">Lifecycle namespaces</h3>
      </div>
    </div>
    <div class="status-grid">
      <article>
        <div class="status-title"><span>Calculation</span><strong>{countPairs(workspace.project.statuses.calculations)}</strong></div>
        {#if workspace.project.statuses.calculations.length === 0}<p>None</p>{:else}
          <ul>{#each workspace.project.statuses.calculations as [status, count]}<li><span>{status}</span><strong>{count}</strong></li>{/each}</ul>
        {/if}
      </article>
      <article>
        <div class="status-title"><span>Analysis</span><strong>{countPairs(workspace.project.statuses.analyses)}</strong></div>
        {#if workspace.project.statuses.analyses.length === 0}<p>None</p>{:else}
          <ul>{#each workspace.project.statuses.analyses as [status, count]}<li><span>{status}</span><strong>{count}</strong></li>{/each}</ul>
        {/if}
      </article>
      <article>
        <div class="status-title"><span>ExecutionAttempt</span><strong>{countPairs(workspace.project.statuses.execution_attempts)}</strong></div>
        {#if workspace.project.statuses.execution_attempts.length === 0}<p>None</p>{:else}
          <ul>{#each workspace.project.statuses.execution_attempts as [status, count]}<li><span>{status}</span><strong>{count}</strong></li>{/each}</ul>
        {/if}
      </article>
      <article>
        <div class="status-title"><span>Scheduler / RemoteJob</span><strong>{countPairs(workspace.project.statuses.scheduler_jobs)}</strong></div>
        {#if workspace.project.statuses.scheduler_jobs.length === 0}<p>None</p>{:else}
          <ul>{#each workspace.project.statuses.scheduler_jobs as [status, count]}<li><span>{status}</span><strong>{count}</strong></li>{/each}</ul>
        {/if}
      </article>
    </div>
    <p class="boundary-note">
      Scheduler completion is displayed independently and is never interpreted as scientific convergence.
    </p>
  </section>

  <section class="workspace-section" aria-labelledby="attention-heading">
    <div class="section-heading">
      <div>
        <span class="eyebrow">Freshness is authoritative</span>
        <h3 id="attention-heading">Attention and freshness</h3>
      </div>
      <span class:warning={attentionRows.length > 0} class="count-chip">{attentionRows.length} attention rows</span>
    </div>
    {#if attentionRows.length === 0}
      <div class="empty-state"><strong>No non-fresh inventory rows</strong><p>No stale, invalid, superseded, or otherwise attention-marked rows were supplied.</p></div>
    {:else}
      <div class="table-scroll">
        <table>
          <thead><tr><th>Entity</th><th>Kind</th><th>Status</th><th>Freshness</th><th>Attention</th><th>Reasons</th></tr></thead>
          <tbody>
            {#each attentionRows as row (row.entity_id)}
              <tr class="attention-row">
                <td><code>{row.entity_id}</code></td>
                <td>{row.entity_kind}</td>
                <td>{statusLabel(row)}</td>
                <td><span class="state-chip">{row.freshness.state}</span></td>
                <td>{codes(row.attention_codes)}</td>
                <td>{codes(row.freshness.reasons.map((reason) => reason.code))}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <section class="workspace-section" aria-labelledby="inventory-heading">
    <div class="section-heading">
      <div>
        <span class="eyebrow">Scientific inventory</span>
        <h3 id="inventory-heading">Entities</h3>
      </div>
      <span class="count-chip">{workspace.inventory.rows.length} rows</span>
    </div>
    {#if workspace.inventory.rows.length === 0}
      <div class="empty-state"><strong>Empty project inventory</strong><p>No scientific entities are present in this ProjectStore.</p></div>
    {:else}
      <div class="table-scroll">
        <table>
          <thead><tr><th>Label</th><th>Kind</th><th>Status</th><th>Freshness</th><th>Scientific hash</th><th>Ancestors</th></tr></thead>
          <tbody>
            {#each workspace.inventory.rows as row (row.entity_id)}
              <tr class:attention-row={row.attention_codes.length > 0 || row.freshness.state !== "fresh"}>
                <td><strong>{row.display_label}</strong><br /><code>{row.entity_id}</code></td>
                <td>{row.entity_kind}</td>
                <td>{statusLabel(row)}</td>
                <td>{row.freshness.state}</td>
                <td><code title={row.current_scientific_hash ?? undefined}>{shortHash(row.current_scientific_hash)}</code></td>
                <td>{row.scientific_ancestor_ids.length}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <div class="two-column">
    <section class="workspace-section" aria-labelledby="provenance-heading">
      <div class="section-heading">
        <div><span class="eyebrow">Source traceability</span><h3 id="provenance-heading">Provenance</h3></div>
        <span class="count-chip">{provenanceRecords.length}</span>
      </div>
      {#if provenanceRecords.length === 0}
        <div class="empty-state compact"><strong>No provenance records</strong></div>
      {:else}
        <div class="record-stack">
          {#each provenanceRecords as item (item.provenance_id)}
            <article class="record-card">
              <strong>{item.tool} <span>@ {item.tool_version}</span></strong>
              <code>{item.provenance_id}</code>
              <dl>
                <div><dt>Subject</dt><dd>{item.subject_id}</dd></div>
                <div><dt>Parameters hash</dt><dd title={item.parameters_hash ?? undefined}>{shortHash(item.parameters_hash)}</dd></div>
                <div><dt>MethodFingerprint</dt><dd>{item.method_fingerprint_id ?? "—"}</dd></div>
              </dl>
            </article>
          {/each}
        </div>
      {/if}
    </section>

    <section class="workspace-section" aria-labelledby="dependencies-heading">
      <div class="section-heading">
        <div><span class="eyebrow">Explicit edges only</span><h3 id="dependencies-heading">Dependencies</h3></div>
        <span class="count-chip">{workspace.inventory.dependencies.length}</span>
      </div>
      {#if workspace.inventory.dependencies.length === 0}
        <div class="empty-state compact"><strong>No dependency records</strong></div>
      {:else}
        <div class="record-stack">
          {#each workspace.inventory.dependencies as item (item.dependency_id)}
            <article class="record-card">
              <div class="dependency-heading"><strong>{item.kind}</strong><span>{item.role}</span></div>
              <code>{item.upstream_id} → {item.downstream_id}</code>
              <dl>
                <div><dt>Dependency ID</dt><dd>{item.dependency_id}</dd></div>
                <div><dt>Recorded source hash</dt><dd title={item.recorded_hash}>{shortHash(item.recorded_hash)}</dd></div>
              </dl>
            </article>
          {/each}
        </div>
      {/if}
    </section>
  </div>

  <section class="workspace-section" aria-labelledby="readiness-heading">
    <div class="section-heading">
      <div>
        <span class="eyebrow">Scientific gates ≠ scheduler state</span>
        <h3 id="readiness-heading">Workflow readiness</h3>
      </div>
      <span class="count-chip">{workspace.workflow_readiness.length} dashboards</span>
    </div>
    {#if workspace.workflow_readiness.length === 0}
      <div class="empty-state">
        <strong>No authoritative readiness dashboard supplied</strong>
        <p>The desktop does not guess workflow gates from filenames, scheduler state, or history order.</p>
      </div>
    {:else}
      <div class="readiness-stack">
        {#each workspace.workflow_readiness as dashboard (dashboard.workflow_plan_id)}
          <article class="readiness-card">
            <h4>Workflow <code>{dashboard.workflow_plan_id}</code></h4>
            <div class="table-scroll">
              <table>
                <thead><tr><th>Step</th><th>Scientific state</th><th>Readiness</th><th>Calculation</th><th>Generation</th><th>Gate reasons</th></tr></thead>
                <tbody>
                  {#each dashboard.steps as step (step.step_key)}
                    <tr>
                      <td><strong>{step.step_key}</strong></td>
                      <td>{step.scientific_state}</td>
                      <td>{step.readiness}</td>
                      <td>{step.calculation_status ?? "—"}<br /><code>{step.calculation_id ?? "—"}</code></td>
                      <td>{step.current_generation ?? "—"}</td>
                      <td>{codes(step.gate_reason_codes)}</td>
                    </tr>
                    {#if step.execution_attempts.length > 0}
                      <tr class="attempt-row">
                        <td colspan="6">
                          <div class="attempt-stack">
                            {#each step.execution_attempts as attempt (attempt.execution_attempt_id)}
                              <div>
                                <strong>ExecutionAttempt #{attempt.attempt_number}: {attempt.status}</strong>
                                <code>{attempt.execution_attempt_id}</code>
                                {#if attempt.remote_jobs.length > 0}
                                  <ul>
                                    {#each attempt.remote_jobs as job (job.remote_job_id)}
                                      <li>{job.scheduler} job {job.scheduler_job_id}: <strong>{job.state}</strong> · <code>{job.remote_directory}</code></li>
                                    {/each}
                                  </ul>
                                {/if}
                              </div>
                            {/each}
                          </div>
                        </td>
                      </tr>
                    {/if}
                  {/each}
                </tbody>
              </table>
            </div>
            {#if dashboard.edges.length > 0}
              <div class="edge-list">
                {#each dashboard.edges as edge (`${edge.upstream_step_key}-${edge.downstream_step_key}-${edge.role}`)}
                  <div>
                    <strong>{edge.upstream_step_key} → {edge.downstream_step_key}</strong>
                    <span>{edge.role} · {edge.verdict}</span>
                    <small>{codes(edge.reason_codes)}</small>
                    {#if edge.accepted_structure_snapshot_id}<code>{edge.accepted_structure_snapshot_id}</code>{/if}
                  </div>
                {/each}
              </div>
            {/if}
          </article>
        {/each}
      </div>
    {/if}
  </section>

  <section class="workspace-section" aria-labelledby="presentations-heading">
    <div class="section-heading">
      <div>
        <span class="eyebrow">Presentation-only DTOs</span>
        <h3 id="presentations-heading">Scientific presentations</h3>
      </div>
      <span class="count-chip">{workspace.presentations.length}</span>
    </div>
    {#if workspace.presentations.length === 0}
      <div class="empty-state"><strong>No scientific presentations supplied</strong></div>
    {:else}
      <div class="presentation-stack">
        {#each workspace.presentations as presentation, index (`${presentation.kind}-${index}`)}
          {#if presentation.kind === "structure"}
            <StructurePresentationView {presentation} />
          {:else if presentation.kind === "dos_pdos"}
            <article class="presentation-card">
              <span class="eyebrow">DOS / PDOS · unchanged canonical values</span>
              <h4>Structure <code>{presentation.structure_snapshot_id}</code></h4>
              <dl class="presentation-grid">
                <div><dt>Source hash</dt><dd>{shortHash(presentation.source_content_hash)}</dd></div>
                <div><dt>Energy reference</dt><dd>{presentation.source_energy_reference}</dd></div>
                <div><dt>Fermi energy</dt><dd>{presentation.fermi_energy_ev} {presentation.energy_unit}</dd></div>
                <div><dt>Density unit</dt><dd>{presentation.density_unit}</dd></div>
                <div><dt>Grid points</dt><dd>{presentation.native_energies_ev.length}</dd></div>
                <div><dt>Series</dt><dd>{presentation.series.length}</dd></div>
              </dl>
              <p class="boundary-note">No smoothing, clipping, aggregation, or spin inversion is applied by the desktop.</p>
            </article>
          {:else if presentation.kind === "cohp_icohp"}
            <article class="presentation-card">
              <span class="eyebrow">COHP / ICOHP · native source convention</span>
              <h4>Structure <code>{presentation.structure_snapshot_id}</code></h4>
              <dl class="presentation-grid">
                <div><dt>Source hash</dt><dd>{shortHash(presentation.source_content_hash)}</dd></div>
                <div><dt>Energy reference</dt><dd>{presentation.energy_reference}</dd></div>
                <div><dt>Sign convention</dt><dd>{presentation.sign_convention}</dd></div>
                <div><dt>Fermi energy</dt><dd>{presentation.source_fermi_energy_ev} {presentation.energy_unit}</dd></div>
                <div><dt>Interactions</dt><dd>{presentation.interactions.length}</dd></div>
                <div><dt>Bond-length unit</dt><dd>{presentation.bond_length_unit}</dd></div>
              </dl>
              <p class="boundary-note">The desktop does not silently transform LOBSTER native COHP into −COHP.</p>
            </article>
          {:else}
            <article class="presentation-card">
              <span class="eyebrow">Reaction diagram · explicit CHE conditions</span>
              <h4>Project <code>{presentation.project_id}</code></h4>
              <dl class="presentation-grid">
                <div><dt>Source result hash</dt><dd>{shortHash(presentation.source_result_hash)}</dd></div>
                <div><dt>Temperature</dt><dd>{presentation.requested_conditions.temperature_k} K</dd></div>
                <div><dt>Potential</dt><dd>{presentation.requested_conditions.potential_v} {presentation.potential_unit} vs {presentation.requested_conditions.potential_reference.toUpperCase()}</dd></div>
                <div><dt>pH</dt><dd>{presentation.requested_conditions.ph} ({presentation.requested_conditions.ph_semantics})</dd></div>
              </dl>
              <div class="table-scroll compact-table">
                <table>
                  <thead><tr><th>State</th><th>Cumulative ΔG ({presentation.free_energy_unit})</th></tr></thead>
                  <tbody>{#each presentation.states as state (state.index)}<tr><td>{state.state_key}</td><td>{state.cumulative_free_energy_ev}</td></tr>{/each}</tbody>
                </table>
              </div>
              <div class="table-scroll compact-table">
                <table>
                  <thead><tr><th>Step</th><th>From → To</th><th>ΔG target ({presentation.free_energy_unit})</th><th>Potential slope</th></tr></thead>
                  <tbody>{#each presentation.steps as step (step.index)}<tr><td>{step.step_key}</td><td>{step.from_state_key} → {step.to_state_key}</td><td>{step.target_delta_g_ev}</td><td>{step.potential_slope_ev_per_v}</td></tr>{/each}</tbody>
                </table>
              </div>
            </article>
          {/if}
        {/each}
      </div>
    {/if}
  </section>
</section>

<style>
  .scientific-workspace { display: grid; gap: 1rem; }
  .workspace-header, .section-heading, .status-title, .dependency-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
  .workspace-header { padding: 0.2rem 0; }
  .workspace-header h2, .section-heading h3, .presentation-card h4, .readiness-card h4 { margin: 0.2rem 0 0; }
  .workspace-header p { max-width: 760px; margin: 0.45rem 0 0; color: var(--text-muted, #59636e); }
  .eyebrow { font-size: 0.7rem; font-weight: 750; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-muted, #59636e); }
  .refresh-button { flex: none; border: 1px solid #1c252e; border-radius: 9px; padding: 0.55rem 0.8rem; background: #1c252e; color: #fff; font: inherit; cursor: pointer; }
  .refresh-button:disabled { opacity: 0.5; cursor: default; }
  .workspace-section { min-width: 0; border: 1px solid var(--border-subtle, #d7dce2); border-radius: 14px; padding: 1rem; background: var(--surface, #fff); }
  .identity-section { background: linear-gradient(135deg, #fff, #f7f9fb); }
  .schema-chip, .count-chip, .state-chip { border-radius: 999px; padding: 0.25rem 0.55rem; background: var(--surface-muted, #eef1f4); font-size: 0.72rem; white-space: nowrap; }
  .count-chip.warning { background: #fff1dd; color: #7a4300; }
  .hash-grid, .presentation-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 0.6rem; margin: 0.85rem 0 0; }
  .hash-grid div, .presentation-grid div { min-width: 0; border-radius: 9px; padding: 0.65rem; background: var(--surface-muted, #f5f7f9); }
  dt { font-size: 0.67rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted, #59636e); }
  dd { margin: 0.2rem 0 0; overflow-wrap: anywhere; font-size: 0.78rem; }
  .status-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.7rem; margin-top: 0.85rem; }
  .status-grid article { min-width: 0; padding: 0.8rem; border-radius: 10px; background: var(--surface-muted, #f5f7f9); }
  .status-title span { font-weight: 700; font-size: 0.79rem; }
  .status-title strong { font-size: 1.15rem; }
  .status-grid ul { list-style: none; padding: 0; margin: 0.65rem 0 0; display: grid; gap: 0.3rem; }
  .status-grid li { display: flex; justify-content: space-between; gap: 0.6rem; font-size: 0.74rem; }
  .status-grid p { margin: 0.65rem 0 0; color: var(--text-muted, #59636e); font-size: 0.75rem; }
  .boundary-note { margin: 0.7rem 0 0; font-size: 0.74rem; color: var(--text-muted, #59636e); }
  .table-scroll { overflow-x: auto; margin-top: 0.85rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.76rem; }
  th { text-align: left; color: var(--text-muted, #59636e); font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.04em; }
  th, td { padding: 0.55rem 0.45rem; border-bottom: 1px solid var(--border-subtle, #e1e5e9); vertical-align: top; }
  code { font-size: 0.7rem; overflow-wrap: anywhere; }
  .attention-row { background: #fffaf1; }
  .empty-state { margin-top: 0.85rem; border-radius: 10px; padding: 0.9rem; background: var(--surface-muted, #f5f7f9); }
  .empty-state.compact { padding: 0.7rem; }
  .empty-state p { margin: 0.3rem 0 0; color: var(--text-muted, #59636e); font-size: 0.76rem; }
  .two-column { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
  .record-stack, .readiness-stack, .presentation-stack { display: grid; gap: 0.7rem; margin-top: 0.85rem; }
  .record-card, .presentation-card, .readiness-card { min-width: 0; border: 1px solid var(--border-subtle, #e1e5e9); border-radius: 10px; padding: 0.8rem; }
  .record-card > strong span { font-weight: 500; color: var(--text-muted, #59636e); }
  .record-card > code { display: block; margin: 0.25rem 0 0.55rem; }
  .record-card dl { margin: 0; display: grid; gap: 0.45rem; }
  .dependency-heading span { font-size: 0.72rem; color: var(--text-muted, #59636e); }
  .readiness-card h4 code { font-size: inherit; }
  .attempt-row { background: var(--surface-muted, #f8f9fb); }
  .attempt-stack { display: grid; gap: 0.55rem; }
  .attempt-stack > div { display: grid; gap: 0.2rem; }
  .attempt-stack ul { margin: 0.25rem 0 0; padding-left: 1.2rem; }
  .edge-list { display: grid; gap: 0.45rem; margin-top: 0.8rem; }
  .edge-list > div { display: grid; grid-template-columns: minmax(120px, auto) minmax(120px, auto) 1fr; gap: 0.5rem; align-items: baseline; padding: 0.55rem; border-radius: 8px; background: var(--surface-muted, #f5f7f9); font-size: 0.73rem; }
  .edge-list code { grid-column: 1 / -1; }
  .compact-table { max-width: 760px; }
  @media (max-width: 1000px) { .status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .two-column { grid-template-columns: 1fr; } }
  @media (max-width: 680px) { .workspace-header { display: grid; } .refresh-button { justify-self: start; } .status-grid { grid-template-columns: 1fr; } .edge-list > div { grid-template-columns: 1fr; } }
</style>
