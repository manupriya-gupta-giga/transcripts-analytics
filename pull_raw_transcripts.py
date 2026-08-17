#!/usr/bin/env python3
"""Pull raw per-conversation transcript JSON for a date range, stored date-wise.

Unlike pull_daily_transcripts.py (which flattens to Excel), this keeps the raw
CLI output — one JSON file per conversation, grouped by UTC day:

  raw_transcripts/
    2026-08-01/<conversationId>__<ticketId>.json
    2026-08-02/...

Before hitting the API, each day is seeded from the local JSON cache that
pull_daily_transcripts.py maintains (if present), so already-downloaded days
cost no quota. The `giga tickets download --resume` pass then fetches only
what is missing.

Run from a directory containing giga.config.json (e.g. a flex-operations
clone), or pass --config-dir; the CLI resolves org/team from it.

Examples:
  python3 pull_raw_transcripts.py 2026-08-01 2026-08-07
  python3 pull_raw_transcripts.py 2026-08-12 2026-08-12 --format txt \
      --config-dir ~/src/flex-operations
"""

import argparse
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_ID = "24bd649e-6a44-4efd-af97-bfaf4dadfb43"  # Customer Support Chat Agent
DEFAULT_CACHE = Path.home() / ".cache" / "giga_transcripts_daily"

MAX_ATTEMPTS = 6
BASE_BACKOFF = 60  # seconds; doubles per retry: 60/120/240/480/960


def seed_from_cache(day: date, out_dir: Path, cache_root: Path, template_id: str) -> int:
    """Copy already-downloaded JSON for this day into out_dir/<day>. Returns count."""
    day_dir = out_dir / day.isoformat()
    copied = 0
    src_root = cache_root / template_id / day.isoformat()
    if not src_root.exists():
        return 0
    day_dir.mkdir(parents=True, exist_ok=True)
    for src in src_root.rglob("*.json"):
        dst = day_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            copied += 1
    return copied


def download_day(day: date, args) -> bool:
    cmd = [
        "giga", "tickets", "download",
        "--medium", args.medium,
        "-q", f"agent_template_id:{args.agent_template_id}",
        "--timestamp-start", f"{day.isoformat()}T00:00:00Z",
        "--timestamp-end", f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z",
        "--output-dir", str(args.out_dir),
        "--format", args.format,
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
    parser.add_argument("--format", default="json", choices=["json", "txt"],
                        help="Artifact format the CLI writes (default: json; "
                             "cache seeding applies to json only)")
    parser.add_argument("--out-dir", type=Path, default=HERE / "raw_transcripts",
                        help="Root folder; one subfolder per day (default: ./raw_transcripts)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE,
                        help="pull_daily_transcripts.py JSON cache to seed from")
    parser.add_argument("--no-seed", action="store_true",
                        help="Skip cache seeding; always fetch via the API")
    parser.add_argument("--config-dir", default=None,
                        help="Directory containing giga.config.json (default: cwd)")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("end date is before start date")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    day = args.start
    while day <= args.end:
        print(f"[{day.isoformat()}]", flush=True)
        seeded = 0
        if args.format == "json" and not args.no_seed:
            seeded = seed_from_cache(day, args.out_dir, args.cache_dir, args.agent_template_id)
        ok = download_day(day, args)
        if not ok:
            failed.append(day.isoformat())
        have = sum(1 for _ in (args.out_dir / day.isoformat()).glob(f"*.{args.format}")) \
            if (args.out_dir / day.isoformat()).exists() else 0
        print(f"  seeded {seeded} from cache; {have} file(s) on disk", flush=True)
        day += timedelta(days=1)
        if day <= args.end:
            time.sleep(10)  # stay friendly to the download quota between days

    if failed:
        print(f"\nfailed day(s), re-run to resume: {failed}")
        sys.exit(1)
    print("\ndone")


if __name__ == "__main__":
    main()
