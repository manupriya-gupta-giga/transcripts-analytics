# Skills — pulling agent transcripts with the `giga` CLI

Operational notes for [pull_agent_transcripts.py](pull_agent_transcripts.py). Written from a
real export run (Customer Support Chat Agent, 2026-07-10 → 2026-08-12, ~84k chat
tickets). Most of the time lost on that run went to two things that are not
obvious from `--help`: **org scoping** and **rate limits**. Read those sections first.

---

## 1. Environment setup

The SDK requires **Python 3.12+**. macOS system Python is 3.9, so the script will not
run under `/usr/bin/python3`.

```bash
/opt/homebrew/bin/python3.12 -m venv /Users/manupriya/trillions/giga-sdk/venv
/Users/manupriya/trillions/giga-sdk/venv/bin/pip install -e /Users/manupriya/trillions/giga-sdk openpyxl
```

Then put that venv on `PATH` for every run — the script shells out to a bare `giga`:

```bash
export PATH="/Users/manupriya/trillions/giga-sdk/venv/bin:$PATH"
```

`openpyxl` is only needed for `.xlsx` output; without it the script silently falls back
to two CSVs.

Verify: `giga --version` → `giga 1.5.0`.

---

## 2. Org and team scoping — the big gotcha

**The CLI resolves team/org from `giga.config.json` in the current working directory.**
With no config file present, it falls back to a "config-free" team stored at login.

This bites because **more than one org can have a team named "Operations."** A bare
`giga login` from a directory with no config landed in the wrong org entirely — one that
also had an "Operations" team, with its own agents and its own (stale) tickets. Nothing
errored. `giga whoami` looked correct. The data was just quietly the wrong tenant:

| | Wrong org (config-free default) | Flex (correct) |
|---|---|---|
| org | `org_01KWMFZG622W8NC6A5WNYE9B1Q` | `org_01KWMFYTDJENE6JFP52QQ8T43Z` |
| team | `d7c901f8-…` "Operations" | `a3d64350-0d6a-4fcd-9abc-d5795601ffc7` "Operations" |
| chat tickets | 98, ending 2026-07-01 | 130,397, live to today |

**Symptom that this has happened to you:** `giga teams list` shows only one team, the
agent you want does not exist, and ticket counts are implausibly small or the dates stop
months ago. It does *not* produce an error.

### The fix

Always run from inside a clone of the operations repo, which carries the right
`giga.config.json`:

```bash
gh repo clone Giga-customers/flex-operations
cd flex-operations
giga whoami          # confirm org_01KWMFYTDJENE6JFP52QQ8T43Z
```

If `whoami` shows the right *team* but commands fail with
`Error: Access denied. Check your team permissions.`, the team ID came from the config
but the **credentials still carry the old org**. Re-authenticate from inside the repo:

```bash
cd flex-operations
printf 'y\n' | giga login    # 'y' answers the "Log in again? [y/N]" prompt
```

Confirm the browser URL contains `organization_id=org_01KWMFYTDJENE6JFP52QQ8T43Z`.

Notes:
- `giga login` has **no `--force` flag**. If already authenticated it prompts
  `Log in again? [y/N]` and defaults to **No** — so a non-interactive/backgrounded
  `giga login` will silently abort with exit 1. Pipe `y` in.
- `giga set-team <name>` only reaches teams your *current* org grants you. It cannot
  cross orgs; you must re-login. Switching orgs is a login operation, not a team operation.
- Do **not** run `giga logout` inside Scout — the sandbox is pre-authenticated.

---

## 3. Finding the agent

`giga agent` has no `list` subcommand (only `giga agent versions`). Two ways to get IDs:

**From the repo** — `agents/*/agent.yaml` has the display `name` that ticket data uses:

```bash
grep -h '^name:' flex-operations/agents/*/agent.yaml
```

Watch for near-duplicates. `flex-operations` has both `customer-support-chat-agent`
("Customer Support Chat Agent") and `cs-chat-agent-v2` ("CS Chat Agent v2") — different
agents, different templates.

**From ticket data** — the yaml does *not* contain the template UUID, so sample real
tickets to get it:

