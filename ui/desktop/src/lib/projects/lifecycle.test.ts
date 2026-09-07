import { describe, expect, it } from "vitest";

import type { DesktopSuccessResponse, OpenProjectPayload } from "../backend/contracts";
import { DESKTOP_IPC_CONTRACT_VERSION } from "../backend/contracts";
import {
  DESKTOP_PREFERENCES_CONTRACT_VERSION,
  type DesktopPreferences,
  defaultDesktopPreferences,
} from "../preferences/contracts";
import {
  DesktopProjectLifecycle,
  type PreferencesPort,
  type ProjectBackendPort,
} from "./lifecycle";

function project(root: string, id: string): OpenProjectPayload {
  return {
    project_root: root,
    project_id: id,
    project_name: `Project ${id}`,
    project_slug: id.toLowerCase(),
    schema_version: 3,
  };
}

function success(payload: OpenProjectPayload): DesktopSuccessResponse<OpenProjectPayload> {
  return {
    protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
    request_id: `open-${payload.project_id}`,
    operation: "open_project",
    ok: true,
    payload,
  };
}

class FakeBackend implements ProjectBackendPort {
  readonly calls: string[] = [];
  readonly projects = new Map<string, OpenProjectPayload>();

  async openProject(projectRoot: string): Promise<DesktopSuccessResponse<OpenProjectPayload>> {
    this.calls.push(projectRoot);
    const payload = this.projects.get(projectRoot);
    if (payload === undefined) {
      throw new Error(`project unavailable: ${projectRoot}`);
    }
    return success(payload);
  }
}

class FakePreferences implements PreferencesPort {
  value: DesktopPreferences;
  readonly saves: DesktopPreferences[] = [];
  failNextSave = false;

  constructor(initial: DesktopPreferences = defaultDesktopPreferences()) {
    this.value = {
      ...initial,
      recent_project_roots: [...initial.recent_project_roots],
    };
  }

  async load(): Promise<DesktopPreferences> {
    return {
      ...this.value,
      recent_project_roots: [...this.value.recent_project_roots],
    };
  }

  async save(preferences: DesktopPreferences): Promise<void> {
    if (this.failNextSave) {
      this.failNextSave = false;
      throw new Error("preferences disk unavailable");
    }
    this.value = {
      ...preferences,
      recent_project_roots: [...preferences.recent_project_roots],
    };
    this.saves.push(this.value);
  }
}

describe("desktop project lifecycle", () => {
  it("switches A -> B -> A through fresh backend validation without cross-project leakage", async () => {
    const backend = new FakeBackend();
    backend.projects.set("/project-a", project("/project-a", "A"));
    backend.projects.set("/project-b", project("/project-b", "B"));
    const preferences = new FakePreferences();
    const lifecycle = new DesktopProjectLifecycle(backend, preferences);

    expect((await lifecycle.open("/project-a")).project?.project_id).toBe("A");
    expect((await lifecycle.open("/project-b")).project?.project_id).toBe("B");
    const final = await lifecycle.open("/project-a");

    expect(final.project?.project_id).toBe("A");
    expect(final.preferences.current_project_root).toBe("/project-a");
    expect(final.preferences.recent_project_roots).toEqual(["/project-a", "/project-b"]);
    expect(backend.calls).toEqual(["/project-a", "/project-b", "/project-a"]);
  });

  it("does not persist an invalid project and leaves the previous project selected", async () => {
    const backend = new FakeBackend();
    backend.projects.set("/project-a", project("/project-a", "A"));
    const preferences = new FakePreferences();
    const lifecycle = new DesktopProjectLifecycle(backend, preferences);

    await lifecycle.open("/project-a");
    const saveCount = preferences.saves.length;
    await expect(lifecycle.open("/missing")).rejects.toThrow(/project unavailable/);

    const current = lifecycle.currentSnapshot();
    expect(current.project?.project_id).toBe("A");
    expect(current.preferences.current_project_root).toBe("/project-a");
    expect(current.preferences.recent_project_roots).toEqual(["/project-a"]);
    expect(preferences.saves).toHaveLength(saveCount);
  });

  it("does not half-switch when local preference persistence fails", async () => {
    const backend = new FakeBackend();
    backend.projects.set("/project-a", project("/project-a", "A"));
    backend.projects.set("/project-b", project("/project-b", "B"));
    const preferences = new FakePreferences();
    const lifecycle = new DesktopProjectLifecycle(backend, preferences);

    await lifecycle.open("/project-a");
    preferences.failNextSave = true;
    await expect(lifecycle.open("/project-b")).rejects.toThrow(/preferences disk unavailable/);

    const current = lifecycle.currentSnapshot();
    expect(current.project?.project_id).toBe("A");
    expect(current.preferences.current_project_root).toBe("/project-a");
    expect(current.preferences.recent_project_roots).toEqual(["/project-a"]);
    expect(preferences.value.current_project_root).toBe("/project-a");
    expect(backend.calls).toEqual(["/project-a", "/project-b"]);
  });

  it("clears a missing saved current project on restore while preserving recents", async () => {
    const backend = new FakeBackend();
    const preferences = new FakePreferences({
      contract_version: DESKTOP_PREFERENCES_CONTRACT_VERSION,
      current_project_root: "/missing",
      recent_project_roots: ["/missing", "/project-b"],
    });
    const lifecycle = new DesktopProjectLifecycle(backend, preferences);

    const restored = await lifecycle.restore();

    expect(restored.project).toBeNull();
    expect(restored.restore_error).toMatch(/project unavailable/);
    expect(restored.preferences.current_project_root).toBeNull();
    expect(restored.preferences.recent_project_roots).toEqual(["/missing", "/project-b"]);
  });

  it("revalidates the saved project after a desktop/backend restart", async () => {
    const preferences = new FakePreferences();
    const firstBackend = new FakeBackend();
    firstBackend.projects.set("/project-a", project("/project-a", "A"));
    const firstLifecycle = new DesktopProjectLifecycle(firstBackend, preferences);
    await firstLifecycle.open("/project-a");

    const restartedBackend = new FakeBackend();
    restartedBackend.projects.set("/project-a", project("/project-a", "A-restarted"));
    const restartedLifecycle = new DesktopProjectLifecycle(restartedBackend, preferences);
    const restored = await restartedLifecycle.restore();

    expect(restartedBackend.calls).toEqual(["/project-a"]);
    expect(restored.project?.project_id).toBe("A-restarted");
    expect(restored.preferences.current_project_root).toBe("/project-a");
  });
});
