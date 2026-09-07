<script lang="ts">
  import { onMount } from "svelte";

  import { DesktopBackendClient } from "./lib/backend/client";
  import type { HealthPayload } from "./lib/backend/contracts";

  const client = new DesktopBackendClient();

  let connectionState: "connecting" | "ready" | "error" = "connecting";
  let health: HealthPayload | null = null;
  let errorMessage = "";

  onMount(() => {
    let disposed = false;

    void client
      .connect()
      .then((response) => {
        if (disposed) return;
        health = response.payload;
        connectionState = "ready";
      })
      .catch((error: unknown) => {
        if (disposed) return;
        errorMessage = error instanceof Error ? error.message : "desktop backend connection failed";
        connectionState = "error";
      });

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
    <div class:ready={connectionState === "ready"} class:error={connectionState === "error"} class="connection-indicator">
      <span aria-hidden="true"></span>
      {connectionState === "connecting" ? "Connecting" : connectionState === "ready" ? "Backend ready" : "Backend unavailable"}
    </div>
  </header>

  <section class="workspace-frame" aria-live="polite">
    <div class="section-heading">
      <div>
        <h2>Desktop runtime</h2>
        <p>The desktop shell is connected through the versioned local Python sidecar contract.</p>
      </div>
    </div>

    {#if connectionState === "connecting"}
      <div class="runtime-state">
        <strong>Establishing health handshake</strong>
        <p>No project state is opened or cached while compatibility is being verified.</p>
      </div>
    {:else if connectionState === "error"}
      <div class="runtime-state runtime-error">
        <strong>Compatibility handshake failed</strong>
        <p>{errorMessage}</p>
      </div>
    {:else if health !== null}
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
    {/if}
  </section>
</main>
