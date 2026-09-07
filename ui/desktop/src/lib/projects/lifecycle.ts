import type { DesktopSuccessResponse, OpenProjectPayload } from "../backend/contracts";
import {
  type DesktopPreferences,
  defaultDesktopPreferences,
  withOpenedProject,
  withoutCurrentProject,
  withoutRecentProject,
} from "../preferences/contracts";

export interface ProjectBackendPort {
  openProject(projectRoot: string): Promise<DesktopSuccessResponse<OpenProjectPayload>>;
}

export interface PreferencesPort {
  load(): Promise<DesktopPreferences>;
  save(preferences: DesktopPreferences): Promise<void>;
}

export interface ProjectLifecycleSnapshot {
  preferences: DesktopPreferences;
  project: OpenProjectPayload | null;
  restore_error: string | null;
}

export class DesktopProjectLifecycle {
  private preferences = defaultDesktopPreferences();
  private project: OpenProjectPayload | null = null;

  constructor(
    private readonly backend: ProjectBackendPort,
    private readonly preferencesClient: PreferencesPort,
  ) {}

  async restore(): Promise<ProjectLifecycleSnapshot> {
    this.preferences = await this.preferencesClient.load();
    const root = this.preferences.current_project_root;
    if (root === null) {
      this.project = null;
      return this.snapshot(null);
    }

    try {
      const response = await this.backend.openProject(root);
      this.project = response.payload;
      this.preferences = withOpenedProject(this.preferences, response.payload.project_root);
      await this.preferencesClient.save(this.preferences);
      return this.snapshot(null);
    } catch (error: unknown) {
      this.project = null;
      this.preferences = withoutCurrentProject(this.preferences);
      await this.preferencesClient.save(this.preferences);
      return this.snapshot(describeError(error));
    }
  }

  async open(projectRoot: string): Promise<ProjectLifecycleSnapshot> {
    const root = projectRoot.trim();
    if (root.length === 0) {
      throw new Error("project root must not be blank");
    }
    const response = await this.backend.openProject(root);
    this.project = response.payload;
    this.preferences = withOpenedProject(this.preferences, response.payload.project_root);
    await this.preferencesClient.save(this.preferences);
    return this.snapshot(null);
  }

  async close(): Promise<ProjectLifecycleSnapshot> {
    this.project = null;
    this.preferences = withoutCurrentProject(this.preferences);
    await this.preferencesClient.save(this.preferences);
    return this.snapshot(null);
  }

  async forget(projectRoot: string): Promise<ProjectLifecycleSnapshot> {
    const wasCurrent = this.preferences.current_project_root === projectRoot;
    this.preferences = withoutRecentProject(this.preferences, projectRoot);
    if (wasCurrent) {
      this.project = null;
    }
    await this.preferencesClient.save(this.preferences);
    return this.snapshot(null);
  }

  currentSnapshot(): ProjectLifecycleSnapshot {
    return this.snapshot(null);
  }

  private snapshot(restoreError: string | null): ProjectLifecycleSnapshot {
    return {
      preferences: {
        ...this.preferences,
        recent_project_roots: [...this.preferences.recent_project_roots],
      },
      project: this.project === null ? null : { ...this.project },
      restore_error: restoreError,
    };
  }
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : "saved project could not be reopened";
}
