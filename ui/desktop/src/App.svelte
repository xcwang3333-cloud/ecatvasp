<script lang="ts">
  import { onMount } from "svelte";

  import ApplicationActionsView from "./lib/actions/ApplicationActionsView.svelte";
  import { DesktopBackendClientV2 } from "./lib/backend/client-v2";
  import type { BackendRuntimeDiagnostics, OpenProjectPayload } from "./lib/backend/contracts";
  import type { DesktopV2HealthPayload } from "./lib/backend/contracts-v2";
  import { CalculationWizardClient } from "./lib/calculations/client";
  import CalculationWorkflowWizardView from "./lib/calculations/CalculationWorkflowWizardView.svelte";
  import { JobCenterClient } from "./lib/jobs/client";
  import JobCenterView from "./lib/jobs/JobCenterView.svelte";
  import ModelStudioView from "./lib/models/ModelStudioView.svelte";
  import { DesktopPreferencesClient } from "./lib/preferences/client";
  import {
    type DesktopPreferences,
    defaultDesktopPreferences,
  } from "./lib/preferences/contracts";
  import {
    DesktopProjectLifecycle,
    type ProjectLifecycleSnapshot,
  } from "./lib/projects/lifecycle";
  import WorkspaceView from "./lib/workspace/WorkspaceView.svelte";
  import {
    type ScientificWorkspace,
    parseScientificWorkspace,
  } from "./lib/workspace/contracts";
  import { LatestWorkspaceLoad } from "./lib/workspace/load_guard";

  type PrimaryTaskSurface = "overview" | "model" | "calculation" | "jobs" | "scientific";

  const client = new DesktopBackendClientV2();
  const calculationClient = new CalculationWizardClient();
  const jobCenterClient = new JobCenterClient();
  const preferencesClient = new DesktopPreferencesClient();
  const workspaceLoads = new LatestWorkspaceLoad();

  const taskLabels: Record<PrimaryTaskSurface, string> = {
    overview: "Project overview",
    model: "Model Studio",
    calculation: "VASP setup",
    jobs: "Jobs & HPC",
    scientific: "Results & analysis",
  };

  let lifecycle: DesktopProjectLifecycle | null = null;
  let connectionState: "connecting" | "ready" | "error" = "connecting";
  let health: DesktopV2HealthPayload | null = null;
  let runtimeDiagnostics: BackendRuntimeDiagnostics | null = null;
  let project: OpenProjectPayload | null = null;
  let preferences: DesktopPreferences = defaultDesktopPreferences();
  let workspace: ScientificWorkspace | null = null;
  let activeTaskSurface: PrimaryTaskSurface = "overview";
  let projectRootInput = "";
  let projectBusy = false;
  let workspaceBusy = false;
  let recoveryBusy = false;
  let connectionError = "";
  let projectError = "";
  let workspaceError = "";

  let createProjectOpen = false;
  let newProjectRoot = "";
  let newProjectName = "";
  let newProjectSlug = "";
  let newProjectDescription = "";

  $: activeTaskLabel = taskLabels[activeTaskSurface];

  function describeError(error: unknown, fallback: string): string {
    return error instanceof Error ? error.message : fallback;
  }

  function applySnapshot(snapshot: ProjectLifecycleSnapshot): void {
    project = snapshot.project;
    preferences = snapshot.preferences;
    if (snapshot.project !== null) projectRootInput = snapshot.project.project_root;
    if (snapshot.restore_error !== null) {
      projectError = "Saved project could not be reopened: " + snapshot.restore_error;
    }
  }

  function resetProjectViews(): void {
    workspaceLoads.invalidate();
    workspace = null;
    workspaceError = "";
    workspaceBusy = false;
    activeTaskSurface = "overview";
  }

  function selectTaskSurface(surface: PrimaryTaskSurface): void {
    activeTaskSurface = surface;
    if (surface === "scientific" && project !== null && workspace === null && !workspaceBusy) {
      void loadWorkspace(project);
    }
  }

  async function refreshDiagnostics(): Promise<void> {
    try {
      runtimeDiagnostics = await client.diagnostics();
    } catch {
      runtimeDiagnostics = null;
    }
  }

  async function loadWorkspace(
    selectedProject: OpenProjectPayload | null,
    preserveCurrent = false,
  ): Promise<void> {
    const loadToken = workspaceLoads.begin();
    if (selectedProject === null) {
      workspace = null;
      workspaceError = "";
      workspaceBusy = false;
      return;
    }

    workspaceBusy = true;
    workspaceError = "";
    if (!preserveCurrent) workspace = null;
    try {
      const response = await client.frontendHandoff(selectedProject.project_root);
      if (!workspaceLoads.isCurrent(loadToken)) return;
      if (response.payload.project_root !== selectedProject.project_root) {
        throw new Error("desktop workspace handoff returned a different project root");
      }
      const parsed = parseScientificWorkspace(response.payload.handoff);
      if (parsed.project.project_id !== selectedProject.project_id) {
        throw new Error("desktop workspace handoff belongs to a different project");
      }
      workspace = parsed;
    } catch (error: unknown) {
      if (!workspaceLoads.isCurrent(loadToken)) return;
      workspaceError = describeError(error, "Scientific workspace could not be loaded");
      await refreshDiagnostics();
    } finally {
      if (workspaceLoads.isCurrent(loadToken)) workspaceBusy = false;
    }
  }

  async function openProject(root: string = projectRootInput): Promise<void> {
    if (lifecycle === null || projectBusy) return;
    projectBusy = true;
    projectError = "";
    try {
      const snapshot = await lifecycle.open(root);
      applySnapshot(snapshot);
      resetProjectViews();
    } catch (error: unknown) {
      projectError = describeError(error, "Project could not be opened");
      await refreshDiagnostics();
    } finally {
      projectBusy = false;
    }
  }

  async function createProject(): Promise<void> {
    if (lifecycle === null || projectBusy) return;
    projectBusy = true;
    projectError = "";
    try {
      await client.createProject({
        project_root: newProjectRoot,
        name: newProjectName,
        slug: newProjectSlug,
        ...(newProjectDescription.trim() ? { description: newProjectDescription } : {}),
      });
      const snapshot = await lifecycle.open(newProjectRoot);
      applySnapshot(snapshot);
      resetProjectViews();
      createProjectOpen = false;
      newProjectRoot = "";
      newProjectName = "";
      newProjectSlug = "";
      newProjectDescription = "";
    } catch (error: unknown) {
      projectError = describeError(error, "Project could not be created");
    } finally {
      projectBusy = false;
    }
  }

  async function closeProject(): Promise<void> {
    if (lifecycle === null || projectBusy) return;
    projectBusy = true;
    projectError = "";
    try {
      const snapshot = await lifecycle.close();
      applySnapshot(snapshot);
      resetProjectViews();
      projectRootInput = "";
    } catch (error: unknown) {
      projectError = describeError(error, "Project could not be closed");
    } finally {
      projectBusy = false;
    }
  }

  async function forgetRecent(root: string): Promise<void> {
    if (lifecycle === null || projectBusy) return;
    projectBusy = true;
    const wasCurrent = project?.project_root === root;
    try {
      const snapshot = await lifecycle.forget(root);
      applySnapshot(snapshot);
      if (wasCurrent) resetProjectViews();
    } catch (error: unknown) {
      projectError = describeError(error, "Recent project could not be removed");
    } finally {
      projectBusy = false;
    }
  }

  function refreshWorkspace(): void {
    if (project === null || workspaceBusy) return;
    void loadWorkspace(project, workspace !== null);
  }

  async function refreshCurrentProjectAfterAction(): Promise<void> {
    if (project === null) return;
    if (workspace !== null || activeTaskSurface === "scientific") {
      await loadWorkspace(project, workspace !== null);
    }
    await refreshDiagnostics();
  }

  async function restoreProjectAfterHandshake(): Promise<void> {
    const projectLifecycle = lifecycle ?? new DesktopProjectLifecycle(client, preferencesClient);
    lifecycle = projectLifecycle;
    projectBusy = true;
    projectError = "";
    try {
      const restored = project === null
        ? await projectLifecycle.restore()
        : await projectLifecycle.open(project.project_root);
      applySnapshot(restored);
      resetProjectViews();
    } catch (error: unknown) {
      projectError =
        "Project could not be reloaded after backend recovery: " +
        describeError(error, "current ProjectStore could not be reopened");
    } finally {
      projectBusy = false;
    }
  }

  async function restartBackend(): Promise<void> {
    if (recoveryBusy) return;
    recoveryBusy = true;
    connectionState = "connecting";
    connectionError = "";
    workspaceLoads.invalidate();
    try {
      const response = await client.restart();
      health = response.payload;
      connectionState = "ready";
      await restoreProjectAfterHandshake();
    } catch (error: unknown) {
      connectionError = describeError(error, "Desktop backend restart failed");
      connectionState = "error";
    } finally {
      await refreshDiagnostics();
      recoveryBusy = false;
    }
  }

  onMount(() => {
    let disposed = false;
    void (async () => {
      try {
        const response = await client.connect();
        if (disposed) return;
        health = response.payload;
        connectionState = "ready";
        const projectLifecycle = new DesktopProjectLifecycle(client, preferencesClient);
        lifecycle = projectLifecycle;
        projectBusy = true;
        try {
          const restored = await projectLifecycle.restore();
          if (!disposed) {
            applySnapshot(restored);
            resetProjectViews();
          }
        } finally {
          if (!disposed) projectBusy = false;
        }
      } catch (error: unknown) {
        if (disposed) return;
        connectionError = describeError(error, "Desktop backend connection failed");
        connectionState = "error";
      } finally {
        if (!disposed) await refreshDiagnostics();
      }
    })();

    return () => {
      disposed = true;
      workspaceLoads.invalidate();
      void client.shutdown().catch(() => undefined);
    };
  });
