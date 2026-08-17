---
name: exclude-flagged-users
description: Mandatory pre-step for EVERY analysis over Flex chat/transcript data in this repo — escalation rates, containment, intent breakdowns, volumes, any metric or chart. Fetch the current flagged-user list from the shared Google Sheet and exclude those customers' conversations before computing anything. Trigger whenever an analysis, report, chart, or export is produced from raw_transcripts/, ticket_metadata/, or transcripts/ data.
---

# Exclude flagged users from every analysis

Every metric produced from this repo's conversation data must exclude the
flagged customer IDs **before** aggregation. Results computed without the
exclusion are not deliverable.

## Source of truth (never cache long-term, never commit)

The list lives in this Google Sheet and grows over time:

- URL: https://docs.google.com/spreadsheets/d/1t8r_EcNJ9hdhCZqPesTmnw9ADDdDDncRQPEH2b4FRWA/edit?gid=0#gid=0
- Drive file ID: `1t8r_EcNJ9hdhCZqPesTmnw9ADDdDDncRQPEH2b4FRWA`

Fetch it fresh at the start of each analysis session — do not reuse a list
from a previous session. As of 2026-08-17 it held 11,727 unique IDs;
treat the count as changing.

**These IDs are customer PII.** Write them only to a local, gitignored file
(`exclude_user_ids.txt` at the repo root is gitignored for this purpose).
Never commit them, never paste them into deliverables, PRs, or chat tools,
never upload them anywhere.

## Fetching the list

Via the Claude Google Drive connector:

1. `read_file_content` with fileId `1t8r_EcNJ9hdhCZqPesTmnw9ADDdDDncRQPEH2b4FRWA`
   (large result — it will be saved to a tool-results file; read it from disk).
2. The content is a one-column markdown table. Extract IDs with the pattern
   `\|\s*([A-Za-z0-9]{20,40})\s*\|` — the IDs are Firebase-style, 25 or 28
   chars. Non-matching lines are only table separators and blank cells;
   verify matched + separators ≈ total lines.
3. Deduplicate and write one ID per line to `exclude_user_ids.txt`.

## Applying the exclusion

- **`bp_escalation_rates.py`** already supports it:
  `python3 bp_escalation_rates.py --exclude-users exclude_user_ids.txt`
- **New analyses over `raw_transcripts/`**: a conversation's customer ID is in
  `messages[*].variableStore.customer_id` (fallback `external_id`) — the first
  non-null value across messages. Reuse `customer_id()` from
  `bp_escalation_rates.py` rather than re-deriving it. Skip the conversation
  when its ID is in the set.
- Count what you drop and report it.

## Known limitation — disclose it every time

`ticket_metadata/*.jsonl` records (the fallback for days without raw
transcripts) carry **no customer ID**, so the exclusion cannot be applied on
metadata-only days. Any deliverable touching those days must say so
explicitly (see the footnote in `results/escalation_rate_timeseries.html`
for the pattern). If exactness matters, pull raw transcripts for those days
first (`pull_raw_transcripts.py`).

## Deliverable checklist

- [ ] List fetched fresh from the Sheet this session
- [ ] Exclusion applied to every raw-transcript-backed number
- [ ] Deliverable states: "Excludes N flagged users (M conversations removed)"
- [ ] Metadata-fallback days (if any) called out as unfiltered
- [ ] No IDs committed, pasted, or uploaded