```bash
giga tickets download --medium chat -r today --output-dir /tmp/probe --format json --limit 50
python3 -c '
import json,glob,collections
c=collections.Counter()
for p in glob.glob("/tmp/probe/**/*.json",recursive=True):
    t=json.load(open(p)); c[(t.get("agentName"),t.get("agentTemplateId"))]+=1
print(*c.most_common(),sep="\n")'
```

Known: **Customer Support Chat Agent** = `24bd649e-6a44-4efd-af97-bfaf4dadfb43`.

Prefer `--agent-template-id` over `--agent-name`: it covers every version of the agent
and avoids ambiguity between similarly-named agents. Both returned the same set here
(84,115 vs 84,111 — the drift is just tickets created between the two queries).

---

## 4. CLI constraints that will bite you

From `giga tickets download --help`, none of which the wrapper script surfaces:

| Flag | Default | Limit | Why it matters |
|---|---|---|---|
| `--limit` | 10,000 | **hard cap 50,000** | The script defaults to `--limit 10000`. On an 84k-ticket window that silently truncates to the newest 10k with **no warning** — you get a plausible-looking file covering a few days. Values above 50,000 are rejected. |
| `--batch-size` | 100 | max 500 | At the default, 84k tickets means ~840 requests fired back-to-back. Always pass `--batch-size 500`. |
| `--resume` | on | — | Skips conversations already on disk for that format. Makes retries and chunking cheap — always download into the *same* directory. |
| `--no-limit` | — | — | Overrides the soft limit up to the 50k hard cap. |

**Any window over 50,000 tickets cannot be pulled in one call and must be chunked by time.**

---

## 5. Rate limits and chunking

Analytics rate-limits at **100 req/min per org**, shared across *all* `giga` commands and
*all* users in the org (documented in `docs/kpi-improvement-items.md`, not in the tickets
docs). Exceeding it returns:

```
Error: API error (HTTP 429): Too many requests, please try again later
```

The CLI does **not** retry on 429 — it exits non-zero and the wrapper script aborts.

Observed empirically on the 84k run: ~160 conversations/sec sustained for the first
~13,000, then a hard 429 that persisted through **4+ minutes** of exponential backoff
(30s → 60s → 120s → 240s). Backing off is necessary but not always sufficient; the
limiter stays closed longer than a naive retry expects. Budget for this, and prefer
resuming a partial download over restarting.

Working approach — weekly slices into one `--resume` directory, backoff on 429, then
flatten once. A reference implementation lives in
`scratchpad/chunked_export.py` from the original run; the shape is:

```
for each weekly window:
    giga tickets download ... --output-dir <SHARED_DIR> --limit 50000 --batch-size 500 --resume
    on 429: sleep 30 * 2^attempt, retry (up to 5)
    sleep 10 between windows
then: import flatten/write_xlsx from pull_agent_transcripts.py, write one workbook
```

Reusing the script's own `flatten()` and `write_xlsx()` keeps the output identical to a
normal run. If a chunk exhausts its retries, re-running the driver is cheap — `--resume`
re-fetches only what is missing.

Be aware that **probe downloads count against the same budget.** Sampling a few hundred
tickets to find template IDs, then immediately launching a large pull, can 429 the real run.

---

## 6. Output

The script writes two tables:

- **Tickets** — one row per conversation: timestamps, medium, status, agent name/version/IDs,
  numbers, duration, plus analysis fields (intent, summary, resolution, sentiment, CSAT, tags)
- **Messages** — one row per turn: index, timestamp, role, content, tool calls

Things to know:

- **Excel cell limit.** Message content over 32,000 chars is truncated (`EXCEL_CELL_LIMIT`).
  Actual Excel limit is 32,767; the script keeps headroom.
- **Excel row limit.** Sheets cap at 1,048,576 rows. At ~7.5 messages/ticket, ~84k tickets
  ≈ 630k message rows — fits, but a larger window will not. The script does **not** check
  this; openpyxl will raise or drop rows. Use `.csv` output for anything materially bigger.
- **Active tickets** appear mid-conversation if the window includes right now.
- **Disk.** Raw JSON averages ~66 KB/conversation → ~5.5 GB for 84k. The script deletes it
  unless `--keep-json` is passed. Redirect with `TMPDIR=...` to control where it lands.
