#!/usr/bin/env python3
"""Pull ticket transcripts for one agent and export them to Excel or CSV.

Uses the `giga` CLI (from giga-sdk) for auth + download, then flattens the
per-conversation JSON files into two tables:

  - tickets:  one row per conversation (metadata + analysis)
  - messages: one row per transcript turn

The client scope comes from the CLI's active team (set it with `giga login` /
`giga teams`); the agent scope comes from --agent-template-id, --agent-id, or
--agent-name.

Examples:
  # All of last week's chat tickets for one agent template -> Excel
  python3 pull_agent_transcripts.py --medium chat \
      --agent-template-id 0198c9a2-1111-2222-3333-444455556666 \
      -r last1week --out transcripts.xlsx

  # Voice tickets by agent name, absolute window -> CSVs
  python3 pull_agent_transcripts.py --medium voice --agent-name "Support Bot" \
      --start 2026-08-01T00:00:00Z --end 2026-08-11T00:00:00Z --out transcripts.csv

Setup (one-time), if `giga` is not installed:
  uv tool install /Users/manupriya/trillions/giga-sdk   # or: pip install -e .../giga-sdk
  giga login
"""

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Label maps mirrored from giga-sdk giga/cli/commands/tickets/helpers.py
MEDIUM_LABELS = {0: "unknown", 1: "voice", 2: "chat", 3: "email", 4: "translation"}
STATUS_LABELS = {
    0: "unknown", 1: "active", 2: "awaiting_customer", 3: "user_ended",
    4: "agent_ended", 5: "transferred", 6: "failed", 7: "unreachable",
    8: "voicemail", 10: "expired",
}
ROLE_LABELS = {0: "UNKNOWN", 1: "USER", 2: "AGENT", 3: "SYSTEM", 4: "TOOL", 5: "HUMAN_AGENT"}
RESOLUTION_LABELS = {0: "unknown", 1: "resolved", 2: "abandoned", 3: "not_applicable", 4: "transferred"}

TICKET_COLUMNS = [
    "id", "conversationId", "externalConversationId", "createdAt", "finishedAt",
    "medium", "channel", "status", "agentName", "agentVersion", "agentId",
    "agentTemplateId", "fromNumber", "toNumber", "emailFrom", "isOutbound",
    "languageCode", "callDurationMs", "transferTo", "intent", "summary",
    "resolutionStatus", "sentiment", "csat", "tags", "messageCount",
]
MESSAGE_COLUMNS = [
    "ticketId", "conversationId", "agentName", "messageIndex", "sentAt",
    "role", "content", "toolCalls",
]

EXCEL_CELL_LIMIT = 32_000  # true limit is 32,767 chars; keep headroom


def label(value, mapping):
    if isinstance(value, int):
        return mapping.get(value, str(value))
    return value if value is not None else ""


def build_lucene_query(args) -> str:
    if args.agent_template_id:
        # Analytics stores bare UUIDs; strip a pasted Django-style prefix.
        bare = args.agent_template_id.removeprefix("agent_template_")
        return f"agent_template_id:{bare}"
    if args.agent_id:
        return f"agent_id:{args.agent_id.removeprefix('agent_')}"
    return f'agent_name:"{args.agent_name}"'


def run_download(args, json_dir: Path) -> None:
    cmd = [
        "giga", "tickets", "download",
        "--medium", args.medium,
        "-q", build_lucene_query(args),
        "--output-dir", str(json_dir),
        "--format", "json",
        "--limit", str(args.limit),
    ]
    if args.start and args.end:
        cmd += ["--timestamp-start", args.start, "--timestamp-end", args.end]
    elif args.range:
        cmd += ["-r", args.range]

    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"error: giga tickets download failed (exit {result.returncode})")


