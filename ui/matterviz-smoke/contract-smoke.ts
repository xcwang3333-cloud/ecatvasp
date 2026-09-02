import { Structure, type Crystal, type StructureBond } from 'matterviz/structure'
import type { ComponentProps } from 'svelte'

const structure: Crystal = {
  sites: [
    {
      species: [{ element: 'Pb', occu: 1, oxidation_state: 0 }],
      abc: [0.5, 0.5, 0.58],
      xyz: [5, 5, 11.6],
      label: 'Pb',
      properties: { ecatvasp_atom_uid: '018f0000-0000-7000-8000-000000000001' },
    },
    {
      species: [{ element: 'Pb', occu: 1, oxidation_state: 0 }],
      abc: [0.5, 0.5, 0.42],
      xyz: [5, 5, 8.4],
      label: 'Pb',
      properties: { ecatvasp_atom_uid: '018f0000-0000-7000-8000-000000000002' },
    },
    {
      species: [{ element: 'C', occu: 1, oxidation_state: 0 }],
      abc: [0.6, 0.5, 0.68],
      xyz: [6, 5, 13.6],
      label: 'C',
      properties: { ecatvasp_atom_uid: '018f0000-0000-7000-8000-000000000003' },
    },
    {
      species: [{ element: 'O', occu: 1, oxidation_state: 0 }],
      abc: [0.55, 0.5, 0.62],
      xyz: [5.5, 5, 12.4],
      label: 'O',
      properties: { ecatvasp_atom_uid: '018f0000-0000-7000-8000-000000000004' },
    },
  ],
  lattice: {
    matrix: [
      [10, 0, 0],
      [0, 10, 0],
      [0, 0, 20],
    ],
    pbc: [true, true, true],
    volume: 2000,
    a: 10,
    b: 10,
    c: 20,
    alpha: 90,
    beta: 90,
    gamma: 90,
  },
  properties: {
    ecatvasp_contract_version: 'ecatvasp-matterviz-v1',
  },
}

const bindingIntentBonds: StructureBond[] = [
  { site_idx_1: 2, site_idx_2: 0, order: 1 },
  { site_idx_1: 3, site_idx_2: 1, order: 1 },
]

const highlightedSites = [0, 1, 2, 3]

type StructureProps = ComponentProps<typeof Structure>
const props: Pick<
  StructureProps,
  | 'structure'
  | 'bonds'
  | 'highlighted_sites'
  | 'selected_sites'
  | 'cell_type'
  | 'supercell_scaling'
  | 'apply_supercell_scaling'
  | 'show_image_atoms'
> = {
  structure,
  bonds: bindingIntentBonds,
  highlighted_sites: highlightedSites,
  selected_sites: [],
  cell_type: 'original',
  supercell_scaling: '1x1x1',
  apply_supercell_scaling: false,
  show_image_atoms: false,
}

void props