- Output lands in the repo root and is untracked. Consider a `.gitignore` entry for
  `*.xlsx` / `*.csv` if exports become routine.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no tickets matched` | Wrong org (see §2), or window genuinely empty | `giga whoami`; sanity-check with `giga tickets list -r all --limit 5` |
| Agent "doesn't exist" | Wrong org | Re-login from inside `flex-operations` (§2) |
| `Access denied. Check your team permissions.` | Team from config, credentials from old org | `printf 'y\n' \| giga login` inside the repo |
| `giga login` exits 1 immediately | Interactive `[y/N]` prompt, defaulted to No | Pipe `y` in |
| `HTTP 429` | 100 req/min/org exceeded | Chunk by time, `--batch-size 500`, backoff (§5) |
| Export finishes suspiciously fast / too few rows | `--limit` default of 10,000 | Pass an explicit `--limit` (≤50,000) and chunk |
| `Team not found: <name>` | Team belongs to another org | Re-login to that org; `set-team` cannot cross orgs |
| `ModuleNotFoundError: openpyxl` / CSVs instead of xlsx | Wrong interpreter or missing dep | Use the venv Python (§1) |

Useful sanity check before any long run — confirms scope and gives the real count:

```bash
giga tickets list --medium chat \
  -q 'agent_template_id:24bd649e-6a44-4efd-af97-bfaf4dadfb43' \
  --timestamp-start 2026-07-10T00:00:00Z --timestamp-end 2026-08-12T23:59:59Z --limit 1
```

The header reads `Tickets (chat) — page 1/84115, 84115 total`. If that number exceeds
50,000, you must chunk.

---

## 8. Lucene query notes

`-q` / `--lucene-query` is ANDed with `--medium`. Full reference:
`flex-operations/docs/lucene-query-reference.md`. Key points:

- **Field names are snake_case** — `agent_name`, `agent_template_id`, `created_at`
  (not the camelCase used in the JSON output).
- **Bare string values do substring matching** — `agent_name:support` becomes
  `ILIKE '%support%'`. Quote for exact-ish matching: `agent_name:"Customer Support Chat Agent"`.
  This is why the script quotes names but not UUIDs.
- **IDs are bare UUIDs** — no `agent_` / `agent_template_` prefix. The script's
  `build_lucene_query()` strips a pasted Django-style prefix for you.
- **Custom fields use `cf.`** — `cf.is_vip:true`. Discover them with `giga fields list`.
- **Enum tokens are case/underscore insensitive** — `status:user_ended` ≡ `status:USER_ENDED`.
  Unlisted tokens fail with `Invalid numeric value`.

---

## 9. Reference links

**Repos**
- [Giga-customers/flex-operations](https://github.com/Giga-customers/flex-operations) — Flex / Operations agents + the authoritative `giga.config.json`
- [Giga-customers/giga-sdk](https://github.com/Giga-customers/giga-sdk) — the CLI; local checkout at `/Users/manupriya/trillions/giga-sdk`

**Docs in the `flex-operations` clone** (most useful, and more current than the hosted docs)
- `docs/cli.md` — CLI index
- `docs/cli-tickets.md` — `tickets list` / `download` / `show`
- `docs/cli-auth.md` — login, logout, org selection
- `docs/lucene-query-reference.md` — authoritative field list
- `docs/kpi-improvement-items.md` — where the 100 req/min rate limit is documented
- `docs/conversation-lifecycle.md` — ticket status semantics
- `CLI_LOCAL_DEV.md` (in `giga-sdk`) — running the CLI from a local checkout

**External**
- [docs.giga.ai](https://docs.giga.ai)
- [openpyxl docs](https://openpyxl.readthedocs.io/) — cell/row limits

---

## 10. Known-good command

```bash
export PATH="/Users/manupriya/trillions/giga-sdk/venv/bin:$PATH"
cd /path/to/flex-operations          # for giga.config.json

python3 /Users/manupriya/Desktop/Github\ Projects/transcripts-analytics/pull_agent_transcripts.py \
    --medium chat \
    --agent-template-id 24bd649e-6a44-4efd-af97-bfaf4dadfb43 \
    --start 2026-07-10T00:00:00Z --end 2026-08-12T23:59:59Z \
    --limit 50000 \
    --out "/Users/manupriya/Desktop/Github Projects/transcripts-analytics/transcripts.xlsx"
```

Valid **only** if the window holds under 50,000 tickets. Check with the §7 sanity query
first; if it is over, chunk per §5.