</script>

<svelte:head><title>ECatVASP</title></svelte:head>

<main class="app-shell">
  <aside class="sidebar">
    <div class="brand-block">
      <div class="brand-mark" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>
      <div>
        <strong>ECatVASP</strong>
        <small>Electrocatalysis workbench</small>
      </div>
    </div>

    {#if project === null}
      <nav class="primary-nav" aria-label="Application navigation">
        <button class="nav-item active" type="button">
          <span class="nav-index">01</span>
          <span><strong>Projects</strong><small>Open or create research</small></span>
        </button>
        <div class="nav-section-label">Research flow</div>
        <div class="nav-placeholder"><span>02</span><p><strong>Build</strong><small>Surface & adsorbate models</small></p></div>
        <div class="nav-placeholder"><span>03</span><p><strong>Prepare</strong><small>VASP workflows</small></p></div>
        <div class="nav-placeholder"><span>04</span><p><strong>Run</strong><small>Local / SSH / Slurm</small></p></div>
        <div class="nav-placeholder"><span>05</span><p><strong>Analyze</strong><small>Results & electrochemistry</small></p></div>
      </nav>
    {:else}
      <nav class="primary-nav" aria-label="Project navigation">
        <button class:active={activeTaskSurface === "overview"} class="nav-item" type="button" onclick={() => selectTaskSurface("overview")}>
          <span class="nav-index">01</span>
          <span><strong>Overview</strong><small>Research pipeline</small></span>
        </button>
        <button class:active={activeTaskSurface === "model"} class="nav-item" type="button" onclick={() => selectTaskSurface("model")}>
          <span class="nav-index">02</span>
          <span><strong>Model Studio</strong><small>Structures & surfaces</small></span>
        </button>
        <button class:active={activeTaskSurface === "calculation"} class="nav-item" type="button" onclick={() => selectTaskSurface("calculation")}>
          <span class="nav-index">03</span>
          <span><strong>VASP Setup</strong><small>Inputs & workflows</small></span>
        </button>
        <button class:active={activeTaskSurface === "jobs"} class="nav-item" type="button" onclick={() => selectTaskSurface("jobs")}>
          <span class="nav-index">04</span>
          <span><strong>Jobs & HPC</strong><small>Submit, monitor, retrieve</small></span>
        </button>
        <button class:active={activeTaskSurface === "scientific"} class="nav-item" type="button" onclick={() => selectTaskSurface("scientific")}>
          <span class="nav-index">05</span>
          <span><strong>Results</strong><small>Analysis & electrochemistry</small></span>
        </button>
      </nav>
    {/if}

    <div class="sidebar-footer">
      {#if project !== null}
        <div class="project-chip">
          <span class="project-chip-label">Current project</span>
          <strong>{project.project_name}</strong>
          <small title={project.project_root}>{project.project_root}</small>
        </div>
      {/if}

      <details class="system-menu">
        <summary>
          <span class:ready={connectionState === "ready"} class:error={connectionState === "error"} class="status-dot"></span>
          <span>
            <strong>{connectionState === "connecting" ? "Starting backend" : connectionState === "ready" ? "Backend ready" : "Backend unavailable"}</strong>
            <small>{health?.backend_version ?? "Scientific runtime"}</small>
          </span>
        </summary>
        <div class="system-menu-body">
          <dl>
            <div><dt>IPC</dt><dd>v2</dd></div>
            <div><dt>Runtime</dt><dd>{runtimeDiagnostics?.backend_state ?? "Unknown"}</dd></div>
          </dl>
          <div class="system-actions">
            <button type="button" onclick={() => void refreshDiagnostics()}>Refresh</button>
            <button type="button" disabled={recoveryBusy} onclick={() => void restartBackend()}>
              {recoveryBusy ? "Restarting…" : "Restart"}
            </button>
          </div>
        </div>
      </details>
    </div>
  </aside>

  <section class="main-stage">
    {#if connectionState === "connecting"}
      <div class="center-state">
        <div class="state-spinner" aria-hidden="true"></div>
        <span class="eyebrow">Scientific runtime</span>
        <h1>Starting ECatVASP</h1>
        <p>Verifying the bundled backend and desktop compatibility contract.</p>
      </div>
    {:else if connectionState === "error"}
      <div class="center-state error-state">
        <span class="eyebrow">Runtime unavailable</span>
        <h1>Backend could not start</h1>
        <p>{connectionError}</p>
        <button class="primary-button" disabled={recoveryBusy} onclick={() => void restartBackend()}>
          {recoveryBusy ? "Restarting…" : "Restart backend"}
        </button>
      </div>
    {:else if project === null}
      <header class="home-header">
        <div>
          <span class="eyebrow">Research projects</span>
          <h1>Start from a catalyst system, not from a file tree.</h1>
          <p>Keep structures, VASP calculations, HPC execution, provenance, and electrocatalytic analysis in one project workspace.</p>
        </div>
        <button class="primary-button" type="button" onclick={() => (createProjectOpen = true)}>New project</button>
      </header>

      <section class="home-grid">
        <div class="start-card">
          <div class="card-heading">
            <span class="step-badge">Open</span>
            <div><h2>Continue a research project</h2><p>Open an existing ECatVASP ProjectStore by its project folder.</p></div>
          </div>
          <form class="path-form" onsubmit={(event) => { event.preventDefault(); void openProject(); }}>
            <label for="project-root">Project folder</label>
            <div class="path-row">
              <input id="project-root" bind:value={projectRootInput} autocomplete="off" disabled={projectBusy} placeholder="C:\Research\ECatVASP\FeNC-ORR" />
              <button type="submit" class="primary-button" disabled={projectBusy || !projectRootInput.trim()}>
                {projectBusy ? "Opening…" : "Open"}
              </button>
            </div>
          </form>
          {#if projectError}<div class="inline-error" role="alert">{projectError}</div>{/if}
        </div>

        <aside class="workflow-preview" aria-label="ECatVASP research workflow">
          <span class="eyebrow">VASP-first research flow</span>
          <h2>From surface model to reaction energetics</h2>
          <ol>
            <li><span>01</span><div><strong>Build</strong><small>Slabs, adsorbates, defects, active sites</small></div></li>
            <li><span>02</span><div><strong>Prepare</strong><small>INCAR, KPOINTS, POTCAR policy, workflow DAG</small></div></li>
            <li><span>03</span><div><strong>Run</strong><small>Local or SSH/Slurm execution and retrieval</small></div></li>
            <li><span>04</span><div><strong>Analyze</strong><small>DOS, Bader, COHP, thermochemistry, CHE</small></div></li>
          </ol>
        </aside>
      </section>

      <section class="recent-section">
        <div class="section-title-row">
          <div><span class="eyebrow">Recent</span><h2>Projects</h2></div>
          <span class="count-pill">{preferences.recent_project_roots.length}</span>
        </div>
        {#if preferences.recent_project_roots.length === 0}
          <div class="recent-empty">
            <strong>No recent projects</strong>
            <p>Create a project to establish a durable ProjectStore and start Model Studio.</p>
          </div>
        {:else}
          <div class="recent-grid">
            {#each preferences.recent_project_roots as root (root)}
              <article class="recent-card">
                <button class="recent-main" type="button" onclick={() => { projectRootInput = root; void openProject(root); }}>
                  <span class="recent-symbol" aria-hidden="true">EC</span>
                  <span><strong>{root.split(/[\\/]/).filter(Boolean).at(-1) ?? "ECatVASP project"}</strong><small title={root}>{root}</small></span>
                </button>
                <button class="recent-remove" type="button" aria-label={"Forget " + root} onclick={() => void forgetRecent(root)}>×</button>
              </article>
            {/each}
          </div>
        {/if}
      </section>
    {:else}
      <header class="workspace-header">
        <div class="workspace-breadcrumb">
          <span class="eyebrow">{project.project_name}</span>
          <h1>{activeTaskLabel}</h1>
        </div>
        <div class="workspace-header-actions">
          <span class="schema-pill">ProjectStore v{project.schema_version}</span>
          <button class="quiet-button" type="button" disabled={projectBusy} onclick={() => void closeProject()}>Close project</button>
        </div>
      </header>

      {#if projectError}<div class="content-error" role="alert">{projectError}</div>{/if}

      {#if activeTaskSurface === "overview"}
        <section class="overview-layout">
          <div class="overview-main">
            <section class="overview-hero">
              <span class="eyebrow">Electrocatalysis pipeline</span>
              <h2>Move the current system through one explicit scientific chain.</h2>
              <p>ECatVASP keeps execution status separate from scientific convergence and keeps provenance attached as the project moves from atomistic models to electrochemical interpretation.</p>
            </section>

            <section class="pipeline-grid" aria-label="Project workflow">
              <button class="pipeline-card" type="button" onclick={() => selectTaskSurface("model")}>
                <span class="pipeline-number">01</span>
                <div><span class="pipeline-kicker">Build</span><h3>Model Studio</h3><p>Import or construct slabs, adsorbates, active sites, and durable structure snapshots.</p></div>
                <span class="pipeline-arrow" aria-hidden="true">→</span>
              </button>
              <button class="pipeline-card" type="button" onclick={() => selectTaskSurface("calculation")}>
                <span class="pipeline-number">02</span>
                <div><span class="pipeline-kicker">Prepare</span><h3>VASP setup</h3><p>Choose a recipe, validate inputs, materialize immutable calculation files, and plan workflows.</p></div>
                <span class="pipeline-arrow" aria-hidden="true">→</span>
              </button>
              <button class="pipeline-card" type="button" onclick={() => selectTaskSurface("jobs")}>
                <span class="pipeline-number">03</span>
                <div><span class="pipeline-kicker">Run</span><h3>Jobs & HPC</h3><p>Submit locally or through SSH/Slurm, monitor scheduler state, recover, and retrieve artifacts.</p></div>
                <span class="pipeline-arrow" aria-hidden="true">→</span>
              </button>
              <button class="pipeline-card" type="button" onclick={() => selectTaskSurface("scientific")}>
                <span class="pipeline-number">04</span>
                <div><span class="pipeline-kicker">Analyze</span><h3>Results & electrochemistry</h3><p>Classify convergence, inspect electronic structure, apply thermochemistry, and evaluate CHE pathways.</p></div>
                <span class="pipeline-arrow" aria-hidden="true">→</span>
              </button>
            </section>
          </div>

          <aside class="project-inspector">
            <div class="inspector-heading">
              <span class="eyebrow">Project identity</span>
              <h2>{project.project_name}</h2>
            </div>
            <dl class="identity-list">
              <div><dt>Schema</dt><dd>v{project.schema_version}</dd></div>
              <div><dt>Project ID</dt><dd title={project.project_id}>{project.project_id}</dd></div>
              <div><dt>Root</dt><dd title={project.project_root}>{project.project_root}</dd></div>
            </dl>
            <div class="research-boundary">
              <strong>Scientific authority</strong>
              <p>Python owns convergence, freshness, identity, and provenance. The desktop renders typed projections and actions.</p>
            </div>
          </aside>
        </section>
      {:else if activeTaskSurface === "model"}
        <section class="workbench-surface">
          {#key project.project_id}
            <ModelStudioView client={client} projectRoot={project.project_root} disabled={projectBusy} onMutation={refreshCurrentProjectAfterAction} />
          {/key}
        </section>
      {:else if activeTaskSurface === "calculation"}
        <section class="workbench-surface">
          {#key project.project_id}
            <CalculationWorkflowWizardView client={calculationClient} projectRoot={project.project_root} disabled={projectBusy} onMutation={refreshCurrentProjectAfterAction} />
          {/key}
        </section>
      {:else if activeTaskSurface === "jobs"}
        <section class="workbench-surface">
          {#key project.project_id}
            <JobCenterView client={jobCenterClient} projectRoot={project.project_root} disabled={projectBusy} onMutation={refreshCurrentProjectAfterAction} />
          {/key}
        </section>
      {:else}
        <section class="workbench-surface" aria-live="polite">
          {#if workspaceBusy && workspace === null}
            <div class="surface-state"><strong>Reading scientific workspace</strong><p>Loading ProjectStore-backed inventory for results and analysis.</p></div>
          {:else if workspaceError}
            <div class="surface-state surface-error"><strong>Scientific workspace unavailable</strong><p>{workspaceError}</p><button class="primary-button" onclick={refreshWorkspace}>Retry</button></div>
          {:else if workspace !== null && health !== null}
            <ApplicationActionsView
              client={client}
              projectRoot={project.project_root}
              projectId={project.project_id}
              workflowRecipes={health.workflow_recipes}
              {workspace}
              disabled={projectBusy || workspaceBusy}
              onMutation={refreshCurrentProjectAfterAction}
            />
            <details class="advanced-workspace">
              <summary>Advanced scientific inventory & provenance</summary>
              <WorkspaceView {workspace} refreshing={workspaceBusy} onRefresh={refreshWorkspace} />
            </details>
          {:else}
            <div class="surface-state"><strong>Scientific workspace not loaded</strong><button class="primary-button" onclick={refreshWorkspace}>Load workspace</button></div>
          {/if}
        </section>
      {/if}
    {/if}
  </section>

  {#if createProjectOpen}
    <div class="modal-layer" role="presentation">
      <section class="project-modal" role="dialog" aria-modal="true" aria-labelledby="new-project-title">
        <header class="modal-header">
          <div><span class="eyebrow">New research workspace</span><h2 id="new-project-title">Create ECatVASP project</h2></div>
          <button class="modal-close" type="button" aria-label="Close" onclick={() => (createProjectOpen = false)}>×</button>
        </header>
        <p class="modal-intro">Create the durable project root first. Structures, calculations, retrieved outputs, analyses, and provenance will be anchored here.</p>
        <form class="project-create-form" onsubmit={(event) => { event.preventDefault(); void createProject(); }}>
          <label>Project folder<input bind:value={newProjectRoot} placeholder="C:\Research\ECatVASP\FeNC-ORR" /></label>
          <div class="form-row">
            <label>Name<input bind:value={newProjectName} placeholder="Fe–N–C ORR project" /></label>
            <label>Slug<input bind:value={newProjectSlug} placeholder="fenc-orr" /></label>
          </div>
          <label>Description<textarea bind:value={newProjectDescription} rows="3" placeholder="Optional research context"></textarea></label>
          {#if projectError}<div class="inline-error" role="alert">{projectError}</div>{/if}
          <footer class="modal-actions">
            <button class="quiet-button" type="button" disabled={projectBusy} onclick={() => (createProjectOpen = false)}>Cancel</button>
            <button class="primary-button" type="submit" disabled={projectBusy || !newProjectRoot.trim() || !newProjectName.trim() || !newProjectSlug.trim()}>
              {projectBusy ? "Creating…" : "Create project"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  {/if}
</main>
