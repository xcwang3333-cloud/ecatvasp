# ECatVASP Workbench UI Rearchitecture

## Goal

The desktop should behave like a scientific workbench rather than a collection of forms. The primary navigation is therefore organized by research intent:

1. **Overview** — project identity and the end-to-end research pipeline.
2. **Model Studio** — slabs, adsorbates, defects, active sites, and structure snapshots.
3. **VASP Setup** — input validation, calculation recipes, and workflow preparation.
4. **Jobs & HPC** — local/SSH/Slurm execution, monitoring, recovery, and retrieval.
5. **Results** — convergence, electronic analysis, thermochemistry, and electrocatalytic reaction analysis.

The Python backend remains the scientific authority. This change only restructures presentation and navigation.

## Open-source UI references

The redesign borrows interaction patterns, not code, from several established open-source materials-science tools:

- **AiiDAlab / aiidalab-qe** — task-oriented workflow progression from structure selection through calculation setup, submission, monitoring, and results.
- **NOMAD** — persistent application navigation, project/data context, dense scientific workspaces, and overview/detail separation.
- **Materials Project / Crystal Toolkit** — modular scientific panels, structure-first exploration, linked visualization and property views.
- **VASP-GUI** — simple left-side task navigation around VASP input preparation.

ECatVASP differs from these references by making the electrocatalysis pipeline first-class: catalyst model → VASP workflow → HPC execution → retrieved result → electronic/thermochemical analysis → CHE/reaction pathway.

## UX rules

- A user opening the application should immediately understand what a project is and what the next scientific action is.
- Project creation is a focused modal instead of expanding the whole home page.
- Opening an existing ProjectStore remains path-based until a native folder-picker is added through a reviewed Tauri capability.
- Backend state is visible but subordinate to scientific work. Detailed runtime diagnostics live in the sidebar system menu.
- A restored project lands on an overview rather than forcing Model Studio to open.
- Heavy scientific catalogs remain lazy-loaded. Entering Results triggers the existing typed workspace handoff.
- No scientific inference, convergence classification, freshness decision, or provenance logic is moved into Svelte.

## Follow-up slices

The shell refactor should be followed by component-level work rather than another monolithic rewrite:

1. Model Studio: split structure inventory, 3D viewer, and construction tools into a stable three-pane layout.
2. VASP Setup: convert the current wizard into a visible stepper with preflight status and an input-file preview.
3. Jobs & HPC: add a queue table with filterable states and a job detail drawer with scheduler/log/retrieval tabs.
4. Results: replace the nested launcher pattern with stable analysis tabs: Results, Electronic, Thermochemistry, Reaction Pathway.
5. Native desktop ergonomics: folder picker, keyboard shortcuts, window-state persistence, and hidden sidecar console on Windows.

## Non-goals

This refactor does not change IPC v2, ProjectStore schema, workflow semantics, execution semantics, scientific parsers, thermochemistry, or electrocatalysis models.
