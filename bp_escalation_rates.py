#!/usr/bin/env python3
"""Escalation rate by intent around each billing-period (BP) day.

BP Day = the last day of a month. For each BP day and each intent, computes
the escalation rate (escalated / total chats, i.e. 1 - containment) for every
relative day T-7 .. T+9, where T is the BP day.

Output: one sheet, rows = (BP Day, Intent), columns = T-7 .. T+5.

Sources, per day: raw_transcripts/<date>/ when present (escalation =
create_ticket tool call, exact), else ticket_metadata/<date>.jsonl from
pull_ticket_metadata.py (escalation via cf flag or validated summary proxy).
Customer Support Chat Agent only. No noise filter — every listed conversation
counts, so all days share one basis. Intent is native analysis.intent when
present, else derived from the first taxonomy-mapped analysis tag
(GreetingOnly / RequestHumanSupport never derive an intent).

Example:
  python3 bp_escalation_rates.py --bp-days 2026-06-30 2026-07-31
"""

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_NAME = "Customer Support Chat Agent"
AGENT_TEMPLATE_ID = "24bd649e-6a44-4efd-af97-bfaf4dadfb43"
REL_DAYS = list(range(-7, 10))  # T-7 .. T+9
SKIP_DERIVE_TAGS = {"GreetingOnly", "RequestHumanSupport"}


def tag_to_intent():
    path = HERE / "tag_taxonomy.json"
    if not path.exists():
        return {}
    return {t["name"]: t["intentName"]
            for t in json.loads(path.read_text())["tags"]
            if t["name"] not in SKIP_DERIVE_TAGS}


def resolve_intent(intent, tags, taxonomy):
    if not intent:
        intent = next((taxonomy[tag] for tag in tags if tag in taxonomy), None)
    return intent or "(no intent)"


def customer_id(transcript):
    for m in transcript.get("messages") or []:
        store = m.get("variableStore") or {}
        cid = store.get("customer_id") or store.get("external_id")
        if cid:
            return cid
    return None


