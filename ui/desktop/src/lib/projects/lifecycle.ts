import type { DesktopBackendClient } from "../backend/client";
import type { OpenProjectPayload } from "../backend/contracts";
import type { DesktopPreferencesClient } from "../preferences/client";
import {
  type DesktopPreferences,
  defaultDesktopPreferences,
  withOpenedProject,
  withoutCurrentProject,
  withoutRecentProject,
} from "../preferences/contracts";

export interface ProjectLifecycleSnapshot {
  preferences: DesktopPreferences;
  project: OpenProjectPayload | null;
  restore_error: string | null;
}

export class DesktopProjectLifecycle {
  private preferences = defaultDesktopPreferences();

  constructor(
    private readonly backend: DesktopBackendClient,
    private readonly preferencesClient: DesktopPreferencesClient,
  ) {}

  async restore(): Promise<ProjectLifecycleSnapshot> {
    this.preferences = await this.preferencesClient.load();
    const root = this.preferences.current_project_root;
    if (root === null) {
      return this.snapshot(null, null);
    }

    try {
      const response = await this.backend.openProject(root);
      this.preferences = withOpenedProject(this.preferences, response.payload.project_root);
      await this.preferencesClient.save(this.preferences);
      return this.snapshot(response.payload, null);
    } catch (error: unknown) {
      this.preferences = withoutCurrentProject(this.preferences);
      await this.preferencesClient.save(this.preferences);
      return this.snapshot(null, describeError(error));
    }
  }

  async open(projectRoot: string): Promise<ProjectLifecycleSnapshot> {
    const root = projectRoot.trim();
    if (root.length === 0) {
      throw new Error("project root must not be blank");
    }
    const response = await this.backend.openProject(root);
    this.preferences = withOpenedProject(this.preferences, response.payload.project_root);
    await this.preferencesClient.save(this.preferences);
    return this.snapshot(response.payload, null);
  }

  async close(): Promise<ProjectLifecycleSnapshot> {
    this.preferences = withoutCurrentProject(this.preferences);
    await this.preferencesClient.save(this.preferences);
    return this.snapshot(null, null);
  }

  async forget(projectRoot: string): Promise<ProjectLifecycleSnapshot> {
    this.preferences = withoutRecentProject(this.preferences, projectRoot);
    await this.preferencesClient.save(this.preferences);
    return this.snapshot(null, null);
  }

  currentPreferences(): DesktopPreferences {
    return {
      ...this.preferences,
      recent_project_roots: [...this.preferences.recent_project_roots],
    };
  }

  private snapshot(
    project: OpenProjectPayload | null,
    restoreError: string | null,
  ): ProjectLifecycleSnapshot {
    return {
      preferences: this.currentPreferences(),
      project,
      restore_error: restoreError,
    };
  }
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : "saved project could not be reopened";
}
