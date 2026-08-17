#!/usr/bin/env python3
"""Pull lightweight per-ticket metadata (no transcripts) for a date range.

Uses `giga tickets list -f json` — cheap paging, not subject to the bulk
download quota. Per Customer Support Chat Agent ticket it stores: id,
createdAt, intent, tags, resolutionStatus, and an `escalated` flag.

Escalation detection (in order of accuracy):
  - days >= 2026-07-10: membership in a second, filtered listing
    (cf.zendesk_ticket_creation_confirmed:true) — near-exact
  - earlier days (custom fields not backfilled): a summary-text proxy
    ("created/filed a support ticket", "escalated ...") — validated against
    create_ticket ground truth at precision 0.94-0.98, recall 0.99-1.0

Output: ticket_metadata/<date>.jsonl (skipped if the file already exists).

Example:
  python3 pull_ticket_metadata.py 2026-06-22 2026-07-09 --config-dir ~/src/flex-operations
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE_ID = "24bd649e-6a44-4efd-af97-bfaf4dadfb43"
CF_BACKFILL_START = date(2026, 7, 10)  # zendesk_* custom fields exist from here
PAGE_SIZE = 100
PAUSE = 0.7  # seconds between list calls, to stay friendly to the API

ESC_PROXY = re.compile(
    r"(creat|open|fil|submitt)(ed|ing)\b.{0,60}\btickets?"
    r"|tickets? (was|were|has been|have been) (created|opened|filed|submitted)"
    r"|\bescalat(ed|ing)\b", re.I)


def list_page(day: date, page: int, args, extra_query=""):
    cmd = [
        "giga", "tickets", "list",
        "--medium", "chat",
        "-q", f"agent_template_id:{TEMPLATE_ID}" + extra_query,
        "--timestamp-start", f"{day.isoformat()}T00:00:00Z",
        "--timestamp-end", f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z",
        "--limit", str(PAGE_SIZE), "--page", str(page), "-f", "json",
    ]
    for attempt in range(5):
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=args.config_dir)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass
        blob = (result.stdout or "") + (result.stderr or "")
        if "No tickets found" in blob:
            return {"items": [], "totalPages": 0}
        wait = 30 * (2 ** attempt)
        print(f"  list failed (attempt {attempt + 1}): {blob.strip()[:120]} — sleep {wait}s",
              flush=True)
        time.sleep(wait)
    raise RuntimeError(f"list kept failing for {day} page {page}")


def list_all(day: date, args, extra_query=""):
    items, page = [], 1
    while True:
        data = list_page(day, page, args, extra_query)
        items.extend(data.get("items") or [])
        if page >= (data.get("totalPages") or 0):
            return items
        page += 1
        time.sleep(PAUSE)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    parser.add_argument("--out-dir", type=Path, default=HERE / "ticket_metadata")
    parser.add_argument("--config-dir", default=None,
                        help="Directory containing giga.config.json (default: cwd)")
    parser.add_argument("--force", action="store_true", help="Refetch existing days")
    args = parser.parse_args()
    args.out_dir.mkdir(exist_ok=True)

    failed = []
    day = args.start
    while day <= args.end:
        out = args.out_dir / f"{day.isoformat()}.jsonl"
        if out.exists() and not args.force:
            print(f"[{day}] exists — skipping", flush=True)
            day += timedelta(days=1)
            continue
        try:
            items = list_all(day, args)
            use_cf = day >= CF_BACKFILL_START
            escalated_ids = set()
            if use_cf and items:
                time.sleep(PAUSE)
                escalated_ids = {t["id"] for t in list_all(
                    day, args, " AND cf.zendesk_ticket_creation_confirmed:true")}
            with out.open("w") as fh:
                for t in items:
                    if use_cf:
                        esc = t["id"] in escalated_ids
                    else:
                        esc = bool(ESC_PROXY.search(t.get("summary") or ""))
                    fh.write(json.dumps({
                        "id": t["id"], "createdAt": t.get("createdAt"),
                        "intent": t.get("intent"), "tags": t.get("tags") or [],
                        "resolutionStatus": t.get("resolutionStatus"),
                        "escalated": esc,
                        "escalation_source": "cf" if use_cf else "summary_proxy",
                    }) + "\n")
            n_esc = sum(1 for line in out.read_text().splitlines()
                        if json.loads(line)["escalated"])
            print(f"[{day}] tickets={len(items)} escalated={n_esc} "
                  f"({'cf' if use_cf else 'proxy'})", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[{day}] FAILED: {exc}", flush=True)
            failed.append(day.isoformat())
            if out.exists():
                out.unlink()  # never leave a partial day behind
        day += timedelta(days=1)
        time.sleep(PAUSE)

    if failed:
        print(f"failed days, re-run to fill: {failed}")
        sys.exit(1)
    print("done")


if __name__ == "__main__":
    main()
