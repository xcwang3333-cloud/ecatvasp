export const DESKTOP_PREFERENCES_CONTRACT_VERSION = "ecatvasp-desktop-preferences-v1" as const;
export const MAX_RECENT_PROJECTS = 8;

export interface DesktopPreferences {
  contract_version: typeof DESKTOP_PREFERENCES_CONTRACT_VERSION;
  current_project_root: string | null;
  recent_project_roots: string[];
}

export function defaultDesktopPreferences(): DesktopPreferences {
  return {
    contract_version: DESKTOP_PREFERENCES_CONTRACT_VERSION,
    current_project_root: null,
    recent_project_roots: [],
  };
}

export function parseDesktopPreferences(value: unknown): DesktopPreferences {
  if (!isRecord(value)) {
    throw new Error("desktop preferences must be an object");
  }
  const allowed = new Set([
    "contract_version",
    "current_project_root",
    "recent_project_roots",
  ]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) {
      throw new Error(`desktop preferences contain unknown field: ${key}`);
    }
  }
  if (value.contract_version !== DESKTOP_PREFERENCES_CONTRACT_VERSION) {
    throw new Error("unsupported desktop preferences contract version");
  }
  if (
    value.current_project_root !== null &&
    (typeof value.current_project_root !== "string" || value.current_project_root.trim().length === 0)
  ) {
    throw new Error("desktop current project root is invalid");
  }
  if (
    !Array.isArray(value.recent_project_roots) ||
    !value.recent_project_roots.every(
      (root) => typeof root === "string" && root.trim().length > 0,
    )
  ) {
    throw new Error("desktop recent project roots are invalid");
  }
  const recent = value.recent_project_roots as string[];
  if (recent.length > MAX_RECENT_PROJECTS || new Set(recent).size !== recent.length) {
    throw new Error("desktop recent project roots violate MRU constraints");
  }
  return {
    contract_version: DESKTOP_PREFERENCES_CONTRACT_VERSION,
    current_project_root: value.current_project_root as string | null,
    recent_project_roots: [...recent],
  };
}

export function withOpenedProject(
  preferences: DesktopPreferences,
  projectRoot: string,
): DesktopPreferences {
  const root = projectRoot.trim();
  if (root.length === 0) {
    throw new Error("project root must not be blank");
  }
  return {
    ...preferences,
    current_project_root: projectRoot,
    recent_project_roots: [
      projectRoot,
      ...preferences.recent_project_roots.filter((candidate) => candidate !== projectRoot),
    ].slice(0, MAX_RECENT_PROJECTS),
  };
}

export function withoutCurrentProject(preferences: DesktopPreferences): DesktopPreferences {
  return {
    ...preferences,
    current_project_root: null,
  };
}

export function withoutRecentProject(
  preferences: DesktopPreferences,
  projectRoot: string,
): DesktopPreferences {
  return {
    ...preferences,
    current_project_root:
      preferences.current_project_root === projectRoot ? null : preferences.current_project_root,
    recent_project_roots: preferences.recent_project_roots.filter(
      (candidate) => candidate !== projectRoot,
    ),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