def day_stats(raw_dir: Path, meta_dir: Path, day: date, taxonomy,
              exclude_users=frozenset(), report=None):
    """{intent: [total, escalated]} for one day; None if no data available.

    Prefers raw transcripts (escalation = create_ticket tool call, exact);
    falls back to ticket_metadata jsonl from pull_ticket_metadata.py
    (escalation via cf flag or validated summary proxy). No noise filter in
    either path, so day-level rates are computed on the same basis.
    """
    stats = defaultdict(lambda: [0, 0])

    folder = raw_dir / day.isoformat()
    if folder.exists() and any(folder.glob("*.json")):
        for p in folder.glob("*.json"):
            try:
                t = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not (t.get("agentTemplateId") == AGENT_TEMPLATE_ID
                    or t.get("agentName") == AGENT_NAME):
                continue
            if exclude_users and customer_id(t) in exclude_users:
                if report is not None:
                    report["excluded"] += 1
                continue
            analysis = t.get("analysis") or {}
            intent = resolve_intent(analysis.get("intent"),
                                    analysis.get("tags") or [], taxonomy)
            escalated = any(
                (tc.get("name") or (tc.get("function") or {}).get("name")) == "create_ticket"
                for m in t.get("messages") or [] for tc in m.get("toolCalls") or [])
            for key in (intent, "ALL"):
                stats[key][0] += 1
                stats[key][1] += int(escalated)
        return stats

    meta = meta_dir / f"{day.isoformat()}.jsonl"
    if meta.exists():
        # Metadata records carry no customer id, so user exclusion cannot
        # apply here; flag the day so the caller can surface the gap.
        if exclude_users and report is not None:
            report["unfilterable_days"].add(day.isoformat())
        for line in meta.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            intent = resolve_intent(r.get("intent"), r.get("tags") or [], taxonomy)
            for key in (intent, "ALL"):
                stats[key][0] += 1
                stats[key][1] += int(bool(r.get("escalated")))
        return stats
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bp-days", nargs="+", type=date.fromisoformat,
                        default=[date(2026, 6, 30), date(2026, 7, 31)],
                        help="BP days (last day of month), e.g. 2026-06-30")
    parser.add_argument("--raw-dir", type=Path, default=HERE / "raw_transcripts")
    parser.add_argument("--meta-dir", type=Path, default=HERE / "ticket_metadata")
    parser.add_argument("--min-chats", type=int, default=5,
                        help="Blank out cells with fewer chats than this")
    parser.add_argument("--top-intents", type=int, default=8,
                        help="Keep only the N highest-volume intents (plus ALL)")
    parser.add_argument("--exclude-users", type=Path, default=None,
                        help="File with one customer_id per line; their conversations "
                             "are dropped (raw-transcript days only — metadata "
                             "days have no customer id)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    taxonomy = tag_to_intent()
    out = args.out or HERE / "results" / "escalation_rate_breakdown.xlsx"

    exclude_users = frozenset()
    if args.exclude_users:
        exclude_users = frozenset(
            line.strip() for line in args.exclude_users.read_text().splitlines()
            if line.strip())
        print(f"excluding {len(exclude_users)} user ids")
    report = {"excluded": 0, "unfilterable_days": set()}

    # per BP day: rel_day -> {intent: [total, escalated]}
    per_bp = {}
    intents = set()
    for bp in args.bp_days:
        per_bp[bp] = {}
        for rel in REL_DAYS:
            stats = day_stats(args.raw_dir, args.meta_dir, bp + timedelta(days=rel),
                              taxonomy, exclude_users, report)
            per_bp[bp][rel] = stats
            if stats:
                intents.update(stats.keys())

    if exclude_users:
        print(f"excluded {report['excluded']} conversations from raw-transcript days")
        if report["unfilterable_days"]:
            print("WARNING: no customer ids in metadata fallback — exclusion NOT "
                  "applied on:", ", ".join(sorted(report["unfilterable_days"])))

    # Keep only the top-N intents by total chat volume across all windows.
    volume = defaultdict(int)
    for bp in args.bp_days:
        for stats in per_bp[bp].values():
            if stats:
                for intent, (total, _esc) in stats.items():
                    # OutOfDomain exists only where native intent classification
                    # ran (Aug 8+), so its early cells are structurally empty.
                    if intent not in ("ALL", "(no intent)", "OutOfDomain"):
                        volume[intent] += total
    top = sorted(volume, key=volume.get, reverse=True)[:args.top_intents]
    print("top intents by volume:",
          ", ".join(f"{i} ({volume[i]})" for i in top))
    intents = {"ALL", *top}

    def order(i):  # ALL first, then by volume
        return (0 if i == "ALL" else 1, -volume.get(i, 0))

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Escalation rates"
    header = ["BP Day", "Intent"] + [f"T{r:+d}" if r else "T" for r in REL_DAYS]
    ws.append(header)
    for bp in args.bp_days:
        for intent in sorted(intents, key=order):
            row = [bp.isoformat(), intent]
            for rel in REL_DAYS:
                stats = per_bp[bp][rel]
                cell = None
                if stats and intent in stats and stats[intent][0] >= args.min_chats:
                    total, esc = stats[intent]
                    cell = round(esc / total, 4)
                row.append(cell)
            ws.append(row)
            for c in ws[ws.max_row][2:]:
                c.number_format = "0.0%"
    ws.freeze_panes = "C2"

    # Sample sizes behind every rate, for judging significance.
    ws2 = wb.create_sheet("Chat counts")
    ws2.append(header)
    for bp in args.bp_days:
        for intent in sorted(intents, key=order):
            row = [bp.isoformat(), intent]
            for rel in REL_DAYS:
                stats = per_bp[bp][rel]
                row.append(stats[intent][0] if stats and intent in stats else None)
            ws2.append(row)
    ws2.freeze_panes = "C2"

    out.parent.mkdir(exist_ok=True)
    wb.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
