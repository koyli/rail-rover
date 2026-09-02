#!/usr/bin/env python3
"""
Compare freshly-fetched data/tickets_raw.json against the last committed
version and flag a big drop -- catches National Rail site changes that make
fetch_tickets.py silently return too little data, the way a August 2026 site
redesign broke scraping for a month (91 tickets -> 0) without anyone
noticing, since the workflow just committed whatever came out.

Writes old_count/new_count/drop_pct/significant to $GITHUB_OUTPUT when run
inside a GitHub Actions step. Always exits 0 -- the workflow decides what to
do with the "significant" output.
"""
import json, os, subprocess
from pathlib import Path

DROP_THRESHOLD_PCT = 10
TICKETS_FILE = Path(__file__).parent.parent / "data" / "tickets_raw.json"


def committed_count():
    result = subprocess.run(
        ["git", "show", "HEAD:data/tickets_raw.json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return 0
    return len(json.loads(result.stdout))


def main():
    old_count = committed_count()
    new_count = len(json.loads(TICKETS_FILE.read_text()))
    drop_pct = round((old_count - new_count) / old_count * 100, 1) if old_count else 0.0
    significant = old_count > 0 and drop_pct >= DROP_THRESHOLD_PCT

    print(f"Ticket count: {old_count} -> {new_count} ({drop_pct:+.1f}% change)")
    if significant:
        print(f"SIGNIFICANT DROP: {drop_pct}% >= {DROP_THRESHOLD_PCT}% threshold")

    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"old_count={old_count}\n")
            f.write(f"new_count={new_count}\n")
            f.write(f"drop_pct={drop_pct}\n")
            f.write(f"significant={'true' if significant else 'false'}\n")


if __name__ == "__main__":
    main()
