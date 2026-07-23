import type { components } from "./schema";

export type AlertOut = components["schemas"]["AlertOut"];
export type BaselineOverview = components["schemas"]["BaselineOverview"];
export type BaselineRow = components["schemas"]["BaselineRow"];

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return (await resp.json()) as T;
}