def flatten(json_dir: Path):
    ticket_rows, message_rows = [], []
    files = sorted(p for p in json_dir.rglob("*.json") if p.name != "stats.txt")
    for path in files:
        try:
            ticket = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: skipping {path.name}: {exc}", file=sys.stderr)
            continue

        analysis = ticket.get("analysis") or {}
        messages = ticket.get("messages") or []
        ticket_rows.append({
            "id": ticket.get("id", ""),
            "conversationId": ticket.get("conversationId", ""),
            "externalConversationId": ticket.get("externalConversationId", ""),
            "createdAt": ticket.get("createdAt", ""),
            "finishedAt": ticket.get("finishedAt", ""),
            "medium": label(ticket.get("medium"), MEDIUM_LABELS),
            "channel": ticket.get("channel", ""),
            "status": label(ticket.get("status"), STATUS_LABELS),
            "agentName": ticket.get("agentName", ""),
            "agentVersion": ticket.get("agentVersion", ""),
            "agentId": ticket.get("agentId", ""),
            "agentTemplateId": ticket.get("agentTemplateId", ""),
            "fromNumber": ticket.get("fromNumber", ""),
            "toNumber": ticket.get("toNumber", ""),
            "emailFrom": ticket.get("emailFrom", ""),
            "isOutbound": ticket.get("isOutbound", ""),
            "languageCode": ticket.get("languageCode", ""),
            "callDurationMs": ticket.get("callDurationMs", ""),
            "transferTo": ticket.get("transferTo", ""),
            "intent": analysis.get("intent", ""),
            "summary": analysis.get("summary", ""),
            "resolutionStatus": label(analysis.get("resolutionStatus"), RESOLUTION_LABELS),
            "sentiment": analysis.get("sentiment", ""),
            "csat": analysis.get("csat", ""),
            "tags": ", ".join(analysis.get("tags") or []),
            "messageCount": len(messages),
        })
        for message in messages:
            tool_calls = message.get("toolCalls")
            message_rows.append({
                "ticketId": ticket.get("id", ""),
                "conversationId": ticket.get("conversationId", ""),
                "agentName": ticket.get("agentName", ""),
                "messageIndex": message.get("messageIndex", ""),
                "sentAt": message.get("sentAt", ""),
                "role": label(message.get("role"), ROLE_LABELS),
                "content": message.get("content") or "",
                "toolCalls": json.dumps(tool_calls, ensure_ascii=False) if tool_calls else "",
            })
    return ticket_rows, message_rows


def write_csv(path: Path, columns, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


def write_xlsx(path: Path, ticket_rows, message_rows) -> bool:
    try:
        from openpyxl import Workbook
    except ImportError:
        return False

    workbook = Workbook()
    for sheet_name, columns, rows in (
        ("Tickets", TICKET_COLUMNS, ticket_rows),
        ("Messages", MESSAGE_COLUMNS, message_rows),
    ):
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(columns)
        for row in rows:
            sheet.append([
                str(row.get(col, ""))[:EXCEL_CELL_LIMIT] if row.get(col) is not None else ""
                for col in columns
            ])
        sheet.freeze_panes = "A2"
    workbook.remove(workbook["Sheet"])
    workbook.save(path)
    print(f"wrote {path} ({len(ticket_rows)} tickets, {len(message_rows)} messages)")
    return True


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    agent = parser.add_mutually_exclusive_group(required=True)
    agent.add_argument("--agent-template-id", help="AgentTemplate UUID (prefix ok, it is stripped)")
    agent.add_argument("--agent-id", help="Agent version UUID")
    agent.add_argument("--agent-name", help='Agent display name, e.g. "Support Bot"')
    parser.add_argument("--medium", required=True, choices=["voice", "chat", "email"])
    parser.add_argument("-r", "--range", default="last1week",
                        help="Relative window: all|today|last1week|last4weeks|mtd|qtd|ytd (default last1week)")
    parser.add_argument("--start", help="ISO start, e.g. 2026-08-01T00:00:00Z (with --end, overrides -r)")
    parser.add_argument("--end", help="ISO end")
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--out", default="transcripts.xlsx",
                        help=".xlsx for one Excel file with two sheets, .csv for two CSV files")
    parser.add_argument("--keep-json", action="store_true",
                        help="Keep the raw per-conversation JSON download directory")
    args = parser.parse_args()
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be used together")

    if shutil.which("giga") is None:
        sys.exit(
            "error: the `giga` CLI is not installed.\n"
            "  uv tool install /Users/manupriya/trillions/giga-sdk   # or pip install -e\n"
            "  giga login"
        )

    out = Path(args.out)
    json_dir = out.parent / f".{out.stem}_json" if args.keep_json else Path(
        tempfile.mkdtemp(prefix="giga_transcripts_")
    )
    try:
        run_download(args, json_dir)
        ticket_rows, message_rows = flatten(json_dir)
        if not ticket_rows:
            sys.exit("error: no tickets matched — check the agent id/name, medium, and time range")

        if out.suffix.lower() == ".xlsx":
            if not write_xlsx(out, ticket_rows, message_rows):
                print("openpyxl not installed (pip install openpyxl) — writing CSVs instead",
                      file=sys.stderr)
                write_csv(out.with_name(f"{out.stem}_tickets.csv"), TICKET_COLUMNS, ticket_rows)
                write_csv(out.with_name(f"{out.stem}_messages.csv"), MESSAGE_COLUMNS, message_rows)
        else:
            write_csv(out.with_name(f"{out.stem}_tickets.csv"), TICKET_COLUMNS, ticket_rows)
            write_csv(out.with_name(f"{out.stem}_messages.csv"), MESSAGE_COLUMNS, message_rows)
    finally:
        if not args.keep_json:
            shutil.rmtree(json_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
