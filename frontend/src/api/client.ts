import type { components } from "./schema";

export type AlertOut = components["schemas"]["AlertOut"];

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return (await resp.json()) as T;
}
