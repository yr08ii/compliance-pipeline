import { useEffect, useState } from "react";
import { apiGet, type Glossary, type GlossaryTerm } from "./client";

/** Fetched once per page load and shared: the vocabulary changes only when the
 *  backend is redeployed, so refetching per component would be waste. */
let cached: Promise<Glossary> | null = null;

function load(): Promise<Glossary> {
  if (!cached) cached = apiGet<Glossary>("/api/glossary");
  return cached;
}

export type Lookup = {
  detector: (key: string) => string;
  detectorMeaning: (key: string) => string;
  feature: (key: string) => string;
  lane: (key: string) => string;
  alertType: (key: string) => string;
  alertTypeMeaning: (key: string) => string;
  method: (key: string) => string;
  all: Glossary | null;
};

function index(terms: GlossaryTerm[] | undefined) {
  return new Map((terms ?? []).map((t) => [t.key, t]));
}

export function useGlossary(): Lookup {
  const [g, setG] = useState<Glossary | null>(null);

  useEffect(() => {
    let live = true;
    load().then((v) => live && setG(v)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const detectors = index(g?.detectors);
  const features = index(g?.features);
  const lanes = index(g?.lanes);
  const methods = index(g?.baseline_methods);
  const alertTypes = index(g?.alert_types);

  return {
    // Falling back to the raw key keeps a new detector visible rather than
    // blank, and makes a missing label obvious instead of silent.
    detector: (k) => detectors.get(k)?.label ?? k,
    detectorMeaning: (k) => detectors.get(k)?.meaning ?? "",
    feature: (k) => {
      const hit = features.get(k);
      if (hit) return hit.label;
      // Card-origin features carry the country in the key itself.
      const origin = k.match(/^card_origin_(.+)$/);
      return origin ? `Cards issued in ${origin[1]}` : k;
    },
    lane: (k) => lanes.get(k)?.label ?? k,
    alertType: (k) => alertTypes.get(k)?.label ?? k,
    alertTypeMeaning: (k) => alertTypes.get(k)?.meaning ?? "",
    method: (k) => methods.get(k)?.label ?? k,
    all: g,
  };
}
