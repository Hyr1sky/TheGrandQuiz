import { apiClient, toApiRequestError } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

export type SettingsView = components["schemas"]["SettingsView"];
export type SettingsPatch = Partial<
  Pick<
    SettingsView["preferences"],
    "question_language" | "difficulty_mode" | "asr_material_hints_enabled"
  >
>;

export async function getSettings(): Promise<SettingsView> {
  const { data, error } = await apiClient.GET("/api/v1/settings");
  if (error !== undefined) throw toApiRequestError(error);
  return data;
}

export async function updateSettings(patch: SettingsPatch): Promise<SettingsView> {
  const { data, error } = await apiClient.PATCH("/api/v1/settings", {
    body: patch,
  });
  if (error !== undefined) throw toApiRequestError(error);
  return data;
}
