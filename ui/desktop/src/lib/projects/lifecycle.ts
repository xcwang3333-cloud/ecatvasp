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
    const loaded = await this.preferencesClient.load();
    const root = loaded.current_project_root;
    if (root === null) {
      this.preferences = loaded;
      this.project = null;
      return this.snapshot(null);
    }

    let response: DesktopSuccessResponse<OpenProjectPayload>;
    try {
      response = await this.backend.openProject(root);
    } catch (error: unknown) {
      const next = withoutCurrentProject(loaded);
      await this.preferencesClient.save(next);
      this.preferences = next;
      this.project = null;
      return this.snapshot(describeError(error));
    }

    const next = withOpenedProject(loaded, response.payload.project_root);
    await this.preferencesClient.save(next);
    this.preferences = next;
    this.project = response.payload;
    return this.snapshot(null);
  }

  async open(projectRoot: string): Promise<ProjectLifecycleSnapshot> {
    const root = projectRoot.trim();
    if (root.length === 0) {
      throw new Error("project root must not be blank");
    }
    const response = await this.backend.openProject(root);
    const next = withOpenedProject(this.preferences, response.payload.project_root);
    await this.preferencesClient.save(next);
    this.preferences = next;
    this.project = response.payload;
    return this.snapshot(null);
  }

  async close(): Promise<ProjectLifecycleSnapshot> {
    const next = withoutCurrentProject(this.preferences);
    await this.preferencesClient.save(next);
    this.preferences = next;
    this.project = null;
    return this.snapshot(null);
  }

  async forget(projectRoot: string): Promise<ProjectLifecycleSnapshot> {
    const wasCurrent = this.preferences.current_project_root === projectRoot;
    const next = withoutRecentProject(this.preferences, projectRoot);
    await this.preferencesClient.save(next);
    this.preferences = next;
    if (wasCurrent) {
      this.project = null;
    }
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
