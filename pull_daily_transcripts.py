#!/usr/bin/env python3
"""Pull transcripts day by day over a date range: one Excel workbook per date.

For each UTC day in [START, END] (inclusive):
  - skip if transcripts/transcripts_YYYY-MM-DD.xlsx already exists
  - download that day's tickets via `giga tickets download` into a per-day JSON
    cache dir (kept between runs, so retries only fetch what's missing)
  - flatten with pull_agent_transcripts.py's flatten() and write the workbook

Retries on HTTP 429 with exponential backoff (60s..960s): the analytics
service's bulk-download quota is per-org with roughly an hour-long window, and
the giga CLI itself exits on the first 429 without retrying.

Run from a directory containing giga.config.json (e.g. a flex-operations
clone), or pass --config-dir; the CLI resolves org/team from it, and the
default config-free team is usually the wrong tenant.

Examples:
  python3 pull_daily_transcripts.py 2026-08-03 2026-08-09
  python3 pull_daily_transcripts.py 2026-08-10 2026-08-10 \
      --agent-template-id 24bd649e-6a44-4efd-af97-bfaf4dadfb43 \
      --config-dir ~/src/flex-operations
"""

import argparse
import importlib.util
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_ID = "24bd649e-6a44-4efd-af97-bfaf4dadfb43"  # Customer Support Chat Agent

MAX_ATTEMPTS = 6
BASE_BACKOFF = 60  # seconds; doubles per retry: 60/120/240/480/960


def load_flattener():
    spec = importlib.util.spec_from_file_location(
        "pull_agent_transcripts", HERE / "pull_agent_transcripts.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def download_day(day: date, json_dir: Path, args) -> bool:
    cmd = [
        "giga", "tickets", "download",
        "--medium", args.medium,
        "-q", f"agent_template_id:{args.agent_template_id}",
        "--timestamp-start", f"{day.isoformat()}T00:00:00Z",
        "--timestamp-end", f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z",
        "--output-dir", str(json_dir),
        "--format", "json",
        "--limit", "50000",
        "--batch-size", "500",
        "--resume",
    ]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=args.config_dir)
        blob = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            return True
        if "429" in blob or "Too many requests" in blob:
            wait = BASE_BACKOFF * (2 ** (attempt - 1))
            print(f"  429 on attempt {attempt}/{MAX_ATTEMPTS}; sleeping {wait}s", flush=True)
            time.sleep(wait)
            continue
        print(f"  failed (exit {result.returncode}): {blob.strip()[:500]}", flush=True)
        return False
    print("  giving up after repeated 429s", flush=True)
    return False


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("start", type=date.fromisoformat, help="First day, YYYY-MM-DD (UTC)")
    parser.add_argument("end", type=date.fromisoformat, help="Last day, inclusive")
    parser.add_argument("--agent-template-id", default=DEFAULT_TEMPLATE_ID)
    parser.add_argument("--medium", default="chat", choices=["voice", "chat", "email"])
    parser.add_argument("--out-dir", type=Path, default=HERE / "transcripts",
                        help="Where the per-day .xlsx files go (default: ./transcripts)")
    parser.add_argument("--json-cache-dir", type=Path,
                        default=Path.home() / ".cache" / "giga_transcripts_daily",
                        help="Per-day raw JSON cache; kept so re-runs resume")
    parser.add_argument("--config-dir", default=None,
                        help="Directory containing giga.config.json (default: cwd)")
    parser.add_argument("--force", action="store_true",
                        help="Re-export days whose workbook already exists")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("end date is before start date")

    mod = load_flattener()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    done, skipped, failed = [], [], []
    day = args.start
    while day <= args.end:
        out = args.out_dir / f"transcripts_{day.isoformat()}.xlsx"
        print(f"[{day.isoformat()}]", flush=True)

        if out.exists() and not args.force:
            print(f"  {out.name} exists — skipping", flush=True)
            skipped.append(day.isoformat())
            day += timedelta(days=1)
            continue

        json_dir = args.json_cache_dir / args.agent_template_id / day.isoformat()
        json_dir.mkdir(parents=True, exist_ok=True)
        if not download_day(day, json_dir, args):
            failed.append(day.isoformat())
            day += timedelta(days=1)
            continue

        ticket_rows, message_rows = mod.flatten(json_dir)
        print(f"  tickets={len(ticket_rows)} messages={len(message_rows)}", flush=True)
        if not ticket_rows:
            print("  no tickets this day — no workbook written", flush=True)
        elif not mod.write_xlsx(out, ticket_rows, message_rows):
            print("  openpyxl missing — writing CSVs instead", flush=True)
            mod.write_csv(out.with_name(f"{out.stem}_tickets.csv"), mod.TICKET_COLUMNS, ticket_rows)
            mod.write_csv(out.with_name(f"{out.stem}_messages.csv"), mod.MESSAGE_COLUMNS, message_rows)
        done.append(day.isoformat())

        day += timedelta(days=1)
        if day <= args.end:
            time.sleep(10)  # stay friendly to the download quota between days

    print(f"\nexported={len(done)} skipped={len(skipped)} failed={len(failed)}")
    if failed:
        print(f"failed days (re-run to resume): {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
