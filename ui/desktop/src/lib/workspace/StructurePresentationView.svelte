<script lang="ts">
  import { Structure, type Crystal, type StructureBond } from "matterviz/structure";

  import type { StructurePresentation } from "./contracts";

  export let presentation: StructurePresentation;

  $: matterviz = presentation.matterviz;
  $: structure = matterviz.structure as unknown as Crystal;
  $: bonds = matterviz.overlay.binding_segments.map(
    (segment): StructureBond => ({
      site_idx_1: segment.adsorbate_index,
      site_idx_2: segment.site_index,
      order: 1,
    }),
  );
  $: highlightedSites = Array.from(
    new Set([
      ...matterviz.overlay.active_center_indices,
      ...matterviz.overlay.bound_adsorbate_indices,
    ]),
  );
</script>

<section class="structure-card">
  <header>
    <div>
      <span class="eyebrow">StructureSnapshot</span>
      <h4>{presentation.structure_snapshot_id}</h4>
    </div>
    <span class:available={matterviz.runtime.interactive_available} class="runtime-chip">
      {matterviz.runtime.interactive_available ? "MatterViz 0.6.0" : "Fallback only"}
    </span>
  </header>

  <dl class="identity-grid">
    <div>
      <dt>Scientific source hash</dt>
      <dd>{presentation.source_scientific_hash}</dd>
    </div>
    <div>
      <dt>Index frame</dt>
      <dd>{matterviz.display_policy.cell_type} · {matterviz.display_policy.supercell_scaling}</dd>
    </div>
    <div>
      <dt>Atoms</dt>
      <dd>{matterviz.atom_index_map.atom_uid_by_viewer_index.length}</dd>
    </div>
    <div>
      <dt>Explicit binding segments</dt>
      <dd>{matterviz.overlay.binding_segments.length}</dd>
    </div>
  </dl>

  {#if matterviz.runtime.interactive_available}
    <div class="matterviz-frame" aria-label={`MatterViz structure ${presentation.structure_snapshot_id}`}>
      <Structure
        {structure}
        {bonds}
        highlighted_sites={highlightedSites}
        selected_sites={[]}
        cell_type={matterviz.display_policy.cell_type}
        supercell_scaling={matterviz.display_policy.supercell_scaling}
        apply_supercell_scaling={matterviz.display_policy.apply_supercell_scaling}
        show_image_atoms={matterviz.display_policy.show_image_atoms}
      />
    </div>
  {:else}
    <div class="fallback-state">
      <strong>Interactive structure runtime unavailable</strong>
      <p>{matterviz.runtime.message ?? "Use the identity-preserving extXYZ fallback."}</p>
      <code>{matterviz.runtime.fallback_kind}</code>
    </div>
  {/if}

  <details>
    <summary>Atom identity map</summary>
    <div class="atom-map">
      {#each matterviz.atom_index_map.atom_uid_by_viewer_index as atomUid, index (atomUid)}
        <div>
          <span>Viewer index {index}</span>
          <code>{atomUid}</code>
        </div>
      {/each}
    </div>
  </details>

  {#if matterviz.overlay.binding_mode || matterviz.overlay.state_label || matterviz.overlay.conformer_name}
    <div class="overlay-context">
      {#if matterviz.overlay.state_label}<span>State: {matterviz.overlay.state_label}</span>{/if}
      {#if matterviz.overlay.binding_mode}<span>Binding: {matterviz.overlay.binding_mode}</span>{/if}
      {#if matterviz.overlay.conformer_name}<span>Conformer: {matterviz.overlay.conformer_name}</span>{/if}
    </div>
  {/if}
</section>

<style>
  .structure-card {
    display: grid;
    gap: 1rem;
    border: 1px solid var(--border-subtle, #d7dce2);
    border-radius: 14px;
    padding: 1rem;
    background: var(--surface, #ffffff);
  }

  header {
    display: flex;
    gap: 1rem;
    align-items: flex-start;
    justify-content: space-between;
  }

  h4 {
    margin: 0.2rem 0 0;
    font-size: 0.9rem;
    overflow-wrap: anywhere;
  }

  .eyebrow {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    opacity: 0.6;
  }

  .runtime-chip {
    flex: none;
    border: 1px solid currentColor;
    border-radius: 999px;
    padding: 0.25rem 0.55rem;
    font-size: 0.72rem;
    opacity: 0.7;
  }

  .runtime-chip.available {
    opacity: 1;
  }

  .identity-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 0.65rem;
    margin: 0;
  }

  .identity-grid div {
    min-width: 0;
    padding: 0.7rem;
    border-radius: 10px;
    background: var(--surface-muted, #f5f7f9);
  }

  dt {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.6;
  }

  dd {
    margin: 0.25rem 0 0;
    font-size: 0.78rem;
    overflow-wrap: anywhere;
  }

  .matterviz-frame {
    min-height: 360px;
    overflow: hidden;
    border: 1px solid var(--border-subtle, #d7dce2);
    border-radius: 12px;
    background: #fafafa;
  }

  .fallback-state {
    padding: 1rem;
    border-radius: 10px;
    background: var(--surface-muted, #f5f7f9);
  }

  .fallback-state p {
    margin: 0.3rem 0 0.6rem;
  }

  details {
    border-top: 1px solid var(--border-subtle, #d7dce2);
    padding-top: 0.75rem;
  }

  summary {
    cursor: pointer;
    font-weight: 650;
    font-size: 0.8rem;
  }

  .atom-map {
    display: grid;
    gap: 0.4rem;
    margin-top: 0.65rem;
  }

  .atom-map div {
    display: grid;
    grid-template-columns: minmax(90px, auto) 1fr;
    gap: 0.75rem;
    align-items: baseline;
    font-size: 0.76rem;
  }

  code {
    overflow-wrap: anywhere;
  }

  .overlay-context {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
  }

  .overlay-context span {
    border-radius: 999px;
    padding: 0.28rem 0.55rem;
    background: var(--surface-muted, #f5f7f9);
    font-size: 0.72rem;
  }
</style>
