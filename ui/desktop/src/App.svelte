<script lang="ts">
  import { onMount } from "svelte";

  import { DesktopBackendClient } from "./lib/backend/client";
  import type { HealthPayload, OpenProjectPayload } from "./lib/backend/contracts";
  import { DesktopPreferencesClient } from "./lib/preferences/client";
  import {
    type DesktopPreferences,
    defaultDesktopPreferences,
  } from "./lib/preferences/contracts";
  import {
    DesktopProjectLifecycle,
    type ProjectLifecycleSnapshot,
  } from "./lib/projects/lifecycle";

  const client = new DesktopBackendClient();
  const preferencesClient = new DesktopPreferencesClient();

  let lifecycle: DesktopProjectLifecycle | null = null;
  let connectionState: "connecting" | "ready" | "error" = "connecting";
  let health: HealthPayload | null = null;
  let project: OpenProjectPayload | null = null;
  let preferences: DesktopPreferences = defaultDesktopPreferences();
  let projectRootInput = "";
  let projectBusy = false;
  let connectionError = "";
  let projectError = "";

  function describeError(error: unknown, fallback: string): string {
    return error instanceof Error ? error.message : fallback;
  }

  function applySnapshot(snapshot: ProjectLifecycleSnapshot): void {
    project = snapshot.project;
    preferences = snapshot.preferences;
    if (snapshot.project !== null) {
      projectRootInput = snapshot.project.project_root;
    }
    if (snapshot.restore_error !== null) {
      projectError = `Saved project could not be reopened: ${snapshot.restore_error}`;
    }
  }

  async function openProject(root: string = projectRootInput): Promise<void> {
    if (lifecycle === null || projectBusy) return;
    projectBusy = true;
    projectError = "";
    try {
      applySnapshot(await lifecycle.open(root));
    } catch (error: unknown) {
      projectError = describeError(error, "project could not be opened");
    } finally {
      projectBusy = false;
    }
  }

  async function closeProject(): Promise<void> {
    if (lifecycle === null || projectBusy) return;
    projectBusy = true;
    projectError = "";
    try {
      applySnapshot(await lifecycle.close());
      projectRootInput = "";
    } catch (error: unknown) {
      projectError = describeError(error, "project could not be closed");
    } finally {
      projectBusy = false;
    }
  }

  async function forgetRecent(root: string): Promise<void> {
    if (lifecycle === null || projectBusy) return;
    projectBusy = true;
    projectError = "";
    try {
      applySnapshot(await lifecycle.forget(root));
      if (project === null && projectRootInput === root) {
        projectRootInput = "";
      }
    } catch (error: unknown) {
      projectError = describeError(error, "recent project could not be removed");
    } finally {
      projectBusy = false;
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
          if (!disposed) applySnapshot(restored);
        } catch (error: unknown) {
          if (!disposed) {
            projectError = `Desktop preferences unavailable: ${describeError(
              error,
              "local preferences could not be loaded",
            )}`;
          }
        } finally {
          if (!disposed) projectBusy = false;
        }
      } catch (error: unknown) {
        if (disposed) return;
        connectionError = describeError(error, "desktop backend connection failed");
        connectionState = "error";
      }
    })();

    return () => {
      disposed = true;
      void client.shutdown().catch(() => undefined);
    };
  });
</script>

<svelte:head>
  <title>ECatVASP</title>
</svelte:head>

