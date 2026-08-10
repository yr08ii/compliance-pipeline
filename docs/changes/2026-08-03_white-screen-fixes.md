# Dashboard white screen, and three bugs found alongside it

**Reported:** "theres a bug in the site, the website dashboard doesnt show it goes white"

---

## The white screen

Every endpoint returned 200 and the JS bundle imported cleanly, but
`document.getElementById('root').children.length` was `0` — a render crash, not
a network or server fault.

Cause: during feedback02, `/api/alerts` changed from returning a bare array to
returning a paginated `AlertPage` object. `AlertQueue` was updated for it;
`Dashboard` was not. It still ran:

```ts
apiGet<AlertOut[]>("/api/alerts").then(setAlerts)
new Set(alerts.map((a) => a.merchant_id))   // .map is not a function
```

TypeScript could not catch this. The annotation on `apiGet<AlertOut[]>` is an
assertion about a runtime JSON payload, not a check of one — the compiler
believed the lie it was told.

The dashboard now requests `?page=1&page_size=5` and reads `p.items` / `p.total`.

Two figures on the page were wrong independently of the crash, so they changed too:

- **Flagged merchants** counted distinct merchants in the fetched alerts. Once
  the fetch is a five-row page, that is a page count presented as a portfolio
  figure. Removed.
- The merchant tile now reads `baselines.total_count`, which is portfolio-wide.

### Containment

One broken screen should not blank the whole application — to an operator that
looks identical to the server being down, which is the worst possible failure
mode for a monitoring tool.

`lib/ErrorBoundary.tsx` now wraps the route outlet. A failing screen renders its
error inline; the navigation stays usable. The boundary resets on pathname
change, so navigating away clears it rather than holding the failure for the
session.

It lives in a `RoutedContent` component rather than in `App`, because reading
the pathname requires being *inside* the Router that `App` renders.

---

## Model info: every card read "0 reasons check this"

The three framing cards counted detectors by matching `compared_against`
against hardcoded strings — `"Its own history"`, `"Same merchant category"`,
`"Same district"`. The backend glossary says `"Own history"`, `"MCC peer
group"`, `"Subdistrict peer group"`. The wording was changed on one side only,
and the screen silently counted zero rather than failing.

The cards are now **derived from the data**: group the detectors by the head of
their `compared_against` (anything after a comma refines it — *"Own history,
per payment method"* is still own history), then look up the plain-English
question. A frame the lookup does not recognise still renders, showing its own
name. It can no longer count zero.

Now reads 7 / 4 / 2 — all thirteen detectors accounted for.

---

## Case review: headline contradicted itself

The heading named the detector that raised the alert; the line under it came
from `diag.root_cause`. On alert 10141:

> **Amount anomaly vs subdistrict baseline**
> Amount anomaly vs own baseline → 1 transaction(s) outlier vs own baseline

`root_cause` returns the *first* failing detector of the night, which is a
merchant-level summary and frequently a different detector than the one being
reviewed. It is correct for the CLI merchant study, so it is unchanged.

The case-review headline is alert-scoped and now looks up its own detector's
verdict:

> **Amount anomaly vs subdistrict baseline**
> Transaction outlier vs district peers · 30.9σ from subdistrict peer group

---

## Case board showed a raw enum

The reason column rendered `STRUCTURING_CONFIRMED`. The controlled vocabulary
existed only inside `DecisionPanel`, so any screen that read a decision back
showed the internal code.

Moved to `lib/reasons.ts`; the board now shows *"Structuring — amounts split to
stay under thresholds"*. Unknown codes fall back to the code itself, so a
decision recorded before a rename stays legible rather than rendering blank.

Also fixed a keyless fragment in the case table's row map.

---

## Still open

**The reason-code vocabulary is not enforced server-side.** `reason_code` is
validated only as `min_length=2`, so the API will accept any string. The list
of valid codes lives in the frontend. `CASE_STAGES` is already validated against
the backend glossary — reason codes should be too, since they are what the
training loop learns from. Not changed here; it is a schema change, not a bug fix.

Verified: frontend builds, all six routes render, no console errors, 157 backend
tests pass.
