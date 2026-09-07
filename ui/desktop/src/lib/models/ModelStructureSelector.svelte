<script lang="ts">
  import { Structure, type Crystal } from "matterviz/structure";

  import type { StructurePresentation } from "../workspace/contracts";

  export let presentation: StructurePresentation;
  export let selectedAtomUids: string[] = [];
  export let onSelectionChange: (atomUids: string[]) => void;

  $: matterviz = presentation.matterviz;
  $: structure = matterviz.structure as unknown as Crystal;
  $: atomUids = matterviz.atom_index_map.atom_uid_by_viewer_index;
  $: selectedIndices = selectedAtomUids
    .map((atomUid) => matterviz.atom_index_map.viewer_index_by_atom_uid[atomUid])
    .filter((value): value is number => typeof value === "number");

  function toggle(atomUid: string): void {
    const next = selectedAtomUids.includes(atomUid)
      ? selectedAtomUids.filter((value) => value !== atomUid)
      : [...selectedAtomUids, atomUid];
    onSelectionChange(next);
  }
</script>

<section class="selector-shell">
  <div class="viewer-frame">
    <Structure
      {structure}
      highlighted_sites={selectedIndices}
      selected_sites={selectedIndices}
      cell_type={matterviz.display_policy.cell_type}
      supercell_scaling={matterviz.display_policy.supercell_scaling}
      apply_supercell_scaling={matterviz.display_policy.apply_supercell_scaling}
      show_image_atoms={matterviz.display_policy.show_image_atoms}
    />
  </div>

  <div class="selection-panel">
    <div class="selection-heading">
      <div>
        <strong>Atoms</strong>
        <small>Select by viewer index; ECatVASP submits the mapped atom identity.</small>
      </div>
      <span>{selectedAtomUids.length} selected</span>
    </div>
    <div class="atom-grid">
      {#each atomUids as atomUid, index (atomUid)}
        <button
          type="button"
          class:selected={selectedAtomUids.includes(atomUid)}
          aria-pressed={selectedAtomUids.includes(atomUid)}
          onclick={() => toggle(atomUid)}
        >
          Atom {index}
        </button>
      {/each}
    </div>
    <details>
      <summary>Advanced identity map</summary>
      {#each atomUids as atomUid, index (atomUid)}
        <div class="identity-row"><span>Atom {index}</span><code>{atomUid}</code></div>
      {/each}
    </details>
  </div>
</section>

<style>
  .selector-shell {
    display: grid;
    grid-template-columns: minmax(0, 1.4fr) minmax(230px, 0.6fr);
    gap: 1rem;
  }

  .viewer-frame {
    min-height: 380px;
    overflow: hidden;
    border: 1px solid rgba(100, 110, 125, 0.25);
    border-radius: 12px;
    background: #fafafa;
  }

  .selection-panel {
    display: grid;
    align-content: start;
    gap: 0.8rem;
  }

  .selection-heading {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
  }

  .selection-heading > div {
    display: grid;
    gap: 0.2rem;
  }

  small {
    color: var(--muted-text, #5d6470);
    line-height: 1.35;
  }

  .atom-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(74px, 1fr));
    gap: 0.4rem;
    max-height: 310px;
    overflow: auto;
  }

  .atom-grid button {
    border: 1px solid rgba(100, 110, 125, 0.3);
    border-radius: 8px;
    padding: 0.5rem;
    background: transparent;
    color: inherit;
    cursor: pointer;
  }

  .atom-grid button.selected {
    border-width: 2px;
    font-weight: 700;
  }

  details {
    border-top: 1px solid rgba(100, 110, 125, 0.2);
    padding-top: 0.65rem;
  }

  summary {
    cursor: pointer;
    font-size: 0.8rem;
    font-weight: 650;
  }

  .identity-row {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.6rem;
    margin-top: 0.4rem;
    font-size: 0.72rem;
  }

  code {
    overflow-wrap: anywhere;
  }

  @media (max-width: 900px) {
    .selector-shell {
      grid-template-columns: 1fr;
    }
  }
</style>
