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
    <div class="viewer-overlay viewer-overlay-left">
      <span>Interactive structure</span>
      <strong>{atomUids.length} atoms</strong>
    </div>
    <div class="viewer-overlay viewer-overlay-right" class:active={selectedAtomUids.length > 0}>
      <strong>{selectedAtomUids.length}</strong>
      <span>selected</span>
      {#if selectedAtomUids.length > 0}
        <button type="button" onclick={() => onSelectionChange([])}>Clear</button>
      {/if}
    </div>
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

  <div class="atom-drawer">
    <div class="drawer-heading">
      <div>
        <strong>Atom selection</strong>
        <small>Select viewer indices here when precise atom targeting is required. ECatVASP sends mapped atom UIDs to the backend.</small>
      </div>
      <span>{selectedAtomUids.length}/{atomUids.length}</span>
    </div>
    <div class="atom-grid">
      {#each atomUids as atomUid, index (atomUid)}
        <button
          type="button"
          class:selected={selectedAtomUids.includes(atomUid)}
          aria-pressed={selectedAtomUids.includes(atomUid)}
          title={atomUid}
          onclick={() => toggle(atomUid)}
        >
          <span>{index}</span>
        </button>
      {/each}
    </div>
    <details>
      <summary>Atom identity map</summary>
      <div class="identity-map">
        {#each atomUids as atomUid, index (atomUid)}
          <div class:selected={selectedAtomUids.includes(atomUid)}>
            <span>Atom {index}</span><code>{atomUid}</code>
          </div>
        {/each}
      </div>
    </details>
  </div>
</section>

<style>
  .selector-shell { display: grid; grid-template-rows: minmax(430px,1fr) auto; height: 100%; min-height: 520px; gap: .45rem; }
  .viewer-frame { position: relative; min-height: 430px; overflow: hidden; border: 1px solid var(--border, #dce4e0); border-radius: 9px; background: #f8faf9; }
  .viewer-overlay { position: absolute; z-index: 2; top: .55rem; display: grid; gap: .06rem; padding: .4rem .48rem; border: 1px solid rgb(203 214 209 / 86%); border-radius: 7px; background: rgb(255 255 255 / 88%); box-shadow: 0 5px 16px rgb(24 46 39 / 7%); backdrop-filter: blur(5px); pointer-events: none; }
  .viewer-overlay-left { left: .55rem; }
  .viewer-overlay-right { right: .55rem; grid-template-columns: auto auto; align-items: center; column-gap: .3rem; }
  .viewer-overlay span { color: #697a74; font-size: .46rem; text-transform: uppercase; letter-spacing: .06em; }
  .viewer-overlay strong { color: #20302b; font-size: .58rem; }
  .viewer-overlay-right.active { border-color: #97c7b9; background: rgb(235 248 243 / 92%); }
  .viewer-overlay-right button { grid-column: 1 / -1; margin-top: .15rem; padding: .18rem .28rem; border: 0; border-radius: 4px; color: #0e6656; background: rgb(23 123 104 / 10%); font-size: .44rem; font-weight: 700; cursor: pointer; pointer-events: auto; }
  .atom-drawer { display: grid; gap: .35rem; padding: .45rem .5rem .35rem; border: 1px solid var(--border, #dce4e0); border-radius: 8px; background: var(--surface, #fff); }
  .drawer-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: .8rem; }
  .drawer-heading > div { min-width: 0; }
  .drawer-heading strong, .drawer-heading small { display: block; }
  .drawer-heading strong { font-size: .57rem; }
  .drawer-heading small { max-width: 580px; margin-top: .08rem; color: var(--muted-text, #687570); font-size: .45rem; line-height: 1.35; }
  .drawer-heading > span { color: var(--accent-strong, #0e6656); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .5rem; font-weight: 700; }
  .atom-grid { display: flex; gap: .2rem; overflow-x: auto; overflow-y: hidden; padding-bottom: .12rem; scrollbar-width: thin; }
  .atom-grid button { display: inline-flex; min-width: 27px; height: 25px; align-items: center; justify-content: center; border: 1px solid var(--border, #dce4e0); border-radius: 5px; color: #65756f; background: var(--surface-soft, #f7f9f8); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .48rem; cursor: pointer; }
  .atom-grid button:hover { border-color: #9ab9af; }
  .atom-grid button.selected { border-color: var(--accent, #177b68); color: #fff; background: var(--accent, #177b68); font-weight: 800; }
  details { border-top: 1px solid var(--border, #dce4e0); padding-top: .28rem; }
  summary { color: var(--muted-text, #687570); font-size: .48rem; font-weight: 650; cursor: pointer; }
  .identity-map { display: grid; max-height: 140px; overflow: auto; margin-top: .3rem; border: 1px solid var(--border, #dce4e0); border-radius: 6px; }
  .identity-map > div { display: grid; grid-template-columns: 56px minmax(0,1fr); gap: .35rem; padding: .28rem .35rem; border-bottom: 1px solid var(--border, #dce4e0); font-size: .45rem; }
  .identity-map > div:last-child { border-bottom: 0; }
  .identity-map > div.selected { background: var(--accent-soft, #e7f4ef); }
  code { overflow-wrap: anywhere; color: var(--muted-text, #687570); }
  @media (prefers-color-scheme: dark) {
    .viewer-frame { background: #111816; }
    .viewer-overlay { border-color: #34453f; background: rgb(24 34 31 / 90%); }
    .viewer-overlay span { color: #91a39d; }
    .viewer-overlay strong { color: #e1ebe7; }
    .viewer-overlay-right.active { background: rgb(27 55 47 / 92%); }
  }
</style>
