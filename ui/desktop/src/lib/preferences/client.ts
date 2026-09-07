import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import {
  type DesktopPreferences,
  defaultDesktopPreferences,
  parseDesktopPreferences,
} from "./contracts";

export type PreferencesInvokeFn = <T>(
  command: string,
  args?: Record<string, unknown>,
) => Promise<T>;

export class DesktopPreferencesClient {
  private readonly invokeFn: PreferencesInvokeFn;

  constructor(invokeFn: PreferencesInvokeFn = tauriInvoke) {
    this.invokeFn = invokeFn;
  }

  async load(): Promise<DesktopPreferences> {
    const raw = await this.invokeFn<unknown>("desktop_preferences_load");
    if (raw === null) {
      return defaultDesktopPreferences();
    }
    return parseDesktopPreferences(raw);
  }

  async save(preferences: DesktopPreferences): Promise<void> {
    const normalized = parseDesktopPreferences(preferences);
    await this.invokeFn<void>("desktop_preferences_save", { preferences: normalized });
  }
}