<main class="app-shell">
  <header class="topbar">
    <div>
      <h1>ECatVASP</h1>
      <p>An Electrocatalysis-oriented VASP Research Workbench</p>
    </div>
    <div
      class:ready={connectionState === "ready"}
      class:error={connectionState === "error"}
      class="connection-indicator"
    >
      <span aria-hidden="true"></span>
      {connectionState === "connecting"
        ? "Connecting"
        : connectionState === "ready"
          ? "Backend ready"
          : "Backend unavailable"}
    </div>
  </header>

  {#if connectionState === "connecting"}
    <section class="workspace-frame" aria-live="polite">
      <div class="runtime-state">
        <strong>Establishing health handshake</strong>
        <p>No project state is opened or cached while compatibility is being verified.</p>
      </div>
    </section>
  {:else if connectionState === "error"}
    <section class="workspace-frame" aria-live="assertive">
      <div class="runtime-state runtime-error">
        <strong>Compatibility handshake failed</strong>
        <p>{connectionError}</p>
      </div>
    </section>
  {:else}
    <section class="workspace-frame project-lifecycle" aria-live="polite">
      <div class="section-heading">
        <div>
          <h2>Project workspace</h2>
          <p>
            Open or switch a ProjectStore by path. Current/recent paths are desktop-local preferences;
            every project open is revalidated by the Python backend.
          </p>
        </div>
      </div>

      <form
        class="project-open-form"
        onsubmit={(event) => {
          event.preventDefault();
          void openProject();
        }}
      >
        <label for="project-root">Project root</label>
        <div class="project-open-row">
          <input
            id="project-root"
            bind:value={projectRootInput}
            autocomplete="off"
            disabled={projectBusy}
            placeholder="C:\\Research\\ECatVASP\\project or /work/ecatvasp/project"
          />
          <button type="submit" class="primary-button" disabled={projectBusy || projectRootInput.trim().length === 0}>
            {projectBusy ? "Working…" : "Open project"}
          </button>
        </div>
      </form>

      {#if projectError.length > 0}
        <div class="project-error" role="alert">{projectError}</div>
      {/if}

      <div class="project-columns">
        <section class="project-panel" aria-labelledby="current-project-heading">
          <div class="panel-heading">
            <div>
              <span class="eyebrow">Current</span>
              <h3 id="current-project-heading">Validated project</h3>
            </div>
            {#if project !== null}
              <button type="button" class="text-button" disabled={projectBusy} onclick={() => void closeProject()}>
                Close
              </button>
            {/if}
          </div>

          {#if project === null}
            <div class="empty-state">
              <strong>No project selected</strong>
              <p>Choose a validated recent path or enter a project root above.</p>
            </div>
          {:else}
            <dl class="project-metadata">
              <div>
                <dt>Name</dt>
                <dd>{project.project_name}</dd>
              </div>
              <div>
                <dt>Project ID</dt>
                <dd>{project.project_id}</dd>
              </div>
              <div>
                <dt>Schema</dt>
                <dd>v{project.schema_version}</dd>
              </div>
              <div class="project-root-row">
                <dt>Root</dt>
                <dd>{project.project_root}</dd>
              </div>
            </dl>
          {/if}
        </section>

        <section class="project-panel" aria-labelledby="recent-projects-heading">
          <div class="panel-heading">
            <div>
              <span class="eyebrow">Local only</span>
              <h3 id="recent-projects-heading">Recent projects</h3>
            </div>
            <span class="recent-count">{preferences.recent_project_roots.length}/8</span>
          </div>

          {#if preferences.recent_project_roots.length === 0}
            <div class="empty-state">
              <strong>No recent projects</strong>
              <p>Validated project paths will appear here after a successful open.</p>
            </div>
          {:else}
            <ul class="recent-list">
              {#each preferences.recent_project_roots as root (root)}
                <li class:active={project?.project_root === root}>
                  <button
                    class="recent-open"
                    type="button"
                    disabled={projectBusy}
                    onclick={() => {
                      projectRootInput = root;
                      void openProject(root);
                    }}
                  >
                    <span>{root}</span>
                    <small>{project?.project_root === root ? "Current" : "Open"}</small>
                  </button>
                  <button
                    class="recent-forget"
                    type="button"
                    aria-label={`Forget ${root}`}
                    disabled={projectBusy}
                    onclick={() => void forgetRecent(root)}
                  >
                    ×
                  </button>
                </li>
              {/each}
            </ul>
          {/if}
        </section>
      </div>
    </section>

    {#if health !== null}
      <section class="workspace-frame runtime-contract">
        <div class="section-heading compact-heading">
          <div>
            <h2>Runtime contract</h2>
            <p>Compatibility details remain independent from project scientific state.</p>
          </div>
        </div>
        <dl class="runtime-grid">
          <div>
            <dt>Backend package</dt>
            <dd>{health.backend_version}</dd>
          </div>
          <div>
            <dt>IPC contract</dt>
            <dd>ecatvasp-desktop-ipc-v1</dd>
          </div>
          <div>
            <dt>Frontend handoff</dt>
            <dd>{health.frontend_handoff_contract_version}</dd>
          </div>
          <div>
            <dt>Project requests</dt>
            <dd>{health.stateless_project_requests ? "Stateless" : "Unsupported"}</dd>
          </div>
        </dl>
      </section>
    {/if}
  {/if}
</main>
