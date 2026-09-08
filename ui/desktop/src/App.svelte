<script lang="ts">
  import { onMount } from "svelte";

  import ApplicationActionsView from "./lib/actions/ApplicationActionsView.svelte";
  import { DesktopBackendClientV2 } from "./lib/backend/client-v2";
  import type { BackendRuntimeDiagnostics, OpenProjectPayload } from "./lib/backend/contracts";
  import type { DesktopV2HealthPayload } from "./lib/backend/contracts-v2";
  import { CalculationWizardClient } from "./lib/calculations/client";
  import CalculationWorkflowWizardView from "./lib/calculations/CalculationWorkflowWizardView.svelte";
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

  const client = new DesktopBackendClientV2();
  const calculationClient = new CalculationWizardClient();
  const preferencesClient = new DesktopPreferencesClient();
  const workspaceLoads = new LatestWorkspaceLoad();

  let lifecycle: DesktopProjectLifecycle | null = null;
  let connectionState: "connecting" | "ready" | "error" = "connecting";
  let health: DesktopV2HealthPayload | null = null;
  let runtimeDiagnostics: BackendRuntimeDiagnostics | null = null;
  let project: OpenProjectPayload | null = null;
  let preferences: DesktopPreferences = defaultDesktopPreferences();
  let workspace: ScientificWorkspace | null = null;
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

  function describeError(error: unknown, fallback: string): string {
    return error instanceof Error ? error.message : fallback;
  }

  function applySnapshot(snapshot: ProjectLifecycleSnapshot): void {
    project = snapshot.project;
    preferences = snapshot.preferences;
    if (snapshot.project !== null) projectRootInput = snapshot.project.project_root;
    if (snapshot.restore_error !== null) {
      projectError = `Saved project could not be reopened: ${snapshot.restore_error}`;
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
      await loadWorkspace(snapshot.project);
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
      await loadWorkspace(snapshot.project);
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
      await loadWorkspace(null);
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
      if (wasCurrent) await loadWorkspace(snapshot.project);
    } catch (error: unknown) {
      projectError = describeError(error, "Recent project could not be removed");
    } finally {
      projectBusy = false;
    }
  }

  function refreshWorkspace(): void {
    if (project === null || workspaceBusy) return;
    void loadWorkspace(project);
  }

  async function refreshCurrentProjectAfterAction(): Promise<void> {
    if (project === null) return;
    await loadWorkspace(project, true);
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
      await loadWorkspace(restored.project);
    } catch (error: unknown) {
      projectError = `Project could not be reloaded after backend recovery: ${describeError(
        error,
        "current ProjectStore could not be reopened",
      )}`;
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
            await loadWorkspace(restored.project);
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
  <header class="topbar">
    <div>
      <h1>ECatVASP</h1>
      <p>An Electrocatalysis-oriented VASP Research Workbench</p>
    </div>
    <div class:ready={connectionState === "ready"} class:error={connectionState === "error"} class="connection-indicator">
      <span aria-hidden="true"></span>
      {connectionState === "connecting" ? "Connecting" : connectionState === "ready" ? "Backend ready" : "Backend unavailable"}
    </div>
  </header>

  {#if connectionState === "connecting"}
    <section class="workspace-frame"><div class="runtime-state"><strong>Starting scientific backend</strong><p>Compatibility is verified before project state is opened.</p></div></section>
  {:else if connectionState === "error"}
    <section class="workspace-frame"><div class="runtime-state runtime-error"><strong>Backend unavailable</strong><p>{connectionError}</p><button class="primary-button" disabled={recoveryBusy} onclick={() => void restartBackend()}>{recoveryBusy ? "Restarting…" : "Restart backend"}</button></div></section>
  {:else}
    <section class="workspace-frame project-lifecycle">
      <div class="section-heading">
        <div><h2>Projects</h2><p>Create a new research project or open an existing ProjectStore.</p></div>
        <button class="primary-button" type="button" onclick={() => (createProjectOpen = !createProjectOpen)}>{createProjectOpen ? "Cancel new project" : "New project"}</button>
      </div>

      {#if createProjectOpen}
        <form class="project-create-form" onsubmit={(event) => { event.preventDefault(); void createProject(); }}>
          <label>Project folder<input bind:value={newProjectRoot} placeholder="C:\\Research\\ECatVASP\\FeNC-ORR" /></label>
          <label>Name<input bind:value={newProjectName} placeholder="Fe–N–C ORR project" /></label>
          <label>Slug<input bind:value={newProjectSlug} placeholder="fenc-orr" /></label>
          <label>Description<input bind:value={newProjectDescription} placeholder="Optional" /></label>
          <button class="primary-button" type="submit" disabled={projectBusy || !newProjectRoot.trim() || !newProjectName.trim() || !newProjectSlug.trim()}>{projectBusy ? "Creating…" : "Create project"}</button>
        </form>
      {/if}

      <form class="project-open-form" onsubmit={(event) => { event.preventDefault(); void openProject(); }}>
        <label for="project-root">Open project folder</label>
        <div class="project-open-row">
          <input id="project-root" bind:value={projectRootInput} autocomplete="off" disabled={projectBusy} placeholder="Project folder path" />
          <button type="submit" class="primary-button" disabled={projectBusy || !projectRootInput.trim()}>Open project</button>
        </div>
      </form>
      {#if projectError}<div class="project-error" role="alert">{projectError}</div>{/if}

      <div class="project-columns">
        <section class="project-panel">
          <div class="panel-heading"><div><span class="eyebrow">Current</span><h3>Research project</h3></div>{#if project}<button class="text-button" onclick={() => void closeProject()}>Close</button>{/if}</div>
          {#if project === null}
            <div class="empty-state"><strong>No project selected</strong><p>Create or open a project to enter Model Studio.</p></div>
          {:else}
            <dl class="project-metadata">
              <div><dt>Name</dt><dd>{project.project_name}</dd></div>
              <div><dt>Schema</dt><dd>v{project.schema_version}</dd></div>
              <div class="project-root-row"><dt>Root</dt><dd>{project.project_root}</dd></div>
            </dl>
            <details><summary>Advanced project identity</summary><code>{project.project_id}</code></details>
          {/if}
        </section>
        <section class="project-panel">
          <div class="panel-heading"><div><span class="eyebrow">Recent</span><h3>Recent projects</h3></div></div>
          {#if preferences.recent_project_roots.length === 0}<div class="empty-state">No recent projects</div>{:else}
            <ul class="recent-list">
              {#each preferences.recent_project_roots as root (root)}
                <li class:active={project?.project_root === root}>
                  <button class="recent-open" onclick={() => { projectRootInput = root; void openProject(root); }}><span>{root}</span><small>{project?.project_root === root ? "Current" : "Open"}</small></button>
                  <button class="recent-forget" aria-label={`Forget ${root}`} onclick={() => void forgetRecent(root)}>×</button>
                </li>
              {/each}
            </ul>
          {/if}
        </section>
      </div>
    </section>

    {#if project !== null}
      <section class="workspace-frame">
        {#key project.project_id}
          <ModelStudioView client={client} projectRoot={project.project_root} disabled={projectBusy} onMutation={refreshCurrentProjectAfterAction} />
        {/key}
      </section>
      <section class="workspace-frame">
        {#key project.project_id}
          <CalculationWorkflowWizardView client={calculationClient} projectRoot={project.project_root} disabled={projectBusy || workspaceBusy} onMutation={refreshCurrentProjectAfterAction} />
        {/key}
      </section>
    {/if}

    {#if project !== null}
      <section class="workspace-frame" aria-live="polite">
        {#if workspaceBusy && workspace === null}
          <div class="runtime-state"><strong>Reading project state</strong></div>
        {:else if workspaceError}
          <div class="runtime-state runtime-error"><strong>Scientific workspace unavailable</strong><p>{workspaceError}</p><button class="primary-button" onclick={refreshWorkspace}>Retry</button></div>
        {:else if workspace !== null}
          <details class="advanced-workspace"><summary>Advanced scientific inventory & provenance</summary><WorkspaceView {workspace} refreshing={workspaceBusy} onRefresh={refreshWorkspace} /></details>
        {/if}
      </section>
    {/if}

    {#if project !== null && workspace !== null && health !== null}
      <section class="workspace-frame">
        <ApplicationActionsView
          client={client}
          projectRoot={project.project_root}
          projectId={project.project_id}
          workflowRecipes={health.workflow_recipes}
          {workspace}
          disabled={projectBusy || workspaceBusy}
          onMutation={refreshCurrentProjectAfterAction}
        />
      </section>
    {/if}

    {#if health !== null}
      <section class="workspace-frame runtime-contract">
        <details>
          <summary>Runtime & compatibility diagnostics</summary>
          <div class="section-heading compact-heading"><div><h2>Runtime contract</h2></div><div><button class="text-button" onclick={() => void refreshDiagnostics()}>Refresh</button><button class="primary-button" disabled={recoveryBusy} onclick={() => void restartBackend()}>{recoveryBusy ? "Restarting…" : "Restart backend"}</button></div></div>
          <dl class="runtime-grid">
            <div><dt>Backend</dt><dd>{health.backend_version}</dd></div>
            <div><dt>Production IPC</dt><dd>ecatvasp-desktop-ipc-v2</dd></div>
            <div><dt>Compatibility</dt><dd>{health.supported_protocol_versions.join(" · ")}</dd></div>
            <div><dt>Runtime</dt><dd>{runtimeDiagnostics?.backend_state ?? "Unavailable"}</dd></div>
          </dl>
        </details>
      </section>
    {/if}
  {/if}
</main>
