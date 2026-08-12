# transcripts-analytics

Pull ticket transcripts for a specific agent (voice / chat / email) and export
them to Excel or CSV.

`pull_agent_transcripts.py` wraps the `giga` CLI for auth and download, then
flattens the per-conversation JSON into two tables:

- **Tickets** — one row per conversation: timestamps, medium, status, agent
  name/version/IDs, numbers, duration, and analysis fields (intent, summary,
  resolution, sentiment, CSAT, tags)
- **Messages** — one row per transcript turn: index, timestamp, role, content,
  tool calls

## Setup

```bash
uv tool install <path-to>/trillions/giga-sdk   # or: pip install -e
giga login                                     # client scope = the CLI's active team
pip install openpyxl                           # only needed for .xlsx output
```

## Usage

```bash
# All of last 4 weeks' chat tickets for one agent template -> Excel (two sheets)
python3 pull_agent_transcripts.py --medium chat \
    --agent-template-id <uuid> -r last4weeks --out transcripts.xlsx

# Voice tickets by agent name, absolute window -> two CSVs
python3 pull_agent_transcripts.py --medium voice --agent-name "Support Bot" \
    --start 2026-08-01T00:00:00Z --end 2026-08-11T00:00:00Z --out transcripts.csv
```

The agent can be targeted by `--agent-template-id` (all versions),
`--agent-id` (one version), or `--agent-name`. Time window via
`-r all|today|last1week|last4weeks|mtd|qtd|ytd` or `--start`/`--end`.
`--keep-json` preserves the raw per-conversation JSON download directory.
