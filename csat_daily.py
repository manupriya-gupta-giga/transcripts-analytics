#!/usr/bin/env python3
"""Daily average CSAT over a rolling window, split by containment.

Mirrors the three CSAT charts on the console Metrics View
(dashboard_view=019ffc55-4f2a-7836-8a54-aca2a76649ae):

  - Daily avg CSAT — all rated chats
  - Daily avg CSAT — contained chats only (no create_ticket)
  - Daily avg CSAT — escalated chats only (create_ticket)

Sources raw_transcripts/<date>/ (Customer Support Chat Agent, medium chat).
A chat is "rated" when analysis.csat is set (1-5); escalated when the agent
called create_ticket (same definition as the dashboard's tools.name filter
and the other reports here). Flagged users are excluded before aggregation
(--exclude-users, required unless --allow-unfiltered).

Output: results/csat_daily.html — a self-contained page (published via
GitLab Pages by the CI job, same as the other results/ reports).

Example:
  python3 csat_daily.py --exclude-users exclude_user_ids.txt
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_NAME = "Customer Support Chat Agent"
AGENT_TEMPLATE_ID = "24bd649e-6a44-4efd-af97-bfaf4dadfb43"

# Days excluded from analysis (data flagged inaccurate; same set as
# analyze_transcripts.py). Files stay on disk, they are just not read.
EXCLUDE_DAYS = {"2026-08-04"}

WINDOW_DAYS = 30


def customer_id(transcript):
    for m in transcript.get("messages") or []:
        store = m.get("variableStore") or {}
        cid = store.get("customer_id") or store.get("external_id")
        if cid:
            return cid
    return None


def is_escalated(transcript):
    return any(
        (tc.get("name") or (tc.get("function") or {}).get("name")) == "create_ticket"
        for m in transcript.get("messages") or [] for tc in m.get("toolCalls") or [])


def day_stats(folder: Path, exclude_users, report):
    """Per-day aggregates over rated chats, or None if no data on disk."""
    if not folder.exists() or not any(folder.glob("*.json")):
        return None
    day = {"nConv": 0,
           "all": {"n": 0, "sum": 0.0},
           "contained": {"n": 0, "sum": 0.0},
           "escalated": {"n": 0, "sum": 0.0}}
    for p in folder.glob("*.json"):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not (t.get("agentTemplateId") == AGENT_TEMPLATE_ID
                or t.get("agentName") == AGENT_NAME):
            continue
        if exclude_users and customer_id(t) in exclude_users:
            report["excluded"] += 1
            continue
        day["nConv"] += 1
        csat = (t.get("analysis") or {}).get("csat")
        if csat is None:
            continue
        csat = float(csat)
        seg = "escalated" if is_escalated(t) else "contained"
        for key in ("all", seg):
            day[key]["n"] += 1
            day[key]["sum"] += csat
    return day


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat,
                        help="First day (default: end minus 29 days)")
    parser.add_argument("--end", type=date.fromisoformat,
                        help="Last day, inclusive (default: yesterday UTC)")
    parser.add_argument("--raw-dir", type=Path, default=HERE / "raw_transcripts")
    parser.add_argument("--exclude-users", type=Path,
                        default=HERE / "exclude_user_ids.txt",
                        help="File with one flagged customer_id per line")
    parser.add_argument("--allow-unfiltered", action="store_true",
                        help="Proceed without a flagged-user list (not deliverable)")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "csat_daily.html")
    args = parser.parse_args()

    end = args.end or datetime.now(timezone.utc).date() - timedelta(days=1)
    start = args.start or end - timedelta(days=WINDOW_DAYS - 1)

    if args.exclude_users.exists():
        exclude_users = frozenset(
            line.strip() for line in args.exclude_users.read_text().splitlines()
            if line.strip())
        print(f"excluding {len(exclude_users)} flagged user ids")
    elif args.allow_unfiltered:
        exclude_users = frozenset()
        print("WARNING: no flagged-user list — results are not deliverable")
    else:
        sys.exit(f"error: {args.exclude_users} not found — fetch the flagged-user "
                 f"list first (see .claude/skills/exclude-flagged-users), or pass "
                 f"--allow-unfiltered for a non-deliverable run")

    report = {"excluded": 0}
    days, missing = [], []
    day = start
    while day <= end:
        iso = day.isoformat()
        if iso in EXCLUDE_DAYS:
            days.append({"date": iso, "excluded": True})
        else:
            stats = day_stats(args.raw_dir / iso, exclude_users, report)
            if stats is None:
                missing.append(iso)
                days.append({"date": iso, "missing": True})
            else:
                days.append({"date": iso, **{
                    k: ({"n": v["n"],
                         "avg": round(v["sum"] / v["n"], 3) if v["n"] else None}
                        if isinstance(v, dict) else v)
                    for k, v in stats.items() if k != "date"}})
        day += timedelta(days=1)

    have = [d for d in days if not d.get("missing") and not d.get("excluded")]
    if not have:
        sys.exit("error: no data for any day in the window — pull transcripts first")
    totals = {}
    for seg in ("all", "contained", "escalated"):
        n = sum(d[seg]["n"] for d in have)
        s = sum(d[seg]["avg"] * d[seg]["n"] for d in have if d[seg]["n"])
        totals[seg] = {"n": n, "avg": round(s / n, 3) if n else None}

    payload = {
        "agent": AGENT_NAME,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "flaggedUsers": len(exclude_users),
        "excludedConvs": report["excluded"],
        "excludedDays": sorted(EXCLUDE_DAYS & {d["date"] for d in days}),
        "missingDays": missing,
        "totals": totals,
        "days": days,
    }

    print(f"window {start} .. {end}: {len(have)} day(s) with data, "
          f"{len(missing)} missing, excluded days: {payload['excludedDays'] or 'none'}")
    print(f"excluded {report['excluded']} conversations from "
          f"{len(exclude_users)} flagged users")
    for seg, t in totals.items():
        print(f"  {seg:9s} n={t['n']:5d} avg={t['avg']}")

    template = (HERE / "csat_daily_template.html").read_text(encoding="utf-8")
    html = template.replace("/*__DATA__*/", json.dumps(payload))
    args.out.parent.mkdir(exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
