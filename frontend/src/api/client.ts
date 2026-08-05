import type { components } from "./schema";

export type AlertOut = components["schemas"]["AlertOut"];
export type BaselineOverview = components["schemas"]["BaselineOverview"];
export type BaselineRow = components["schemas"]["BaselineRow"];
export type Glossary = components["schemas"]["Glossary"];
export type Diagnostics = components["schemas"]["Diagnostics"];
export type DetectorVerdict = components["schemas"]["DetectorVerdict"];
export type Ledger = components["schemas"]["Ledger"];
export type LedgerRow = components["schemas"]["LedgerRow"];
export type AlertPage = components["schemas"]["AlertPage"];
export type CasePage = components["schemas"]["CasePage"];
export type CaseSummary = components["schemas"]["CaseSummary"];
export type CaseDetail = components["schemas"]["CaseDetail"];
export type DispositionIn = components["schemas"]["DispositionIn"];
export type RuleSet = components["schemas"]["RuleSet"];
export type RuleTemplateOut = components["schemas"]["RuleTemplateOut"];
export type RuleInstanceIn = components["schemas"]["RuleInstanceIn"];
export type RuleParam = components["schemas"]["RuleParam"];
export type TxnContribution = components["schemas"]["TxnContribution"];

/** POST/PUT helper. Throws with the server's message so a failed decision
 *  surfaces the reason rather than failing silently. */
export async function apiSend<T>(
  path: string,
  body: unknown,
  method: "POST" | "PUT" = "POST"
): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const payload = await resp.json();
      if (payload?.detail) detail = String(payload.detail);
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}
export type GlossaryTerm = components["schemas"]["GlossaryTerm"];

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return (await resp.json()) as T;
}
