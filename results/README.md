# Results

Versioned snapshots of the analysis report pages. Each file is a self-contained
HTML page with its data embedded — open locally in a browser, no dependencies.
These are the same pages published as Claude artifacts; a snapshot is saved here
every time a report is (re)generated, so any past state can be reopened or
re-published exactly.

## Naming convention

```
{report}_{data_start}_{data_end}_v{N}.html
```

- **report** — `escalation_heatmaps` | `containment_landscape` (add new slugs as
  new report types appear)
- **data_start / data_end** — the ticket date range the embedded data covers,
  `YYYY-MM-DD`, both inclusive (UTC days)
- **vN** — increments each time the same report + range is regenerated with a
  different basis, filter set, or presentation. What changed per version is
  recorded in the index below — a version bump without an index row is meaningless.

Workflow when regenerating a report: re-run `analyze_transcripts.py`, re-embed
into the report HTML, copy it here as the next `v{N}`, add an index row, then
republish the artifact.

## Index

| File | Basis / what changed | Artifact |
|---|---|---|
| `escalation_heatmaps_2026-07-10_2026-08-11_v1.html` | Full window incl. Aug 4; noise filter (≤1 message) on; tag-derived intents; wide single-column layout | [escalation map](https://claude.ai/code/artifact/d9bc683f-d8b5-4c55-afd8-84c3da355067) (label `wide-layout`) |
| `escalation_heatmaps_2026-07-10_2026-08-11_v2.html` | Aug 4 excluded (flagged inaccurate) — current published version | [escalation map](https://claude.ai/code/artifact/d9bc683f-d8b5-4c55-afd8-84c3da355067) (label `aug4-excluded`) |
| `containment_landscape_2026-07-10_2026-08-11_v1.html` | Plain bars: width ∝ conversations, height = containment, fixed 82% target line; Aug 4 excluded | [original landscape](https://claude.ai/code/artifact/e320adfd-fdf8-4481-9fae-97a477a40ed8) |
| `containment_landscape_2026-07-10_2026-08-11_v2.html` | SOP-gap escalation rate stacked as an orange topper per bar; legend + gap columns in table — current published version | [stacked landscape](https://claude.ai/code/artifact/92474b33-b05a-412b-a5ac-13e1745d97bb) |

Not recoverable as files (exist only in the escalation-map artifact's version
history): the initial Aug 8–10 3-day cut and the pre-noise-filter full-window cut.

Shared basis of all current versions: Customer Support Chat Agent
(`24bd649e-6a44-4efd-af97-bfaf4dadfb43`), chat medium, escalation =
`create_ticket` called, SOP-gap = `general_escalation` tag ∨ `is_out_of_scope` ∨
`OutOfDomain` intent (proxy), intents native from Aug 8 / tag-derived before,
conversations with ≤1 message excluded.
